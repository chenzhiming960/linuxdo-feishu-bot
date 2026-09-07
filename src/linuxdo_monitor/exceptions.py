"""自定义异常。"""


class MonitorError(Exception):
    """所有本项目异常的基类。"""


class ConfigError(MonitorError):
    """配置缺失或非法。"""


class SourceError(MonitorError):
    """数据源拉取或解析失败。"""


class DiscourseAuthError(SourceError):
    """Discourse 数据源不可用：Cookie 失效、未登录或被 Cloudflare 拦截。

    上层捕获后应降级到备用数据源（通常是 RSS），而不是继续重试。
    """


class NotifierError(MonitorError):
    """通知渠道发送失败。"""


class NotifierBlockedError(NotifierError):
    """接收方已封禁/拉黑机器人（Telegram Forbidden 等）。

    上层捕获后应把该用户标记为 blocked，而不是继续重试。
    """

    def __init__(self, user_id: str, message: str = ""):
        super().__init__(message or f"user {user_id} blocked the bot")
        self.user_id = user_id


class MigrationError(MonitorError):
    """数据库迁移失败。"""
