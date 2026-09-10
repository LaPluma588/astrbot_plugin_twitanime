import sqlite3
import asyncio
from pathlib import Path
from typing import Optional, List
from astrbot.api import logger

class DedupManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """初始化表结构与索引"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tweet_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tweet_id TEXT NOT NULL,
                    author_handle TEXT DEFAULT '',
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # 建立联合唯一索引，加速 (tweet_id, action_type) 查询
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_tweet_action 
                ON tweet_history(tweet_id, action_type);
            """)
            conn.commit()

    async def is_processed(self, tweet_id: str, action_type: str = "fetch_push") -> bool:
        """
        检查某推文针对特定动作是否已经处理过（包含 success / filtered）
        返回 True 表示已处理，应直接跳过
        """
        def _check():
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM tweet_history WHERE tweet_id = ? AND action_type = ? LIMIT 1;",
                    (str(tweet_id), action_type)
                )
                return cursor.fetchone() is not None

        return await asyncio.to_thread(_check)

    async def record_action(
        self, 
        tweet_id: str, 
        action_type: str, 
        status: str, 
        author_handle: str = "", 
        reason: str = ""
    ):
        """
        记录推文处理状态
        :param tweet_id: 推文唯一 ID
        :param action_type: 动作类型 ('fetch_push', 'like', 'retweet' 等)
        :param status: 状态 ('success', 'filtered', 'failed')
        :param author_handle: 作者 @handle
        :param reason: 拦截或处理原因 (例如: 'WD14:黑名单标签', 'Gemini:分数未达标')
        """
        def _insert():
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO tweet_history (tweet_id, author_handle, action_type, status, reason)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(tweet_id, action_type) DO UPDATE SET
                        status = excluded.status,
                        reason = excluded.reason,
                        created_at = CURRENT_TIMESTAMP;
                """, (str(tweet_id), author_handle, action_type, status, reason))
                conn.commit()

        try:
            await asyncio.to_thread(_insert)
        except Exception as e:
            logger.error(f"[Twitanime] ❌ 写入去重数据库失败: {e}")