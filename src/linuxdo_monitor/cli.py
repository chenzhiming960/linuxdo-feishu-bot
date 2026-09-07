"""命令行入口。

::

    linuxdo-monitor init              交互式生成配置
    linuxdo-monitor run               启动监控（可选 --web-port 开启管理页）
    linuxdo-monitor run --once        只跑一轮就退出
    linuxdo-monitor test-notifier     测试各通知渠道连通性
    linuxdo-monitor migrate           仅执行数据库迁移
    linuxdo-monitor stats             打印数据库统计
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Optional

import click

from .app import Application
from .config import CHANNEL_FEISHU, CHANNEL_TELEGRAM, AppConfig, load_config, save_config
from .database import Database
from .logging_setup import get_logger, setup_logging

logger = get_logger("cli")

DEFAULT_CONFIG_PATH = os.getenv("CONFIG_PATH", "config/config.json")


def _load(ctx_config: Optional[str]) -> AppConfig:
    return load_config(ctx_config or DEFAULT_CONFIG_PATH)


@click.group()
@click.version_option(package_name="linuxdo-monitor", message="%(version)s")
def cli() -> None:
    """LinuxDo 多渠道监控与通知系统。"""


@cli.command()
@click.option("--config", "config_path", default=DEFAULT_CONFIG_PATH, show_default=True)
@click.option("--force", is_flag=True, help="已存在时覆盖")
def init(config_path: str, force: bool) -> None:
    """交互式生成配置文件（同时兼容旧版扁平格式）。"""
    if os.path.exists(config_path) and not force:
        click.confirm(f"{config_path} 已存在，是否覆盖？", abort=True)

    click.echo("=== LinuxDo 监控配置初始化 ===")
    rss_url = click.prompt("RSS 地址", default="https://linux.do/latest.rss")
    fetch_interval = click.prompt("轮询间隔（秒）", default=60, type=int)

    notifiers = []
    if click.confirm("是否配置飞书机器人？", default=True):
        webhook = click.prompt("飞书 Webhook URL").strip()
        keywords_raw = click.prompt(
            "飞书关键词（英文逗号分隔，留空=推送全部新帖）", default=""
        )
        keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
        notifiers.append(
            {
                "notifier_type": CHANNEL_FEISHU,
                "feishu_webhook_url": webhook,
                "feishu_keywords": keywords,
                "feishu_push_all": not keywords,
            }
        )

    admin_chat_id: Optional[int] = None
    if click.confirm("是否配置 Telegram Bot？", default=False):
        token = click.prompt("Telegram Bot Token").strip()
        notifiers.append(
            {"notifier_type": CHANNEL_TELEGRAM, "telegram_bot_token": token}
        )
        if click.confirm("是否设置管理员 chat_id（接收异常告警）？", default=False):
            admin_chat_id = click.prompt("管理员 chat_id", type=int)

    config = AppConfig(
        forums=[
            {
                "forum_id": "linux-do",
                "name": "Linux.do",
                "source_type": "rss",
                "rss_url": rss_url,
                "fetch_interval": fetch_interval,
                "notifiers": notifiers,
            }
        ],
        admin_chat_id=admin_chat_id,
    )
    save_config(config, config_path)
    click.echo(f"✅ 配置已写入 {config_path}")


@cli.command()
@click.option("--config", "config_path", default=None, help=f"默认 {DEFAULT_CONFIG_PATH}")
@click.option("--web-port", type=int, default=None, help="开启 Web 管理页的端口")
@click.option("--once", is_flag=True, help="只执行一轮拉取推送后退出")
def run(config_path: Optional[str], web_port: Optional[int], once: bool) -> None:
    """启动监控服务。"""
    config = _load(config_path)
    setup_logging(config.log_dir)

    app = Application(config, config_path=config_path or DEFAULT_CONFIG_PATH)

    if web_port:
        from .web import start_web_thread

        start_web_thread(app, port=web_port)
        logger.info("Web 管理页已启动: http://0.0.0.0:%d/linuxdo/config", web_port)

    if once:
        asyncio.run(_run_once(app))
        return

    try:
        asyncio.run(app.run_forever())
    except KeyboardInterrupt:  # pragma: no cover
        pass


async def _run_once(app: Application) -> None:
    await app.start(schedule=False)
    app.mark_initial_sync_done()
    for forum_id in list(app.runtimes):
        await app.fetch_and_notify(forum_id)
    await app.stop()


@cli.command("test-notifier")
@click.option("--config", "config_path", default=None)
def test_notifier(config_path: Optional[str]) -> None:
    """测试所有已配置通知渠道的连通性。"""
    config = _load(config_path)
    setup_logging(config.log_dir)
    app = Application(config, config_path=config_path or DEFAULT_CONFIG_PATH)

    async def _check():
        # schedule=False：只构建渠道实例，不注册定时任务（否则会立刻真实拉取一次）
        await app.start(schedule=False)
        results = await app.health_check()
        await app.stop()
        return results

    results = asyncio.run(_check())
    if not results:
        click.echo("没有已启用的通知渠道")
        return
    for item in results:
        flag = "✅" if item["healthy"] else "❌"
        click.echo(
            f"{flag} {item['forum_id']} / {item['channel']}: {item['message']}"
        )
    if not all(r["healthy"] for r in results):
        sys.exit(1)


@cli.command()
@click.option("--db", "db_path", default=None, help="数据库路径，默认取配置里的 database_path")
@click.option("--config", "config_path", default=None)
def migrate(db_path: Optional[str], config_path: Optional[str]) -> None:
    """执行数据库迁移（会自动备份）。"""
    path = db_path or _load(config_path).database_path
    Database(path)
    click.echo(f"✅ 数据库已就绪: {path}")


@cli.command()
@click.option("--config", "config_path", default=None)
def stats(config_path: Optional[str]) -> None:
    """打印数据库统计信息。"""
    config = _load(config_path)
    db = Database(config.database_path, backup=False)
    overall = db.get_stats()
    click.echo(json.dumps(overall, ensure_ascii=False, indent=2))
    for forum in config.forums:
        click.echo(f"\n[{forum.forum_id}] {forum.name}")
        click.echo(json.dumps(db.get_stats(forum.forum_id), ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    cli()
