# LinuxDo 多渠道监控与通知系统

Linux.do 论坛新帖监控，支持 **RSS** 与 **Discourse API** 双数据源、
**飞书群机器人** 与 **Telegram Bot** 双通知渠道，
可同时管理多个论坛、多个通知渠道，并提供 Web 管理界面。

本项目由原 `linuxdo-feishu-bot`（飞书单文件版）与 `linux-do-keyword-monitor`（Telegram 完整版）
融合重构而来，保留了 Telegram 版的分层架构，并把通知渠道抽象成可插拔接口。
（旧版单文件代码与空目录已在重构完成后清理，仓库只保留新架构。）

---

## 1. 项目结构

```
linuxdo-feishu-bot/
├── src/linuxdo_monitor/          主程序包
│   ├── config.py                 配置模型（多论坛 / 多渠道，兼容旧版格式）
│   ├── database.py               SQLite 存储（去重、订阅、通知记录）
│   ├── migrations.py             schema 版本迁移（自动备份）
│   ├── matcher.py                关键词 / 正则匹配
│   ├── app.py                    Application 编排（定时任务 + 多渠道分发）
│   ├── cli.py                    命令行入口
│   ├── logging_setup.py          按小时日志轮转 + 过期清理
│   ├── source/                   数据源抽象
│   │   ├── base.py               BaseSource
│   │   ├── rss.py                RSSSource
│   │   ├── discourse.py         DiscourseSource（JSON API + Cookie）
│   │   ├── fallback.py           FallbackSource（主备自动切换）
│   │   └── flaresolverr.py       FlareSolverr 客户端（Cloudflare 绕过）
│   ├── notifier/                 通知渠道抽象
│   │   ├── base.py               BaseNotifier
│   │   ├── feishu.py             FeishuNotifier（广播模式 + 交互卡片）
│   │   └── telegram.py           TelegramNotifier
│   └── web/                      Flask 管理界面
│       ├── app.py
│       └── templates/
├── tests/                        单元测试（覆盖率 91%）
├── config/                       运行配置（config.json）
├── config.example.json           完整配置示例
├── data/                         SQLite 数据库
├── logs/                         日志
├── pyproject.toml                依赖与 CLI 入口
├── Dockerfile / docker-compose.yml
```

---

## 2. 安装与启动

### 本地

```bash
pip install -e .
linuxdo-monitor init                    # 交互式生成 config/config.json
linuxdo-monitor run                     # 启动监控
linuxdo-monitor run --web-port 8080     # 同时开启 Web 管理页
```

### Docker

```bash
cp config.example.json config/config.json
# 按需修改 config/config.json
docker compose up -d --build
```

Web 管理页默认不开启。需要时在 `docker-compose.yml` 里取消 `command` 注释，
或 `docker compose exec linuxdo-monitor linuxdo-monitor run --web-port 8080`。

---

## 3. 配置说明

完整示例见 [`config.example.json`](config.example.json)。

```jsonc
{
  "forums": [
    {
      "forum_id": "linux-do",
      "name": "Linux.do",
      "enabled": true,
      "source_type": "rss",              // rss 或 discourse（见下文「数据源对比」）
      "rss_url": "https://linux.do/latest.rss",
      "fetch_interval": 60,              // 拉取间隔（秒）
      "skip_first_run": true,            // 首次轮询只落库不推送，避免灌屏
      "notifiers": [
        {
          "notifier_type": "telegram",
          "enabled": true,
          "telegram_bot_token": "123456:AAF..."
        },
        {
          "notifier_type": "feishu",
          "enabled": true,
          "feishu_webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
          "feishu_mode": "broadcast",    // broadcast（群广播）
          "feishu_keywords": ["Docker", "NAS", "求助"],
          "feishu_push_all": false,      // true = 忽略关键词，推送全部新帖
          "feishu_secret": null          // 可选：飞书加签密钥
        }
      ]
    }
  ],
  "admin_chat_id": 123456789,            // 接收异常告警的 Telegram chat_id
  "web_password": "",                    // Web 管理页密码，留空=不设防
  "sql_admin_password": "",              // SQL 页管理员密码（解锁写操作）
  "web_port": 8080,
  "log_cleanup_interval_seconds": 3600,
  "log_retention_hours": 4,
  "db_cleanup_interval_seconds": 43200,
  "db_retention_hours": 24,
  "database_path": "/data/monitor.db",
  "log_dir": "/logs"
}
```

### 关键词写法

| 写法 | 含义 |
| --- | --- |
| `Docker` | 普通关键词，大小写不敏感子串匹配 |
| `/求助\|提问/` | 正则表达式（`/pattern/flags`，flags 可选 `aimsux`） |
| `re:^NAS` | 正则表达式（`re:` 前缀写法） |

### 两种渠道的差异

| | Telegram | 飞书 |
| --- | --- | --- |
| 推送对象 | 定向到 chat_id | 广播到群（无用户概念） |
| 订阅来源 | 数据库 `subscriptions` / `user_subscriptions` / `subscribe_all` | 配置里的 `feishu_keywords` |
| 去重记录 | `(chat_id, post_id, keyword, forum, channel)` | 同上，chat_id 固定为 `broadcast` |
| 封禁处理 | `Forbidden` → 自动标记 blocked 并跳过 | 不适用 |

### 数据源：RSS vs Discourse API

| | RSS | Discourse API |
| --- | --- | --- |
| 配置 | `source_type: "rss"` + `rss_url` | `source_type: "discourse"` + `discourse_url` |
| 鉴权 | 无需 | 通常需要 `discourse_cookie`（浏览器 F12 复制） |
| 作者 / 分类 | 取决于 feed 内容 | 完整支持（作者、分类名会自动同步进库） |
| Cloudflare | feed 地址一般不受影响 | 可能被拦截，可配 `flaresolverr_url` 绕过 |
| 可靠性 | 简单稳定 | Cookie 会过期，**失效时自动降级到 RSS 并告警管理员，恢复后自动切回** |

Discourse 数据源的关键配置：

```jsonc
{
  "source_type": "discourse",
  "discourse_url": "https://linux.do",
  "discourse_path": "/latest.json",       // 也可以是 /new.json、/top.json 等
  "discourse_cookie": "_t=xxx; _forum_session=yyy",
  "flaresolverr_url": null,               // 可选：http://flaresolverr:8191
  "fallback_to_rss": true,                // Cookie 失效时自动降级（需配 rss_url）
  "rss_url": "https://linux.do/latest.rss",
  "cookie_check_interval": 3600            // Cookie 定时检测间隔（秒）
}
```

降级行为：

* 拉取时 Cookie 失效（或被 Cloudflare 拦截且未配 FlareSolverr）→ 自动改用 RSS，
  主备源的帖子 id 使用同一套链接格式，**去重表连续，不会重复推送**
* 降级发生和恢复各向 `admin_chat_id` 发一条告警
* `_check_cookie_task` 定时检测 Cookie 并同步 Discourse 分类表（按检测间隔限流告警）

---

## 4. CLI

| 命令 | 说明 |
| --- | --- |
| `linuxdo-monitor init` | 交互式生成配置（`--force` 覆盖已有） |
| `linuxdo-monitor run` | 启动监控，可选 `--web-port` / `--once` |
| `linuxdo-monitor run --once` | 只跑一轮拉取推送后退出 |
| `linuxdo-monitor test-notifier` | 测试所有渠道连通性（失败退出码 1） |
| `linuxdo-monitor migrate` | 仅执行数据库迁移 |
| `linuxdo-monitor stats` | 打印数据库统计 |

---

## 5. Web 管理界面

加 `--web-port 8080` 启动后访问：

| 路径 | 功能 |
| --- | --- |
| `/linuxdo/config` | 论坛配置、通知渠道增删、连通性测试、热重载 |
| `/linuxdo/users` | 用户/订阅/屏蔽统计、关键词热度 |
| `/linuxdo/sql` | SQL 查询（默认只读，`sql_admin_password` 解锁写操作） |

配置改动会立即写回 `config.json` 并热更新（重建数据源与通知渠道、重设定时任务间隔）。

---

## 6. 数据库与迁移

启动或执行 `linuxdo-monitor migrate` 时自动迁移，**迁移前会备份为 `<db>.bak-<时间戳>`**。

所有用户/订阅相关表都带 `channel` 列（默认 `telegram`），主键包含 `channel`，
因此同一 `chat_id` 可以在飞书和 Telegram 两个渠道各自订阅而不冲突。

支持的迁移起点：

* 全新数据库 → 直接建到最新 schema
* 旧版 `linuxdo-feishu-bot` 的库（`posts(link, created_at)`）→ 转换成新 `posts` 表
* 旧版 Telegram 监控的库（无 `channel` 列）→ 补列并重建主键，历史数据填充 `telegram`

### 从旧版 `linuxdo-feishu-bot` 迁移

旧版扁平配置会被自动识别并转换，无需手工改写：

```jsonc
{
  "feishu_webhook_url": "...",      // → forums[0].notifiers[0]（feishu）
  "rss_url": "...",                 // → forums[0].rss_url
  "poll_interval": 30,              // → forums[0].fetch_interval
  "keyword_monitor": {
    "enabled": true,                // false → feishu_push_all = true
    "keywords": ["A", "B"]          // → feishu_keywords
  }
}
```

旧数据库 `data/rss_posts.db` 会被自动升级为新结构（首次启动前建议先备份）。

---

## 7. 开发

```bash
pip install -e ".[dev]"
pytest                                  # 164 个测试
pytest --cov=linuxdo_monitor            # 覆盖率约 91%
```

新增通知渠道：实现 `notifier/base.py` 的 `BaseNotifier`，
再用 `notifier/__init__.py` 的 `register_notifier()` 注册即可。
新增数据源同理（`source/base.py` 的 `BaseSource` + `register_source()`）。

---

## 8. 已知限制

* Discourse 数据源的 Cloudflare 绕过仅支持 FlareSolverr；DrissionPage 方案依赖过重，未实现
* 飞书仅支持群 Webhook 广播模式；个人定向需要飞书自建应用（App ID/Secret），当前未支持
* Telegram 的交互式订阅命令（`/subscribe` 等）尚未接入，订阅数据需通过数据库或后续 Bot 命令写入
* 内存缓存层（旧版 Telegram 项目的 AppCache）未迁移——当前查询量级下 SQLite 直查足够
