"""Flask Web 管理界面。

路由（前缀 ``/linuxdo``）：

* ``/linuxdo/config``  论坛与通知渠道管理
* ``/linuxdo/users``   用户与订阅统计
* ``/linuxdo/sql``     SQL 查询（只读 / 管理员模式）
"""

from __future__ import annotations

import asyncio
import os
import secrets
from functools import wraps
from typing import Any, Optional

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..app import Application
from ..config import NotifierConfig
from ..logging_setup import get_logger

logger = get_logger("web")

READ_ONLY_SQL_PREFIXES = ("select", "pragma", "explain", "with")


def create_app(monitor_app: Application, secret_key: Optional[str] = None) -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.secret_key = secret_key or os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(16)
    app.config["MONITOR_APP"] = monitor_app
    app.config["PASSWORD"] = monitor_app.config.web_password or ""
    app.config["SQL_ADMIN_PASSWORD"] = monitor_app.config.sql_admin_password or ""

    def login_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if app.config["PASSWORD"] and not session.get("auth"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)

        return wrapper

    # ---------------------------------------------------------------- 认证

    @app.route("/linuxdo/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if request.form.get("password") == app.config["PASSWORD"]:
                session["auth"] = True
                return redirect(request.args.get("next") or url_for("config_page"))
            error = "密码错误"
        return render_template("login.html", error=error)

    @app.route("/linuxdo/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------------------------------------------------------------- 首页

    @app.route("/")
    def index():
        return redirect(url_for("config_page"))

    # ------------------------------------------------------------ 配置管理

    @app.route("/linuxdo/config")
    @login_required
    def config_page():
        monitor: Application = app.config["MONITOR_APP"]
        return render_template(
            "config.html",
            config=monitor.config,
            notifier_stats={s["forum_id"] + ":" + s["channel"]: s for s in monitor.notifier_stats()},
            config_path=monitor.config_path,
        )

    @app.post("/linuxdo/config/forum/<forum_id>")
    @login_required
    def update_forum(forum_id: str):
        monitor: Application = app.config["MONITOR_APP"]
        forum = monitor.config.get_forum(forum_id)
        if forum is None:
            flash(f"论坛不存在: {forum_id}", "error")
            return redirect(url_for("config_page"))

        forum.name = request.form.get("name", forum.name)
        forum.rss_url = request.form.get("rss_url", forum.rss_url)
        forum.enabled = request.form.get("enabled") == "on"
        try:
            forum.fetch_interval = max(1, int(request.form.get("fetch_interval", forum.fetch_interval)))
        except ValueError:
            flash("轮询间隔必须是整数", "error")

        _persist(monitor)
        flash(f"论坛 {forum_id} 已更新", "success")
        return redirect(url_for("config_page"))

    @app.post("/linuxdo/config/notifier/add")
    @login_required
    def add_notifier():
        monitor: Application = app.config["MONITOR_APP"]
        forum_id = request.form.get("forum_id", "")
        forum = monitor.config.get_forum(forum_id)
        if forum is None:
            flash(f"论坛不存在: {forum_id}", "error")
            return redirect(url_for("config_page"))

        notifier_type = request.form.get("notifier_type", "")
        raw: dict[str, Any] = {"notifier_type": notifier_type}
        if notifier_type == "telegram":
            raw["telegram_bot_token"] = request.form.get("telegram_bot_token", "").strip()
        elif notifier_type == "feishu":
            raw["feishu_webhook_url"] = request.form.get("feishu_webhook_url", "").strip()
            keywords = request.form.get("feishu_keywords", "")
            raw["feishu_keywords"] = [k.strip() for k in keywords.split(",") if k.strip()]
            raw["feishu_push_all"] = request.form.get("feishu_push_all") == "on"
        else:
            flash(f"未知渠道类型: {notifier_type}", "error")
            return redirect(url_for("config_page"))

        try:
            forum.notifiers.append(NotifierConfig.model_validate(raw))
        except Exception as exc:
            flash(f"新增渠道失败: {exc}", "error")
            return redirect(url_for("config_page"))

        _persist(monitor)
        flash(f"已为 {forum_id} 新增 {notifier_type} 渠道", "success")
        return redirect(url_for("config_page"))

    @app.post("/linuxdo/config/notifier/delete")
    @login_required
    def delete_notifier():
        monitor: Application = app.config["MONITOR_APP"]
        forum_id = request.form.get("forum_id", "")
        index = int(request.form.get("index", -1))
        forum = monitor.config.get_forum(forum_id)
        if forum is None or not (0 <= index < len(forum.notifiers)):
            flash("渠道不存在", "error")
            return redirect(url_for("config_page"))
        removed = forum.notifiers.pop(index)
        _persist(monitor)
        flash(f"已删除 {forum_id} 的 {removed.notifier_type} 渠道", "success")
        return redirect(url_for("config_page"))

    @app.post("/linuxdo/config/reload")
    @login_required
    def reload():
        monitor: Application = app.config["MONITOR_APP"]
        monitor.reload_config(monitor.config)
        flash("配置已重新加载", "success")
        return redirect(url_for("config_page"))

    @app.post("/linuxdo/api/test-notifier")
    @login_required
    def test_notifier():
        """测试指定渠道连通性，返回 JSON。"""
        monitor: Application = app.config["MONITOR_APP"]
        forum_id = request.form.get("forum_id", "")
        index = int(request.form.get("index", -1))
        runtime = monitor.runtimes.get(forum_id)
        if runtime is None or not (0 <= index < len(runtime.notifiers)):
            return {"ok": False, "message": "渠道不存在"}
        notifier = runtime.notifiers[index]
        try:
            ok, message = asyncio.run(notifier.health_check())
        except Exception as exc:  # noqa: BLE001
            ok, message = False, str(exc)
        return {"ok": ok, "message": message}

    # ------------------------------------------------------------ 用户统计

    @app.route("/linuxdo/users")
    @login_required
    def users_page():
        monitor: Application = app.config["MONITOR_APP"]
        db = monitor.db
        forums = []
        for forum in monitor.config.forums:
            forums.append(
                {
                    "forum": forum,
                    "stats": db.get_stats(forum.forum_id),
                    "users": db.get_users(forum.forum_id),
                    "blocked": db.get_blocked(forum.forum_id),
                    "keywords": db.get_keyword_stats(forum.forum_id),
                }
            )
        return render_template("users.html", forums=forums, overall=db.get_stats())

    # -------------------------------------------------------------- SQL 查询

    @app.route("/linuxdo/sql", methods=["GET", "POST"])
    @login_required
    def sql_page():
        monitor: Application = app.config["MONITOR_APP"]
        rows: list[dict] = []
        columns: list[str] = []
        error = None
        affected = None
        sql = request.form.get("sql", "")

        if request.method == "POST" and request.form.get("admin_password"):
            if request.form["admin_password"] == app.config["SQL_ADMIN_PASSWORD"]:
                session["sql_admin"] = True
            else:
                error = "管理员密码错误"

        is_admin = bool(session.get("sql_admin")) or not app.config["SQL_ADMIN_PASSWORD"]

        if request.method == "POST" and sql.strip():
            statement = sql.strip().rstrip(";")
            if not is_admin and not statement.lower().startswith(READ_ONLY_SQL_PREFIXES):
                error = "只读模式下仅允许 SELECT / PRAGMA / EXPLAIN / WITH 语句"
            else:
                try:
                    if statement.lower().startswith("select"):
                        result = monitor.db.query(statement)
                        rows = result
                        columns = list(result[0].keys()) if result else []
                    else:
                        affected = monitor.db.execute(statement)
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)

        return render_template(
            "sql.html",
            sql=sql,
            rows=rows,
            columns=columns,
            error=error,
            affected=affected,
            is_admin=is_admin,
        )

    return app


def _persist(monitor: Application) -> None:
    """保存配置并热更新；没有配置路径时只热更新。"""
    try:
        if monitor.config_path:
            monitor.save_config()
    except Exception as exc:  # noqa: BLE001
        logger.error("保存配置失败: %s", exc)
        flash(f"保存配置失败: {exc}", "error")
    monitor.reload_config(monitor.config)


def start_web_thread(
    monitor_app: Application, port: int = 8080, host: str = "0.0.0.0"
) -> Any:
    """在守护线程里启动 Flask（主线程跑 asyncio 事件循环）。"""
    import threading

    flask_app = create_app(monitor_app)
    thread = threading.Thread(
        target=lambda: flask_app.run(
            host=host, port=port, debug=False, use_reloader=False, threaded=True
        ),
        name="web-admin",
        daemon=True,
    )
    thread.start()
    return thread
