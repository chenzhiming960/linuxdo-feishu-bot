import json

import pytest

from linuxdo_monitor.exceptions import SourceError
from linuxdo_monitor.source.flaresolverr import (
    FlareSolverrClient,
    looks_like_cloudflare,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload) if isinstance(payload, dict) else str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _install(monkeypatch, response):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(
        "linuxdo_monitor.source.flaresolverr.requests.post", fake_post
    )
    return calls


def test_get_parses_solution_json(monkeypatch):
    solution = {"status": 200, "response": json.dumps({"ok": True})}
    calls = _install(monkeypatch, FakeResponse({"status": "ok", "solution": solution}))
    client = FlareSolverrClient("http://fs:8191/")

    assert client.get("https://linux.do/latest.json") == {"ok": True}
    payload = calls[0]["json"]
    assert payload["cmd"] == "request.get"
    assert payload["url"] == "https://linux.do/latest.json"
    assert calls[0]["url"] == "http://fs:8191/v1"


def test_cookie_is_converted_to_cookies_array(monkeypatch):
    solution = {"status": 200, "response": "{}"}
    calls = _install(monkeypatch, FakeResponse({"status": "ok", "solution": solution}))
    FlareSolverrClient("http://fs:8191").get("https://x", cookie="_t=abc; foo=bar")

    assert calls[0]["json"]["cookies"] == [
        {"name": "_t", "value": "abc"},
        {"name": "foo", "value": "bar"},
    ]


def test_non_ok_status_raises(monkeypatch):
    _install(monkeypatch, FakeResponse({"status": "error", "message": "no browser"}))
    with pytest.raises(SourceError, match="FlareSolverr 返回异常"):
        FlareSolverrClient("http://fs:8191").get("https://x")


def test_non_json_solution_raises(monkeypatch):
    solution = {"status": 200, "response": "<html>Just a moment</html>"}
    _install(monkeypatch, FakeResponse({"status": "ok", "solution": solution}))
    with pytest.raises(SourceError, match="不是合法 JSON"):
        FlareSolverrClient("http://fs:8191").get("https://x")


def test_request_failure_raises(monkeypatch):
    _install(monkeypatch, RuntimeError("connection refused"))
    with pytest.raises(SourceError, match="FlareSolverr 请求失败"):
        FlareSolverrClient("http://fs:8191").get("https://x")


def test_want_json_false_returns_raw(monkeypatch):
    solution = {"status": 200, "response": "<html>ok</html>"}
    _install(monkeypatch, FakeResponse({"status": "ok", "solution": solution}))
    assert FlareSolverrClient("http://fs:8191").get("https://x", want_json=False) == "<html>ok</html>"


@pytest.mark.parametrize("status", [403, 429, 503])
def test_cloudflare_status_codes(status):
    assert looks_like_cloudflare(status, "<html>normal</html>") is True


@pytest.mark.parametrize(
    "body",
    [
        "<html>Just a moment...</html>",
        "<script>cf-browser-verification</script>",
        "Checking your browser before accessing",
        "Enable JavaScript and cookies to continue",
        "__cf_chl_jschl_tk__=abc",
        "Attention Required! | CloudFlare",
    ],
)
def test_cloudflare_markers(body):
    assert looks_like_cloudflare(200, body) is True


def test_normal_response_is_not_cloudflare():
    assert looks_like_cloudflare(200, json.dumps({"topic_list": {}})) is False
