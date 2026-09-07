import json

import pytest
from pydantic import ValidationError

from linuxdo_monitor.config import (
    CHANNEL_FEISHU,
    CHANNEL_TELEGRAM,
    AppConfig,
    ForumConfig,
    NotifierConfig,
    load_config,
    save_config,
)


def test_notifier_requires_type_specific_field():
    with pytest.raises(ValidationError):
        NotifierConfig(notifier_type="telegram")
    with pytest.raises(ValidationError):
        NotifierConfig(notifier_type="feishu")


def test_notifier_unknown_type():
    with pytest.raises(ValidationError):
        NotifierConfig(notifier_type="slack")


def test_bot_token_becomes_telegram_notifier():
    forum = ForumConfig(forum_id="f", name="F", rss_url="https://x/rss", bot_token="1:A")
    assert [n.notifier_type for n in forum.notifiers] == [CHANNEL_TELEGRAM]
    assert forum.notifiers[0].telegram_bot_token == "1:A"


def test_rss_source_requires_url():
    with pytest.raises(ValidationError):
        ForumConfig(forum_id="f")


def test_duplicate_forum_ids_rejected():
    with pytest.raises(ValidationError):
        AppConfig(
            forums=[
                ForumConfig(forum_id="a", rss_url="https://x"),
                ForumConfig(forum_id="a", rss_url="https://y"),
            ]
        )


def test_legacy_flat_config_conversion(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "feishu_webhook_url": "https://open.feishu.cn/hook/x",
                "rss_url": "https://linux.do/latest.rss",
                "poll_interval": 30,
                "keyword_monitor": {"enabled": True, "keywords": ["Docker", "NAS"]},
            },
            ensure_ascii=False,
        )
    )
    config = load_config(str(path))
    assert len(config.forums) == 1
    forum = config.forums[0]
    assert forum.forum_id == "linux-do"
    assert forum.fetch_interval == 30
    notifier = forum.notifiers[0]
    assert notifier.notifier_type == CHANNEL_FEISHU
    assert notifier.feishu_keywords == ["Docker", "NAS"]
    assert notifier.feishu_push_all is False


def test_legacy_disabled_keyword_monitor_means_push_all(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "feishu_webhook_url": "https://open.feishu.cn/hook/x",
                "rss_url": "https://linux.do/latest.rss",
                "keyword_monitor": {"enabled": False, "keywords": []},
            },
            ensure_ascii=False,
        )
    )
    config = load_config(str(path))
    assert config.forums[0].notifiers[0].feishu_push_all is True


def test_load_config_missing_file():
    from linuxdo_monitor.exceptions import ConfigError

    with pytest.raises(ConfigError):
        load_config("/definitely/not/exist.json")


def test_load_config_invalid_json(tmp_path):
    from linuxdo_monitor.exceptions import ConfigError

    path = tmp_path / "bad.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError):
        load_config(str(path))


def test_save_and_reload_roundtrip(tmp_path, app_config):
    path = tmp_path / "config.json"
    save_config(app_config, str(path))
    reloaded = load_config(str(path))
    assert reloaded.forums[0].forum_id == "linux-do"
    assert reloaded.forums[0].notifiers[0].notifier_type == CHANNEL_FEISHU


def test_get_notifiers_filters_by_channel(forum_config, feishu_config):
    telegram = NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A", enabled=False)
    forum_config.notifiers.append(telegram)
    assert len(forum_config.get_notifiers()) == 1
    assert forum_config.get_notifiers(CHANNEL_TELEGRAM) == []
