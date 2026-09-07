import asyncio
import json

import pytest

from linuxdo_monitor import app as app_module
from linuxdo_monitor.app import BROADCAST_USER_ID, Application
from linuxdo_monitor.config import NotifierConfig
from linuxdo_monitor.exceptions import SourceError
from linuxdo_monitor.notifier.base import BaseNotifier


class FakeSource:
    def __init__(self, posts):
        self.posts = posts
        self.closed = False

    def fetch(self):
        return self.posts

    def close(self):
        self.closed = True


class FakeNotifier(BaseNotifier):
    """定向渠道（模拟 Telegram），记录所有发送。"""

    channel_type = "telegram"

    def __init__(self, config, forum_config=None):
        super().__init__(config, forum_config)
        self.sent: list = []

    async def send_notification(self, user_id, post, keyword=None, category_name=None):
        self.sent.append((user_id, post.id, keyword, False))
        return True

    async def send_notification_all(self, user_id, post, category_name=None):
        self.sent.append((user_id, post.id, None, True))
        return True

    async def send_text(self, user_id, text):
        self.sent.append((user_id, text, None, None))
        return True


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"code": 0}


@pytest.fixture
def feishu_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "linuxdo_monitor.notifier.feishu.requests.post",
        lambda url, **kw: (calls.append({"url": url, **kw}), FakeResponse())[1],
    )
    return calls


def build_app(monkeypatch, config, db, source, notifiers=None):
    monkeypatch.setattr(app_module, "create_source", lambda forum: source)
    if notifiers is not None:
        monkeypatch.setattr(app_module, "create_notifiers", lambda forum: notifiers)
    return Application(config, db=db, config_path=None)


async def test_first_run_only_seeds_database(monkeypatch, app_config, tmp_db, sample_post, feishu_calls):
    app = build_app(monkeypatch, app_config, tmp_db, FakeSource([sample_post]))
    await app.start(schedule=False)
    await app.fetch_and_notify("linux-do")

    assert tmp_db.post_exists(sample_post.id, "linux-do") is True
    assert feishu_calls == []


async def test_second_run_broadcasts_matched_post(
    monkeypatch, app_config, tmp_db, sample_post, feishu_calls
):
    app = build_app(monkeypatch, app_config, tmp_db, FakeSource([sample_post]))
    await app.start(schedule=False)
    app.mark_initial_sync_done()
    await app.fetch_and_notify("linux-do")

    assert len(feishu_calls) == 1
    payload = json.loads(feishu_calls[0]["data"].decode("utf-8"))
    assert payload["msg_type"] == "interactive"


async def test_unmatched_post_is_not_pushed(
    monkeypatch, app_config, tmp_db, sample_post, feishu_calls
):
    sample_post = type(sample_post)(
        id=sample_post.id,
        title="与关键词完全无关的标题",
        link=sample_post.link,
        author=sample_post.author,
    )
    app = build_app(monkeypatch, app_config, tmp_db, FakeSource([sample_post]))
    await app.start(schedule=False)
    app.mark_initial_sync_done()
    await app.fetch_and_notify("linux-do")
    assert feishu_calls == []


async def test_push_all_ignores_keywords(monkeypatch, app_config, tmp_db, sample_post, feishu_calls):
    app_config.forums[0].notifiers[0].feishu_push_all = True
    unmatched = type(sample_post)(
        id="https://linux.do/t/9", title="无关标题", link="https://linux.do/t/9"
    )
    app = build_app(monkeypatch, app_config, tmp_db, FakeSource([unmatched]))
    await app.start(schedule=False)
    app.mark_initial_sync_done()
    await app.fetch_and_notify("linux-do")
    assert len(feishu_calls) == 1


async def test_broadcast_notification_is_deduplicated(
    monkeypatch, app_config, tmp_db, sample_post, feishu_calls
):
    app = build_app(monkeypatch, app_config, tmp_db, FakeSource([sample_post]))
    await app.start(schedule=False)
    app.mark_initial_sync_done()
    await app.fetch_and_notify("linux-do")
    await app.fetch_and_notify("linux-do")
    assert len(feishu_calls) == 1


async def test_telegram_subscriber_receives_matched_post(
    monkeypatch, forum_config, tmp_db, sample_post
):
    tmp_db.add_subscription("1001", "Docker", "linux-do", "telegram")
    tmp_db.add_subscription("1002", "NAS", "linux-do", "telegram")
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum_config
    )
    from linuxdo_monitor.config import AppConfig

    config = AppConfig(forums=[forum_config])
    app = build_app(monkeypatch, config, tmp_db, FakeSource([sample_post]), notifiers=[notifier])

    await app.start(schedule=False)
    app.mark_initial_sync_done()
    await app.fetch_and_notify("linux-do")

    # 两个关键词都命中同一个帖子，两个订阅者都应收到
    assert sorted(s[0] for s in notifier.sent) == ["1001", "1002"]


async def test_subscribe_all_user_receives_every_new_post(
    monkeypatch, forum_config, tmp_db, sample_post
):
    tmp_db.add_subscribe_all("1001", "linux-do", "telegram")
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum_config
    )
    from linuxdo_monitor.config import AppConfig

    config = AppConfig(forums=[forum_config])
    app = build_app(monkeypatch, config, tmp_db, FakeSource([sample_post]), notifiers=[notifier])

    await app.start(schedule=False)
    app.mark_initial_sync_done()
    await app.fetch_and_notify("linux-do")

    assert notifier.sent == [("1001", sample_post.id, None, True)]


async def test_blocked_user_is_skipped(monkeypatch, forum_config, tmp_db, sample_post):
    tmp_db.add_subscription("1001", "Docker", "linux-do", "telegram")
    tmp_db.mark_blocked("1001", "linux-do", "telegram")
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum_config
    )
    from linuxdo_monitor.config import AppConfig

    config = AppConfig(forums=[forum_config])
    app = build_app(monkeypatch, config, tmp_db, FakeSource([sample_post]), notifiers=[notifier])

    await app.start(schedule=False)
    app.mark_initial_sync_done()
    await app.fetch_and_notify("linux-do")
    assert notifier.sent == []


async def test_source_error_notifies_admin(monkeypatch, forum_config, tmp_db):
    from linuxdo_monitor.config import AppConfig

    class BoomSource:
        def fetch(self):
            raise SourceError("RSS 500")

        def close(self):
            pass

    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum_config
    )
    monkeypatch.setattr(app_module, "create_source", lambda forum: BoomSource())
    config = AppConfig(forums=[forum_config], admin_chat_id=999)
    app = build_app(monkeypatch, config, tmp_db, BoomSource(), notifiers=[notifier])

    await app.start(schedule=False)
    await app.fetch_and_notify("linux-do")
    assert notifier.sent and notifier.sent[0][0] == 999


async def test_reload_config_rebuilds_runtimes(monkeypatch, app_config, tmp_db):
    app = build_app(monkeypatch, app_config, tmp_db, FakeSource([]))
    await app.start(schedule=False)
    app_config.forums[0].fetch_interval = 120
    app.reload_config(app_config)
    assert app.runtimes["linux-do"].config.fetch_interval == 120


async def test_notifier_stats(monkeypatch, forum_config, tmp_db):
    from linuxdo_monitor.config import AppConfig

    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum_config
    )
    config = AppConfig(forums=[forum_config])
    app = build_app(monkeypatch, config, tmp_db, FakeSource([]), notifiers=[notifier])
    await app.start(schedule=False)
    stats = app.notifier_stats()
    assert stats == [{"channel": "telegram", "sent": 0, "failed": 0, "last_error": None, "forum_id": "linux-do"}]
