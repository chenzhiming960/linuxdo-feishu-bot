"""配置模型与加载逻辑。

支持两种配置形态：

1. 新版多论坛 / 多渠道::

       {
         "forums": [
           {"forum_id": "linux-do", "name": "Linux.do",
            "notifiers": [
              {"notifier_type": "telegram", "telegram_bot_token": "..."},
              {"notifier_type": "feishu", "feishu_webhook_url": "...",
               "feishu_keywords": ["Docker"]}
            ],
            "source_type": "rss", "rss_url": "...", "fetch_interval": 60}
         ]
       }

2. 旧版 ``linuxdo-feishu-bot`` 扁平配置（自动转换）::

       {"feishu_webhook_url": "...", "rss_url": "...", "poll_interval": 30,
        "keyword_monitor": {"enabled": true, "keywords": ["a", "b"]}}

3. 旧版单论坛 Telegram 配置（``bot_token`` 自动转成 telegram notifier）。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .exceptions import ConfigError

CHANNEL_TELEGRAM = "telegram"
CHANNEL_FEISHU = "feishu"

SOURCE_RSS = "rss"
SOURCE_DISCOURSE = "discourse"

DEFAULT_CONFIG_PATHS = (
    os.getenv("CONFIG_PATH", ""),
    "config/config.json",
    "/config/config.json",
    "config.json",
)


class NotifierConfig(BaseModel):
    """单个通知渠道的配置。"""

    notifier_type: str = Field(description="telegram | feishu")
    enabled: bool = True
    channel: Optional[str] = Field(
        default=None, description="数据库里的渠道标识，默认等于 notifier_type"
    )

    # Telegram
    telegram_bot_token: Optional[str] = None

    # 飞书
    feishu_webhook_url: Optional[str] = None
    feishu_mode: str = Field(default="broadcast", description="broadcast | personal")
    feishu_keywords: List[str] = Field(default_factory=list)
    feishu_push_all: bool = Field(
        default=False,
        description="广播模式下忽略关键词，推送全部新帖（旧版 keyword_monitor.enabled=false 的语义）",
    )
    feishu_secret: Optional[str] = None

    # 通用
    max_retries: int = 3
    retry_delay: float = 2.0
    concurrency: int = 5
    timeout: int = 15

    @field_validator("notifier_type")
    @classmethod
    def _normalize_type(cls, v: str) -> str:
        value = (v or "").strip().lower()
        if value not in (CHANNEL_TELEGRAM, CHANNEL_FEISHU):
            raise ValueError(f"不支持的通知渠道: {v}（可选 telegram / feishu）")
        return value

    @field_validator("feishu_mode")
    @classmethod
    def _normalize_mode(cls, v: str) -> str:
        value = (v or "broadcast").strip().lower()
        if value not in ("broadcast", "personal"):
            raise ValueError(f"不支持的飞书模式: {v}（可选 broadcast / personal）")
        return value

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "NotifierConfig":
        if self.notifier_type == CHANNEL_TELEGRAM and not self.telegram_bot_token:
            raise ValueError("telegram 渠道缺少 telegram_bot_token")
        if self.notifier_type == CHANNEL_FEISHU and not self.feishu_webhook_url:
            raise ValueError("feishu 渠道缺少 feishu_webhook_url")
        return self

    @property
    def channel_type(self) -> str:
        return self.channel or self.notifier_type


class ForumConfig(BaseModel):
    """单个论坛的配置。"""

    forum_id: str
    name: str = ""
    enabled: bool = True

    source_type: str = SOURCE_RSS
    rss_url: Optional[str] = None

    # --- Discourse 数据源 ---
    discourse_url: Optional[str] = None
    discourse_cookie: Optional[str] = None
    discourse_path: str = Field(default="/latest.json", description="主题列表接口路径")
    flaresolverr_url: Optional[str] = Field(
        default=None, description="被 Cloudflare 拦截时使用的 FlareSolverr 服务地址"
    )
    fallback_to_rss: bool = Field(
        default=True, description="Discourse 不可用时自动降级到 RSS"
    )
    cookie_check_interval: int = Field(
        default=3600, ge=60, description="Cookie 健康检测间隔（秒）"
    )

    fetch_interval: int = Field(default=60, ge=1)
    skip_first_run: bool = Field(
        default=True, description="首次轮询只落库不推送，避免灌屏历史帖子"
    )

    notifiers: List[NotifierConfig] = Field(default_factory=list)

    # --- 旧版兼容字段 ---
    bot_token: Optional[str] = None
    keyword_monitor: Optional[Dict[str, Any]] = None

    @field_validator("source_type")
    @classmethod
    def _normalize_source(cls, v: str) -> str:
        value = (v or SOURCE_RSS).strip().lower()
        if value not in (SOURCE_RSS, SOURCE_DISCOURSE):
            raise ValueError(f"不支持的数据源: {v}（可选 rss / discourse）")
        return value

    @model_validator(mode="after")
    def _apply_backward_compat(self) -> "ForumConfig":
        if not self.name:
            self.name = self.forum_id
        # 旧版 bot_token 自动转成 telegram 渠道
        if self.bot_token and not any(
            n.notifier_type == CHANNEL_TELEGRAM for n in self.notifiers
        ):
            self.notifiers.append(
                NotifierConfig(
                    notifier_type=CHANNEL_TELEGRAM, telegram_bot_token=self.bot_token
                )
            )
        if self.source_type == SOURCE_RSS and not self.rss_url:
            raise ValueError(f"论坛 {self.forum_id}: source_type=rss 但缺少 rss_url")
        if self.source_type == SOURCE_DISCOURSE and not self.discourse_url:
            raise ValueError(
                f"论坛 {self.forum_id}: source_type=discourse 但缺少 discourse_url"
            )
        if self.source_type == SOURCE_DISCOURSE and self.fallback_to_rss and not self.rss_url:
            raise ValueError(
                f"论坛 {self.forum_id}: 开启了 fallback_to_rss 但缺少 rss_url"
            )
        return self

    def get_notifiers(self, channel: Optional[str] = None) -> List[NotifierConfig]:
        if channel is None:
            return [n for n in self.notifiers if n.enabled]
        return [n for n in self.notifiers if n.enabled and n.channel_type == channel]


class AppConfig(BaseModel):
    """全局配置。"""

    forums: List[ForumConfig] = Field(default_factory=list)

    admin_chat_id: Optional[int] = None
    web_password: Optional[str] = None
    sql_admin_password: Optional[str] = None
    web_port: int = 8080

    # 日志与数据库清理（沿用旧版语义）
    log_cleanup_interval_seconds: int = 3600
    log_retention_hours: int = 4
    db_cleanup_interval_seconds: int = 43200
    db_retention_hours: int = 24

    database_path: str = Field(default_factory=lambda: os.getenv("DB_PATH", "data/monitor.db"))
    log_dir: str = Field(default_factory=lambda: os.getenv("LOG_DIR", "logs"))

    @model_validator(mode="after")
    def _validate_unique_forum_ids(self) -> "AppConfig":
        seen = set()
        for forum in self.forums:
            if forum.forum_id in seen:
                raise ValueError(f"重复的 forum_id: {forum.forum_id}")
            seen.add(forum.forum_id)
        return self

    def get_forum(self, forum_id: str) -> Optional[ForumConfig]:
        for forum in self.forums:
            if forum.forum_id == forum_id:
                return forum
        return None


def _convert_legacy(raw: Dict[str, Any]) -> Dict[str, Any]:
    """把旧版扁平配置转换成多论坛结构。"""
    if "forums" in raw:
        return raw

    feishu_webhook = (raw.get("feishu_webhook_url") or "").strip()
    rss_url = (raw.get("rss_url") or "").strip()
    if not feishu_webhook and not raw.get("bot_token"):
        return raw

    keyword_cfg = raw.get("keyword_monitor") or {}
    keywords = list(keyword_cfg.get("keywords") or [])
    # 旧版 keyword_monitor.enabled=false 表示「不过滤，推送全部」
    push_all = not bool(keyword_cfg.get("enabled", True))

    notifiers: List[Dict[str, Any]] = []
    if feishu_webhook:
        notifiers.append(
            {
                "notifier_type": CHANNEL_FEISHU,
                "feishu_webhook_url": feishu_webhook,
                "feishu_keywords": keywords,
                "feishu_push_all": push_all,
            }
        )
    if raw.get("bot_token"):
        notifiers.append(
            {"notifier_type": CHANNEL_TELEGRAM, "telegram_bot_token": raw["bot_token"]}
        )

    forum: Dict[str, Any] = {
        "forum_id": "linux-do",
        "name": "Linux.do",
        "source_type": SOURCE_RSS,
        "rss_url": rss_url or "https://linux.do/latest.rss",
        "fetch_interval": int(raw.get("poll_interval") or 60),
        "notifiers": notifiers,
        "keyword_monitor": keyword_cfg or None,
    }

    converted = dict(raw)
    converted["forums"] = [forum]
    return converted


def load_config(path: Optional[str] = None) -> AppConfig:
    """从 JSON 文件加载配置，自动兼容旧版格式。"""
    candidates = [path] if path else [p for p in DEFAULT_CONFIG_PATHS if p]
    if not candidates:
        raise ConfigError("未指定配置文件路径")

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"配置文件 {candidate} 不是合法 JSON: {exc}") from exc
            try:
                return AppConfig.model_validate(_convert_legacy(raw))
            except Exception as exc:  # pydantic ValidationError
                raise ConfigError(f"配置文件 {candidate} 校验失败: {exc}") from exc

    raise ConfigError(f"配置文件不存在: {candidates[0]}")


def save_config(config: AppConfig, path: str) -> None:
    """把配置写回 JSON 文件。"""
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    payload = config.model_dump(mode="json", exclude_none=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
