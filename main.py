import os
import shutil
import asyncio
from pathlib import Path
from typing import Set, List

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .utils.cookie_manager import CookieManager
from .utils.twitter_fetcher import TwitterFetcher
from .utils.wd14_filter import WD14Filter
from .utils.ai_evaluator import AIEvaluator


@register(
    "astrbot_plugin_twitanime",
    "LaPluma588",
    "基于 WD14 与 Gemini 多模态 AI 的推特二次元插画抓取与审美精选插件",
    "1.0.2"
)
class TwitanimePlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        self.plugin_dir = Path(__file__).parent
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_twitanime"
        self.temp_dir = self.data_dir / "temp"
        self.cookies_file = self.data_dir / "cookies.json"

        self.temp_dir.mkdir(parents=True, exist_ok=True)

        tw_conf = config.get("twitter_section", {})
        gm_conf = config.get("gemini_section", {})
        ft_conf = config.get("filter_section", {})

        self.cookie_json_str = tw_conf.get("cookie_json", "[]")
        self.proxy = tw_conf.get("proxy", "").strip() or None

        self.gemini_key = gm_conf.get("gemini_api_key", "").strip()
        self.gemini_model = gm_conf.get("model_name", "gemini-3.1-flash-lite")
        self.score_threshold = float(gm_conf.get("score_threshold", 7.0))

        self.wd14_threshold = float(ft_conf.get("wd14_threshold", 0.35))
        self.whitelist_tags: Set[str] = {
            t.strip() for t in ft_conf.get("whitelist_tags", "").split(",") if t.strip()
        }
        self.must_reject_tags: Set[str] = {
            t.strip() for t in ft_conf.get("must_reject_tags", "").split(",") if t.strip()
        }

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

    @filter.command("Ximage")
    async def fetch_x_images(self, event: AstrMessageEvent, count: int = 1):
        """持续抓取并推送合格的 X/Twitter 二次元插画推文"""
        target_tweet_count = max(1, min(count, 10))

        if not self.gemini_key:
            yield event.plain_result("❌ 未配置 Gemini API Key，无法使用 AI 审美精选功能。")
            return

        yield event.plain_result(f"🔍 正在为您检索并精选 {target_tweet_count} 条合格插画推文，请稍候...")
        logger.info(f"[Twitanime] 🚀 收到指令，目标寻找并推送 {target_tweet_count} 条精选推文...")

        fetcher = TwitterFetcher(cookies_file=self.cookies_file, proxy=self.proxy)
        if not await fetcher.init_client():
            yield event.plain_result("❌ Twitter 登录失败，请检查 WebUI 中的 Cookie 配置是否有效。")
            return

        evaluator = AIEvaluator(
            api_key=self.gemini_key,
            model_name=self.gemini_model,
            score_threshold=self.score_threshold,
            proxy=self.proxy
        )

        sent_tweet_count = 0  # 记录成功推送的合格推文数量
        scanned_tweet_count = 0  # 扫描过的含图推文数量

        async for tweet_data in fetcher.fetch_image_tweets_stream(batch_size=20):
            if sent_tweet_count >= target_tweet_count:
                break

            scanned_tweet_count += 1
            tweet_id = tweet_data["tweet_id"]
            author_name = tweet_data["author_name"]
            author_handle = f"@{tweet_data['author_screen_name']}" if tweet_data["author_screen_name"] else ""
            photos = tweet_data["photos"]

            author_text = f"{author_name} ({author_handle})" if author_handle else author_name
            logger.info(f"[Twitanime] 🧐 正在处理第 {scanned_tweet_count} 条含图推文 (ID: {tweet_id}, 共 {len(photos)} 张图片)...")

            passed_image_paths: List[Path] = []

            # 遍历该推文下的每张图片进行检测
            for m_idx, media_obj in photos:
                temp_img_path = self.temp_dir / f"tweet_{tweet_id}_{m_idx}.jpg"
                logger.info(f"[Twitanime] 📥 下载图片: tweet_{tweet_id}_{m_idx}.jpg")

                try:
                    await media_obj.download(str(temp_img_path))
                except Exception as e:
                    logger.error(f"[Twitanime] ❌ 图片下载失败: {e}")
                    continue

                # 1. WD14 粗筛
                if self.wd14_filter:
                    passed, reason = self.wd14_filter.check_pass(
                        str(temp_img_path),
                        whitelist=self.whitelist_tags,
                        must_reject=self.must_reject_tags
                    )
                    if not passed:
                        logger.info(f"[Twitanime] 🔴 [WD14 粗筛拦截] 原因: {reason}")
                        if temp_img_path.exists():
                            temp_img_path.unlink()
                        continue
                    else:
                        logger.info(f"[Twitanime] 🟢 [WD14 粗筛通过]")

                # 2. Gemini 审美评估
                logger.info(f"[Twitanime] 🤖 送往 Gemini 进行审美评估...")
                ai_res = await evaluator.evaluate(str(temp_img_path))

                if ai_res["pass"]:
                    logger.info(f"[Twitanime] 🟢 [AI 审美放行] 得分: {ai_res['score']} | 评语: {ai_res['reason']}")
                    passed_image_paths.append(temp_img_path)
                else:
                    logger.info(f"[Twitanime] 🔴 [AI 审美未达标] 得分: {ai_res['score']} | 原因: {ai_res['reason']}")
                    if temp_img_path.exists():
                        temp_img_path.unlink()

            # 如果本条推文有通过筛选的图片，则进行推送，并增加【精选推文计数】
            if passed_image_paths:
                sent_tweet_count += 1
                logger.info(f"[Twitanime] 🎉 推文 {tweet_id} 有 {len(passed_image_paths)} 张合格图片，推送给用户 [{sent_tweet_count}/{target_tweet_count}]")

                for img_path in passed_image_paths:
                    chain = MessageChain()
                    chain.message(f"🎨 作者：{author_text}\n🆔 推文 ID：{tweet_id}")
                    chain.file_image(str(img_path))
                    
                    await event.send(chain)
                    await asyncio.sleep(1.0)

                    # 发送完后清理临时文件
                    if img_path.exists():
                        img_path.unlink()

                await asyncio.sleep(1.5)

        logger.info(f"[Twitanime] 🏁 任务完成，共扫描 {scanned_tweet_count} 条推文，成功推送 {sent_tweet_count} 条符合要求的推文。")

        if sent_tweet_count == 0:
            yield event.plain_result("😢 检索完当前 Timeline，未找到符合要求的精选插画推文。")
        elif sent_tweet_count < target_tweet_count:
            yield event.plain_result(f"✅ 精选完成，已扫描全部可用推文，共推送 {sent_tweet_count} 条合格推文。")