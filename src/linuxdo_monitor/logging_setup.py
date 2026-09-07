"""日志初始化与轮转清理。

沿用旧版 ``linuxdo-feishu-bot/app.py`` 的行为：按小时切分日志文件、
定期清理过期日志。
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Optional

LOGGER_NAME = "linuxdo_monitor"

_current_log_file: Optional[str] = None
_current_file_handler: Optional[logging.FileHandler] = None


def _hourly_log_file(log_dir: str) -> str:
    filename = datetime.now().strftime("app-%Y%m%d-%H.log")
    return os.path.join(log_dir, filename)


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> logging.Logger:
    """初始化 logger 并切换到当前小时的日志文件。"""
    global _current_log_file, _current_file_handler

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    os.makedirs(log_dir, exist_ok=True)
    target = _hourly_log_file(log_dir)

    if _current_log_file == target and _current_file_handler is not None:
        return logger

    if _current_file_handler is not None:
        logger.removeHandler(_current_file_handler)
        _current_file_handler.close()
        _current_file_handler = None

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    ):
        stream = logging.StreamHandler()
        stream.setLevel(level)
        stream.setFormatter(formatter)
        logger.addHandler(stream)

    file_handler = logging.FileHandler(target, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _current_file_handler = file_handler
    _current_log_file = target
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def cleanup_old_logs(log_dir: str, retention_hours: int = 4) -> int:
    """删除超过保留时长的 app-*.log，返回删除数量。"""
    if retention_hours < 0 or not os.path.isdir(log_dir):
        return 0

    now_ts = time.time()
    expire_seconds = retention_hours * 3600
    removed = 0

    for filename in os.listdir(log_dir):
        if not (filename.startswith("app-") and filename.endswith(".log")):
            continue
        path = os.path.join(log_dir, filename)
        if not os.path.isfile(path):
            continue
        if _current_log_file and os.path.abspath(path) == os.path.abspath(_current_log_file):
            continue
        try:
            if now_ts - os.path.getmtime(path) > expire_seconds:
                os.remove(path)
                removed += 1
        except OSError as exc:
            get_logger("logging_setup").warning("删除日志文件失败: %s - %s", path, exc)
    return removed
