import os
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, Optional
from twikit import Client

from astrbot.api import logger

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
            await self.client.login(auth_info_1="", password="", cookies_file=str(self.cookies_file))
            logger.info(f"[Twitanime] ✅ Twitter 登录成功！")
            return True
        except Exception as e:
            logger.error(f"[Twitanime] ❌ Twikit 登录/加载 Cookie 失败: {e}")
            return False

    async def fetch_image_tweets_stream(self, batch_size: int = 20) -> AsyncGenerator[Dict[str, Any], None]:
        """
        持续分页获取 Timeline，并逐条返回包含图片的推文数据对象
        """
        try:
            logger.info(f"[Twitanime] 📡 正在向 Twitter 获取初始 Timeline (单页: {batch_size} 条)...")
            timeline = await self.client.get_timeline(count=batch_size)
        except Exception as e:
            logger.error(f"[Twitanime] ❌ 获取 Timeline 失败: {e}")
            return

        page_count = 1
        while timeline:
            logger.info(f"[Twitanime] 📊 第 {page_count} 页获取到 {len(timeline)} 条推文，开始筛选图片...")

            for tweet in timeline:
                target_tweet = tweet
                if hasattr(tweet, 'retweeted_tweet') and tweet.retweeted_tweet is not None:
                    target_tweet = tweet.retweeted_tweet

                media_list = getattr(target_tweet, 'media', []) or []
                photo_medias = [
                    (idx + 1, m) for idx, m in enumerate(media_list) 
                    if getattr(m, 'type', '') == 'photo'
                ]

                if not photo_medias:
                    continue

                tweet_id = getattr(target_tweet, 'id', 'unknown')
                user_obj = getattr(target_tweet, 'user', None)
                author_name = getattr(user_obj, 'name', '未知作者') if user_obj else '未知作者'
                author_screen_name = getattr(user_obj, 'screen_name', '') if user_obj else ''

                # 产出整条推文及其包含的所有图片信息
                yield {
                    "tweet_id": tweet_id,
                    "author_name": author_name,
                    "author_screen_name": author_screen_name,
                    "photos": photo_medias  # List of (media_index, media_obj)
                }

            # 翻页逻辑：请求下一页 Timeline
            try:
                logger.info(f"[Twitanime] 🔄 正在加载下一页 Timeline...")
                timeline = await timeline.next()
                page_count += 1
            except Exception as e:
                logger.warning(f"[Twitanime] ⚠️ 无法获取下一页 Timeline (可能已到尽头): {e}")
                break