import json

import pytest
from click.testing import CliRunner

from linuxdo_monitor.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_help(runner):
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "run", "test-notifier", "migrate", "stats"):
        assert command in result.output


def test_init_creates_config(runner, tmp_path):
    target = str(tmp_path / "config.json")
    result = runner.invoke(
        cli,
        ["init", "--config", target],
        input="\n"  # RSS 默认
        "\n"  # 间隔默认
        "y\n"  # 配置飞书
        "https://open.feishu.cn/hook/x\n"
        "Docker,NAS\n"
        "n\n",  # 不配 Telegram
    )
    assert result.exit_code == 0, result.output
    data = json.loads(open(target, encoding="utf-8").read())
    forum = data["forums"][0]
    assert forum["rss_url"] == "https://linux.do/latest.rss"
    notifier = forum["notifiers"][0]
    assert notifier["notifier_type"] == "feishu"
    assert notifier["feishu_keywords"] == ["Docker", "NAS"]
    assert notifier["feishu_push_all"] is False


def test_init_aborts_on_existing_config(runner, tmp_path):
    target = tmp_path / "config.json"
    target.write_text("{}", encoding="utf-8")
    result = runner.invoke(cli, ["init", "--config", str(target)], input="n\n")
    assert result.exit_code != 0


def test_init_force_overwrites(runner, tmp_path):
    target = str(tmp_path / "config.json")
    result = runner.invoke(
        cli,
        ["init", "--config", target, "--force"],
        input="\n\nn\nn\n",  # RSS / 间隔 / 不配飞书 / 不配 Telegram
    )
    assert result.exit_code == 0, result.output


def test_stats_command(runner, tmp_path, app_config):
    config_path = tmp_path / "config.json"
    app_config.database_path = str(tmp_path / "stats.db")
    config_path.write_text(
        json.dumps(app_config.model_dump(mode="json")), encoding="utf-8"
    )
    result = runner.invoke(cli, ["stats", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "users" in result.output


def test_migrate_command(runner, tmp_path, app_config):
    config_path = tmp_path / "config.json"
    app_config.database_path = str(tmp_path / "migrate.db")
    config_path.write_text(
        json.dumps(app_config.model_dump(mode="json")), encoding="utf-8"
    )
    result = runner.invoke(cli, ["migrate", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "数据库已就绪" in result.output


def test_test_notifier_command(runner, tmp_path, app_config, monkeypatch):
    app_config.database_path = str(tmp_path / "tn.db")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(app_config.model_dump(mode="json")), encoding="utf-8"
    )

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0}

    monkeypatch.setattr(
        "linuxdo_monitor.notifier.feishu.requests.post",
        lambda url, **kw: FakeResponse(),
    )
    result = runner.invoke(cli, ["test-notifier", "--config", str(config_path)])
    assert result.exit_code == 0, result.output
    assert "feishu" in result.output


def test_test_notifier_reports_failure(runner, tmp_path, app_config, monkeypatch):
    app_config.database_path = str(tmp_path / "tn2.db")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(app_config.model_dump(mode="json")), encoding="utf-8"
    )

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 19001, "msg": "bot not found"}

    monkeypatch.setattr(
        "linuxdo_monitor.notifier.feishu.requests.post",
        lambda url, **kw: FakeResponse(),
    )
    result = runner.invoke(cli, ["test-notifier", "--config", str(config_path)])
    assert result.exit_code == 1
    assert "❌" in result.output


def test_run_once(runner, tmp_path, app_config, monkeypatch, sample_post):
    from linuxdo_monitor import app as app_module
    from linuxdo_monitor.source.base import BaseSource

    app_config.database_path = str(tmp_path / "once.db")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(app_config.model_dump(mode="json")), encoding="utf-8"
    )

    class OnePostSource(BaseSource):
        def fetch(self):
            return [sample_post]

    monkeypatch.setattr(app_module, "create_source", lambda forum: OnePostSource())

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0}

    calls = []
    monkeypatch.setattr(
        "linuxdo_monitor.notifier.feishu.requests.post",
        lambda url, **kw: (calls.append(kw), FakeResponse())[1],
    )

    result = runner.invoke(cli, ["run", "--config", str(config_path), "--once"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1, "--once 应立即推送匹配的帖子"
