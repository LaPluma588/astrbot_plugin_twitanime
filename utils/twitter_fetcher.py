import os
import re
import aiohttp
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, Optional, Tuple
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

    async def init_client(self) -> Tuple[bool, str]:
        """初始化 Twitter 客户端并加载 Cookie
        Returns:
            Tuple[成功标志, 错误信息]
        """
        if not self.cookies_file.exists():
            msg = f"Cookie 文件不存在: {self.cookies_file}"
            logger.error(msg)
            return False, msg
        try:
            logger.info("正在加载 Cookies...")
            self.client.load_cookies(path=str(self.cookies_file))
            logger.info("Twitter Cookie 加载成功！")
            return True, ""
        except Exception as e:
            msg = f"Twikit Cookie 加载失败: {e}"
            logger.error(msg)
            return False, msg

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
                        logger.error(f"图片下载 HTTP 状态异常: {resp.status}")
                        return False
        except Exception as e:
            logger.error(f"aiohttp 下载失败: {e}")
            return False

    async def fetch_image_tweets_stream(self, batch_size: int = 20) -> AsyncGenerator[Dict[str, Any], None]:
        try:
            logger.info("正在向 Twitter 获取初始 Timeline...")
            timeline = await self.client.get_timeline(count=batch_size)
        except Exception as e:
            import traceback
            logger.error(f"获取 Timeline 失败: {e,traceback.print_exc()}")
            return

        page_count = 1
        while timeline:
            logger.info(f"第 {page_count} 页获取到 {len(timeline)} 条推文...")

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
                
                # 提取 user 的 verified_type 属性
                verified_type = getattr(user_obj, 'verified_type', '') or ''
                if isinstance(verified_type, str):
                    verified_type = verified_type.strip()

                yield {
                    "tweet_id": tweet_id,
                    "author_name": author_name,
                    "author_screen_name": author_screen_name,
                    "verified_type": verified_type,
                    "photos": photo_medias
                }

            try:
                logger.info("正在加载下一页 Timeline...")
                timeline = await timeline.next()
                page_count += 1
            except Exception as e:
                logger.warning(f"无法获取下一页 Timeline: {e}")
                break
            
    async def fetch_tweet_data(self, tweet_id: str) -> Optional[Dict[str, Any]]:
        """根据推文 ID 拉取完整推文图文数据（文本 + 图片），跳过视频"""
        try:
            tweet = await self.client.get_tweet_by_id(tweet_id)
            if not tweet:
                logger.error(f"未能获取到推文 (ID: {tweet_id})")
                return None

            # 转推穿透
            target = tweet
            if hasattr(tweet, 'retweeted_tweet') and tweet.retweeted_tweet is not None:
                target = tweet.retweeted_tweet

            text = getattr(target, 'full_text', None) or getattr(target, 'text', '')

            user_obj = getattr(target, 'user', None)
            author_name = getattr(user_obj, 'name', '未知作者') if user_obj else '未知作者'
            author_screen_name = getattr(user_obj, 'screen_name', '') if user_obj else ''

            media_list = getattr(target, 'media', []) or []
            photo_urls = []
            for m in media_list:
                if getattr(m, 'type', '') == 'photo':
                    base_url = getattr(m, 'media_url_https', None) or getattr(m, 'media_url', None) or getattr(m, 'url', '')
                    if base_url:
                        photo_urls.append(base_url)

            return {
                "tweet_id": tweet_id,
                "text": text,
                "author_name": author_name,
                "author_screen_name": author_screen_name,
                "photos": photo_urls
            }
        except Exception as e:
            logger.error(f"获取推文 {tweet_id} 数据失败: {e}")
            return None

    async def favorite_tweet_by_id(self, tweet_id: str) -> bool:
        """根据推文 ID 拉取 Tweet 对象并执行 favorite() 点赞"""
        try:
            logger.info(f"正在获取推文对象 (ID: {tweet_id})...")
            tweet = await self.client.get_tweet_by_id(tweet_id)
            if not tweet:
                logger.error(f"未能获取到推文 (ID: {tweet_id})，可能已被删除或限制访问。")
                return False

            logger.info(f"正在为推文 {tweet_id} 执行 favorite()...")
            res = await tweet.favorite()
            logger.info(f"点赞返回结果: {res}")
            return True
        except Exception as e:
            logger.error(f"推文 {tweet_id} favorite() 执行失败: {e}")
            return False