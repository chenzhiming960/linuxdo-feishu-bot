import pytest

from linuxdo_monitor.exceptions import SourceError
from linuxdo_monitor.source import create_source
from linuxdo_monitor.source.rss import RSSSource


class FakeFeed:
    def __init__(self, entries, bozo=0):
        self.entries = entries
        self.bozo = bozo
        self.bozo_exception = ValueError("bad xml") if bozo else None


def _entry(**kwargs):
    import time

    base = {
        "title": "测试标题",
        "link": "https://linux.do/t/1",
        "author": "alice",
        "summary": "<p>摘要 <b>内容</b></p>",
        "published_parsed": time.struct_time((2026, 9, 7, 10, 0, 0, 0, 250, 0)),
        "category": None,
    }
    base.update(kwargs)
    return type("Entry", (), base)()


def test_rss_source_parses_entries(monkeypatch, forum_config):
    monkeypatch.setattr(
        "linuxdo_monitor.source.rss.feedparser.parse",
        lambda url: FakeFeed([_entry()]),
    )
    source = RSSSource(forum_config)
    posts = source.fetch()
    assert len(posts) == 1
    post = posts[0]
    assert post.id == "https://linux.do/t/1"
    assert post.title == "测试标题"
    assert post.author == "alice"
    assert post.summary == "摘要 内容"
    assert post.published_at.year == 2026


def test_rss_source_skips_entries_without_link(monkeypatch, forum_config):
    monkeypatch.setattr(
        "linuxdo_monitor.source.rss.feedparser.parse",
        lambda url: FakeFeed([_entry(link="")]),
    )
    assert RSSSource(forum_config).fetch() == []


def test_rss_source_tolerates_bozo(monkeypatch, forum_config):
    monkeypatch.setattr(
        "linuxdo_monitor.source.rss.feedparser.parse",
        lambda url: FakeFeed([_entry()], bozo=1),
    )
    assert len(RSSSource(forum_config).fetch()) == 1


def test_rss_source_wraps_network_error(monkeypatch, forum_config):
    def boom(url):
        raise OSError("network down")

    monkeypatch.setattr("linuxdo_monitor.source.rss.feedparser.parse", boom)
    with pytest.raises(SourceError):
        RSSSource(forum_config).fetch()


def test_rss_source_requires_url():
    from types import SimpleNamespace

    with pytest.raises(SourceError):
        RSSSource(SimpleNamespace(forum_id="x", rss_url=""))


def test_create_source_factory(forum_config):
    assert isinstance(create_source(forum_config), RSSSource)


def test_create_source_unknown_type():
    from linuxdo_monitor.config import ForumConfig
    from linuxdo_monitor.exceptions import ConfigError

    forum = ForumConfig(forum_id="x", rss_url="https://x")
    forum.source_type = "nodeseek-api"
    with pytest.raises(ConfigError):
        create_source(forum)
