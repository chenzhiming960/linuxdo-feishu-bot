import asyncio

import pytest

from linuxdo_monitor import app as app_module
from linuxdo_monitor.app import Application
from linuxdo_monitor.config import AppConfig, ForumConfig, NotifierConfig
from linuxdo_monitor.exceptions import DiscourseAuthError, SourceError
from linuxdo_monitor.source.discourse import DiscourseSource
from linuxdo_monitor.source.fallback import FallbackSource

from .test_app import FakeNotifier, FakeSource  # noqa: F401


class FlakyDiscourse(DiscourseSource):
    """主源，可通过 available 开关模拟 Cookie 失效/恢复。"""

    def __init__(self, posts, available=True):
        # 只借用 DiscourseSource 的身份与 forum_config，行为全部由本类控制
        super().__init__(discourse_forum())
        self.posts = posts
        self.available = available
        self.checks = 0

    def fetch(self):
        if not self.available:
            raise DiscourseAuthError("cookie 失效")
        return self.posts

    def check_cookie(self):
        self.checks += 1
        return self.available, "ok" if self.available else "cookie 失效"

    def fetch_categories(self):
        return [{"id": "14", "name": "搞机零碎", "slug": "gaoji", "description": ""}]

    def close(self):
        pass


def discourse_forum(**kwargs) -> ForumConfig:
    base = {
        "forum_id": "linux-do",
        "name": "Linux.do",
        "source_type": "discourse",
        "discourse_url": "https://linux.do",
        "rss_url": "https://linux.do/latest.rss",
        "cookie_check_interval": 60,
        "notifiers": [
            NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A")
        ],
    }
    base.update(kwargs)
    return ForumConfig(**base)


def build(monkeypatch, forum, db, primary, secondary, notifier):
    source = FallbackSource(primary, secondary, degrade_on=(DiscourseAuthError,))
    monkeypatch.setattr(app_module, "create_source", lambda f: source)
    monkeypatch.setattr(app_module, "create_notifiers", lambda f: [notifier])
    app = Application(AppConfig(forums=[forum], admin_chat_id=999), db=db)
    return app


@pytest.fixture
def rss_posts(sample_post):
    rss_version = type(sample_post)(
        id=sample_post.id,
        title=sample_post.title,
        link=sample_post.link,
        author=None,
        category=None,
        summary=None,
        published_at=None,
    )
    return [rss_version]


async def test_degradation_notifies_admin_once(monkeypatch, tmp_db, rss_posts):
    forum = discourse_forum()
    primary = FlakyDiscourse([], available=False)
    secondary = FakeSource(rss_posts)
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum
    )
    app = build(monkeypatch, forum, tmp_db, primary, secondary, notifier)

    await app.start(schedule=False)
    await app.fetch_and_notify("linux-do")
    await app.fetch_and_notify("linux-do")

    alerts = [s for s in notifier.sent if s[0] == 999]
    assert len(alerts) == 1, "降级告警只应发一次"
    assert "降级" in alerts[0][1]


async def test_recovery_notifies_admin(monkeypatch, tmp_db, rss_posts):
    forum = discourse_forum()
    primary = FlakyDiscourse([], available=False)
    secondary = FakeSource(rss_posts)
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum
    )
    app = build(monkeypatch, forum, tmp_db, primary, secondary, notifier)

    await app.start(schedule=False)
    await app.fetch_and_notify("linux-do")
    primary.available = True
    await app.fetch_and_notify("linux-do")

    alerts = [s[1] for s in notifier.sent if s[0] == 999]
    assert any("恢复" in text for text in alerts)


async def test_fallback_posts_are_pushed(monkeypatch, tmp_db, rss_posts):
    forum = discourse_forum()
    primary = FlakyDiscourse([], available=False)
    secondary = FakeSource(rss_posts)
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum
    )
    tmp_db.add_subscribe_all("1001", "linux-do", "telegram")
    app = build(monkeypatch, forum, tmp_db, primary, secondary, notifier)

    await app.start(schedule=False)
    app.mark_initial_sync_done()
    await app.fetch_and_notify("linux-do")

    pushes = [s for s in notifier.sent if s[0] == "1001"]
    assert len(pushes) == 1, "降级后仍应通过 RSS 推送新帖"


async def test_cookie_check_task_alerts_and_throttles(monkeypatch, tmp_db, rss_posts):
    forum = discourse_forum()
    primary = FlakyDiscourse([], available=False)
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum
    )
    app = build(monkeypatch, forum, tmp_db, primary, FakeSource(rss_posts), notifier)

    await app.start(schedule=False)
    await app._check_cookie_task("linux-do")
    await app._check_cookie_task("linux-do")  # 间隔未到，应被限流

    alerts = [s for s in notifier.sent if s[0] == 999]
    assert len(alerts) == 1
    assert "Cookie" in alerts[0][1]


async def test_cookie_check_syncs_categories(monkeypatch, tmp_db, rss_posts):
    forum = discourse_forum()
    primary = FlakyDiscourse([], available=True)
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum
    )
    app = build(monkeypatch, forum, tmp_db, primary, FakeSource(rss_posts), notifier)

    await app.start(schedule=False)
    await app._check_cookie_task("linux-do")

    assert tmp_db.get_category_name("14", "linux-do") == "搞机零碎"


async def test_cookie_check_skips_rss_only_forums(monkeypatch, forum_config, tmp_db):
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum_config
    )
    monkeypatch.setattr(app_module, "create_source", lambda f: FakeSource([]))
    monkeypatch.setattr(app_module, "create_notifiers", lambda f: [notifier])
    app = Application(AppConfig(forums=[forum_config]), db=tmp_db)

    await app.start(schedule=False)
    await app._check_cookie_task("linux-do")
    assert notifier.sent == []


async def test_unknown_forum_is_ignored(monkeypatch, tmp_db, rss_posts):
    forum = discourse_forum()
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum
    )
    app = build(monkeypatch, forum, tmp_db, FlakyDiscourse([]), FakeSource(rss_posts), notifier)
    await app.start(schedule=False)
    await app._check_cookie_task("nope")  # 不应抛错


def test_discourse_source_is_unwrapped(monkeypatch, tmp_db, rss_posts):
    forum = discourse_forum()
    primary = FlakyDiscourse([])
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"), forum
    )
    app = build(monkeypatch, forum, tmp_db, primary, FakeSource(rss_posts), notifier)
    asyncio.run(app.start(schedule=False))

    runtime = app.runtimes["linux-do"]
    assert isinstance(runtime.source, FallbackSource)
    assert app._discourse_source(runtime) is primary
