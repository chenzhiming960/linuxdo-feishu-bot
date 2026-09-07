import sqlite3

from linuxdo_monitor.database import Database
from linuxdo_monitor.migrations import SCHEMA_VERSION, current_version, table_columns


def test_fresh_database_has_all_tables(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    Database(db_path, backup=False)
    conn = sqlite3.connect(db_path)
    try:
        assert current_version(conn) == SCHEMA_VERSION
        for table in (
            "users",
            "subscriptions",
            "user_subscriptions",
            "subscribe_all",
            "posts",
            "notifications",
            "blocked_users",
            "categories",
        ):
            assert table_columns(conn, table), f"{table} 未创建"
    finally:
        conn.close()


def test_channel_column_exists_with_default(tmp_db: Database):
    tmp_db.ensure_user("1001", "linux-do", "telegram")
    users = tmp_db.get_users("linux-do")
    assert users[0]["channel"] == "telegram"


def test_same_user_can_exist_on_two_channels(tmp_db: Database):
    tmp_db.ensure_user("1001", "linux-do", "telegram")
    tmp_db.ensure_user("1001", "linux-do", "feishu")
    assert len(tmp_db.get_users("linux-do")) == 2


def test_add_post_deduplicates(tmp_db: Database, sample_post):
    assert tmp_db.add_post(sample_post, "linux-do") is True
    assert tmp_db.add_post(sample_post, "linux-do") is False
    assert tmp_db.get_stats("linux-do")["posts"] == 1


def test_subscription_crud(tmp_db: Database):
    assert tmp_db.add_subscription("1001", "Docker", "linux-do", "telegram") is True
    assert tmp_db.add_subscription("1001", "Docker", "linux-do", "telegram") is False
    assert tmp_db.get_subscriptions("1001", "linux-do", "telegram") == ["Docker"]
    assert tmp_db.remove_subscription("1001", "Docker", "linux-do", "telegram") is True
    assert tmp_db.get_subscriptions("1001", "linux-do", "telegram") == []


def test_keyword_subscriptions_are_channel_scoped(tmp_db: Database):
    tmp_db.add_subscription("1001", "Docker", "linux-do", "telegram")
    tmp_db.add_subscription("1002", "Docker", "linux-do", "feishu")
    assert tmp_db.get_keyword_subscriptions("linux-do", "telegram") == [("1001", "Docker")]
    assert len(tmp_db.get_keyword_subscriptions("linux-do")) == 2


def test_subscribe_all(tmp_db: Database):
    assert tmp_db.add_subscribe_all("1001", "linux-do", "telegram") is True
    assert tmp_db.is_subscribe_all("1001", "linux-do", "telegram") is True
    assert tmp_db.is_subscribe_all("1001", "linux-do", "feishu") is False
    assert tmp_db.get_subscribe_all_users("linux-do", "telegram") == ["1001"]
    assert tmp_db.remove_subscribe_all("1001", "linux-do", "telegram") is True


def test_author_subscriptions(tmp_db: Database):
    tmp_db.add_author_subscription("1001", "alice", "linux-do", "telegram")
    assert tmp_db.get_author_subscribers("Alice", "linux-do", "telegram") == ["1001"]
    assert tmp_db.get_author_subscriptions("1001", "linux-do", "telegram") == ["alice"]


def test_notification_dedup(tmp_db: Database, sample_post):
    assert tmp_db.notification_sent("1001", sample_post.id, "Docker", "linux-do", "telegram") is False
    tmp_db.record_notification("1001", sample_post.id, "Docker", "linux-do", "telegram")
    assert tmp_db.notification_sent("1001", sample_post.id, "Docker", "linux-do", "telegram") is True
    # 同帖子换个渠道不冲突
    assert tmp_db.notification_sent("1001", sample_post.id, "Docker", "linux-do", "feishu") is False


def test_blocked_users(tmp_db: Database):
    assert tmp_db.is_blocked("1001", "linux-do", "telegram") is False
    tmp_db.mark_blocked("1001", "linux-do", "telegram")
    assert tmp_db.is_blocked("1001", "linux-do", "telegram") is True
    assert len(tmp_db.get_blocked("linux-do")) == 1


def test_cleanup_posts(tmp_db: Database, sample_post):
    tmp_db.add_post(sample_post, "linux-do")
    assert tmp_db.cleanup_posts(retention_hours=-1) == 1
    assert tmp_db.get_stats("linux-do")["posts"] == 0


def test_category_lookup(tmp_db: Database):
    tmp_db.upsert_category("14", "linux-do", name="搞机零碎")
    assert tmp_db.get_category_name("14", "linux-do") == "搞机零碎"
    assert tmp_db.get_category_name("99", "linux-do") is None


def test_keyword_stats(tmp_db: Database, sample_post):
    tmp_db.record_notification("1001", sample_post.id, "Docker", "linux-do", "telegram")
    tmp_db.record_notification("1002", sample_post.id, "Docker", "linux-do", "telegram")
    stats = tmp_db.get_keyword_stats("linux-do")
    assert stats[0]["keyword"] == "Docker"
    assert stats[0]["cnt"] == 2


def test_query_returns_dicts(tmp_db: Database):
    tmp_db.ensure_user("1001", "linux-do", "telegram")
    rows = tmp_db.query("SELECT chat_id FROM users")
    assert rows == [{"chat_id": "1001"}]
