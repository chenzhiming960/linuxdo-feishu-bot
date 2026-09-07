"""应用编排层。

一个 :class:`Application` 管理多个论坛；每个论坛由
「数据源 + 若干通知渠道」组合而成（见 :class:`ForumRuntime`）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import AppConfig, CHANNEL_TELEGRAM, ForumConfig
from .database import Database
from .exceptions import NotifierBlockedError, NotifierError, SourceError
from .logging_setup import cleanup_old_logs, get_logger
from .matcher import KeywordMatcher
from .models import Post
from .notifier import create_notifiers
from .notifier.base import BaseNotifier
from .source import create_source
from .source.base import BaseSource
from .source.discourse import DiscourseSource
from .source.fallback import FallbackSource

#: 飞书等无用户概念的广播渠道在 notifications 表里使用的虚拟 user_id
BROADCAST_USER_ID = "broadcast"

CLEANUP_JOB_ID = "cleanup"
CLEANUP_TICK_SECONDS = 300


@dataclass
class ForumRuntime:
    """单个论坛的运行期组件。"""

    config: ForumConfig
    source: BaseSource
    notifiers: List[BaseNotifier] = field(default_factory=list)
    #: 降级告警是否已发送过，用于避免重复骚扰管理员
    degraded_notified: bool = False


class Application:
    """多论坛、多渠道的监控应用。"""

    def __init__(
        self,
        config: AppConfig,
        db: Optional[Database] = None,
        auto_start_scheduler: bool = True,
        config_path: Optional[str] = None,
    ):
        self.config = config
        self.config_path = config_path
        self.db = db or Database(config.database_path)
        self.logger = get_logger("app")
        self.matcher = KeywordMatcher()
        self.runtimes: Dict[str, ForumRuntime] = {}
        self.scheduler = AsyncIOScheduler()
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._last_log_cleanup = 0.0
        self._last_db_cleanup = 0.0
        self._first_run_done: set[str] = set()
        self._auto_start_scheduler = auto_start_scheduler
        #: forum_id -> 上次发送 Cookie 告警的时间戳，用于限流
        self._cookie_alerted_at: Dict[str, float] = {}

    # ------------------------------------------------------------ 构建

    @staticmethod
    def _discourse_source(runtime: "ForumRuntime") -> Optional[DiscourseSource]:
        """取出论坛的 Discourse 数据源（可能被 FallbackSource 包着）。"""
        source = runtime.source
        if isinstance(source, DiscourseSource):
            return source
        if isinstance(source, FallbackSource) and isinstance(
            source.primary, DiscourseSource
        ):
            return source.primary
        return None

    def _build_runtime(self, forum: ForumConfig) -> ForumRuntime:
        source = create_source(forum)
        notifiers = create_notifiers(forum)
        return ForumRuntime(config=forum, source=source, notifiers=notifiers)

    def _build_all(self) -> None:
        for forum in self.config.forums:
            if not forum.enabled:
                continue
            self.runtimes[forum.forum_id] = self._build_runtime(forum)

    def reload_config(self, new_config: AppConfig) -> None:
        """热更新配置：重建数据源与通知渠道，并重新调度定时任务的间隔。"""
        self.config = new_config
        self.runtimes.clear()
        self._semaphores.clear()
        self._build_all()

        for forum in new_config.forums:
            if not forum.enabled:
                continue
            job = self.scheduler.get_job(f"fetch-{forum.forum_id}")
            if job:
                self.scheduler.reschedule_job(
                    job.id, trigger="interval", seconds=forum.fetch_interval
                )
            cookie_job = self.scheduler.get_job(f"cookie-{forum.forum_id}")
            if cookie_job:
                self.scheduler.reschedule_job(
                    cookie_job.id,
                    trigger="interval",
                    seconds=max(60, forum.cookie_check_interval),
                )
        self.logger.info("配置已热更新，论坛数: %d", len(self.runtimes))

    # ------------------------------------------------------------ 生命周期

    async def start(self, schedule: bool = True) -> None:
        """构建运行时组件。

        :param schedule: 是否注册定时任务；``--once`` 模式下传 False，只构建组件。
        """
        self._build_all()
        if not schedule:
            return

        for forum in self.config.forums:
            if not forum.enabled:
                continue
            self.scheduler.add_job(
                self.fetch_and_notify,
                trigger="interval",
                seconds=forum.fetch_interval,
                args=[forum.forum_id],
                id=f"fetch-{forum.forum_id}",
                replace_existing=True,
                next_run_time=datetime.now(),
            )
        self.scheduler.add_job(
            self._cleanup_tick,
            trigger="interval",
            seconds=CLEANUP_TICK_SECONDS,
            id=CLEANUP_JOB_ID,
            replace_existing=True,
        )
        for forum_id, runtime in self.runtimes.items():
            if self._discourse_source(runtime) is None:
                continue
            self.scheduler.add_job(
                self._check_cookie_task,
                trigger="interval",
                seconds=max(60, runtime.config.cookie_check_interval),
                args=[forum_id],
                id=f"cookie-{forum_id}",
                replace_existing=True,
                next_run_time=datetime.now(),
            )
        if self._auto_start_scheduler and not self.scheduler.running:
            self.scheduler.start()
        self.logger.info(
            "应用已启动，论坛: %s",
            ", ".join(self.runtimes) or "（无）",
        )

    async def run_forever(self) -> None:
        await self.start()
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.logger.info("收到退出信号")
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        for runtime in self.runtimes.values():
            runtime.source.close()
            for notifier in runtime.notifiers:
                try:
                    await notifier.close()
                except Exception:  # pragma: no cover - 关闭失败忽略
                    pass
        self.logger.info("应用已停止")

    # ------------------------------------------------------------ 核心流程

    async def fetch_and_notify(self, forum_id: str) -> None:
        """拉取一个论坛的新帖并推送到它的所有通知渠道。"""
        runtime = self.runtimes.get(forum_id)
        if runtime is None:
            self.logger.warning("未找到论坛运行时: %s", forum_id)
            return

        forum = runtime.config
        try:
            posts = await asyncio.to_thread(runtime.source.fetch)
        except SourceError as exc:
            self.logger.error("论坛 %s 拉取失败: %s", forum_id, exc)
            await self._notify_admin(f"⚠️ 论坛 {forum.name} 数据源拉取失败：{exc}")
            return
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("论坛 %s 拉取异常: %s", forum_id, exc)
            return

        # 降级状态要在任何提前 return 之前上报，否则无新帖时不会告警
        await self._report_degradation(runtime)

        # 首次运行只做去重落库，避免把历史帖子一次性灌进群里
        if forum_id not in self._first_run_done:
            self._first_run_done.add(forum_id)
            if getattr(forum, "skip_first_run", True):
                for post in posts:
                    self.db.add_post(post, forum_id)
                self.logger.info(
                    "论坛 %s 首次运行，已跳过 %d 条历史帖子", forum_id, len(posts)
                )
                return

        new_posts = [p for p in posts if self.db.add_post(p, forum_id)]
        if not new_posts:
            self.logger.debug("论坛 %s 无新帖", forum_id)
            return

        self.logger.info("论坛 %s 发现 %d 条新帖", forum_id, len(new_posts))
        for post in new_posts:
            await self._dispatch(runtime, post)

    async def _report_degradation(self, runtime: ForumRuntime) -> None:
        """数据源降级/恢复时通知管理员，每种状态只通知一次。"""
        source = runtime.source
        if not isinstance(source, FallbackSource):
            return

        name = runtime.config.name or runtime.config.forum_id
        if source.degraded and not runtime.degraded_notified:
            runtime.degraded_notified = True
            await self._notify_admin(
                f"⚠️ 论坛 {name} 的 Discourse 数据源不可用，已降级到 RSS："
                f"{source.last_error}\n请及时更新 discourse_cookie。"
            )
        elif not source.degraded and runtime.degraded_notified:
            runtime.degraded_notified = False
            await self._notify_admin(f"✅ 论坛 {name} 的 Discourse 数据源已恢复。")

    async def _check_cookie_task(self, forum_id: str) -> None:
        """定期检测 Discourse Cookie 可用性，并顺带同步分类表。

        仅在状态由「可用」变「不可用」时告警，并按检测间隔限流，
        避免 Cookie 长期失效期间反复骚扰管理员。
        """
        runtime = self.runtimes.get(forum_id)
        if runtime is None:
            return
        discourse = self._discourse_source(runtime)
        if discourse is None:
            return

        ok, message = await asyncio.to_thread(discourse.check_cookie)
        name = runtime.config.name or forum_id
        if not ok:
            self.logger.warning("论坛 %s 的 Discourse 不可用: %s", forum_id, message)
            interval = max(60, runtime.config.cookie_check_interval)
            last = self._cookie_alerted_at.get(forum_id, 0.0)
            if time.time() - last >= interval:
                self._cookie_alerted_at[forum_id] = time.time()
                await self._notify_admin(
                    f"⚠️ 论坛 {name} 的 Discourse Cookie 检测失败：{message}"
                )
            return

        self._cookie_alerted_at.pop(forum_id, None)
        self._sync_categories(discourse, forum_id)

    def _sync_categories(self, discourse: DiscourseSource, forum_id: str) -> None:
        """把 Discourse 分类写进 categories 表，供推送消息展示分类名。"""
        categories = discourse.fetch_categories()
        if not categories:
            return
        for category in categories:
            self.db.upsert_category(
                category["id"],
                forum_id,
                name=category["name"],
                slug=category["slug"],
                description=category["description"],
            )
        self.logger.info("论坛 %s 同步了 %d 个分类", forum_id, len(categories))

    async def _dispatch(self, runtime: ForumRuntime, post: Post) -> None:
        """把一个帖子分发到该论坛的所有通知渠道。"""
        forum = runtime.config
        category_name = self.db.get_category_name(post.category, forum.forum_id)

        for notifier in runtime.notifiers:
            channel = notifier.get_channel_type()
            tasks = self._collect_tasks(notifier, post, category_name)
            if not tasks:
                continue

            semaphore = self._semaphores.setdefault(
                f"{forum.forum_id}:{channel}",
                asyncio.Semaphore(notifier.concurrency),
            )
            results = await asyncio.gather(
                *[
                    self._send_one(notifier, user_id, post, keyword, is_all, category_name, semaphore)
                    for user_id, keyword, is_all in tasks
                ],
                return_exceptions=True,
            )
            sent = sum(1 for r in results if r is True)
            if sent:
                self.logger.info(
                    "论坛 %s 通过 %s 推送 %d/%d 条: %s",
                    forum.forum_id,
                    channel,
                    sent,
                    len(tasks),
                    post.display_title,
                )

    def _collect_tasks(
        self, notifier: BaseNotifier, post: Post, category_name: Optional[str]
    ) -> List[Tuple[str, Optional[str], bool]]:
        """计算某个渠道本次要发给谁。

        返回 ``(user_id, keyword, is_subscribe_all)`` 列表。
        """
        forum = notifier.forum_config
        forum_id = forum.forum_id if forum else ""
        channel = notifier.get_channel_type()
        tasks: List[Tuple[str, Optional[str], bool]] = []

        if notifier.supports_broadcast():
            keywords, push_all = notifier.get_broadcast_keywords()
            if push_all:
                tasks.append((BROADCAST_USER_ID, None, True))
            else:
                matched = self.matcher.match_post(post, keywords)
                if matched:
                    tasks.append((BROADCAST_USER_ID, matched[0], False))
            return tasks

        # 定向渠道（Telegram）：从数据库读取订阅关系
        selected: Dict[str, Tuple[Optional[str], bool]] = {}

        for chat_id, keyword in self.db.get_keyword_subscriptions(forum_id, channel):
            if chat_id in selected:
                continue
            if self.matcher.match_post(post, [keyword]):
                selected[chat_id] = (keyword, False)

        if post.author:
            for chat_id in self.db.get_author_subscribers(post.author, forum_id, channel):
                selected.setdefault(chat_id, (f"@{post.author}", False))

        for chat_id in self.db.get_subscribe_all_users(forum_id, channel):
            selected.setdefault(chat_id, (None, True))

        for chat_id, (keyword, is_all) in selected.items():
            if self.db.is_blocked(chat_id, forum_id, channel):
                continue
            if self.db.notification_sent(
                chat_id, post.id, keyword or "", forum_id, channel
            ):
                continue
            tasks.append((chat_id, keyword, is_all))
        return tasks

    async def _send_one(
        self,
        notifier: BaseNotifier,
        user_id: str,
        post: Post,
        keyword: Optional[str],
        is_all: bool,
        category_name: Optional[str],
        semaphore: asyncio.Semaphore,
    ) -> bool:
        forum = notifier.forum_config
        forum_id = forum.forum_id if forum else ""
        channel = notifier.get_channel_type()

        async with semaphore:
            try:
                if is_all:
                    ok = await notifier.send_notification_all(
                        user_id, post, category_name
                    )
                else:
                    ok = await notifier.send_notification(
                        user_id, post, keyword, category_name
                    )
            except NotifierBlockedError as exc:
                self.logger.warning("用户 %s 已封禁机器人，标记屏蔽: %s", user_id, exc)
                self.db.mark_blocked(user_id, forum_id, channel)
                return False
            except NotifierError as exc:
                self.logger.error("渠道 %s 推送失败 -> %s: %s", channel, user_id, exc)
                return False
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("渠道 %s 推送异常: %s", channel, exc)
                return False

        if ok:
            self.db.record_notification(
                user_id, post.id, keyword or "", forum_id, channel
            )
        return ok

    # ------------------------------------------------------------ 辅助任务

    async def _notify_admin(self, text: str) -> None:
        """把异常信息发给管理员（仅 Telegram 渠道）。"""
        admin_chat_id = self.config.admin_chat_id
        if not admin_chat_id:
            return
        for runtime in self.runtimes.values():
            for notifier in runtime.notifiers:
                if notifier.get_channel_type() != CHANNEL_TELEGRAM:
                    continue
                try:
                    await notifier.send_text(admin_chat_id, text)
                except Exception as exc:  # noqa: BLE001
                    self.logger.error("管理员告警发送失败: %s", exc)

    def _cleanup_tick(self) -> None:
        """按配置间隔清理过期日志与数据库记录。"""
        now = time.time()
        cfg = self.config

        if now - self._last_log_cleanup >= max(1, cfg.log_cleanup_interval_seconds):
            removed = cleanup_old_logs(cfg.log_dir, cfg.log_retention_hours)
            if removed:
                self.logger.info("清理过期日志 %d 个", removed)
            self._last_log_cleanup = now

        if now - self._last_db_cleanup >= max(1, cfg.db_cleanup_interval_seconds):
            deleted = self.db.cleanup_posts(cfg.db_retention_hours)
            self.logger.info("清理过期帖子 %d 条", deleted)
            self._last_db_cleanup = now

    # ------------------------------------------------------------ 运维接口

    def mark_initial_sync_done(self) -> None:
        """标记首轮同步已完成。

        ``--once`` 等场景需要跳过「首轮只落库」的保护，立即推送。
        """
        self._first_run_done.update(self.runtimes)

    def save_config(self, path: Optional[str] = None) -> str:
        """把当前配置写回文件。"""
        from .config import save_config as _save

        target = path or self.config_path
        if not target:
            raise ValueError("未指定配置文件路径")
        _save(self.config, target)
        return target

    def notifier_stats(self) -> List[dict]:
        """各渠道的发送统计，供 Web 管理页展示。"""
        stats = []
        for forum_id, runtime in self.runtimes.items():
            for notifier in runtime.notifiers:
                item = notifier.stats()
                item["forum_id"] = forum_id
                stats.append(item)
        return stats

    async def health_check(self) -> List[dict]:
        results = []
        for forum_id, runtime in self.runtimes.items():
            for notifier in runtime.notifiers:
                ok, message = await notifier.health_check()
                results.append(
                    {
                        "forum_id": forum_id,
                        "channel": notifier.get_channel_type(),
                        "healthy": ok,
                        "message": message,
                    }
                )
        return results
