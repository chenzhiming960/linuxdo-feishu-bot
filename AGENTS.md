# AGENTS.md — 项目导航（供 AI 编码助手阅读）

> 面向人类用户的完整文档见 [README.md](README.md)，本文件只保留开发时需要的关键事实。

## 项目概述

Linux.do 论坛多渠道监控与通知系统：

- **数据源**：RSS（`feedparser`）与 Discourse JSON API（Cookie 鉴权 + FlareSolverr 绕 Cloudflare + 自动降级到 RSS）
- **通知渠道**：飞书群 Webhook（广播模式 + 交互卡片）与 Telegram Bot（定向推送）
- **多论坛 / 多渠道**：配置驱动，每个论坛可挂任意组合的通知渠道
- **Web 管理页**：Flask（配置编辑、订阅统计、SQL 查询），`--web-port` 开启
- 由旧版飞书单文件 bot 和 Telegram 监控项目融合重构而来；**旧版代码已全部清理，仓库只含新架构**

## 常用命令

```bash
pip install -e ".[dev]"         # 安装（dev 含 pytest）
pytest                          # 运行测试（当前 164 个，详见「已知问题」）
pytest --cov=linuxdo_monitor    # 覆盖率约 91%
linuxdo-monitor run --once      # 本地跑一轮验证
docker compose up -d --build    # 容器部署
```

## 架构与关键文件

```
src/linuxdo_monitor/
├── cli.py                入口（pyproject entry point: linuxdo_monitor.cli:cli）
│                         命令：init / run / test-notifier / migrate / stats
├── app.py                Application：APScheduler 定时任务 + 多渠道分发编排
├── config.py             Pydantic 配置模型；_convert_legacy 兼容旧版扁平配置
├── database.py           SQLite 存储。每次操作独立短连接（contextmanager _conn）
├── migrations.py         schema 版本迁移（迁移前自动备份 .bak-<ts>）
├── matcher.py            KeywordMatcher：关键词 / 正则（/pat/flags 或 re: 前缀）
├── source/               数据源。工厂 create_source()，插件口 register_source()
│   ├── base.py           BaseSource（fetch / close）
│   ├── rss.py            RSSSource
│   ├── discourse.py      DiscourseSource（JSON API + Cookie + 分类同步）
│   ├── fallback.py       FallbackSource（Cookie 失效自动降级 RSS，去重 id 连续）
│   └── flaresolverr.py   FlareSolverr HTTP 客户端
├── notifier/             通知渠道。工厂 create_notifiers()，插件口 register_notifier()
│   ├── base.py           BaseNotifier
│   ├── feishu.py         FeishuNotifier（广播，chat_id 固定 "broadcast"）
│   └── telegram.py       TelegramNotifier（定向，Forbidden 自动标记 blocked）
└── web/                  Flask 管理页（/linuxdo/config|users|sql）
```

## 重要约定

- **去重**：帖子以 `(id, forum)` 主键，`Database.add_post` 返回是否新增；通知以 `(chat_id, post_id, keyword, forum, channel)` 记录防重发
- **channel 列**：所有用户/订阅表主键含 `channel`（`telegram` / `feishu`），同 chat_id 跨渠道不冲突
- **降级**：Discourse → RSS 的主备切换由 `app.py` 的 `_report_degradation` 轮询检测并告警 `admin_chat_id`
- **新增渠道/数据源**：实现 base 抽象类 + `register_notifier()` / `register_source()` 注册
- **数据库连接**：无长连接、无连接池，全部走 `Database._conn()` 上下文；不要给 Database 加 with 语义
- **日志**：`logging_setup.py` 按小时切分文件、按保留时长清理；模块 logger 用 `get_logger(__name__)`

## 测试

- 位置 `tests/`，pytest + pytest-asyncio（`asyncio_mode = "auto"`，异步测试无需标记）
- 测试大量使用 Fake 对象注入 `Application`（见 `tests/test_app.py` 的 FakeSource/FakeNotifier 模式）
- 覆盖：CLI、配置转换、迁移、匹配器、各数据源、各通知渠道、Web 路由

## 已知问题（截至 2026-09-07）

1. **`tests/test_matcher.py` 的 `test_match_any` 失败**：调用了 `KeywordMatcher.match_any()`，但该方法从未实现（只有 `match` / `match_post`）。修复方向：实现 `match_any` 或删除该测试
2. **注解引用未导入的 `List`**：`notifier/base.py:95`、`notifier/feishu.py:233`。靠 `from __future__ import annotations` 掩盖，做 `get_type_hints()` 或移除 future import 会炸
3. **订阅写入侧 API 只有测试在用**：`database.py` 的 `ensure_user` / `add_subscription` / `remove_subscription` 等 10 个方法无生产调用方——是为 Telegram Bot 交互命令预留的，**不要当死代码删除**
4. `AppConfig.web_port` 配置字段无代码消费方（Web 端口实际来自 CLI `--web-port`），属文档化悬空配置

## 依赖

统一在 `pyproject.toml`（Python ≥ 3.11）：python-telegram-bot、feedparser、apscheduler、pydantic、click、flask、requests。
`requirements.txt` 仅作 `pip install -r` 便捷入口，与 pyproject 保持同步。

## GitHub 操作（gh CLI）

- 本机已安装 gh（v2.98.0）并登录账号 `chenzhiming960`，远端仓库 `chenzhiming960/linuxdo-feishu-bot`（默认分支 `main`）
- **本机直连 github.com 会失败**（Connection reset），gh 与 git 均需走本地代理 `http://127.0.0.1:7897`：
  ```bash
  HTTPS_PROXY=http://127.0.0.1:7897 gh pr list          # gh：环境变量方式
  git -c http.proxy=http://127.0.0.1:7897 push origin main   # git：命令级配置，勿改全局
  ```
- 常用：`gh pr list/view/create`、`gh issue list/view`、`gh run list/view`（CI）、`gh repo view`
- 推送等对远端可见的操作仍需用户确认后执行

