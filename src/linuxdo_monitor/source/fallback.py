"""主备数据源组合。

Discourse 依赖 Cookie，失效是常态；失效时自动降级到 RSS，
恢复后自动切回，无需重启。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from ..exceptions import SourceError
from ..models import Post
from .base import BaseSource

logger = logging.getLogger(__name__)

#: 只有这些异常会触发降级；网络抖动等普通错误应留给下次重试
DEGRADE_ON = ()


class FallbackSource(BaseSource):
    """先尝试 primary，不可用时使用 secondary。"""

    source_type = "fallback"

    def __init__(
        self,
        primary: BaseSource,
        secondary: BaseSource,
        degrade_on=(),
        on_degrade=None,
        on_restore=None,
    ):
        super().__init__(primary.forum_config, getattr(primary, "timeout", 30))
        self.primary = primary
        self.secondary = secondary
        self.degrade_on = tuple(degrade_on) or DEGRADE_ON
        self.on_degrade = on_degrade
        self.on_restore = on_restore
        self.degraded = False
        self.last_error: Optional[str] = None

    def _mark_degraded(self, exc: BaseException) -> None:
        self.last_error = str(exc)
        if not self.degraded:
            self.degraded = True
            logger.warning("主数据源不可用，已降级到备用源: %s", exc)
            if self.on_degrade:
                self.on_degrade(exc)
        else:
            logger.warning("主数据源仍不可用，继续使用备用源: %s", exc)

    def _mark_restored(self) -> None:
        if self.degraded:
            self.degraded = False
            self.last_error = None
            logger.info("主数据源已恢复，切回 %s", type(self.primary).__name__)
            if self.on_restore:
                self.on_restore()

    def fetch(self) -> List[Post]:
        try:
            posts = self.primary.fetch()
        except self.degrade_on as exc:
            self._mark_degraded(exc)
        else:
            self._mark_restored()
            return posts

        try:
            return self.secondary.fetch()
        except SourceError as exc:
            raise SourceError(f"主备数据源均失败: {exc}") from exc

    def close(self) -> None:
        self.primary.close()
        self.secondary.close()
