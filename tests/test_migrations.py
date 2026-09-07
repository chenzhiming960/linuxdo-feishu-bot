import sqlite3

from linuxdo_monitor.database import Database
from linuxdo_monitor.migrations import (
    DEFAULT_CHANNEL,
    LEGACY_FORUM_ID,
    SCHEMA_VERSION,
    current_version,
    migrate,
    table_columns,
)


def _legacy_feishu_db(path) -> sqlite3.Connection:
    """旧版 linuxdo-feishu-bot 的库：只有 posts(link, created_at)。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE posts (link TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO posts (link, created_at) VALUES (?, ?)",
        ("https://linux.do/t/1", "2026-09-01 10:00:00"),
    )
    conn.commit()
    return conn


def _legacy_telegram_db(path) -> sqlite3.Connection:
    """旧版 Telegram 监控的库：表齐全但没有 channel 列。"""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE users (chat_id TEXT NOT NULL, forum TEXT NOT NULL,"
        " created_at TEXT NOT NULL, PRIMARY KEY (chat_id, forum))"
    )
    conn.execute(
        "CREATE TABLE subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " chat_id TEXT NOT NULL, keyword TEXT NOT NULL, forum TEXT NOT NULL,"
        " created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE TABLE posts (id TEXT NOT NULL, forum TEXT NOT NULL, title TEXT, created_at TEXT NOT NULL, PRIMARY KEY (id, forum))")
    conn.execute(
        "INSERT INTO users (chat_id, forum, created_at) VALUES ('1001', 'linux-do', '2026-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO subscriptions (chat_id, keyword, forum, created_at)"
        " VALUES ('1001', 'Docker', 'linux-do', '2026-01-01 00:00:00')"
    )
    conn.commit()
    return conn


def test_legacy_feishu_database_is_converted(tmp_path):
    path = str(tmp_path / "legacy.db")
    conn = _legacy_feishu_db(path)
    try:
        migrate(conn)
        columns = table_columns(conn, "posts")
        assert {"id", "forum", "link", "created_at"} <= set(columns)
        rows = conn.execute("SELECT id, forum, link FROM posts").fetchall()
        assert rows == [("https://linux.do/t/1", LEGACY_FORUM_ID, "https://linux.do/t/1")]
        assert current_version(conn) == SCHEMA_VERSION
    finally:
        conn.close()


def test_legacy_telegram_database_gets_channel(tmp_path):
    path = str(tmp_path / "legacy_tg.db")
    conn = _legacy_telegram_db(path)
    try:
        migrate(conn)
        assert "channel" in table_columns(conn, "users")
        assert "channel" in table_columns(conn, "subscriptions")

        pk = [r[1] for r in conn.execute("PRAGMA table_info(users)") if r[5]]
        assert set(pk) == {"chat_id", "forum", "channel"}

        users = conn.execute("SELECT chat_id, channel FROM users").fetchall()
        assert users == [("1001", DEFAULT_CHANNEL)]
        subs = conn.execute("SELECT keyword, channel FROM subscriptions").fetchall()
        assert subs == [("Docker", DEFAULT_CHANNEL)]
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "fresh.db")
    conn = sqlite3.connect(path)
    try:
        migrate(conn)
        first = current_version(conn)
        migrate(conn)
        assert current_version(conn) == first == SCHEMA_VERSION
    finally:
        conn.close()


def test_database_backup_is_created(tmp_path):
    path = str(tmp_path / "bk.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.commit()
    conn.close()

    Database(path)  # 触发备份
    import glob

    assert glob.glob(path + ".bak-*")
