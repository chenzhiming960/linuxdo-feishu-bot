"""数据库 schema 迁移。

迁移按 ``schema_version`` 递增执行，覆盖三种起点：

* 全新数据库 -> 直接建到最新 schema
* 旧版 ``linuxdo-feishu-bot`` 的库（只有 ``posts(link, created_at)``）-> 转换到新 posts 表
* 旧版 Telegram 监控的库（表结构齐全但没有 channel 列）-> 补列并重建主键

迁移前会做一次文件级备份（``<db>.bak-<timestamp>``）。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from .exceptions import MigrationError

SCHEMA_VERSION = 3
DEFAULT_CHANNEL = "telegram"
LEGACY_FORUM_ID = "linux-do"

# 需要 channel 列的表
CHANNEL_TABLES = (
    "users",
    "subscriptions",
    "user_subscriptions",
    "subscribe_all",
    "notifications",
    "blocked_users",
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, name: str) -> List[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]


# --------------------------------------------------------------------------
# v1 schema（不含 channel，用于兼容旧库）
# --------------------------------------------------------------------------

V1_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS users (
        chat_id TEXT NOT NULL,
        forum TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        keyword TEXT NOT NULL,
        forum TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (chat_id, keyword, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        author TEXT NOT NULL,
        forum TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (chat_id, author, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscribe_all (
        chat_id TEXT NOT NULL,
        forum TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS posts (
        id TEXT NOT NULL,
        forum TEXT NOT NULL,
        title TEXT,
        link TEXT,
        author TEXT,
        category_id TEXT,
        pub_date TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (id, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        chat_id TEXT NOT NULL,
        post_id TEXT NOT NULL,
        keyword TEXT NOT NULL,
        forum TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, post_id, keyword, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blocked_users (
        chat_id TEXT NOT NULL,
        forum TEXT NOT NULL,
        blocked_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS categories (
        id TEXT NOT NULL,
        forum TEXT NOT NULL,
        name TEXT,
        slug TEXT,
        description TEXT,
        PRIMARY KEY (id, forum)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_subscriptions_keyword ON subscriptions(keyword, forum)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at)",
)

# --------------------------------------------------------------------------
# 最新 schema（含 channel）
# --------------------------------------------------------------------------

LATEST_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS users (
        chat_id TEXT NOT NULL,
        forum TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'telegram',
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, forum, channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        keyword TEXT NOT NULL,
        forum TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'telegram',
        created_at TEXT NOT NULL,
        UNIQUE (chat_id, keyword, forum, channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT NOT NULL,
        author TEXT NOT NULL,
        forum TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'telegram',
        created_at TEXT NOT NULL,
        UNIQUE (chat_id, author, forum, channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscribe_all (
        chat_id TEXT NOT NULL,
        forum TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'telegram',
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, forum, channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS posts (
        id TEXT NOT NULL,
        forum TEXT NOT NULL,
        title TEXT,
        link TEXT,
        author TEXT,
        category_id TEXT,
        pub_date TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (id, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notifications (
        chat_id TEXT NOT NULL,
        post_id TEXT NOT NULL,
        keyword TEXT NOT NULL,
        forum TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'telegram',
        created_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, post_id, keyword, forum, channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS blocked_users (
        chat_id TEXT NOT NULL,
        forum TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'telegram',
        blocked_at TEXT NOT NULL,
        PRIMARY KEY (chat_id, forum, channel)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS categories (
        id TEXT NOT NULL,
        forum TEXT NOT NULL,
        name TEXT,
        slug TEXT,
        description TEXT,
        PRIMARY KEY (id, forum)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_subscriptions_keyword ON subscriptions(keyword, forum, channel)",
    "CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at)",
)

# 重建带 channel 主键时使用的建表语句（键为表名）
_CHANNEL_REBUILD_SQL = {
    "users": LATEST_SCHEMA_SQL[0],
    "subscriptions": LATEST_SCHEMA_SQL[1],
    "user_subscriptions": LATEST_SCHEMA_SQL[2],
    "subscribe_all": LATEST_SCHEMA_SQL[3],
    "notifications": LATEST_SCHEMA_SQL[5],
    "blocked_users": LATEST_SCHEMA_SQL[6],
}


# --------------------------------------------------------------------------
# 迁移实现
# --------------------------------------------------------------------------


def _m001_initial_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for sql in V1_SCHEMA_SQL:
        cur.execute(sql)

    # 旧版 linuxdo-feishu-bot：posts(link TEXT PRIMARY KEY, created_at TEXT)
    cols = set(table_columns(conn, "posts"))
    if cols and "id" not in cols:
        cur.execute("ALTER TABLE posts RENAME TO posts_legacy_feishu")
        cur.execute(LATEST_SCHEMA_SQL[4])
        cur.execute(
            """
            INSERT OR IGNORE INTO posts (id, forum, title, link, created_at)
            SELECT link, ?, link, link, COALESCE(created_at, ?)
            FROM posts_legacy_feishu
            """,
            (LEGACY_FORUM_ID, _now()),
        )
        cur.execute("DROP TABLE posts_legacy_feishu")


def _m002_add_channel_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for table in CHANNEL_TABLES:
        if not table_exists(conn, table):
            continue
        if "channel" in table_columns(conn, table):
            continue
        cur.execute(
            f"ALTER TABLE {table} ADD COLUMN channel TEXT NOT NULL DEFAULT '{DEFAULT_CHANNEL}'"
        )
        cur.execute(f"UPDATE {table} SET channel = ? WHERE channel IS NULL OR channel = ''", (DEFAULT_CHANNEL,))


def _m003_channel_primary_keys(conn: sqlite3.Connection) -> None:
    """把含 channel 的表重建为 (…, channel) 联合主键。"""
    cur = conn.cursor()
    for table, create_sql in _CHANNEL_REBUILD_SQL.items():
        if not table_exists(conn, table):
            continue
        cols = table_columns(conn, table)
        if "channel" not in cols:
            continue

        # 判断当前主键是否已包含 channel
        pk_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[5]]
        if "channel" in pk_cols:
            continue

        tmp = f"{table}__migrate"
        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
        cur.execute(
            create_sql.replace(
                f"CREATE TABLE IF NOT EXISTS {table}", f"CREATE TABLE {tmp}"
            )
        )
        cur.execute(
            f"INSERT OR IGNORE INTO {tmp} ({', '.join(cols)}) "
            f"SELECT {', '.join(cols)} FROM {table}"
        )
        cur.execute(f"DROP TABLE {table}")
        cur.execute(f"ALTER TABLE {tmp} RENAME TO {table}")


MIGRATIONS: Tuple[Tuple[int, Callable[[sqlite3.Connection], None]], ...] = (
    (1, _m001_initial_schema),
    (2, _m002_add_channel_columns),
    (3, _m003_channel_primary_keys),
)


def backup_database(db_path: str) -> Optional[str]:
    """迁移前备份数据库文件，返回备份路径。"""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        return None
    backup_path = f"{db_path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def current_version(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "schema_version"):
        # 有 posts 表但没有版本表 -> 视为旧库，从 0 开始跑全部迁移
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def migrate(conn: sqlite3.Connection) -> int:
    """执行所有未应用的迁移，返回迁移前的版本号。"""
    before = current_version(conn)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        for version, fn in MIGRATIONS:
            applied = conn.execute(
                "SELECT 1 FROM schema_version WHERE version = ?", (version,)
            ).fetchone()
            if applied:
                continue
            fn(conn)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, _now()),
            )
        # 确保最新 schema 的索引/表齐全（全新库或旧库缺表）
        for sql in LATEST_SCHEMA_SQL:
            conn.execute(sql)
        conn.commit()
    except Exception as exc:  # pragma: no cover - 迁移失败必须整体回滚
        conn.rollback()
        raise MigrationError(f"数据库迁移失败: {exc}") from exc
    return before
