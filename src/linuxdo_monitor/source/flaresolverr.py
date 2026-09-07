"""FlareSolverr 客户端，用于绕过 Cloudflare 人机校验。

FlareSolverr 是一个独立的 HTTP 服务，接口为::

    POST /v1  {"cmd": "request.get", "url": "...", "maxTimeout": 60000}
    ->    {"status": "ok", "solution": {"status": 200, "response": "<body>"}}

只有配置了 ``flaresolverr_url`` 时才会启用；未配置时 Discourse 数据源直连。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from ..exceptions import SourceError

logger = logging.getLogger(__name__)

#: Cloudflare 质询页的特征文本
CLOUDFLARE_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf_chl_opt",
    "checking your browser",
    "enable javascript and cookies to continue",
    "__cf_chl_",
    "attention required! | cloudflare",
)


def looks_like_cloudflare(status_code: int, body: str) -> bool:
    """根据状态码和响应体判断是否被 Cloudflare 拦截。"""
    if status_code in (403, 429, 503):
        return True
    head = (body or "")[:4000].lower()
    return any(marker in head for marker in CLOUDFLARE_MARKERS)


class FlareSolverrClient:
    """调用 FlareSolverr 取回页面内容。"""

    def __init__(self, base_url: str, timeout: int = 60, max_timeout: int = 60000):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_timeout = max_timeout

    def get(
        self, url: str, cookie: Optional[str] = None, want_json: bool = True
    ) -> Any:
        """取回 url 内容。

        :param want_json: True 时把响应体按 JSON 解析，失败则抛 SourceError。
        """
        import json

        payload: Dict[str, Any] = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": self.max_timeout,
        }
        if cookie:
            payload["cookies"] = [
                {
                    "name": name.strip().split("=", 1)[0],
                    "value": name.strip().split("=", 1)[1],
                }
                for name in cookie.split(";")
                if "=" in name
            ]

        try:
            resp = requests.post(
                f"{self.base_url}/v1", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise SourceError(f"FlareSolverr 请求失败: {exc}") from exc

        if data.get("status") != "ok":
            raise SourceError(f"FlareSolverr 返回异常: {data.get('message')}")

        solution = data.get("solution") or {}
        body = solution.get("response") or ""
        status = solution.get("status") or 0

        if not want_json:
            return body
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise SourceError(
                f"FlareSolverr 返回的内容不是合法 JSON（status={status}）: {exc}"
            ) from exc


__all__ = ["CLOUDFLARE_MARKERS", "FlareSolverrClient", "looks_like_cloudflare"]
