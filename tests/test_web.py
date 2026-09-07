import asyncio

import pytest

from linuxdo_monitor import app as app_module
from linuxdo_monitor.app import Application
from linuxdo_monitor.config import NotifierConfig
from linuxdo_monitor.web import create_app

from .test_app import FakeNotifier, FakeSource  # noqa: F401


@pytest.fixture
def web_app(monkeypatch, app_config, tmp_db, tmp_path):
    """构建一个带 Web 界面的应用（数据源与通知渠道均用假实现）。"""
    app_config.web_password = "secret"
    app_config.sql_admin_password = "sqlsecret"
    config_path = str(tmp_path / "config.json")

    monkeypatch.setattr(app_module, "create_source", lambda forum: FakeSource([]))
    notifier = FakeNotifier(
        NotifierConfig(notifier_type="telegram", telegram_bot_token="1:A"),
        app_config.forums[0],
    )
    monkeypatch.setattr(app_module, "create_notifiers", lambda forum: [notifier])

    monitor = Application(app_config, db=tmp_db, config_path=config_path)
    asyncio.run(monitor.start(schedule=False))
    flask = create_app(monitor)
    flask.config["TESTING"] = True
    return flask, monitor, notifier


@pytest.fixture
def client(web_app):
    flask, _, _ = web_app
    return flask.test_client()


def login(client):
    return client.post("/linuxdo/login", data={"password": "secret"})


def test_login_required(client):
    resp = client.get("/linuxdo/config")
    assert resp.status_code == 302
    assert "/linuxdo/login" in resp.headers["Location"]


def test_wrong_password_rejected(client):
    resp = client.post("/linuxdo/login", data={"password": "nope"}, follow_redirects=True)
    assert "密码错误" in resp.get_data(as_text=True)


def test_pages_render_after_login(client):
    login(client)
    for path in ("/linuxdo/config", "/linuxdo/users", "/linuxdo/sql"):
        assert client.get(path).status_code == 200


def test_index_redirects_to_config(client):
    assert client.get("/", follow_redirects=False).headers["Location"].endswith(
        "/linuxdo/config"
    )


def test_logout_clears_session(client):
    login(client)
    client.get("/linuxdo/logout")
    assert client.get("/linuxdo/config").status_code == 302


def test_update_forum(client, web_app):
    _, monitor, _ = web_app
    login(client)
    resp = client.post(
        "/linuxdo/config/forum/linux-do",
        data={"name": "Linux.do 中文社区", "rss_url": "https://linux.do/latest.rss",
              "fetch_interval": "120", "enabled": "on"},
    )
    assert resp.status_code == 302
    assert monitor.config.forums[0].name == "Linux.do 中文社区"
    assert monitor.config.forums[0].fetch_interval == 120


def test_update_forum_unknown_id(client):
    login(client)
    resp = client.post(
        "/linuxdo/config/forum/nope", data={"name": "x"}, follow_redirects=True
    )
    assert "论坛不存在" in resp.get_data(as_text=True)


def test_update_forum_rejects_non_integer_interval(client, web_app):
    _, monitor, _ = web_app
    login(client)
    client.post(
        "/linuxdo/config/forum/linux-do",
        data={"name": "F", "rss_url": "https://x", "fetch_interval": "abc"},
    )
    assert monitor.config.forums[0].fetch_interval == 60


def test_add_and_delete_notifier(client, web_app):
    _, monitor, _ = web_app
    login(client)
    before = len(monitor.config.forums[0].notifiers)

    client.post(
        "/linuxdo/config/notifier/add",
        data={
            "forum_id": "linux-do",
            "notifier_type": "feishu",
            "feishu_webhook_url": "https://open.feishu.cn/hook/x",
            "feishu_keywords": "Docker, NAS",
        },
    )
    assert len(monitor.config.forums[0].notifiers) == before + 1
    added = monitor.config.forums[0].notifiers[-1]
    assert added.feishu_keywords == ["Docker", "NAS"]

    client.post(
        "/linuxdo/config/notifier/delete",
        data={"forum_id": "linux-do", "index": str(before)},
    )
    assert len(monitor.config.forums[0].notifiers) == before


def test_add_notifier_unknown_type(client):
    login(client)
    resp = client.post(
        "/linuxdo/config/notifier/add",
        data={"forum_id": "linux-do", "notifier_type": "slack"},
        follow_redirects=True,
    )
    assert "未知渠道类型" in resp.get_data(as_text=True)


def test_add_notifier_invalid_payload(client):
    login(client)
    resp = client.post(
        "/linuxdo/config/notifier/add",
        data={"forum_id": "linux-do", "notifier_type": "feishu", "feishu_webhook_url": ""},
        follow_redirects=True,
    )
    assert "新增渠道失败" in resp.get_data(as_text=True)


def test_delete_notifier_out_of_range(client):
    login(client)
    resp = client.post(
        "/linuxdo/config/notifier/delete",
        data={"forum_id": "linux-do", "index": "99"},
        follow_redirects=True,
    )
    assert "渠道不存在" in resp.get_data(as_text=True)


def test_config_is_persisted_to_disk(client, web_app, tmp_path):
    login(client)
    client.post(
        "/linuxdo/config/forum/linux-do",
        data={"name": "已保存", "rss_url": "https://x", "fetch_interval": "90"},
    )
    saved = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert "已保存" in saved


def test_test_notifier_endpoint(client):
    login(client)
    resp = client.post(
        "/linuxdo/api/test-notifier", data={"forum_id": "linux-do", "index": "0"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_test_notifier_invalid_index(client):
    login(client)
    resp = client.post(
        "/linuxdo/api/test-notifier", data={"forum_id": "linux-do", "index": "42"}
    )
    assert resp.get_json()["ok"] is False


def test_sql_readonly_rejects_write(client):
    login(client)
    resp = client.post("/linuxdo/sql", data={"sql": "DELETE FROM posts"})
    assert "只读模式" in resp.get_data(as_text=True)


def test_sql_select_works(client):
    login(client)
    resp = client.post("/linuxdo/sql", data={"sql": "SELECT COUNT(*) AS c FROM posts"})
    assert resp.status_code == 200


def test_sql_admin_unlock(client):
    login(client)
    resp = client.post(
        "/linuxdo/sql",
        data={"sql": "DELETE FROM posts", "admin_password": "sqlsecret"},
    )
    assert "影响行数" in resp.get_data(as_text=True)


def test_sql_admin_wrong_password(client):
    login(client)
    resp = client.post(
        "/linuxdo/sql", data={"sql": "SELECT 1", "admin_password": "wrong"}
    )
    assert "管理员密码错误" in resp.get_data(as_text=True)


def test_reload_endpoint(client):
    login(client)
    assert client.post("/linuxdo/config/reload").status_code == 302
