import os
import re
import shutil
import asyncio
from pathlib import Path
from typing import Set, List, Optional

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.event.filter import EventMessageType
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
    "1.1.0"
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

        # 存储、自动点赞与下载配置
        self.auto_like = bool(st_conf.get("auto_like", False))
        self.save_local = bool(st_conf.get("save_local", False))
        self.download_orig = bool(st_conf.get("download_orig", False))
        self.cookie_json_str = tw_conf.get("cookie_json", "[]")
        self.proxy = tw_conf.get("proxy", "").strip() or None
        
        # 解析 Gemini 多 Key 配置
        raw_keys = gm_conf.get("gemini_api_key", "").replace("\n", ",").split(",")
        self.gemini_keys = [k.strip() for k in raw_keys if k.strip()]
        self.gemini_model = gm_conf.get("model_name", "gemini-3.1-flash-lite")
        self.score_threshold = float(gm_conf.get("score_threshold", 7.0))

        self.wd14_threshold = float(ft_conf.get("wd14_threshold", 0.35))
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
        if model_path.exists() and tags_path.exists():
            try:
                self.wd14_filter = WD14Filter(model_path, tags_path, threshold=self.wd14_threshold)
                logger.info("[Twitanime] ✅ WD14 ONNX 粗筛模型初始化成功！")
            except Exception as e:
                logger.error(f"[Twitanime] ❌ WD14 模型加载失败: {e}")
        else:
            logger.warning(f"[Twitanime] ⚠️ 未找到 WD14 模型文件 ({model_path})，将跳过本地粗筛环节。")

        self.db_file = self.data_dir / "data.db"
        self.dedup_mgr = DedupManager(self.db_file)

    @filter.command("Ximage")
    async def fetch_x_images(self, event: AstrMessageEvent, count: int = 1):
        """抓取并推送合格的二次元插画推文
        用法: /Ximage <数量>
        """
        target_tweet_count = max(1, min(count, 10))

        if not self.gemini_keys:
            yield event.plain_result("❌ 未配置 Gemini API Key，无法使用 AI 审美精选功能。")
            return

        quality_tip = "【原图拉取已开启】" if self.download_orig else ""
        yield event.plain_result(f"🔍 正在为您检索并精选 {target_tweet_count} 条合格插画推文{quality_tip}，请稍候...")
        logger.info(f"[Twitanime] 🚀 开始处理，目标 {target_tweet_count} 条推文 | 原图补拉模式: {self.download_orig}")

        fetcher = TwitterFetcher(cookies_file=self.cookies_file, proxy=self.proxy)
        if not await fetcher.init_client():
            yield event.plain_result("❌ Twitter 登录失败，请检查 WebUI 中的 Cookie 配置是否有效。")
            return

        evaluator = AIEvaluator(
            api_keys=self.gemini_keys,
            model_name=self.gemini_model,
            score_threshold=self.score_threshold,
            proxy=self.proxy
        )

        sent_tweet_count = 0
        scanned_tweet_count = 0

        async for tweet_data in fetcher.fetch_image_tweets_stream(batch_size=20):
            if sent_tweet_count >= target_tweet_count:
                break

            scanned_tweet_count += 1
            tweet_id = tweet_data["tweet_id"]
            author_name = tweet_data["author_name"]
            author_handle = f"@{tweet_data['author_screen_name']}" if tweet_data["author_screen_name"] else ""
            verified_type = tweet_data.get("verified_type", "")
            photos = tweet_data["photos"]

            # 1：早期去重拦截
            if await self.dedup_mgr.is_processed(tweet_id, action_type="fetch_push"):
                logger.info(f"[Twitanime] ⏭️ 推文 {tweet_id} 已在去重库中，跳过。")
                continue

            # 1.5：金标/企业账号硬拦截
            if self.reject_business_user and verified_type == "Business":
                logger.info(f"[Twitanime] 🚫 [金标账号拦截] 作者: {author_name} ({author_handle}) 的 verified_type 为 Business，拦截此推文 (ID: {tweet_id})")
                await self.dedup_mgr.record_action(
                    tweet_id=tweet_id,
                    action_type="fetch_push",
                    status="filtered",
                    author_handle=author_handle,
                    reason="拦截金标企业账号 (verified_type=Business)"
                )
                continue

            author_text = f"{author_name} ({author_handle})" if author_handle else author_name
            logger.info(f"[Twitanime] 🧐 正在处理第 {scanned_tweet_count} 条推文 (ID: {tweet_id}, 共 {len(photos)} 张图片)...")

            passed_items = []
            filter_reason = ""

            for m_idx, media_obj, base_url in photos:
                temp_eval_path = self.temp_dir / f"tweet_{tweet_id}_{m_idx}_eval.jpg"

                download_success = await fetcher.download_image_direct(base_url, temp_eval_path)
                if not download_success:
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
                        logger.info(f"[Twitanime] 🔴 [WD14 粗筛拦截] 原因: {reason}")
                        filter_reason = f"WD14: {reason}"
                        if temp_eval_path.exists():
                            temp_eval_path.unlink()
                        continue

                ai_res = await evaluator.evaluate(str(temp_eval_path))
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

            if passed_items:
                sent_tweet_count += 1
                await self.dedup_mgr.record_action(
                    tweet_id=tweet_id,
                    action_type="fetch_push",
                    status="success",
                    author_handle=author_handle
                )

                # 功能1实现：自动点赞分支
                if self.auto_like:
                    if not await self.dedup_mgr.is_processed(tweet_id, action_type="like"):
                        logger.info(f"[Twitanime] 自动点赞开启，正在为精选通过的推文 (ID: {tweet_id}) 点赞...")
                        like_success = await fetcher.favorite_tweet_by_id(tweet_id)
                        await self.dedup_mgr.record_action(
                            tweet_id=tweet_id,
                            action_type="like",
                            status="success" if like_success else "failed",
                            author_handle=author_handle,
                            reason="" if like_success else "auto_like favorite() 执行失败"
                        )

                for item in passed_items:
                    m_idx = item["m_idx"]
                    eval_path: Path = item["eval_path"]
                    base_url = item["base_url"]
                    final_send_path = eval_path

                    if self.download_orig:
                        orig_url = get_orig_image_url(base_url)
                        orig_path = self.temp_dir / f"tweet_{tweet_id}_{m_idx}_orig.jpg"
                        logger.info(f"[Twitanime] 🚀 [放行后补拉原图]: {orig_url}")
                        
                        orig_success = await fetcher.download_image_direct(orig_url, orig_path)
                        if orig_success:
                            final_send_path = orig_path
                            if eval_path.exists():
                                eval_path.unlink()
                        else:
                            logger.warning(f"[Twitanime] ⚠️ 原图补拉失败，回退发送评估标准图。")

                    chain = MessageChain()
                    orig_tag = " [高清原图]" if (self.download_orig and final_send_path != eval_path) else ""
                    chain.message(f"🎨 作者：{author_text}\n🆔 推文 ID：{tweet_id}{orig_tag}")
                    chain.file_image(str(final_send_path))
                    
                    await event.send(chain)
                    await asyncio.sleep(1.0)
                    if self.save_local:
                        save_target_path = self.saved_dir / final_send_path.name
                        shutil.copy(final_send_path, save_target_path)
                        logger.info(f"[Twitanime] 💾 插画已保存至: {save_target_path}")

                    if final_send_path.exists():
                        final_send_path.unlink()

                await asyncio.sleep(1.5)
            else:
                await self.dedup_mgr.record_action(
                    tweet_id=tweet_id,
                    action_type="fetch_push",
                    status="filtered",
                    author_handle=author_handle,
                    reason=filter_reason
                )

        logger.info(f"[Twitanime] 🏁 任务完成，成功推送 {sent_tweet_count} 条符合要求的推文。")

        if sent_tweet_count == 0:
            yield event.plain_result("😢 检索完当前 Timeline，未找到符合要求的精选插画推文。")
        elif sent_tweet_count < target_tweet_count:
            yield event.plain_result(f"✅ 精选完成，共推送 {sent_tweet_count} 条合格推文。")

    # 功能2实现：/xlike 指令版本点赞
    @filter.command("xlike")
    async def cmd_like_tweet(self, event: AstrMessageEvent, tweet_id: str = ""):
        """点赞指定推文
        用法 1: /xlike <推文ID>
        用法 2: 回复某条插画消息并输入 /xlike
        """
        target_id = tweet_id.strip()
        
        # 参数为空时，尝试从引用链获取
        if not target_id:
            target_id = self.extract_tweet_id_from_chain(event.get_messages())

        if not target_id:
            yield event.plain_result("⚠️ 请提供具体的推文 ID，或在回复 Bot 发送的插画消息时使用 `/xlike` 命令。")
            return

        await self._execute_like(event, target_id)

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_like_reply(self, event: AstrMessageEvent):
        """监听 @机器人 + 引用插画推文 + 发送 '喜欢/点赞' 的自然语言操作"""
        if not self.is_at_bot(event):
            return

        raw_msg = event.message_str.strip()
        like_keywords = ["喜欢", "点赞", "赞", "❤️", "👍"]
        if not any(kw in raw_msg for kw in like_keywords):
            return

        tweet_id = self.extract_tweet_id_from_chain(event.get_messages())
        if not tweet_id:
            return

        await self._execute_like(event, tweet_id)

    async def _execute_like(self, event: AstrMessageEvent, tweet_id: str):
        """点赞公共逻辑拆分封装"""
        if await self.dedup_mgr.is_processed(tweet_id, action_type="like"):
            await event.send(event.plain_result(f"💡 这条推文 (ID: {tweet_id}) 之前已经点过赞啦，无需重复操作~"))
            return

        await event.send(event.plain_result(f"⏳ 收到！正在为您点赞推文 (ID: {tweet_id})，请稍候..."))

        fetcher = TwitterFetcher(cookies_file=self.cookies_file, proxy=self.proxy)
        if not await fetcher.init_client():
            await event.send(event.plain_result("❌ Twitter 登录失败，请检查 WebUI 中的 Cookie 配置是否有效。"))
            return

        success = await fetcher.favorite_tweet_by_id(tweet_id)

        if success:
            await self.dedup_mgr.record_action(
                tweet_id=tweet_id,
                action_type="like",
                status="success"
            )
            await event.send(event.plain_result(f"❤️ 已成功为您点赞该推文！(ID: {tweet_id})"))
        else:
            await self.dedup_mgr.record_action(
                tweet_id=tweet_id,
                action_type="like",
                status="failed",
                reason="favorite() 执行失败"
            )
            await event.send(event.plain_result(f"❌ 点赞失败，可能推文已被删除或触发了 Twitter 限流。"))

    def is_at_bot(self, event: AstrMessageEvent) -> bool:
        """通用工具函数：检测当前消息链中是否 @ 了机器人本身"""
        try:
            bot_self_id = str(event.get_self_id())
        except Exception as e:
            logger.error(f"[Twitanime] ⚠️ 获取 Bot Self ID 失败: {e}")
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