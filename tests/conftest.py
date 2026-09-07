import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from linuxdo_monitor.config import AppConfig, ForumConfig, NotifierConfig  # noqa: E402
from linuxdo_monitor.database import Database  # noqa: E402
from linuxdo_monitor.models import Post  # noqa: E402


@pytest.fixture
def sample_post() -> Post:
    return Post(
        id="https://linux.do/t/123",
        title="Docker 部署 NAS 求助",
        link="https://linux.do/t/123",
        author="alice",
        category="14",
        summary="这是一个摘要",
        published_at=datetime(2026, 9, 7, 10, 0, 0),
    )


@pytest.fixture
def tmp_db(tmp_path) -> Database:
    return Database(str(tmp_path / "test.db"), backup=False)


@pytest.fixture
def feishu_config() -> NotifierConfig:
    return NotifierConfig(
        notifier_type="feishu",
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        feishu_keywords=["Docker", "NAS"],
        max_retries=1,
        retry_delay=0,
    )


@pytest.fixture
def forum_config(feishu_config) -> ForumConfig:
    return ForumConfig(
        forum_id="linux-do",
        name="Linux.do",
        source_type="rss",
        rss_url="https://linux.do/latest.rss",
        fetch_interval=60,
        notifiers=[feishu_config],
    )


@pytest.fixture
def app_config(forum_config) -> AppConfig:
    return AppConfig(forums=[forum_config], admin_chat_id=999)
