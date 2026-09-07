"""RSS 数据源。

迁移自旧版 ``linuxdo-feishu-bot/app.py`` 的 feedparser 解析逻辑。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, List, Optional

import feedparser

from ..exceptions import SourceError
from ..models import Post
from .base import BaseSource

logger = logging.getLogger(__name__)


def _parse_time(entry: Any) -> Optional[datetime]:
    """从 feed entry 里尽力解析发布时间。"""
    for attr in ("published_parsed", "updated_parsed"):
        value = getattr(entry, attr, None)
        if value:
            try:
                return datetime(*value[:6])
            except (TypeError, ValueError):
                continue
    for attr in ("published", "updated"):
        value = getattr(entry, attr, None)
        if value:
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(
                    tzinfo=None
                )
            except ValueError:
                continue
    return None


def _strip_html(text: str, limit: int = 300) -> str:
    import re

    plain = re.sub(r"<[^>]+>", " ", text or "")
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit]


class RSSSource(BaseSource):
    """解析 RSS / Atom feed。"""

    source_type = "rss"

    def __init__(self, forum_config=None, timeout: int = 30):
        super().__init__(forum_config, timeout)
        self.rss_url = getattr(forum_config, "rss_url", None) if forum_config else None
        if not self.rss_url:
            raise SourceError("RSSSource 缺少 rss_url")

    def fetch(self) -> List[Post]:
        try:
            feed = feedparser.parse(self.rss_url)
        except Exception as exc:
            raise SourceError(f"抓取 RSS 失败: {self.rss_url} - {exc}") from exc

        if getattr(feed, "bozo", 0):
            logger.warning(
                "RSS 解析可能存在问题: %s - %s",
                self.rss_url,
                getattr(feed, "bozo_exception", "unknown"),
            )

        entries = getattr(feed, "entries", []) or []
        logger.info("从 %s 获取到 %d 条条目", self.rss_url, len(entries))

        posts: List[Post] = []
        for entry in entries:
            link = (getattr(entry, "link", "") or "").strip()
            if not link:
                continue
            posts.append(
                Post(
                    id=link,
                    title=(getattr(entry, "title", "") or "无标题").strip(),
                    link=link,
                    author=getattr(entry, "author", None),
                    category=getattr(entry, "category", None),
                    summary=_strip_html(getattr(entry, "summary", "") or ""),
                    published_at=_parse_time(entry),
                )
            )
        return posts
