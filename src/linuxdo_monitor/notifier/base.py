"""通知渠道抽象层。

所有通知渠道（Telegram / 飞书）实现 :class:`BaseNotifier`，
:class:`~linuxdo_monitor.app.Application` 只依赖这个接口，从而支持多渠道并行推送。

与 PRD 的差异：PRD 里的 ``send_notification(user_id, title, link, keyword)``
改为接收统一的 :class:`~linuxdo_monitor.models.Post` 对象，避免参数列表随字段增加而膨胀
（Post 已包含 title / link / author / category 等）。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Optional, Tuple, TypeVar

from ..config import NotifierConfig
from ..exceptions import NotifierBlockedError, NotifierError
from ..models import Post

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseNotifier(ABC):
    """通知渠道基类。"""

    #: 渠道标识，与数据库里的 channel 列一致
    channel_type: str = ""

    def __init__(self, config: NotifierConfig, forum_config=None):
        self.config = config
        self.forum_config = forum_config
        self.max_retries = max(1, int(getattr(config, "max_retries", 3) or 1))
        self.retry_delay = float(getattr(config, "retry_delay", 2.0) or 0)
        self.timeout = int(getattr(config, "timeout", 15) or 15)
        self.concurrency = max(1, int(getattr(config, "concurrency", 5) or 1))
        self.last_error: Optional[str] = None
        self.sent_count = 0
        self.failed_count = 0

    # ------------------------------------------------------------ 抽象接口

    @abstractmethod
    async def send_notification(
        self,
        user_id: str,
        post: Post,
        keyword: Optional[str] = None,
        category_name: Optional[str] = None,
    ) -> bool:
        """向指定用户发送「关键词命中」通知。"""

    @abstractmethod
    async def send_notification_all(
        self,
        user_id: str,
        post: Post,
        category_name: Optional[str] = None,
    ) -> bool:
        """向指定用户发送「订阅全部新帖」通知。"""

    @abstractmethod
    async def send_text(self, user_id: str, text: str) -> bool:
        """发送纯文本消息（用于命令回复、管理员告警）。"""

    async def broadcast(
        self,
        post: Post,
        keyword: Optional[str] = None,
        category_name: Optional[str] = None,
    ) -> bool:
        """广播模式：不指定接收者，直接投到群/频道。

        默认退化为向配置里的默认接收者发送；飞书等无用户概念的渠道会重写此方法。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 不支持广播模式，请通过订阅方式推送"
        )

    async def health_check(self) -> Tuple[bool, str]:
        """渠道连通性自检，返回 (是否健康, 说明)。"""
        return True, f"{self.channel_type} 未实现自检"

    # ------------------------------------------------------------ 公共能力

    def get_channel_type(self) -> str:
        return self.config.channel or self.channel_type

    def supports_broadcast(self) -> bool:
        return type(self).broadcast is not BaseNotifier.broadcast

    def get_broadcast_keywords(self) -> Tuple[List[str], bool]:
        """广播渠道的 (关键词列表, 是否忽略关键词推送全部)。

        非广播渠道返回空列表。
        """
        return [], False

    async def _with_retry(
        self,
        operation: Callable[[], Awaitable[T]],
        description: str = "",
    ) -> T:
        """带指数退避的重试包装。封禁类错误不重试，直接上抛。"""
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await operation()
            except NotifierBlockedError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 渠道异常统一收敛
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                delay = self.retry_delay * attempt
                logger.warning(
                    "%s 第 %d/%d 次失败，%.1fs 后重试: %s",
                    description or self.channel_type,
                    attempt,
                    self.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        self.last_error = str(last_exc)
        self.failed_count += 1
        raise NotifierError(
            f"{description or self.channel_type} 重试 {self.max_retries} 次后仍失败: {last_exc}"
        ) from last_exc

    def _record_success(self) -> None:
        self.sent_count += 1
        self.last_error = None

    async def close(self) -> None:
        """释放渠道资源，默认无操作。"""

    def stats(self) -> dict:
        return {
            "channel": self.get_channel_type(),
            "sent": self.sent_count,
            "failed": self.failed_count,
            "last_error": self.last_error,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} channel={self.get_channel_type()}>"
