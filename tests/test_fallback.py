import pytest

from linuxdo_monitor.exceptions import DiscourseAuthError, SourceError
from linuxdo_monitor.source.fallback import FallbackSource
from linuxdo_monitor.source.rss import RSSSource


class FakeSource:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self.result = result or []
        self.error = error
        self.calls = 0
        self.closed = False
        self.forum_config = None
        self.timeout = 30

    def fetch(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


def test_uses_primary_when_healthy():
    primary = FakeSource("primary", result=["a"])
    secondary = FakeSource("secondary", result=["b"])
    source = FallbackSource(primary, secondary, degrade_on=(DiscourseAuthError,))

    assert source.fetch() == ["a"]
    assert secondary.calls == 0
    assert source.degraded is False


def test_degrades_on_configured_exception():
    primary = FakeSource("primary", error=DiscourseAuthError("cookie 失效"))
    secondary = FakeSource("secondary", result=["b"])
    source = FallbackSource(primary, secondary, degrade_on=(DiscourseAuthError,))

    assert source.fetch() == ["b"]
    assert source.degraded is True
    assert "cookie 失效" in source.last_error


def test_restores_automatically():
    primary = FakeSource("primary", error=DiscourseAuthError("down"))
    secondary = FakeSource("secondary", result=["b"])
    source = FallbackSource(primary, secondary, degrade_on=(DiscourseAuthError,))

    source.fetch()
    assert source.degraded is True

    primary.error = None
    primary.result = ["a"]
    assert source.fetch() == ["a"]
    assert source.degraded is False
    assert source.last_error is None


def test_other_errors_do_not_degrade():
    """网络抖动等普通错误不应切到备用源，留给下次重试。"""
    primary = FakeSource("primary", error=SourceError("timeout"))
    secondary = FakeSource("secondary", result=["b"])
    source = FallbackSource(primary, secondary, degrade_on=(DiscourseAuthError,))

    with pytest.raises(SourceError):
        source.fetch()
    assert secondary.calls == 0
    assert source.degraded is False


def test_both_failing_raises():
    primary = FakeSource("primary", error=DiscourseAuthError("down"))
    secondary = FakeSource("secondary", error=SourceError("rss 也挂了"))
    source = FallbackSource(primary, secondary, degrade_on=(DiscourseAuthError,))

    with pytest.raises(SourceError, match="主备数据源均失败"):
        source.fetch()


def test_callbacks_fire_once_each():
    events = []
    primary = FakeSource("primary", error=DiscourseAuthError("down"))
    secondary = FakeSource("secondary", result=["b"])
    source = FallbackSource(
        primary,
        secondary,
        degrade_on=(DiscourseAuthError,),
        on_degrade=lambda exc: events.append(("degrade", str(exc))),
        on_restore=lambda: events.append(("restore", None)),
    )

    source.fetch()
    source.fetch()  # 仍处于降级，不应重复触发 on_degrade
    primary.error = None
    source.fetch()

    assert events == [("degrade", "down"), ("restore", None)]


def test_close_closes_both():
    primary = FakeSource("primary")
    secondary = FakeSource("secondary")
    FallbackSource(primary, secondary).close()
    assert primary.closed and secondary.closed
