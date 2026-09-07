# 融合飞书和 Telegram 监控项目 PRD

## 项目概述

融合两个 Linux.do 论坛监控项目：
1. **linuxdo-feishu-bot**: 基于 RSS 的飞书通知机器人（简单版）
2. **linux-do-keyword-monitor**: 基于 Telegram 的关键词订阅机器人（完整版）

目标是创建一个统一的多渠道通知系统，支持飞书和 Telegram 双通道，提供灵活的订阅管理。

---

## 当前项目分析

### Project 1: linuxdo-feishu-bot (简单飞书版)

**核心功能**:
- RSS feed 轮询 (Linux.do latest.rss)
- 关键词匹配过滤
- 飞书 Webhook 推送
- SQLite 去重 (posts 表)
- 日志轮转和数据库清理

**特点**:
- 单文件实现 (app.py)
- 配置简单 (config.json)
- Docker 部署
- **推送模式**: 广播式（单个飞书 Webhook，所有匹配都推送到同一个群）
- **订阅**: 无个性化订阅，仅支持全局关键词列表

**限制**:
- 不支持多用户订阅
- 不支持订阅特定作者
- 无 Web 管理界面
- 无统计功能

### Project 2: linux-do-keyword-monitor (完整 Telegram 版)

**核心功能**:
- 双数据源支持: RSS + Discourse API (带 Cookie)
- Telegram Bot 交互式订阅
- 多论坛支持 (Linux.do, NodeSeek)
- 多用户个性化订阅管理
- 正则表达式关键词匹配
- 订阅特定用户的帖子
- 订阅所有新帖 (subscribe_all)
- Web 配置管理页面 + SQL 查询页面
- Cookie 失效自动降级 + 管理员告警
- 关键词热度统计

**架构深度分析**:

**1. 数据源抽象 (source/)**:
```python
BaseSource (抽象类)
├── RSSSource: 解析 RSS feed
└── DiscourseSource: 调用 Discourse JSON API (需要 Cookie)
    ├── 支持 Cloudflare 绕过 (FlareSolverr / DrissionPage)
    ├── Cookie 自动检测和降级
    └── 可获取更多字段 (作者、分类)
```

**2. 通知抽象 (bot/)**:
```python
TelegramBot
├── 消息发送带重试机制 (网络超时自动重试)
├── 自动检测用户封禁状态 (Forbidden 异常 -> 标记 blocked_users)
├── 批量发送 (并发控制，避免 Telegram 速率限制)
└── 富文本格式 (HTML, 带样式和链接按钮)
```

**3. 应用核心 (app.py Application 类)**:
```python
Application (单论坛实例)
├── 组件编排
│   ├── TelegramBot: 通知发送
│   ├── BaseSource: 数据拉取
│   ├── KeywordMatcher: 关键词/正则匹配
│   ├── Database: 数据持久化
│   ├── AppCache: 内存缓存 (可选)
│   └── AsyncIOScheduler: 定时任务
│
├── 定时任务
│   ├── fetch_and_notify: 主轮询任务 (拉取帖子 + 推送通知)
│   └── _check_cookie_task: Cookie 健康检测任务 (仅 Discourse)
│
├── 热更新
│   └── reload_config: 运行时重载配置，更新数据源和定时间隔
│
└── 容错机制
    ├── Cookie 失效自动降级 (Discourse -> RSS fallback)
    ├── 拉取失败重试 + 管理员告警
    └── 批量发送失败处理
```

**4. 数据库设计**:
```sql
-- 用户和订阅 (支持多论坛)
users(chat_id, forum, created_at)
subscriptions(id, chat_id, keyword, forum, created_at)
user_subscriptions(id, chat_id, author, forum, created_at)
subscribe_all(chat_id, forum, created_at)

-- 内容去重
posts(id, forum, title, link, pub_date, author, category_id)
notifications(chat_id, post_id, keyword, forum, created_at)

-- 管理
blocked_users(chat_id, forum, blocked_at)
categories(id, forum, name, slug, description)
schema_version(version, applied_at)
```

**5. 配置系统 (config.py)**:
- `ForumConfig`: 单论坛配置 (bot_token, source_type, rss_url, discourse_url, etc.)
- `AppConfig`: 全局配置 (forums[], admin_chat_id, sql_admin_password)
- 向后兼容旧版单论坛配置 (自动转换)

**6. 缓存系统 (cache.py)**:
- `AppCache`: 可选的内存缓存，减少数据库查询
- 缓存内容: keywords, subscribers, subscribe_all_users, authors, author_subscribers
- 配置项: `forum_config.cache_enabled` (默认关闭，便于调试)

**7. Web 管理 (web_flask.py)**:
- `/linuxdo/config`: 论坛配置管理
- `/linuxdo/users`: 用户统计
- `/linuxdo/sql`: SQL 查询页面 (只读 / 管理员模式)
- 密码保护 + Cookie 测试工具

**架构优势**:
- ✅ **抽象清晰**: 数据源、通知、匹配、存储各层解耦
- ✅ **多论坛支持**: 一个进程管理多个论坛 Bot 实例
- ✅ **容错完善**: 重试、降级、告警机制齐全
- ✅ **可扩展性强**: 新增数据源/通知渠道只需实现抽象接口

---

## 融合方案

### 目标架构

创建统一的多渠道监控系统，**保留 Telegram 版的完整架构**，扩展飞书作为新的通知渠道。

### 核心设计原则

1. **保留 Telegram 版的分层架构** (source / notifier / matcher / database)
2. **通知渠道抽象化**: 将 `TelegramBot` 重构为 `BaseNotifier` 接口
3. **添加 `FeishuNotifier`**: 实现飞书 Webhook 通知
4. **配置灵活**: 论坛可配置使用 Telegram / 飞书 / 双通道
5. **向后兼容**: 保持 Telegram Bot 命令和功能不变
6. **数据库扩展**: 支持多渠道用户订阅

### 推荐技术方案

基于对 Telegram 版架构的深入理解，建议采用以下方案：

**1. 通知渠道抽象**

```python
# notifier/base.py
class BaseNotifier(ABC):
    """抽象通知渠道接口"""
    
    @abstractmethod
    async def send_notification(
        self, 
        user_id: str,           # 通用用户标识
        title: str, 
        link: str, 
        keyword: str,
        category_name: Optional[str] = None
    ) -> bool:
        """发送关键词匹配通知"""
        pass
    
    @abstractmethod
    async def send_notification_all(
        self,
        user_id: str,
        title: str,
        link: str,
        category_name: Optional[str] = None
    ) -> bool:
        """发送订阅全部通知"""
        pass
    
    @abstractmethod
    def get_channel_type(self) -> str:
        """返回渠道类型: telegram / feishu"""
        pass

# notifier/telegram.py
class TelegramNotifier(BaseNotifier):
    """现有 TelegramBot 重构为 Notifier"""
    # 保持现有所有功能：重试、封禁检测、批量发送
    
# notifier/feishu.py
class FeishuNotifier(BaseNotifier):
    """飞书 Webhook 通知实现"""
    # 支持飞书卡片消息
    # 广播模式 (单 webhook) 或个人模式 (多 webhook)
```

**2. 配置扩展**

```python
class NotifierConfig(BaseModel):
    """通知渠道配置"""
    notifier_type: str  # "telegram" | "feishu"
    
    # Telegram 配置
    telegram_bot_token: Optional[str] = None
    
    # Feishu 配置
    feishu_webhook_url: Optional[str] = None
    feishu_mode: str = "broadcast"  # "broadcast" | "personal"

class ForumConfig(BaseModel):
    # 现有字段...
    
    # 扩展：支持多通道
    notifiers: List[NotifierConfig] = Field(
        default_factory=list,
        description="Notification channels for this forum"
    )
    
    # 向后兼容旧配置
    bot_token: Optional[str] = None  # 如果设置，自动转为 telegram notifier
```

**3. Application 重构**

```python
class Application:
    def __init__(self, forum_config, db, ...):
        # 原有组件
        self.source = create_source(forum_config)
        self.matcher = KeywordMatcher()
        
        # 重构：多通知渠道
        self.notifiers: List[BaseNotifier] = []
        for notifier_config in forum_config.notifiers:
            if notifier_config.notifier_type == "telegram":
                self.notifiers.append(TelegramNotifier(...))
            elif notifier_config.notifier_type == "feishu":
                self.notifiers.append(FeishuNotifier(...))
    
    async def fetch_and_notify(self):
        posts = self.source.fetch()
        
        # 对每个帖子，遍历所有通知渠道
        for post in posts:
            # 关键词匹配
            matched_keywords = self.matcher.match(post.title, keywords)
            
            for notifier in self.notifiers:
                # 获取该渠道的订阅用户
                subscribers = self._get_channel_subscribers(
                    post, 
                    matched_keywords, 
                    notifier.get_channel_type()
                )
                
                # 批量发送
                await self._send_batch_for_channel(notifier, subscribers, post)
```

**4. 数据库扩展方案**

保持现有表结构，扩展 `channel` 字段支持多渠道：

```sql
-- 用户表扩展
ALTER TABLE users ADD COLUMN channel TEXT DEFAULT 'telegram';
ALTER TABLE users DROP CONSTRAINT users_pkey;
ALTER TABLE users ADD PRIMARY KEY (chat_id, forum, channel);

-- 订阅表扩展
ALTER TABLE subscriptions ADD COLUMN channel TEXT DEFAULT 'telegram';
ALTER TABLE user_subscriptions ADD COLUMN channel TEXT DEFAULT 'telegram';
ALTER TABLE subscribe_all ADD COLUMN channel TEXT DEFAULT 'telegram';

-- notifications 已经有 (chat_id, post_id, keyword, forum) 联合主键
-- 扩展为包含 channel
ALTER TABLE notifications ADD COLUMN channel TEXT DEFAULT 'telegram';
ALTER TABLE notifications DROP CONSTRAINT notifications_pkey;
ALTER TABLE notifications ADD PRIMARY KEY (chat_id, post_id, keyword, forum, channel);

-- blocked_users 扩展
ALTER TABLE blocked_users ADD COLUMN channel TEXT DEFAULT 'telegram';
ALTER TABLE blocked_users DROP CONSTRAINT blocked_users_pkey;
ALTER TABLE blocked_users ADD PRIMARY KEY (chat_id, forum, channel);
```

数据库迁移脚本会自动为现有数据填充 `channel='telegram'`。

---

## 需要明确的问题点

### 1. 飞书推送模式设计 🔴 **（关键决策）**

**问题**: 飞书 Webhook 是群组级别的，如何实现个性化订阅？

**推荐方案 B: 飞书广播模式** ✅

理由：
1. **简单直接**: 与现有 `linuxdo-feishu-bot` 逻辑一致
2. **使用场景匹配**: 飞书主要用于团队共享通知，不是个人订阅场景
3. **技术可行**: 不需要获取飞书用户 Open ID
4. **易于配置**: 只需配置一个 Webhook URL

实现细节：
```python
class FeishuNotifier(BaseNotifier):
    def __init__(self, webhook_url: str, keywords: List[str]):
        self.webhook_url = webhook_url
        self.keywords = keywords  # 全局关键词列表
    
    async def send_notification(self, title, link, keyword, ...):
        # 发送到群组 webhook，无个人定向
        # 使用飞书卡片消息，显示匹配的关键词
```

**数据库表现**:
- 飞书不创建 `users` 记录（无 chat_id 概念）
- 飞书订阅存储在配置文件中，不在数据库 `subscriptions` 表
- 或者创建一个虚拟用户 `chat_id=-1, channel='feishu'` 存储全局订阅

**备选方案 A/C**: 如果未来需要个人订阅，可以：
- 方案 A: 飞书卡片中 @用户 (需要维护 Open ID 映射)
- 方案 C: 升级为飞书 Bot (需要 App ID/Secret，复杂度高)

### 2. 通知渠道配置粒度 🟡

**推荐 Option A: 论坛级别配置** ✅

```json
{
  "forums": [
    {
      "forum_id": "linux-do",
      "name": "Linux.do",
      "notifiers": [
        {
          "notifier_type": "telegram",
          "telegram_bot_token": "123456:ABC..."
        },
        {
          "notifier_type": "feishu",
          "feishu_webhook_url": "https://open.feishu.cn/...",
          "feishu_keywords": ["Docker", "NAS", "求助"]
        }
      ],
      "source_type": "rss",
      "rss_url": "https://linux.do/latest.rss",
      "fetch_interval": 60
    }
  ],
  "admin_chat_id": 123456789
}
```

理由：
- 简洁清晰，每个论坛配置独立
- 支持同一论坛多通道推送
- 向后兼容：`bot_token` 自动转为 telegram notifier

### 3. 用户订阅数据模型 🟡

**推荐方案 A: 扩展现有表（向后兼容）** ✅

```sql
-- 所有表添加 channel 字段，默认 'telegram'
-- 主键扩展为包含 channel

-- 示例
ALTER TABLE users ADD COLUMN channel TEXT DEFAULT 'telegram';
ALTER TABLE subscriptions ADD COLUMN channel TEXT DEFAULT 'telegram';
```

理由：
1. **最小侵入**: 不改变表结构，只增加字段
2. **向后兼容**: 现有 Telegram 数据自动填充 `channel='telegram'`
3. **迁移简单**: 一个迁移脚本搞定

**飞书的特殊处理**:
- 飞书广播模式下，不创建个人订阅记录
- 全局关键词存储在 `ForumConfig.notifiers[].feishu_keywords`
- 或创建虚拟用户: `chat_id=-1, channel='feishu', forum=<forum_id>`

### 4. 飞书消息格式 ✅ **（技术细节）**

**推荐：飞书交互式卡片** 

参考 Telegram 版的消息格式，使用飞书卡片 API：

```python
{
  "msg_type": "interactive",
  "card": {
    "header": {
      "title": {
        "content": "🔔 Linux.do 新帖提醒",
        "tag": "plain_text"
      }
    },
    "elements": [
      {
        "tag": "div",
        "text": {
          "content": f"**📌 匹配关键词**: {keyword}",
          "tag": "lark_md"
        }
      },
      {
        "tag": "div",
        "text": {
          "content": f"**📝 标题**\n{title}",
          "tag": "lark_md"
        }
      },
      {
        "tag": "action",
        "actions": [
          {
            "tag": "button",
            "text": {"content": "🔗 点击查看原帖", "tag": "plain_text"},
            "url": link,
            "type": "primary"
          }
        ]
      }
    ]
  }
}
```

可选升级：
- 添加分类标签
- 添加作者信息
- 添加时间戳

### 5. 配置管理和初始化 ✅

**保持现有机制**:
- ✅ CLI `linux-do-monitor init`: 交互式初始化
- ✅ Web 配置页面: 运行时修改
- ✅ 配置热更新: `Application.reload_config()`
- ✅ 迁移系统: `migrations.py` 自动升级数据库

**新增**:
- Web 配置页面增加 "通知渠道管理" 选项卡
- 支持在线添加/删除/测试通知渠道

### 6. 依赖处理 ✅

**统一依赖**:
```toml
[project]
dependencies = [
    "python-telegram-bot>=20.0",
    "feedparser>=6.0",
    "apscheduler>=3.10",
    "pydantic>=2.0",
    "click>=8.0",
    "flask>=3.0",
    "requests>=2.32.3",  # 飞书 Webhook 使用
]
```

无冲突，直接合并。

### 7. 部署方式 ✅

**统一为 Telegram 版的部署方式**:
- CLI: `linux-do-monitor run --web-port 8080`
- Docker: 提供 Dockerfile（基于 Telegram 版）
- Systemd: 提供 service 文件

**删除**: `linuxdo-feishu-bot` 的独立 Docker 配置

---

## 功能优先级与实施计划

### Phase 1: 架构重构 (不改变现有功能) - 2-3 天

**目标**: 将 `TelegramBot` 抽象为 `BaseNotifier` 接口，为多渠道做准备

**任务**:
- [ ] 创建 `notifier/` 目录和 `base.py` (BaseNotifier 抽象类)
- [ ] 重构 `bot/bot.py` -> `notifier/telegram.py` (TelegramNotifier)
  - 保持所有现有功能不变
  - 只是改名和目录调整
- [ ] 修改 `app.py` 中的引用: `TelegramBot` -> `TelegramNotifier`
- [ ] 运行测试，确保 Telegram 功能正常

**验收标准**:
- ✅ Telegram Bot 所有命令正常工作
- ✅ 消息推送、重试、封禁检测功能正常
- ✅ 无功能回归

### Phase 2: 飞书通知集成 (最小可用版本) - 3-4 天

**目标**: 实现飞书广播模式通知，配置文件支持多通道

**任务**:
- [ ] 实现 `notifier/feishu.py` (FeishuNotifier)
  - 广播模式
  - 飞书卡片消息格式
  - 基本错误处理
- [ ] 扩展 `config.py`:
  - `NotifierConfig` 模型
  - `ForumConfig.notifiers` 字段
  - 向后兼容 `bot_token` (自动转为 telegram notifier)
- [ ] 修改 `app.py`:
  - 支持多通道通知
  - 为飞书创建虚拟订阅逻辑
- [ ] 数据库迁移脚本:
  - 添加 `channel` 字段到所有相关表
  - 为现有数据填充 `channel='telegram'`
- [ ] 更新 CLI `init` 命令: 询问是否启用飞书通道

**验收标准**:
- ✅ 配置文件可以同时配置 Telegram + 飞书
- ✅ 飞书 Webhook 接收关键词匹配通知（卡片格式）
- ✅ Telegram 功能不受影响
- ✅ 数据库迁移平滑，无数据丢失

**示例配置**:
```json
{
  "forums": [
    {
      "forum_id": "linux-do",
      "name": "Linux.do",
      "notifiers": [
        {
          "notifier_type": "telegram",
          "telegram_bot_token": "123:ABC"
        },
        {
          "notifier_type": "feishu",
          "feishu_webhook_url": "https://open.feishu.cn/...",
          "feishu_keywords": ["Docker", "NAS"]
        }
      ],
      "source_type": "rss",
      "rss_url": "https://linux.do/latest.rss",
      "fetch_interval": 60
    }
  ]
}
```

### Phase 3: Web 管理界面扩展 - 2 天

**目标**: Web 配置页面支持管理通知渠道

**任务**:
- [ ] 扩展 `/linuxdo/config` 页面:
  - 显示当前配置的通知渠道列表
  - 添加/删除通知渠道
  - 测试飞书 Webhook（发送测试消息）
- [ ] 增加通知渠道状态监控:
  - 显示最后推送时间
  - 显示失败次数
  - 飞书 Webhook 测试连通性

**验收标准**:
- ✅ 可在 Web 界面添加飞书 Webhook
- ✅ 可测试飞书连通性
- ✅ 可查看各通道的推送状态

### Phase 4: 完善和文档 - 1-2 天

**任务**:
- [ ] 单元测试:
  - `FeishuNotifier` 测试
  - 多通道配置加载测试
  - 数据库迁移测试
- [ ] 更新 README:
  - 添加飞书配置说明
  - 添加多通道配置示例
  - 更新部署文档
- [ ] 示例配置文件:
  - `config.example.json` (包含 Telegram + 飞书)
- [ ] Docker 镜像:
  - 基于 Telegram 版 Dockerfile 构建
  - 支持环境变量配置
- [ ] 迁移指南:
  - 从旧版 `linuxdo-feishu-bot` 迁移到新版
  - 配置转换脚本

**验收标准**:
- ✅ 文档清晰，用户可按文档配置
- ✅ Docker 镜像可正常运行
- ✅ 测试覆盖率 > 70%

---

## 总工作量估算

- **Phase 1**: 2-3 天 (架构重构)
- **Phase 2**: 3-4 天 (飞书集成)
- **Phase 3**: 2 天 (Web 界面)
- **Phase 4**: 1-2 天 (文档和测试)

**总计**: 8-11 天 (约 2 周)

---

## 风险和缓解措施

### 风险 1: 数据库迁移失败
**缓解**: 
- 迁移前自动备份数据库
- 迁移脚本支持回滚
- 在测试环境充分验证

### 风险 2: Telegram 功能回归
**缓解**:
- Phase 1 重构时保持代码逻辑不变
- 完整的回归测试
- 逐步发布，先内部测试

### 风险 3: 飞书卡片格式兼容性
**缓解**:
- 参考飞书官方文档
- 多版本飞书客户端测试
- 提供文本模式降级

---

## 开放问题总结

所有关键决策已完成 ✅：

1. ✅ **飞书推送模式**: 采用广播模式，全局关键词列表
2. ✅ **通知渠道配置**: 论坛级别配置 (`ForumConfig.notifiers[]`)
3. ✅ **用户订阅数据模型**: 扩展现有表添加 `channel` 字段
4. ✅ **飞书消息格式**: 飞书交互式卡片
5. ✅ **配置管理**: 保持现有 CLI + Web 机制
6. ✅ **依赖管理**: 无冲突，直接合并
7. ✅ **部署方式**: 统一为 CLI + Docker

**无阻塞问题，可以直接开始实施** 🚀

---

## 下一步行动

### 立即开始

如果您同意上述技术方案，我将立即开始 **Phase 1: 架构重构**：

1. 创建 `notifier/` 目录结构
2. 定义 `BaseNotifier` 抽象接口
3. 重构 `TelegramBot` -> `TelegramNotifier`
4. 验证 Telegram 功能无回归

### 或者先讨论

如果您对以下任何决策有不同意见，请提出：
- 飞书是否需要个人订阅模式？
- 是否需要支持飞书 Bot (非 Webhook)？
- 数据库迁移方案是否可接受？
- 实施优先级和时间表是否合理？

**请告诉我：是直接开始实施，还是需要调整某些设计决策？**
