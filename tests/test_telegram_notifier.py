import pytest
from telegram.error import Forbidden, RetryAfter, TelegramError

from linuxdo_monitor.config import NotifierConfig
from linuxdo_monitor.exceptions import NotifierBlockedError, NotifierError
from linuxdo_monitor.notifier.telegram import TelegramNotifier


class FakeBot:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return object()

    async def get_me(self):
        if self.error:
            raise self.error
        return type("Me", (), {"username": "testbot"})()

    async def shutdown(self):
        pass


@pytest.fixture
def tg_notifier(forum_config):
    cfg = NotifierConfig(
        notifier_type="telegram",
        telegram_bot_token="123456:AAA",
        max_retries=1,
        retry_delay=0,
    )
    notifier = TelegramNotifier(cfg, forum_config)
    notifier.bot = FakeBot()
    return notifier


async def test_send_notification_content(tg_notifier, sample_post):
    assert await tg_notifier.send_notification("1001", sample_post, "Docker", "搞机零碎") is True
    kwargs = tg_notifier.bot.calls[0]
    assert kwargs["chat_id"] == "1001"
    assert "Docker" in kwargs["text"]
    assert sample_post.link in kwargs["text"]
    assert "搞机零碎" in kwargs["text"]


async def test_send_notification_all_has_no_keyword(tg_notifier, sample_post):
    await tg_notifier.send_notification_all("1001", sample_post)
    assert "匹配关键词" not in tg_notifier.bot.calls[0]["text"]


async def test_forbidden_raises_blocked(tg_notifier, sample_post):
    tg_notifier.bot = FakeBot(error=Forbidden("bot was blocked by the user"))
    with pytest.raises(NotifierBlockedError) as exc:
        await tg_notifier.send_notification("1001", sample_post, "Docker")
    assert exc.value.user_id == "1001"


async def test_telegram_error_becomes_notifier_error(tg_notifier, sample_post):
    tg_notifier.bot = FakeBot(error=TelegramError("chat not found"))
    with pytest.raises(NotifierError):
        await tg_notifier.send_notification("1001", sample_post, "Docker")
    assert tg_notifier.failed_count == 1


async def test_retry_after_is_retried(tg_notifier, sample_post):
    tg_notifier.max_retries = 2

    class FlakyBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.n = 0

        async def send_message(self, **kwargs):
            self.n += 1
            self.calls.append(kwargs)
            if self.n == 1:
                raise RetryAfter(0)

    tg_notifier.bot = FlakyBot()
    assert await tg_notifier.send_notification("1001", sample_post, "Docker") is True
    assert tg_notifier.bot.n == 2


async def test_telegram_does_not_support_broadcast(tg_notifier, sample_post):
    assert tg_notifier.supports_broadcast() is False
    assert tg_notifier.get_broadcast_keywords() == ([], False)


async def test_health_check(tg_notifier):
    ok, message = await tg_notifier.health_check()
    assert ok is True
    assert "testbot" in message
