import asyncio
import json

import pytest

from linuxdo_monitor.exceptions import NotifierError
from linuxdo_monitor.notifier.feishu import FeishuNotifier


class FakeResponse:
    def __init__(self, payload, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._payload


def _install_fake_post(monkeypatch, responses):
    """按顺序返回 FakeResponse；responses 里的 Exception 会被直接抛出。"""
    calls = []
    iterator = iter(responses)

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        item = next(iterator)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("linuxdo_monitor.notifier.feishu.requests.post", fake_post)
    return calls


def _sent_payload(calls, index=0):
    return json.loads(calls[index]["data"].decode("utf-8"))


def test_build_card_contains_keyword_title_and_link(feishu_config, forum_config, sample_post):
    notifier = FeishuNotifier(feishu_config, forum_config)
    card = notifier.build_card(sample_post, "Docker", "搞机零碎")
    assert card["msg_type"] == "interactive"
    assert "Linux.do 新帖提醒" in card["card"]["header"]["title"]["content"]
    blob = json.dumps(card, ensure_ascii=False)
    assert "Docker" in blob
    assert sample_post.title in blob
    assert sample_post.link in blob


async def test_broadcast_sends_interactive_card(monkeypatch, feishu_config, forum_config, sample_post):
    calls = _install_fake_post(monkeypatch, [FakeResponse({"code": 0})])
    notifier = FeishuNotifier(feishu_config, forum_config)

    assert await notifier.broadcast(sample_post, "Docker") is True

    payload = _sent_payload(calls)
    assert payload["msg_type"] == "interactive"
    assert calls[0]["url"] == feishu_config.feishu_webhook_url


async def test_broadcast_falls_back_to_text(monkeypatch, feishu_config, forum_config, sample_post):
    calls = _install_fake_post(
        monkeypatch,
        [
            FakeResponse({"code": 19001, "msg": "card invalid"}),
            FakeResponse({"code": 0}),
        ],
    )
    notifier = FeishuNotifier(feishu_config, forum_config)

    assert await notifier.broadcast(sample_post, "Docker") is True
    assert _sent_payload(calls, 0)["msg_type"] == "interactive"
    assert _sent_payload(calls, 1)["msg_type"] == "text"


async def test_broadcast_raises_when_fallback_also_fails(monkeypatch, feishu_config, forum_config, sample_post):
    _install_fake_post(
        monkeypatch,
        [
            FakeResponse({"code": 19001, "msg": "bad"}),
            FakeResponse({"code": 19001, "msg": "bad"}),
        ],
    )
    notifier = FeishuNotifier(feishu_config, forum_config)
    with pytest.raises(NotifierError):
        await notifier.broadcast(sample_post, "Docker")


async def test_signature_is_added_when_secret_set(monkeypatch, feishu_config, forum_config, sample_post):
    feishu_config.feishu_secret = "topsecret"
    calls = _install_fake_post(monkeypatch, [FakeResponse({"code": 0})])
    notifier = FeishuNotifier(feishu_config, forum_config)

    await notifier.broadcast(sample_post, "Docker")
    payload = _sent_payload(calls)
    assert "timestamp" in payload and "sign" in payload


async def test_get_broadcast_keywords(feishu_config, forum_config):
    notifier = FeishuNotifier(feishu_config, forum_config)
    assert notifier.get_broadcast_keywords() == (["Docker", "NAS"], False)
    assert notifier.supports_broadcast() is True


async def test_health_check_reports_failure(monkeypatch, feishu_config, forum_config):
    _install_fake_post(monkeypatch, [FakeResponse({"code": 19001, "msg": "no bot"})])
    notifier = FeishuNotifier(feishu_config, forum_config)
    ok, message = await notifier.health_check()
    assert ok is False
    assert "no bot" in message


async def test_personal_mode_requires_webhook_user_id(feishu_config, forum_config, sample_post):
    feishu_config.feishu_mode = "personal"
    notifier = FeishuNotifier(feishu_config, forum_config)
    with pytest.raises(NotifierError):
        await notifier.send_notification("1001", sample_post, "Docker")
