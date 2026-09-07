import os
import shutil
import asyncio
from pathlib import Path
from typing import Set

from astrbot.api import logger  # 使用官方 logger 接口
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
    "1.0.0"
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

        # 保存/更新 Cookies
        CookieManager.sanitize_and_save(self.cookie_json_str, self.cookies_file)

        # 加载 WD14 模型
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
        """抓取并精选 Twitter 二次元插画"""
        target_count = max(1, min(count, 10))

        if not self.gemini_key:
            yield event.plain_result("❌ 未配置 Gemini API Key，无法使用 AI 审美精选功能。")
            return

        yield event.plain_result(f"🔍 正在为您检索并精选 {target_count} 张推特二次元插画，请稍候...")
        logger.info(f"[Twitanime] 🚀 收到指令，开始为用户精选 {target_count} 张插画...")

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

        sent_count = 0
        total_scanned = 0

        async for item in fetcher.fetch_image_stream(fetch_tweet_count=30):
            if sent_count >= target_count:
                logger.info(f"[Twitanime] 🎉 已成功精选并推送 {target_count} 张插画，提前结束任务。")
                break

            total_scanned += 1
            media_obj = item["media_obj"]
            tweet_id = item["tweet_id"]
            m_idx = item["media_index"]

            temp_img_path = self.temp_dir / f"tweet_{tweet_id}_{m_idx}.jpg"
            logger.info(f"[Twitanime] 📥 [{total_scanned}] 正在下载推特图片: tweet_{tweet_id}_{m_idx}.jpg")

            try:
                await media_obj.download(str(temp_img_path))
            except Exception as e:
                logger.error(f"[Twitanime] ❌ 图片下载超时/失败: {e}")
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
                    logger.info(f"[Twitanime] 🟢 [WD14 粗筛通过] 原因: {reason}")

            # 2. Gemini 审美评估
            logger.info(f"[Twitanime] 🤖 正在送往 Gemini ({self.gemini_model}) 进行审美与完成度打分...")
            ai_res = await evaluator.evaluate(str(temp_img_path))
            score = ai_res["score"]
            reason = ai_res["reason"]

            if ai_res["pass"]:
                sent_count += 1
                logger.info(f"[Twitanime] 🟢 [AI 审美精选放行] 得分: {score} | 评语: {reason}")

                chain = MessageChain()
                chain.message(f"✨ [精选插画 {sent_count}/{target_count}]\n评分: {score} | 评价: {reason}")
                chain.file_image(str(temp_img_path))
                
                await event.send(chain)
                await asyncio.sleep(1.5)
            else:
                logger.info(f"[Twitanime] 🔴 [AI 审美未达标准] 得分: {score} | 扣分/拒绝原因: {reason}")

            if temp_img_path.exists():
                temp_img_path.unlink()

            await asyncio.sleep(3.0)

        logger.info(f"[Twitanime] 🏁 流水线处理完毕，累计扫描 {total_scanned} 张图片，成功推送 {sent_count} 张。")

        if sent_count == 0:
            yield event.plain_result("😢 本次检索未找到符合审美及过滤标准的插画，请稍后再试。")
        elif sent_count < target_count:
            yield event.plain_result(f"✅ 精选完成，本次符合要求的图片共 {sent_count} 张。")