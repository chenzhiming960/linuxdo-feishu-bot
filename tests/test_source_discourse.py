import json

import pytest

from linuxdo_monitor.config import ForumConfig
from linuxdo_monitor.exceptions import DiscourseAuthError, SourceError
from linuxdo_monitor.source.discourse import DiscourseSource

TOPICS_PAYLOAD = {
    "users": [
        {"id": 1, "username": "alice", "name": "Alice"},
        {"id": 2, "username": "bob"},
    ],
    "topic_list": {
        "per_page": 30,
        "topics": [
            {
                "id": 12345,
                "title": "Docker 部署 NAS 求助",
                "slug": "docker-nas",
                "category_id": 14,
                "created_at": "2026-09-07T02:00:00.000Z",
                "last_poster_username": "alice",
                "excerpt": "摘要内容",
                "posts_count": 3,
                "like_count": 5,
                "views": 100,
                "posters": [
                    {"user_id": 1, "description": "Original Poster", "extras": "latest"}
                ],
            },
            {
                "id": 12346,
                "title": "第二个主题",
                "slug": "second",
                "category_id": 7,
                "created_at": "2026-09-07T03:00:00.000Z",
                "excerpt": "",
                "posters": [
                    {"user_id": 2, "description": "Original Poster", "extras": "latest"}
                ],
            },
        ],
    },
}

CATEGORIES_PAYLOAD = {
    "category_list": {
        "categories": [
            {"id": 14, "name": "搞机零碎", "slug": "gaoji", "description": "硬件相关"},
            {"id": 7, "name": "软件开发", "slug": "dev", "description": ""},
        ]
    }
}


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None, json_error=False):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self._json_error = json_error

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


def make_forum(**kwargs) -> ForumConfig:
    base = {
        "forum_id": "linux-do",
        "name": "Linux.do",
        "source_type": "discourse",
        "discourse_url": "https://linux.do",
        "rss_url": "https://linux.do/latest.rss",
    }
    base.update(kwargs)
    return ForumConfig(**base)


@pytest.fixture
def source():
    return DiscourseSource(make_forum())


def _install_session(monkeypatch, source, responses):
    """按顺序返回响应；列表里的 Exception 会被抛出。"""
    calls = []
    iterator = iter(responses)

    def fake_get(url, **kwargs):
        calls.append(url)
        item = next(iterator)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(source.session, "get", fake_get)
    return calls


# ------------------------------------------------------------------ 解析

def test_fetch_parses_topics(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(payload=TOPICS_PAYLOAD)])
    posts = source.fetch()

    assert len(posts) == 2
    first = posts[0]
    # id 用规范化链接，与 RSSSource 保持一致，主备切换时去重连续
    assert first.id == "https://linux.do/t/docker-nas/12345"
    assert first.link == first.id
    assert first.title == "Docker 部署 NAS 求助"
    assert first.author == "alice"
    assert first.category == "14"
    assert first.summary == "摘要内容"
    assert first.published_at.year == 2026
    assert first.extra["topic_id"] == 12345


def test_author_falls_back_to_posters_user_map(monkeypatch, source):
    """没有 last_poster_username 时，用 posters[].user_id 反查 users。"""
    _install_session(monkeypatch, source, [FakeResponse(payload=TOPICS_PAYLOAD)])
    posts = source.fetch()
    assert posts[1].author == "bob"


def test_empty_topic_list(monkeypatch, source):
    _install_session(
        monkeypatch, source, [FakeResponse(payload={"topic_list": {"topics": []}})]
    )
    assert source.fetch() == []


def test_missing_topic_list_is_tolerated(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(payload={})])
    assert source.fetch() == []


# ------------------------------------------------------------ 错误处理

def test_cloudflare_block_raises_auth_error(monkeypatch, source):
    _install_session(
        monkeypatch,
        source,
        [FakeResponse(status_code=403, text="<html>Just a moment...</html>")],
    )
    with pytest.raises(DiscourseAuthError):
        source.fetch()


def test_cloudflare_marker_in_200_body_raises_auth_error(monkeypatch, source):
    _install_session(
        monkeypatch,
        source,
        [FakeResponse(status_code=200, text="<html>cf-browser-verification</html>")],
    )
    with pytest.raises(DiscourseAuthError):
        source.fetch()


def test_forbidden_without_cloudflare_is_auth_error(monkeypatch, source):
    _install_session(
        monkeypatch, source, [FakeResponse(status_code=401, text="unauthorized")]
    )
    with pytest.raises(DiscourseAuthError):
        source.fetch()


def test_server_error_is_plain_source_error(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(status_code=500, text="boom")])
    with pytest.raises(SourceError) as exc:
        source.fetch()
    assert not isinstance(exc.value, DiscourseAuthError)


def test_invalid_json_raises_source_error(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(json_error=True)])
    with pytest.raises(SourceError):
        source.fetch()


def test_network_exception_is_source_error(monkeypatch, source):
    import requests

    _install_session(monkeypatch, source, [requests.RequestException("timeout")])
    with pytest.raises(SourceError):
        source.fetch()


# ------------------------------------------------------- FlareSolverr 兜底

def test_cloudflare_falls_back_to_flaresolverr(monkeypatch):
    forum = make_forum(flaresolverr_url="http://flaresolverr:8191")
    source = DiscourseSource(forum)
    _install_session(
        monkeypatch,
        source,
        [FakeResponse(status_code=503, text="<html>Just a moment...</html>")],
    )

    captured = {}

    def fake_fs_get(url, cookie=None, want_json=True):
        captured["url"] = url
        captured["cookie"] = cookie
        return TOPICS_PAYLOAD

    monkeypatch.setattr(source.flaresolverr, "get", fake_fs_get)

    posts = source.fetch()
    assert len(posts) == 2
    assert captured["url"] == "https://linux.do/latest.json"


# ------------------------------------------------------------ 分类同步

def test_fetch_categories(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(payload=CATEGORIES_PAYLOAD)])
    categories = source.fetch_categories()
    assert categories == [
        {"id": "14", "name": "搞机零碎", "slug": "gaoji", "description": "硬件相关"},
        {"id": "7", "name": "软件开发", "slug": "dev", "description": ""},
    ]


def test_fetch_categories_swallows_errors(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(status_code=500, text="x")])
    assert source.fetch_categories() == []


# ------------------------------------------------------------ Cookie 检测

def test_check_cookie_ok(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(payload=TOPICS_PAYLOAD)])
    ok, message = source.check_cookie()
    assert ok is True
    assert "2" in message


def test_check_cookie_failure(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(status_code=403, text="cf")])
    ok, message = source.check_cookie()
    assert ok is False
    assert message


def test_check_cookie_rejects_unexpected_shape(monkeypatch, source):
    _install_session(monkeypatch, source, [FakeResponse(payload={"users": []})])
    ok, _ = source.check_cookie()
    assert ok is False


# ------------------------------------------------------------ 配置

def test_cookie_is_sent_as_header():
    source = DiscourseSource(make_forum(discourse_cookie="_t=abc; foo=bar"))
    assert source.session.headers["Cookie"] == "_t=abc; foo=bar"


def test_discourse_path_defaults_to_latest():
    assert DiscourseSource(make_forum()).path == "/latest.json"


def test_discourse_path_normalized():
    assert DiscourseSource(make_forum(discourse_path="new.json")).path == "/new.json"


def test_custom_path_is_used(monkeypatch):
    source = DiscourseSource(make_forum(discourse_path="/new.json"))
    calls = _install_session(
        monkeypatch, source, [FakeResponse(payload={"topic_list": {"topics": []}})]
    )
    source.fetch()
    assert calls == ["https://linux.do/new.json"]


def test_requires_discourse_url():
    from types import SimpleNamespace

    with pytest.raises(SourceError):
        DiscourseSource(SimpleNamespace(forum_id="x", discourse_url=""))


def test_create_source_returns_discourse(monkeypatch):
    from linuxdo_monitor.source import create_source
    from linuxdo_monitor.source.fallback import FallbackSource

    forum = make_forum()
    source = create_source(forum)
    # 配了 rss_url 且 fallback_to_rss 默认开启 -> 包一层 FallbackSource
    assert isinstance(source, FallbackSource)
    assert isinstance(source.primary, DiscourseSource)


def test_create_source_without_fallback():
    from linuxdo_monitor.source import create_source

    forum = make_forum(fallback_to_rss=False)
    source = create_source(forum)
    assert type(source) is DiscourseSource
