"""数据源工厂。"""

from __future__ import annotations

from typing import Dict, Type

from ..config import SOURCE_DISCOURSE, SOURCE_RSS, ForumConfig
from ..exceptions import ConfigError
from .base import BaseSource
from .discourse import DiscourseSource
from .fallback import FallbackSource
from .rss import RSSSource

_REGISTRY: Dict[str, Type[BaseSource]] = {
    RSSSource.source_type: RSSSource,
    DiscourseSource.source_type: DiscourseSource,
}


def register_source(source_cls: Type[BaseSource]) -> None:
    _REGISTRY[source_cls.source_type] = source_cls


def create_source(forum_config: ForumConfig) -> BaseSource:
    """按 ``source_type`` 创建数据源实例。

    ``source_type=discourse`` 且开启 ``fallback_to_rss`` 时，
    返回包了 RSS 备源的 :class:`FallbackSource`。
    """
    source_type = (forum_config.source_type or SOURCE_RSS).lower()
    cls = _REGISTRY.get(source_type)
    if cls is None:
        raise ConfigError(
            f"论坛 {forum_config.forum_id}: 未注册的数据源类型 {source_type}"
            f"（已注册: {', '.join(sorted(_REGISTRY))}）"
        )

    source = cls(forum_config)

    if (
        source_type == SOURCE_DISCOURSE
        and forum_config.fallback_to_rss
        and forum_config.rss_url
    ):
        from ..exceptions import DiscourseAuthError

        return FallbackSource(
            source,
            RSSSource(forum_config),
            degrade_on=(DiscourseAuthError,),
        )
    return source


__all__ = [
    "BaseSource",
    "DiscourseSource",
    "FallbackSource",
    "RSSSource",
    "create_source",
    "register_source",
]
