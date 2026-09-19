import sqlite3
import asyncio
from datetime import date
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
            # 插件键值配置表（存 target_session 等）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plugin_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            # 定时投放运行日志（按 slot+date 唯一防重）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedule_run_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slot_key TEXT NOT NULL,
                    run_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tweet_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(slot_key, run_date)
                );
            """)
            conn.commit()

    async def is_processed(self, tweet_id: str, action_type: str = "fetch_push", status: Optional[str] = None) -> bool:
        """
        检查某推文针对特定动作是否已经处理过
        返回 True 表示已处理，应直接跳过

        默认检查任意状态（success / filtered / failed）的记录。
        传入 status 可只检查特定状态的记录（如只检查 success 避免失败记录阻挡重试）。
        """
        def _check():
            with self._get_conn() as conn:
                cursor = conn.cursor()
                if status:
                    cursor.execute(
                        "SELECT 1 FROM tweet_history WHERE tweet_id = ? AND action_type = ? AND status = ? LIMIT 1;",
                        (str(tweet_id), action_type, status)
                    )
                else:
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
            logger.error(f"写入去重数据库失败: {e}")

    # ── 定时投放相关 ──

    async def save_config(self, key: str, value: str):
        """保存插件键值配置"""
        def _upsert():
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO plugin_config (key, value) VALUES (?, ?);",
                    (key, value)
                )
                conn.commit()
        await asyncio.to_thread(_upsert)

    async def get_config(self, key: str) -> Optional[str]:
        """读取插件键值配置，不存在返回 None"""
        def _get():
            with self._get_conn() as conn:
                cur = conn.execute("SELECT value FROM plugin_config WHERE key = ?;", (key,))
                row = cur.fetchone()
                return row["value"] if row else None
        return await asyncio.to_thread(_get)

    async def delete_config(self, key: str) -> bool:
        """删除插件键值配置，返回是否删除了记录"""
        def _del():
            with self._get_conn() as conn:
                cur = conn.execute("DELETE FROM plugin_config WHERE key = ?;", (key,))
                conn.commit()
                return cur.rowcount > 0
        return await asyncio.to_thread(_del)

    async def record_schedule_run(self, slot_key: str, run_date: str, status: str, tweet_count: int = 0):
        """记录定时投放执行日志"""
        def _insert():
            with self._get_conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO schedule_run_log (slot_key, run_date, status, tweet_count)
                       VALUES (?, ?, ?, ?);""",
                    (slot_key, run_date, status, tweet_count)
                )
                conn.commit()
        await asyncio.to_thread(_insert)

    async def is_slot_posted_today(self, slot_key: str) -> bool:
        """检查某个时间槽今天是否已经投放过了"""
        today = date.today().isoformat()
        def _check():
            with self._get_conn() as conn:
                cur = conn.execute(
                    "SELECT 1 FROM schedule_run_log WHERE slot_key = ? AND run_date = ? LIMIT 1;",
                    (slot_key, today)
                )
                return cur.fetchone() is not None
        return await asyncio.to_thread(_check)