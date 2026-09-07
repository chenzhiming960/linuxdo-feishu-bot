"""通知渠道工厂。"""

from __future__ import annotations

from typing import Dict, Type

from ..config import CHANNEL_FEISHU, CHANNEL_TELEGRAM, ForumConfig, NotifierConfig
from ..exceptions import ConfigError
from .base import BaseNotifier
from .feishu import FeishuNotifier
from .telegram import TelegramNotifier

_REGISTRY: Dict[str, Type[BaseNotifier]] = {
    CHANNEL_TELEGRAM: TelegramNotifier,
    CHANNEL_FEISHU: FeishuNotifier,
}


def register_notifier(notifier_cls: Type[BaseNotifier]) -> None:
    _REGISTRY[notifier_cls.channel_type] = notifier_cls


def create_notifier(
    notifier_config: NotifierConfig, forum_config: ForumConfig | None = None
) -> BaseNotifier:
    """按 ``notifier_type`` 创建通知渠道实例。"""
    cls = _REGISTRY.get(notifier_config.notifier_type)
    if cls is None:
        raise ConfigError(
            f"未注册的通知渠道: {notifier_config.notifier_type}"
            f"（已注册: {', '.join(sorted(_REGISTRY))}）"
        )
    return cls(notifier_config, forum_config)


def create_notifiers(forum_config: ForumConfig) -> list[BaseNotifier]:
    """为一个论坛创建全部已启用的通知渠道。"""
    notifiers = []
    for cfg in forum_config.notifiers:
        if not cfg.enabled:
            continue
        try:
            notifiers.append(create_notifier(cfg, forum_config))
        except Exception as exc:  # noqa: BLE001 - 单个渠道失败不应拖垮整个论坛
            import logging

            logging.getLogger(__name__).error(
                "论坛 %s 的 %s 渠道初始化失败: %s", forum_config.forum_id, cfg.notifier_type, exc
            )
    return notifiers


__all__ = [
    "BaseNotifier",
    "FeishuNotifier",
    "TelegramNotifier",
    "create_notifier",
    "create_notifiers",
    "register_notifier",
]
