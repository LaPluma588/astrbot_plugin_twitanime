import os
import re
import aiohttp
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, Optional
from twikit import Client

from astrbot.api import logger

def get_orig_image_url(url: str) -> str:
    """将 Twitter 图片 URL 强制转换为 name=orig 高清原图 URL"""
    if not url:
        return url
    if 'name=' in url:
        return re.sub(r'name=[a-zA-Z0-9_]+', 'name=orig', url)
    if '?' in url:
        return f"{url}&name=orig"
    return f"{url}?format=jpg&name=orig"

class TwitterFetcher:
    def __init__(self, cookies_file: Path, proxy: Optional[str] = None):
        self.cookies_file = cookies_file
        self.proxy = proxy
        self.client = Client('en-US', proxy=proxy if proxy else None)

    async def init_client(self) -> bool:
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

    async def download_image_direct(self, url: str, filepath: Path) -> bool:
        """直接使用 aiohttp 异步下载指定 URL 图片"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, proxy=self.proxy) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        with open(filepath, 'wb') as f:
                            f.write(content)
                        return True
                    else:
                        logger.error(f"[Twitanime] ❌ 图片下载 HTTP 状态异常: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"[Twitanime] ❌ aiohttp 下载失败: {e}")
            return False

    async def fetch_image_tweets_stream(self, batch_size: int = 20) -> AsyncGenerator[Dict[str, Any], None]:
        try:
            logger.info(f"[Twitanime] 📡 正在向 Twitter 获取初始 Timeline...")
            timeline = await self.client.get_timeline(count=batch_size)
        except Exception as e:
            logger.error(f"[Twitanime] ❌ 获取 Timeline 失败: {e}")
            return

        page_count = 1
        while timeline:
            logger.info(f"[Twitanime] 📊 第 {page_count} 页获取到 {len(timeline)} 条推文...")

            for tweet in timeline:
                target_tweet = tweet
                if hasattr(tweet, 'retweeted_tweet') and tweet.retweeted_tweet is not None:
                    target_tweet = tweet.retweeted_tweet

                media_list = getattr(target_tweet, 'media', []) or []
                photo_medias = []
                
                for idx, m in enumerate(media_list):
                    if getattr(m, 'type', '') == 'photo':
                        base_url = getattr(m, 'media_url_https', None) or getattr(m, 'media_url', None) or getattr(m, 'url', '')
                        photo_medias.append((idx + 1, m, base_url))

                if not photo_medias:
                    continue

                tweet_id = getattr(target_tweet, 'id', 'unknown')
                user_obj = getattr(target_tweet, 'user', None)
                author_name = getattr(user_obj, 'name', '未知作者') if user_obj else '未知作者'
                author_screen_name = getattr(user_obj, 'screen_name', '') if user_obj else ''

                yield {
                    "tweet_id": tweet_id,
                    "author_name": author_name,
                    "author_screen_name": author_screen_name,
                    "photos": photo_medias
                }

            try:
                logger.info(f"[Twitanime] 🔄 正在加载下一页 Timeline...")
                timeline = await timeline.next()
                page_count += 1
            except Exception as e:
                logger.warning(f"[Twitanime] ⚠️ 无法获取下一页 Timeline: {e}")
                break