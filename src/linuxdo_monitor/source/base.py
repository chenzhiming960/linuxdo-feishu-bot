"""数据源抽象层。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..models import Post


class BaseSource(ABC):
    """论坛数据源接口。

    实现类负责把外部数据（RSS / Discourse API）转换成统一的 :class:`Post`。
    """

    #: 子类声明支持的数据源类型，用于工厂注册
    source_type: str = ""

    def __init__(self, forum_config=None, timeout: int = 30):
        self.forum_config = forum_config
        self.timeout = timeout

    @abstractmethod
    def fetch(self) -> List[Post]:
        """拉取最新帖子。失败时抛出 :class:`SourceError`。"""

    def close(self) -> None:
        """释放资源，默认无操作。"""
