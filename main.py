import os
import re
import shutil
import asyncio
from datetime import datetime, date
from pathlib import Path
from typing import Set, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.event.filter import EventMessageType, on_astrbot_loaded
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.api.message_components import Plain, Reply, At

from .utils.cookie_manager import CookieManager
from .utils.twitter_fetcher import TwitterFetcher, get_orig_image_url
from .utils.wd14_filter import WD14Filter
from .utils.ai_evaluator import AIEvaluator
from .utils.dedup_manager import DedupManager

@register(
    "astrbot_plugin_twitanime",
    "LaPluma588",
    "基于 WD14 与 Gemini 多模态 AI 的推特二次元插画抓取与审美精选插件",
    "1.2.0"
)
class TwitanimePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        self.plugin_dir = Path(__file__).parent
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_twitanime"
        self.temp_dir = self.data_dir / "temp"
        self.saved_dir = self.data_dir / "saved"
        self.cookies_file = self.data_dir / "cookies.json"

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.saved_dir.mkdir(parents=True, exist_ok=True)

        tw_conf = config.get("twitter_section", {})
        gm_conf = config.get("gemini_section", {})
        ft_conf = config.get("filter_section", {})
        st_conf = config.get("storage_section", {})
        sc_conf = config.get("schedule_section", {})

        # 存储、自动点赞与下载配置
        self.auto_like = bool(st_conf.get("auto_like", False))
        self.save_local = bool(st_conf.get("save_local", False))
        self.download_orig = bool(st_conf.get("download_orig", False))
        self.cookie_json_str = tw_conf.get("cookie_json", "[]")
        self.proxy = tw_conf.get("proxy", "").strip() or None

        # 解析 Gemini 多 Key 配置（支持 template_list 和旧版字符串格式）
        raw_keys = gm_conf.get("gemini_api_key", [])
        if isinstance(raw_keys, list):
            self.gemini_keys = [item.get("key", "").strip() for item in raw_keys if item.get("key", "").strip()]
        else:
            raw_keys_str = str(raw_keys).replace("\n", ",").split(",")
            self.gemini_keys = [k.strip() for k in raw_keys_str if k.strip()]
        self.gemini_model = gm_conf.get("model_name", "gemini-3.1-flash-lite")
        self.score_threshold = float(gm_conf.get("score_threshold", 7.0))
        self.custom_prompt = gm_conf.get("custom_prompt", "").strip()
        self.wd14_threshold = float(ft_conf.get("wd14_threshold", 0.35))
        self.wd14_enabled = bool(ft_conf.get("wd14_enabled", False))
        self.whitelist_tags: Set[str] = {
            t.strip() for t in ft_conf.get("whitelist_tags", "").split(",") if t.strip()
        }
        self.must_reject_tags: Set[str] = {
            t.strip() for t in ft_conf.get("must_reject_tags", "").split(",") if t.strip()
        }
        self.reject_business_user = bool(ft_conf.get("reject_business_user", True))

        CookieManager.sanitize_and_save(self.cookie_json_str, self.cookies_file)

        model_path = self.plugin_dir / "models" / "wd14" / "model.onnx"
        tags_path = self.plugin_dir / "models" / "wd14" / "selected_tags.csv"

        self.wd14_filter = None
        if self.wd14_enabled:
            if model_path.exists() and tags_path.exists():
                try:
                    self.wd14_filter = WD14Filter(model_path, tags_path, threshold=self.wd14_threshold)
                    logger.info("WD14 ONNX 粗筛模型初始化成功！")
                except Exception as e:
                    logger.error(f"WD14 模型加载失败: {e}")
            else:
                logger.warning(f"未找到 WD14 模型文件 ({model_path})，将跳过本地粗筛环节。")
        else:
            logger.info("WD14 本地粗筛已关闭，所有图片将直接进入 Gemini AI 审核。")

        self.db_file = self.data_dir / "data.db"
        self.dedup_mgr = DedupManager(self.db_file)

        # 定时投放配置
        self.schedule_enabled = bool(sc_conf.get("enabled", False))
        raw_times = sc_conf.get("times", "08:00,20:00")
        self.schedule_times = [t.strip() for t in raw_times.split(",") if t.strip()]
        self.schedule_count = max(1, min(int(sc_conf.get("count", 3)), 10))

    # ═══════════════════════════════════════
    #  公共精选方法
    # ═══════════════════════════════════════

    async def _select_and_prepare_tweets(
        self, fetcher: TwitterFetcher, evaluator: AIEvaluator, target_count: int
    ) -> List[dict]:
        """
        核心精选方法：流式抓取 Timeline → 去重 → WD14 粗筛 → Gemini 打分 → 下载最终图片。
        返回精选通过的推文数据列表，不负责发送。

        每项格式：
        {
            "tweet_id": str,
            "author_text": str,           # "name (@handle)"
            "author_handle": str,         # "@handle"
            "images": [                   # 已下载的最终发送图片
                {"path": Path, "is_orig": bool},
                ...
            ]
        }
        """
        results: List[dict] = []
        scanned = 0

        async for tweet_data in fetcher.fetch_image_tweets_stream(batch_size=20):
            if len(results) >= target_count:
                break

            scanned += 1
            tweet_id = tweet_data["tweet_id"]
            author_name = tweet_data["author_name"]
            author_handle = f"@{tweet_data['author_screen_name']}" if tweet_data["author_screen_name"] else ""
            verified_type = tweet_data.get("verified_type", "")
            photos = tweet_data["photos"]

            # 去重拦截
            if await self.dedup_mgr.is_processed(tweet_id, action_type="fetch_push"):
                logger.info(f"推文 {tweet_id} 已在去重库中，跳过。")
                continue

            # 金标/企业账号拦截
            if self.reject_business_user and verified_type == "Business":
                logger.info(f"[金标账号拦截] 作者: {author_name} ({author_handle}) 的 verified_type 为 Business，拦截此推文 (ID: {tweet_id})")
                await self.dedup_mgr.record_action(
                    tweet_id=tweet_id, action_type="fetch_push",
                    status="filtered", author_handle=author_handle,
                    reason="拦截金标企业账号 (verified_type=Business)"
                )
                continue

            author_text = f"{author_name} ({author_handle})" if author_handle else author_name
            logger.info(f"正在处理第 {scanned} 条推文 (ID: {tweet_id}, 共 {len(photos)} 张图片)...")

            passed_items = []
            filter_reason = ""

            for m_idx, media_obj, base_url in photos:
                temp_eval_path = self.temp_dir / f"tweet_{tweet_id}_{m_idx}_eval.jpg"

                ok = await fetcher.download_image_direct(base_url, temp_eval_path)
                if not ok:
                    try:
                        await media_obj.download(str(temp_eval_path))
                    except Exception:
                        continue

                if self.wd14_filter:
                    passed, reason = self.wd14_filter.check_pass(
                        str(temp_eval_path),
                        whitelist=self.whitelist_tags,
                        must_reject=self.must_reject_tags
                    )
                    if not passed:
                        logger.info(f"[WD14 粗筛拦截] 原因: {reason}")
                        filter_reason = f"WD14: {reason}"
                        if temp_eval_path.exists():
                            temp_eval_path.unlink()
                        continue

                ai_res = await evaluator.evaluate(str(temp_eval_path))
                if ai_res.get("fatal"):
                    raise RuntimeError(f"Gemini API 调用终止: {ai_res['reason']}")
                if ai_res["pass"]:
                    passed_items.append({
                        "m_idx": m_idx,
                        "eval_path": temp_eval_path,
                        "base_url": base_url
                    })
                else:
                    filter_reason = f"Gemini({ai_res['score']}分): {ai_res['reason']}"
                    if temp_eval_path.exists():
                        temp_eval_path.unlink()

            if not passed_items:
                await self.dedup_mgr.record_action(
                    tweet_id=tweet_id, action_type="fetch_push",
                    status="filtered", author_handle=author_handle,
                    reason=filter_reason
                )
                continue

            # 精选通过 → 记录去重
            await self.dedup_mgr.record_action(
                tweet_id=tweet_id, action_type="fetch_push",
                status="success", author_handle=author_handle
            )

            # 自动点赞
            if self.auto_like:
                if not await self.dedup_mgr.is_processed(tweet_id, action_type="like", status="success"):
                    logger.info(f"自动点赞开启，正在为精选通过的推文 (ID: {tweet_id}) 点赞...")
                    like_ok = await fetcher.favorite_tweet_by_id(tweet_id)
                    await self.dedup_mgr.record_action(
                        tweet_id=tweet_id, action_type="like",
                        status="success" if like_ok else "failed",
                        author_handle=author_handle,
                        reason="" if like_ok else "auto_like favorite() 执行失败"
                    )

            # 下载最终发送图片（原图补拉）
            images = []
            for item in passed_items:
                m_idx = item["m_idx"]
                eval_path: Path = item["eval_path"]
                base_url = item["base_url"]
                final_path = eval_path
                is_orig = False

                if self.download_orig:
                    orig_url = get_orig_image_url(base_url)
                    orig_path = self.temp_dir / f"tweet_{tweet_id}_{m_idx}_orig.jpg"
                    logger.info(f"[放行后补拉原图]: {orig_url}")
                    if await fetcher.download_image_direct(orig_url, orig_path):
                        final_path = orig_path
                        is_orig = True
                        if eval_path.exists():
                            eval_path.unlink()
                    else:
                        logger.warning("原图补拉失败，回退发送评估标准图。")

                images.append({"path": final_path, "is_orig": is_orig})

            # 本地保存
            if self.save_local:
                for img in images:
                    dest = self.saved_dir / img["path"].name
                    shutil.copy(img["path"], dest)
                    logger.info(f"插画已保存至: {dest}")

            results.append({
                "tweet_id": tweet_id,
                "author_text": author_text,
                "author_handle": author_handle,
                "images": images
            })

        logger.info(f"精选完成，共 {len(results)} 条推文通过（扫描 {scanned} 条）。")
        return results

    @filter.command("Ximage", alias={"x", "精选"})
    async def fetch_x_images(self, event: AstrMessageEvent, count: int = 1):
        """抓取并推送合格的二次元插画推文
        用法: /Ximage <数量> 或 /x <数量> 或 /精选 <数量>
        """
        target_tweet_count = max(1, min(count, 10))

        if not self.gemini_keys:
            yield event.plain_result("未配置 Gemini API Key，无法使用 AI 审美精选功能。")
            return

        quality_tip = "【原图拉取已开启】" if self.download_orig else ""
        yield event.plain_result(f"正在为您检索并精选 {target_tweet_count} 条合格插画推文{quality_tip}，请稍候...")
        logger.info(f"开始处理，目标 {target_tweet_count} 条推文 | 原图补拉模式: {self.download_orig}")

        fetcher = TwitterFetcher(cookies_file=self.cookies_file, proxy=self.proxy)
        ok, err_msg = await fetcher.init_client()
        if not ok:
            yield event.plain_result(f"Twitter Cookie 失效，请检查 Cookie 配置。\n{err_msg}")
            return

        evaluator = AIEvaluator(
            api_keys=self.gemini_keys,
            model_name=self.gemini_model,
            score_threshold=self.score_threshold,
            proxy=self.proxy,
            custom_prompt=self.custom_prompt
        )

        try:
            results = await self._select_and_prepare_tweets(fetcher, evaluator, target_tweet_count)
        except RuntimeError as e:
            yield event.plain_result(str(e))
            return

        if not results:
            yield event.plain_result("检索完当前 Timeline，未找到符合要求的精选插画推文。")
            return

        sent = 0
        for r in results:
            for img in r["images"]:
                chain = MessageChain()
                orig_tag = " [高清原图]" if img["is_orig"] else ""
                chain.message(f"作者：{r['author_text']}\n推文 ID：{r['tweet_id']}{orig_tag}")
                chain.file_image(str(img["path"]))
                await event.send(chain)
                await asyncio.sleep(1.0)
                if img["path"].exists():
                    img["path"].unlink()
            sent += 1
            await asyncio.sleep(1.5)

        if sent < target_tweet_count:
            yield event.plain_result(f"精选完成，共推送 {sent} 条合格推文。")

    # 功能3实现：/Xfetch 指令按 ID 拉取完整推文图文
    @filter.command("Xfetch", alias={"xf", "抓取"})
    async def fetch_tweet_by_id(self, event: AstrMessageEvent, tweet_id: str = ""):
        """拉取指定推文 ID 的完整图文内容（文本 + 图片，不含视频），所有内容组装在同一条消息中
        用法: /Xfetch <推文ID> 或 /xf <推文ID> 或 /抓取 <推文ID>
        """
        target_id = tweet_id.strip()

        if not target_id:
            target_id = self.extract_tweet_id_from_chain(event.get_messages())

        if not target_id:
            yield event.plain_result("请提供具体的推文 ID。用法: /Xfetch <推文ID> 或 /xf <推文ID> 或 /抓取 <推文ID>")
            return

        yield event.plain_result(f"正在拉取推文 (ID: {target_id}) 的完整内容，请稍候...")

        fetcher = TwitterFetcher(cookies_file=self.cookies_file, proxy=self.proxy)
        ok, err_msg = await fetcher.init_client()
        if not ok:
            yield event.plain_result(f"Twitter Cookie 失效，请检查 Cookie 配置。\n{err_msg}")
            return

        tweet_data = await fetcher.fetch_tweet_data(target_id)
        if not tweet_data:
            yield event.plain_result(f"无法获取推文 (ID: {target_id})，可能已被删除或限制访问。")
            return

        author_text = (
            f"{tweet_data['author_name']} (@{tweet_data['author_screen_name']})"
            if tweet_data['author_screen_name'] else tweet_data['author_name']
        )
        tweet_text = (tweet_data['text'] or "").strip()
        photos = tweet_data['photos']

        if not photos:
            header = f"推文无图片附件\n作者: {author_text}\nID: {target_id}"
            body = f"\n\n{tweet_text}" if tweet_text else ""
            yield event.plain_result(f"{header}{body}")
            return

        # 下载所有图片
        downloaded_paths = []
        for idx, url in enumerate(photos):
            orig_url = get_orig_image_url(url)
            save_path = self.temp_dir / f"fetch_{target_id}_{idx}.jpg"
            success = await fetcher.download_image_direct(orig_url, save_path)
            if success:
                downloaded_paths.append(save_path)
            else:
                # 原图下载失败，回退标准图
                fallback_path = self.temp_dir / f"fetch_{target_id}_{idx}_std.jpg"
                if await fetcher.download_image_direct(url, fallback_path):
                    downloaded_paths.append(fallback_path)

        if not downloaded_paths:
            yield event.plain_result(f"图片下载失败（共 {len(photos)} 张），请稍后重试。")
            return

        # 拼装单条消息链：文本 + 所有图片
        chain = MessageChain()
        header = f"作者: {author_text}\nID: {target_id}"
        chain.message(f"{header}\n\n{tweet_text}" if tweet_text else header)
        for img_path in downloaded_paths:
            chain.file_image(str(img_path))

        await event.send(chain)

        # 清理临时文件
        for p in downloaded_paths:
            if p.exists():
                p.unlink()

    # 功能2实现：/Xlike 指令版本点赞
    @filter.command("Xlike", alias={"xl", "赞"})
    async def cmd_like_tweet(self, event: AstrMessageEvent, tweet_id: str = ""):
        """点赞指定推文
        用法: /Xlike <推文ID> 或 /xl <推文ID> 或 /赞 <推文ID>
        """
        target_id = tweet_id.strip()

        # 参数为空时，尝试从引用链获取
        if not target_id:
            target_id = self.extract_tweet_id_from_chain(event.get_messages())

        if not target_id:
            yield event.plain_result("请提供具体的推文 ID，或在回复 Bot 发送的插画消息时使用 /xl <推文ID> 或 /赞 <推文ID> 命令。")
            return

        await self._execute_like(event, target_id)

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_like_reply(self, event: AstrMessageEvent):
        """监听 @机器人 + 引用插画推文 + 发送 '喜欢/点赞' 的自然语言操作"""
        if not self.is_at_bot(event):
            return

        raw_msg = event.message_str.strip()
        like_keywords = ["喜欢", "点赞", "赞"]
        if not any(kw in raw_msg for kw in like_keywords):
            return

        tweet_id = self.extract_tweet_id_from_chain(event.get_messages())
        if not tweet_id:
            return

        await self._execute_like(event, tweet_id)

    async def _execute_like(self, event: AstrMessageEvent, tweet_id: str):
        """点赞公共逻辑拆分封装"""
        if await self.dedup_mgr.is_processed(tweet_id, action_type="like", status="success"):
            await event.send(event.plain_result(f"这条推文 (ID: {tweet_id}) 之前已经点过赞啦，无需重复操作~"))
            return

        await event.send(event.plain_result(f"收到！正在为您点赞推文 (ID: {tweet_id})，请稍候..."))

        fetcher = TwitterFetcher(cookies_file=self.cookies_file, proxy=self.proxy)
        ok, err_msg = await fetcher.init_client()
        if not ok:
            await event.send(event.plain_result(f"Twitter Cookie 失效，请检查 Cookie 配置。\n{err_msg}"))
            return

        success = await fetcher.favorite_tweet_by_id(tweet_id)

        if success:
            await self.dedup_mgr.record_action(
                tweet_id=tweet_id,
                action_type="like",
                status="success"
            )
            await event.send(event.plain_result(f"已成功为您点赞该推文！(ID: {tweet_id})"))
        else:
            await self.dedup_mgr.record_action(
                tweet_id=tweet_id,
                action_type="like",
                status="failed",
                reason="favorite() 执行失败"
            )
            await event.send(event.plain_result(f"点赞失败 (ID: {tweet_id})，可能推文已被删除或触发了 Twitter 限流。"))

    def is_at_bot(self, event: AstrMessageEvent) -> bool:
        """通用工具函数：检测当前消息链中是否 @ 了机器人本身"""
        try:
            bot_self_id = str(event.get_self_id())
        except Exception as e:
            logger.error(f"获取 Bot Self ID 失败: {e}")
            return False

        message_chain = event.get_messages()
        for comp in message_chain:
            if isinstance(comp, At):
                target_id = str(getattr(comp, 'qq', getattr(comp, 'target', '')))
                if target_id == bot_self_id:
                    return True
        return False

    def extract_tweet_id_from_chain(self, chain: list) -> str:
        """从消息链组件中递归提取推文 ID"""
        for comp in chain:
            if isinstance(comp, Plain):
                match = re.search(r"推文\s*ID[：:]\s*(\d+)", comp.text)
                if match:
                    return match.group(1)
            elif isinstance(comp, Reply) and comp.chain:
                res = self.extract_tweet_id_from_chain(comp.chain)
                if res:
                    return res
        return ""

    # ═══════════════════════════════════════
    #  定时投放
    # ═══════════════════════════════════════

    @on_astrbot_loaded()
    async def start_scheduler(self):
        """AstrBot 加载完成后启动后台定时循环"""
        asyncio.create_task(self._schedule_loop())

    async def _schedule_loop(self):
        """后台循环：每 30 秒检查一次是否需要执行定时投放"""
        await asyncio.sleep(15)  # 等插件完全就绪
        while True:
            try:
                if self.schedule_enabled:
                    now = datetime.now()
                    cur_time = now.strftime("%H:%M")
                    today_str = now.strftime("%Y-%m-%d")

                    for slot in self.schedule_times:
                        if cur_time == slot:
                            if await self.dedup_mgr.is_slot_posted_today(slot):
                                logger.info(f"定时投放 [{slot}] 今天已执行过，跳过。")
                            else:
                                logger.info(f"定时投放 [{slot}] 触发，开始执行...")
                                await self._do_scheduled_post(slot, today_str)
            except Exception as e:
                logger.error(f"定时投放循环异常: {e}")

            await asyncio.sleep(30)

    async def _do_scheduled_post(self, slot: str, today_str: str) -> Tuple[bool, str]:
        """执行一次定时投放
        Returns:
            Tuple[是否成功, 描述信息]
        """
        logger.info(f"定时投放：开始精选 {self.schedule_count} 张图片...")

        if not self.gemini_keys:
            msg = "未配置 Gemini API Key"
            logger.error(f"定时投放：{msg}")
            await self.dedup_mgr.record_schedule_run(slot, today_str, "failed")
            return False, msg

        fetcher = TwitterFetcher(cookies_file=self.cookies_file, proxy=self.proxy)
        ok, err_msg = await fetcher.init_client()
        if not ok:
            msg = f"Twitter Cookie 失效: {err_msg}"
            logger.error(f"定时投放：{msg}")
            await self.dedup_mgr.record_schedule_run(slot, today_str, "failed")
            return False, msg

        evaluator = AIEvaluator(
            api_keys=self.gemini_keys,
            model_name=self.gemini_model,
            score_threshold=self.score_threshold,
            proxy=self.proxy,
            custom_prompt=self.custom_prompt
        )

        try:
            results = await self._select_and_prepare_tweets(fetcher, evaluator, self.schedule_count)
        except RuntimeError as e:
            msg = str(e)
            logger.error(f"定时投放：{msg}")
            await self.dedup_mgr.record_schedule_run(slot, today_str, "failed")
            return False, msg
        if not results:
            msg = "未找到合格推文"
            logger.info(f"定时投放：{msg}")
            await self.dedup_mgr.record_schedule_run(slot, today_str, "failed", 0)
            return False, msg

        # 读取绑定的目标 session
        session_str = await self.dedup_mgr.get_config("target_session")
        if not session_str:
            msg = "未绑定目标会话，请先使用 /xs bind 绑定"
            logger.error(f"定时投放：{msg}")
            await self.dedup_mgr.record_schedule_run(slot, today_str, "failed")
            return False, msg

        total_images = 0
        for r in results:
            for img in r["images"]:
                if not img["path"].exists():
                    logger.warning(f"定时投放：图片文件已不存在 {img['path']}，跳过。")
                    continue
                chain = MessageChain()
                orig_tag = " [高清原图]" if img["is_orig"] else ""
                chain.message(f"作者：{r['author_text']}\n推文 ID：{r['tweet_id']}{orig_tag}")
                chain.file_image(str(img["path"]))
                try:
                    ok = await self.context.send_message(session_str, chain)
                    if not ok:
                        logger.error(f"定时投放：context.send_message 返回 False (推文 {r['tweet_id']}, session: {session_str})")
                except Exception as e:
                    logger.error(f"定时投放：发送消息异常 (推文 {r['tweet_id']}): {e}")
                await asyncio.sleep(1.0)
                if img["path"].exists():
                    img["path"].unlink()
            total_images += len(r["images"])
            await asyncio.sleep(1.5)

        await self.dedup_mgr.record_schedule_run(slot, today_str, "success", total_images)
        logger.info(f"定时投放 [{slot}] 完成，共推送 {len(results)} 条推文 ({total_images} 张图片)。")
        return True, f"共推送 {len(results)} 条推文 ({total_images} 张图片)"

    @filter.command("Xschedule", alias={"xs", "定时"})
    async def cmd_schedule(self, event: AstrMessageEvent, action: str = ""):
        """定时投放管理
        用法:
          /Xschedule bind    或 /xs bind    或 /定时 bind     — 绑定投放目标
          /Xschedule unbind  或 /xs unbind  或 /定时 unbind   — 解绑
          /Xschedule status  或 /xs status  或 /定时 status   — 查看状态
          /Xschedule now     或 /xs now     或 /定时 now      — 立即投放
        """
        action = action.strip().lower()

        if action == "bind":
            session_str = event.unified_msg_origin
            await self.dedup_mgr.save_config("target_session", session_str)
            yield event.plain_result("定时投放目标已绑定到当前会话。")

        elif action == "unbind":
            deleted = await self.dedup_mgr.delete_config("target_session")
            if deleted:
                yield event.plain_result("定时投放目标已解绑。")
            else:
                yield event.plain_result("当前未绑定任何目标，无需解绑。")

        elif action == "status":
            enabled = "开启" if self.schedule_enabled else "关闭"
            times = "，".join(self.schedule_times)
            session = await self.dedup_mgr.get_config("target_session")
            bound = f"已绑定 ({session})" if session else "未绑定"
            yield event.plain_result(
                f"定时投放状态：{enabled}\n"
                f"投放时刻：{times}\n"
                f"每次数量：{self.schedule_count}\n"
                f"绑定目标：{bound}"
            )

        elif action == "now":
            session = await self.dedup_mgr.get_config("target_session")
            if not session:
                yield event.plain_result("当前未绑定投放目标，请先在目标群发送 /xs bind 或 /定时 bind 完成绑定。")
                return
            yield event.plain_result("正在进行手动触发投放...")
            today_str = date.today().isoformat()
            ok, msg = await self._do_scheduled_post("manual", today_str)
            if ok:
                yield event.plain_result(f"手动投放完成：{msg}")
            else:
                yield event.plain_result(f"手动投放失败：{msg}")

        else:
            yield event.plain_result(
                "未知操作。可用指令：\n"
                "/xs bind 或 /定时 bind       — 绑定投放目标\n"
                "/xs unbind 或 /定时 unbind   — 解绑\n"
                "/xs status 或 /定时 status   — 查看状态\n"
                "/xs now 或 /定时 now         — 立即投放"
            )
