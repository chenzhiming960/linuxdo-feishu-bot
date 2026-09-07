"""LinuxDo 多渠道监控与通知系统。

分层结构::

    source/     数据源抽象 (RSS / Discourse)
    notifier/   通知渠道抽象 (Telegram / 飞书)
    matcher     关键词与正则匹配
    database    持久化与去重
    app         应用编排
    web         Flask 管理界面
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
