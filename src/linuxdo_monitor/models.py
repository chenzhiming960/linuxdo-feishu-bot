"""跨层共用的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Post:
    """一个论坛帖子。

    ``id`` 是数据源内的唯一标识（RSS 用 link，Discourse 也规范化为帖子链接），
    用于跨渠道去重——主备数据源切换时去重表仍然连续。
    """

    id: str
    title: str
    link: str
    author: Optional[str] = None
    category: Optional[str] = None
    summary: Optional[str] = None
    published_at: Optional[datetime] = None
    extra: dict = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        return self.title.strip() or "无标题"
