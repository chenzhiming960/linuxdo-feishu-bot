"""Discourse JSON API 数据源。

相比 RSS 能拿到 RSS 里没有的字段：作者（``last_poster_username`` / posters→users）、
分类 id、回复数、点赞数等。部分论坛（如 Linux.do）需要 Cookie 才能访问 JSON 接口，
且常前置 Cloudflare 校验，因此支持：

* 直接带 Cookie 请求
* 被 Cloudflare 拦截时改走 FlareSolverr（需配置 ``flaresolverr_url``）
* 两者都不通时抛 :class:`DiscourseAuthError`，由上层降级到 RSS

``Post.id`` 使用规范化后的帖子链接（``{base}/t/{slug}/{id}``），
与 RSSSource 的 id 形式保持一致，这样主备源切换时去重表仍然连续。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..exceptions import DiscourseAuthError, SourceError
from ..models import Post
from .base import BaseSource
from .flaresolverr import FlareSolverrClient, looks_like_cloudflare

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


class DiscourseSource(BaseSource):
    """通过 Discourse JSON API 拉取最新主题。"""

    source_type = "discourse"

    def __init__(self, forum_config=None, timeout: int = 30):
        super().__init__(forum_config, timeout)
        self.base_url = (getattr(forum_config, "discourse_url", "") or "").rstrip("/")
        if not self.base_url:
            raise SourceError("DiscourseSource 缺少 discourse_url")

        self.cookie = getattr(forum_config, "discourse_cookie", None)
        self.path = getattr(forum_config, "discourse_path", None) or "/latest.json"
        if not self.path.startswith("/"):
            self.path = "/" + self.path

        flaresolverr_url = getattr(forum_config, "flaresolverr_url", None)
        self.flaresolverr = (
            FlareSolverrClient(flaresolverr_url, timeout=max(60, timeout))
            if flaresolverr_url
            else None
        )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": "application/json",
            }
        )
        if self.cookie:
            self.session.headers["Cookie"] = self.cookie

    # ------------------------------------------------------------ HTTP

    def _request_json(self, path: str) -> Any:
        """取回 JSON；被 Cloudflare 拦截时尝试 FlareSolverr。"""
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise SourceError(f"Discourse 请求失败 {url}: {exc}") from exc

        if looks_like_cloudflare(resp.status_code, resp.text):
            if self.flaresolverr is not None:
                logger.warning("直连被 Cloudflare 拦截，改用 FlareSolverr: %s", url)
                return self.flaresolverr.get(url, cookie=self.cookie)
            raise DiscourseAuthError(
                f"Discourse 被 Cloudflare 拦截（HTTP {resp.status_code}），"
                "请配置 discourse_cookie 或 flaresolverr_url"
            )

        if resp.status_code in (401, 403):
            raise DiscourseAuthError(
                f"Discourse 拒绝访问（HTTP {resp.status_code}），Cookie 可能已失效"
            )
        if not resp.ok:
            raise SourceError(f"Discourse 返回 HTTP {resp.status_code}: {url}")

        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"Discourse 返回内容不是合法 JSON: {url}") from exc

    # ------------------------------------------------------------ 解析

    @staticmethod
    def _build_user_map(payload: Dict[str, Any]) -> Dict[int, str]:
        users = payload.get("users") or []
        mapping: Dict[int, str] = {}
        for user in users:
            user_id = user.get("id")
            name = user.get("username") or user.get("name")
            if user_id is not None and name:
                mapping[int(user_id)] = name
        return mapping

    @staticmethod
    def _author_of(topic: Dict[str, Any], user_map: Dict[int, str]) -> Optional[str]:
        if topic.get("last_poster_username"):
            return topic["last_poster_username"]
        posters = topic.get("posters") or []
        for poster in posters:
            user_id = poster.get("user_id")
            if user_id is not None and int(user_id) in user_map:
                return user_map[int(user_id)]
        return None

    @staticmethod
    def _topic_link(base_url: str, topic: Dict[str, Any]) -> str:
        slug = topic.get("slug") or "topic"
        topic_id = topic.get("id")
        return f"{base_url}/t/{slug}/{topic_id}" if topic_id else f"{base_url}/t/{slug}"

    def _parse_topics(self, payload: Dict[str, Any]) -> List[Post]:
        user_map = self._build_user_map(payload)
        topic_list = payload.get("topic_list") or {}
        topics = topic_list.get("topics") or []

        posts: List[Post] = []
        for topic in topics:
            link = self._topic_link(self.base_url, topic)
            category_id = topic.get("category_id")
            posts.append(
                Post(
                    id=link,
                    title=(topic.get("title") or "无标题").strip(),
                    link=link,
                    author=self._author_of(topic, user_map),
                    category=str(category_id) if category_id is not None else None,
                    summary=(topic.get("excerpt") or "").strip() or None,
                    published_at=_parse_time(
                        topic.get("created_at") or topic.get("bumped_at")
                    ),
                    extra={
                        "topic_id": topic.get("id"),
                        "posts_count": topic.get("posts_count"),
                        "like_count": topic.get("like_count"),
                        "views": topic.get("views"),
                    },
                )
            )
        logger.info("从 Discourse %s 获取到 %d 个主题", self.base_url, len(posts))
        return posts

    # ------------------------------------------------------------ 接口

    def fetch(self) -> List[Post]:
        payload = self._request_json(self.path)
        if not isinstance(payload, dict):
            raise SourceError("Discourse 返回结构异常（期望 JSON 对象）")
        return self._parse_topics(payload)

    def fetch_categories(self) -> List[Dict[str, Any]]:
        """拉取分类列表，供上层写入 categories 表。

        失败只记录日志不抛出，分类只用于消息展示，不应阻断主流程。
        """
        try:
            payload = self._request_json("/categories.json")
        except Exception as exc:  # noqa: BLE001
            logger.warning("拉取 Discourse 分类失败: %s", exc)
            return []

        categories = (payload or {}).get("category_list", {}).get("categories") or []
        return [
            {
                "id": str(c.get("id")),
                "name": c.get("name") or "",
                "slug": c.get("slug") or "",
                "description": (c.get("description") or "")[:500],
            }
            for c in categories
            if c.get("id") is not None
        ]

    def check_cookie(self) -> Tuple[bool, str]:
        """检测 Cookie / 连通性是否可用，返回 (是否可用, 说明)。"""
        try:
            payload = self._request_json(self.path)
        except DiscourseAuthError as exc:
            return False, str(exc)
        except SourceError as exc:
            return False, str(exc)

        topics = ((payload or {}).get("topic_list") or {}).get("topics")
        if topics is None:
            return False, "Discourse 响应里没有 topic_list.topics，接口可能被限制"
        return True, f"Discourse 可用，本次返回 {len(topics)} 个主题"

    def close(self) -> None:
        self.session.close()
