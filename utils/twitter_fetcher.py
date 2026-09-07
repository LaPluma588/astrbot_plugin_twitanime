import os
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, Optional
from twikit import Client

from astrbot.api import logger  # 使用官方 logger 接口

class TwitterFetcher:
    def __init__(self, cookies_file: Path, proxy: Optional[str] = None):
        self.cookies_file = cookies_file
        self.proxy = proxy
        self.client = Client('en-US', proxy=proxy if proxy else None)

    async def init_client(self) -> bool:
        """从 Cookie 文件登录推特"""
        if not self.cookies_file.exists():
            logger.error(f"[Twitanime] ❌ Cookie 文件不存在: {self.cookies_file}")
            return False
        try:
            logger.info(f"[Twitanime] 🔐 正在读取 Cookies 并登录 Twitter...")
            await self.client.login(auth_info_1="",password="",cookies_file=str(self.cookies_file))
            logger.info(f"[Twitanime] ✅ Twitter 登录成功！")
            return True
        except Exception as e:
            logger.error(f"[Twitanime] ❌ Twikit 登录/加载 Cookie 失败: {e}")
            return False

    async def fetch_image_stream(self, fetch_tweet_count: int = 20) -> AsyncGenerator[Dict[str, Any], None]:
        """获取 Timeline 推文并逐张 yield 输出图片路径/信息"""
        try:
            logger.info(f"[Twitanime] 📡 正在向 Twitter 发送 Timeline 请求 (抓取数量: {fetch_tweet_count})...")
            timeline = await self.client.get_timeline(count=fetch_tweet_count)
            logger.info(f"[Twitanime] 📊 成功获取到 {len(timeline)} 条推文，开始扫描媒体...")
        except Exception as e:
            logger.error(f"[Twitanime] ❌ 获取 Timeline 失败: {e}")
            return

        for idx, tweet in enumerate(timeline, start=1):
            target_tweet = tweet
            if hasattr(tweet, 'retweeted_tweet') and tweet.retweeted_tweet is not None:
                target_tweet = tweet.retweeted_tweet

            media_list = getattr(target_tweet, 'media', []) or []
            if not media_list:
                continue

            tweet_id = getattr(target_tweet, 'id', 'unknown')

            for m_idx, media in enumerate(media_list, start=1):
                if getattr(media, 'type', '') == 'photo':
                    img_url = getattr(media, 'media_url', None) or getattr(media, 'url', '')
                    logger.info(f"[Twitanime] 🖼️ 发现图片 (推文 ID: {tweet_id}): {img_url}")
                    yield {
                        "tweet_id": tweet_id,
                        "media_index": m_idx,
                        "media_obj": media,
                        "url": img_url
                    }