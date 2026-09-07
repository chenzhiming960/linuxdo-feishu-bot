"""SQLite 存储层。

每次操作独立开连接，天然支持 Flask / APScheduler 的多线程场景。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .migrations import backup_database, migrate
from .models import Post


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    """论坛监控数据的持久化入口。"""

    def __init__(self, path: str = "data/monitor.db", backup: bool = True):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        if backup:
            backup_database(path)
        with self._conn() as conn:
            migrate(conn)

    # ---------------------------------------------------------------- 连接

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------------------------------------------------------- 用户

    def ensure_user(self, chat_id: str, forum: str, channel: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (chat_id, forum, channel, created_at) "
                "VALUES (?, ?, ?, ?)",
                (str(chat_id), forum, channel, _now()),
            )

    def get_users(
        self, forum: Optional[str] = None, channel: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM users WHERE 1=1"
        params: List[Any] = []
        if forum:
            sql += " AND forum = ?"
            params.append(forum)
        if channel:
            sql += " AND channel = ?"
            params.append(channel)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    def mark_blocked(self, chat_id: str, forum: str, channel: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO blocked_users (chat_id, forum, channel, blocked_at) "
                "VALUES (?, ?, ?, ?)",
                (str(chat_id), forum, channel, _now()),
            )

    def is_blocked(self, chat_id: str, forum: str, channel: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM blocked_users WHERE chat_id=? AND forum=? AND channel=?",
                (str(chat_id), forum, channel),
            ).fetchone()
            return row is not None

    def get_blocked(self, forum: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM blocked_users"
        params: List[Any] = []
        if forum:
            sql += " WHERE forum = ?"
            params.append(forum)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    # ------------------------------------------------------------ 关键词订阅

    def add_subscription(
        self, chat_id: str, keyword: str, forum: str, channel: str
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO subscriptions (chat_id, keyword, forum, channel, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(chat_id), keyword, forum, channel, _now()),
            )
            return cur.rowcount > 0

    def remove_subscription(
        self, chat_id: str, keyword: str, forum: str, channel: str
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM subscriptions WHERE chat_id=? AND keyword=? AND forum=? AND channel=?",
                (str(chat_id), keyword, forum, channel),
            )
            return cur.rowcount > 0

    def get_subscriptions(
        self, chat_id: str, forum: str, channel: str
    ) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT keyword FROM subscriptions WHERE chat_id=? AND forum=? AND channel=? "
                "ORDER BY created_at",
                (str(chat_id), forum, channel),
            ).fetchall()
            return [r["keyword"] for r in rows]

    def get_keyword_subscriptions(
        self, forum: str, channel: Optional[str] = None
    ) -> List[Tuple[str, str]]:
        """返回该论坛下所有 (chat_id, keyword) 组合，供正则匹配使用。"""
        sql = "SELECT chat_id, keyword FROM subscriptions WHERE forum=?"
        params: List[Any] = [forum]
        if channel:
            sql += " AND channel=?"
            params.append(channel)
        with self._conn() as conn:
            return [(r["chat_id"], r["keyword"]) for r in conn.execute(sql, params)]

    # -------------------------------------------------------------- 作者订阅

    def add_author_subscription(
        self, chat_id: str, author: str, forum: str, channel: str
    ) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO user_subscriptions (chat_id, author, forum, channel, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(chat_id), author, forum, channel, _now()),
            )
            return cur.rowcount > 0

    def get_author_subscriptions(
        self, chat_id: str, forum: str, channel: str
    ) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT author FROM user_subscriptions WHERE chat_id=? AND forum=? AND channel=? "
                "ORDER BY created_at",
                (str(chat_id), forum, channel),
            ).fetchall()
            return [r["author"] for r in rows]

    def get_author_subscribers(
        self, author: str, forum: str, channel: Optional[str] = None
    ) -> List[str]:
        sql = "SELECT chat_id FROM user_subscriptions WHERE forum=? AND LOWER(author)=LOWER(?)"
        params: List[Any] = [forum, author]
        if channel:
            sql += " AND channel=?"
            params.append(channel)
        with self._conn() as conn:
            return [r["chat_id"] for r in conn.execute(sql, params)]

    # ------------------------------------------------------------ 订阅全部

    def add_subscribe_all(self, chat_id: str, forum: str, channel: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO subscribe_all (chat_id, forum, channel, created_at) "
                "VALUES (?, ?, ?, ?)",
                (str(chat_id), forum, channel, _now()),
            )
            return cur.rowcount > 0

    def remove_subscribe_all(self, chat_id: str, forum: str, channel: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM subscribe_all WHERE chat_id=? AND forum=? AND channel=?",
                (str(chat_id), forum, channel),
            )
            return cur.rowcount > 0

    def is_subscribe_all(self, chat_id: str, forum: str, channel: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM subscribe_all WHERE chat_id=? AND forum=? AND channel=?",
                (str(chat_id), forum, channel),
            ).fetchone()
            return row is not None

    def get_subscribe_all_users(
        self, forum: str, channel: Optional[str] = None
    ) -> List[str]:
        sql = "SELECT chat_id FROM subscribe_all WHERE forum=?"
        params: List[Any] = [forum]
        if channel:
            sql += " AND channel=?"
            params.append(channel)
        with self._conn() as conn:
            return [r["chat_id"] for r in conn.execute(sql, params)]

    # ---------------------------------------------------------------- 帖子

    def post_exists(self, post_id: str, forum: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM posts WHERE id=? AND forum=?", (post_id, forum)
            ).fetchone()
            return row is not None

    def add_post(self, post: Post, forum: str) -> bool:
        """写入帖子，返回是否为新增（False 表示已存在）。"""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO posts "
                "(id, forum, title, link, author, category_id, pub_date, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    post.id,
                    forum,
                    post.title,
                    post.link,
                    post.author,
                    post.category,
                    post.published_at.strftime("%Y-%m-%d %H:%M:%S")
                    if post.published_at
                    else None,
                    _now(),
                ),
            )
            return cur.rowcount > 0

    def cleanup_posts(self, retention_hours: int = 24) -> int:
        expire = (datetime.now() - timedelta(hours=retention_hours)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM posts WHERE created_at < ?", (expire,))
            conn.execute(
                "DELETE FROM notifications WHERE created_at < ?", (expire,)
            )
            return cur.rowcount

    # -------------------------------------------------------------- 通知记录

    def notification_sent(
        self, chat_id: str, post_id: str, keyword: str, forum: str, channel: str
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications WHERE chat_id=? AND post_id=? AND keyword=? "
                "AND forum=? AND channel=?",
                (str(chat_id), post_id, keyword, forum, channel),
            ).fetchone()
            return row is not None

    def record_notification(
        self, chat_id: str, post_id: str, keyword: str, forum: str, channel: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO notifications "
                "(chat_id, post_id, keyword, forum, channel, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(chat_id), post_id, keyword, forum, channel, _now()),
            )

    # ---------------------------------------------------------------- 分类

    def upsert_category(
        self,
        category_id: str,
        forum: str,
        name: str = "",
        slug: str = "",
        description: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO categories (id, forum, name, slug, description) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id, forum) DO UPDATE SET name=excluded.name, slug=excluded.slug, "
                "description=excluded.description",
                (str(category_id), forum, name, slug, description),
            )

    def get_category_name(
        self, category_id: Optional[str], forum: str
    ) -> Optional[str]:
        if not category_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT name FROM categories WHERE id=? AND forum=?",
                (str(category_id), forum),
            ).fetchone()
            return row["name"] if row else None

    # ---------------------------------------------------------------- 统计

    def get_stats(self, forum: Optional[str] = None) -> Dict[str, Any]:
        def scalar(sql: str, params: Sequence[Any] = ()) -> int:
            with self._conn() as conn:
                row = conn.execute(sql, params).fetchone()
                return int(row[0]) if row else 0

        f = " WHERE forum=?" if forum else ""
        p: Tuple[Any, ...] = (forum,) if forum else ()
        return {
            "users": scalar(f"SELECT COUNT(DISTINCT chat_id) FROM users{f}", p),
            "subscriptions": scalar(f"SELECT COUNT(*) FROM subscriptions{f}", p),
            "author_subscriptions": scalar(
                f"SELECT COUNT(*) FROM user_subscriptions{f}", p
            ),
            "subscribe_all": scalar(f"SELECT COUNT(*) FROM subscribe_all{f}", p),
            "posts": scalar(f"SELECT COUNT(*) FROM posts{f}", p),
            "notifications": scalar(f"SELECT COUNT(*) FROM notifications{f}", p),
            "blocked_users": scalar(f"SELECT COUNT(*) FROM blocked_users{f}", p),
        }

    def get_keyword_stats(
        self, forum: Optional[str] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        sql = (
            "SELECT keyword, COUNT(*) AS cnt FROM notifications"
        )
        params: List[Any] = []
        if forum:
            sql += " WHERE forum=?"
            params.append(forum)
        sql += " GROUP BY keyword ORDER BY cnt DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params)]

    # ------------------------------------------------------------ 原始 SQL

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._conn() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount
