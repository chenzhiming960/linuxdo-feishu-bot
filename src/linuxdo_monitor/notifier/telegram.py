"""Telegram Bot 通知渠道。

特性（对齐旧版 Telegram 监控的行为）：
* HTML 富文本 + 链接按钮
* 网络超时 / 429 限流自动重试（``_with_retry`` + ``RetryAfter`` 等待）
* ``Forbidden`` 视为用户封禁 -> 抛 :class:`NotifierBlockedError`，由上层标记 blocked
"""

from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Optional, Tuple

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.request import HTTPXRequest

from ..config import CHANNEL_TELEGRAM, NotifierConfig
from ..exceptions import NotifierBlockedError, NotifierError
from ..models import Post
from .base import BaseNotifier

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """通过 Telegram Bot API 向指定 chat 推送消息。"""

    channel_type = CHANNEL_TELEGRAM

    def __init__(self, config: NotifierConfig, forum_config=None):
        super().__init__(config, forum_config)
        if not config.telegram_bot_token:
            raise NotifierError("TelegramNotifier 缺少 telegram_bot_token")
        request = HTTPXRequest(
            connection_pool_size=self.concurrency,
            read_timeout=self.timeout,
            write_timeout=self.timeout,
            connect_timeout=self.timeout,
        )
        self.bot = Bot(token=config.telegram_bot_token, request=request)

    # ------------------------------------------------------------ 消息构造

    def _build_message(
        self,
        post: Post,
        keyword: Optional[str] = None,
        category_name: Optional[str] = None,
    ) -> str:
        forum_name = escape(getattr(self.forum_config, "name", "") or "论坛")
        lines = [f"🔔 <b>{forum_name} 新帖提醒</b>", ""]
        if keyword:
            lines.append(f"📌 匹配关键词：<code>{escape(keyword)}</code>")
        lines.append(f"📝 标题：{escape(post.display_title)}")
        if category_name or post.category:
            lines.append(f"🗂 分类：{escape(category_name or post.category or '')}")
        if post.author:
            lines.append(f"👤 作者：{escape(post.author)}")
        lines.append("")
        lines.append(f'<a href="{escape(post.link)}">🔗 点击查看原帖</a>')
        return "\n".join(lines)

    @staticmethod
    def _reply_markup(post: Post):
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(text="🔗 查看原帖", url=post.link)]]
        )

    # ------------------------------------------------------------ 发送

    async def _send(self, user_id: str, text: str, post: Optional[Post] = None) -> bool:
        markup = self._reply_markup(post) if post else None

        async def _call():
            try:
                await self.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=markup,
                )
            except Forbidden as exc:
                raise NotifierBlockedError(user_id, str(exc)) from exc
            except RetryAfter as exc:
                wait = float(getattr(exc, "retry_after", 1) or 1)
                logger.warning("Telegram 限流，等待 %.1fs", wait)
                await asyncio.sleep(wait)
                raise
            except TelegramError as exc:
                raise NotifierError(str(exc)) from exc

        try:
            await self._with_retry(_call, f"Telegram 推送 -> {user_id}")
        except NotifierBlockedError:
            raise
        except NotifierError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise NotifierError(f"Telegram 推送失败: {exc}") from exc
        self._record_success()
        return True

    async def send_notification(
        self,
        user_id: str,
        post: Post,
        keyword: Optional[str] = None,
        category_name: Optional[str] = None,
    ) -> bool:
        return await self._send(
            user_id, self._build_message(post, keyword, category_name), post
        )

    async def send_notification_all(
        self,
        user_id: str,
        post: Post,
        category_name: Optional[str] = None,
    ) -> bool:
        return await self._send(
            user_id, self._build_message(post, None, category_name), post
        )

    async def send_text(self, user_id: str, text: str) -> bool:
        return await self._send(user_id, text)

    async def health_check(self) -> Tuple[bool, str]:
        try:
            me = await self.bot.get_me()
            return True, f"Bot @{me.username} 在线"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    async def close(self) -> None:
        try:
            await self.bot.shutdown()
        except Exception:  # pragma: no cover - 关闭失败无需处理
            pass
