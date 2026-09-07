"""飞书自定义机器人（Webhook）通知渠道。

采用 PRD 选定的**广播模式**：一个 Webhook 对应一个群，关键词命中即投递到该群，
不做个人定向。global 关键词来自 ``NotifierConfig.feishu_keywords``。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional, Tuple

import requests

from ..config import CHANNEL_FEISHU, NotifierConfig
from ..exceptions import NotifierError
from ..models import Post
from .base import BaseNotifier

logger = logging.getLogger(__name__)

FEISHU_OK_CODE = 0


class FeishuNotifier(BaseNotifier):
    """通过飞书群机器人 Webhook 推送交互式卡片。"""

    channel_type = CHANNEL_FEISHU

    def __init__(self, config: NotifierConfig, forum_config=None):
        super().__init__(config, forum_config)
        if not config.feishu_webhook_url:
            raise NotifierError("FeishuNotifier 缺少 feishu_webhook_url")
        self.webhook_url = config.feishu_webhook_url
        self.mode = (config.feishu_mode or "broadcast").lower()
        self.secret = config.feishu_secret
        self.keywords = list(config.feishu_keywords or [])
        self.push_all = bool(getattr(config, "feishu_push_all", False))

    # ------------------------------------------------------------ 目标地址

    def _target_url(self, user_id: Optional[str] = None) -> str:
        """broadcast 用配置里的固定 webhook；personal 把 user_id 当作 webhook。"""
        if self.mode == "personal":
            if not user_id or not user_id.startswith("http"):
                raise NotifierError(
                    f"飞书 personal 模式下 user_id 必须是 webhook URL，收到: {user_id!r}"
                )
            return user_id
        return self.webhook_url

    def _signed_payload(self, payload: dict) -> dict:
        """配置了加签密钥时补上 timestamp / sign。"""
        if not self.secret:
            return payload
        timestamp = str(int(time.time()))
        digest = hmac.new(
            f"{timestamp}\n{self.secret}".encode("utf-8"),
            b"",
            digestmod=hashlib.sha256,
        ).digest()
        payload = dict(payload)
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
        return payload

    # ------------------------------------------------------------ 消息构造

    @staticmethod
    def _escape(text: str) -> str:
        return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def build_card(
        self,
        post: Post,
        keyword: Optional[str] = None,
        category_name: Optional[str] = None,
        color: str = "blue",
    ) -> dict:
        """构造飞书交互式卡片消息体。"""
        title = self._escape(post.display_title)
        forum_name = getattr(self.forum_config, "name", "") or "论坛"

        elements: list = []
        if keyword:
            elements.append(
                {
                    "tag": "div",
                    "text": {"content": f"**📌 匹配关键词**：{self._escape(keyword)}", "tag": "lark_md"},
                }
            )
        elements.append(
            {"tag": "div", "text": {"content": f"**📝 标题**\n{title}", "tag": "lark_md"}}
        )

        meta_fields = []
        if post.author:
            meta_fields.append({"is_short": True, "text": {"content": f"**👤 作者**\n{self._escape(post.author)}", "tag": "lark_md"}})
        if category_name or post.category:
            meta_fields.append({"is_short": True, "text": {"content": f"**🗂 分类**\n{self._escape(category_name or post.category or '')}", "tag": "lark_md"}})
        if meta_fields:
            if len(meta_fields) == 1:
                elements.append({"tag": "div", "text": meta_fields[0]["text"]})
            else:
                elements.append({"tag": "div", "fields": meta_fields})

        if post.summary:
            summary = self._escape(post.summary[:200])
            elements.append({"tag": "div", "text": {"content": f"**📄 摘要**\n{summary}", "tag": "lark_md"}})

        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"content": "🔗 点击查看原帖", "tag": "plain_text"},
                        "url": post.link,
                        "type": "primary",
                    }
                ],
            }
        )
        elements.append(
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": f"来自 {forum_name} · {time.strftime('%Y-%m-%d %H:%M:%S')}"}
                ],
            }
        )

        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"content": f"🔔 {forum_name} 新帖提醒", "tag": "plain_text"},
                    "template": color,
                },
                "elements": elements,
            },
        }

    def build_text(self, content: str) -> dict:
        """纯文本消息，用于卡片发送失败时降级或告警。"""
        return {"msg_type": "text", "content": {"text": content}}

    # ------------------------------------------------------------ 发送

    def _post(self, payload: dict, url: str) -> dict:
        """同步发送，放到线程里执行。"""
        body = self._signed_payload(payload)
        resp = requests.post(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        code = data.get("code", data.get("StatusCode"))
        if code not in (None, FEISHU_OK_CODE):
            raise NotifierError(f"飞书返回错误 code={code}: {data.get('msg') or data.get('StatusMessage')}")
        return data

    async def _send_payload(self, payload: dict, url: str, description: str) -> bool:
        def _call() -> dict:
            return self._post(payload, url)

        try:
            await self._with_retry(
                lambda: asyncio.to_thread(_call), description or "飞书推送"
            )
        except NotifierError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise NotifierError(f"飞书推送失败: {exc}") from exc
        self._record_success()
        return True

    async def broadcast(
        self,
        post: Post,
        keyword: Optional[str] = None,
        category_name: Optional[str] = None,
    ) -> bool:
        """向群 webhook 推送卡片；卡片失败则降级为文本。"""
        url = self._target_url()
        try:
            return await self._send_payload(
                self.build_card(post, keyword, category_name), url, "飞书卡片推送"
            )
        except NotifierError as exc:
            logger.warning("飞书卡片推送失败，降级为文本: %s", exc)
            fallback = (
                f"【{getattr(self.forum_config, 'name', '论坛')} 新帖】\n"
                f"标题：{post.display_title}\n"
                f"链接：{post.link}"
                + (f"\n关键词：{keyword}" if keyword else "")
            )
            return await self._send_payload(
                self.build_text(fallback), url, "飞书文本推送"
            )

    async def send_notification(
        self,
        user_id: str,
        post: Post,
        keyword: Optional[str] = None,
        category_name: Optional[str] = None,
    ) -> bool:
        return await self.broadcast(post, keyword, category_name)

    async def send_notification_all(
        self,
        user_id: str,
        post: Post,
        category_name: Optional[str] = None,
    ) -> bool:
        return await self.broadcast(post, None, category_name)

    async def send_text(self, user_id: str, text: str) -> bool:
        return await self._send_payload(
            self.build_text(text), self._target_url(user_id), "飞书文本消息"
        )

    def get_broadcast_keywords(self) -> Tuple[List[str], bool]:
        return self.keywords, self.push_all

    async def health_check(self) -> Tuple[bool, str]:
        """发送一条测试文本验证 Webhook 可用性。"""
        try:
            await self.send_text(None, "✅ 飞书机器人连通性测试")
            return True, "飞书 Webhook 可用"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
