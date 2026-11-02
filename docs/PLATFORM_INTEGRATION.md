# 多平台接入指南

本文档说明如何将文章一键发布到多个内容平台。

---

## 平台支持状态总览

平台枚举 `PlatformType`（`app/models/platform.py:14-20`）共 **6 个**，`PlatformServiceFactory`（`app/services/platforms/__init__.py:18-24`）已注册全部 6 个实现。

| 平台 | 状态 | 认证方式 | 官方文档 | IP 白名单 |
|------|------|----------|----------|-----------|
| 微信公众号 | ✅ 已完成 | app_id + app_secret | - | ✅ 需配置 |
| 今日头条 | ✅ 已完成 | client_key + client_secret | open.toutiao.com | ✅ 需配置 |
| 百度百家号 | ✅ 已完成（骨架） | BDUSS + ptoken | 无公开文档 | ⚠️ 需确认 |
| 小红书 | ✅ 已完成 | xhs_token（Cookie） | 无官方 API | ⚠️ 无统一入口 |
| 抖音 | ✅ 已完成 | client_key + client_secret | open.douyin.com | ✅ 需配置 |
| YouTube | ⚠️ 仅热榜 | api_key + channel_id | developers.google.com/youtube/v3 | ⚠️ 无统一入口 |

---

## 一、微信公众号

### 认证凭证

| 字段 | 说明 |
|------|------|
| `app_id` | 微信公众平台 → 设置与开发 → 基本配置 |
| `app_secret` | 同上页面获取 |

### IP 白名单

登录 [微信公众平台](https://mp.weixin.qq.com/) → 设置与开发 → 基本配置 → IP 白名单，添加本服务器出口 IP。

可在系统「平台 IP 白名单」页面点击「检测本机 IP」获取。

### 限制说明

- **订阅号**：无模板消息权限，只能发客服消息（48h 窗口）
- **每日群发配额**：个人订阅号 1 篇/天，认证号 4 篇/天
- 超出配额时文章保留为草稿，需人工在公众号后台群发

---

## 二、今日头条

### 认证凭证

| 字段 | 说明 |
|------|------|
| `client_key` | 字节跳动开放平台 → 应用管理 → Client ID |
| `client_secret` | 同上页面获取 |

### 获取步骤

1. 访问 [open.toutiao.com](https://open.toutiao.com/)
2. 注册/登录开发者账号，完成企业或个人认证
3. 创建应用（需审核通过）
4. 在「应用管理」→ 「基本信息」获取 `Client ID` 和 `Client Secret`

### IP 白名单

登录字节跳动开放平台 → 应用管理 → 安全设置 → IP 白名单，添加本服务器出口 IP。

### 接口说明

| 接口 | 用途 |
|------|------|
| `POST /douyin/v1/article/create/` | 直接发布图文文章 |
| `POST /douyin/v1/article/draft/create/` | 创建草稿（可人工编辑后再发） |
| `GET /hot/search/hot_word/list/` | 爬取头条热榜 |

### 代码实现

- Service: `app/services/platforms/toutiao_service.py`
- Consumer: `consumers/toutiao_publish_consumer.py`

---

## 三、百度百家号

### 认证凭证

| 字段 | 说明 |
|------|------|
| `bduss` | 百度账号登录态 Cookie（必需） |
| `ptoken` | 同上，为保险起见两个都填 |

### BDUSS 获取方式

1. 登录 [baijiahao.baidu.com](https://baijiahao.baidu.com/)
2. 打开浏览器开发者工具（F12）→ Network（网络）面板
3. 刷新页面，找到任意请求（通常是 `getBuilderFeedList`）
4. 在请求的 Request Headers 或 Cookie 中找到：
   - `BDUSS`：百度账号登录态主 Token
   - `ptoken`：百度账号辅助 Token
5. 将两个值完整复制，填入系统「平台账号管理」

> 注意：BDUSS 是登录态 Cookie，有有效期（通常数天至数周不等），过期后需重新获取。**建议定期检查**，失效后百家号相关操作会失败。

### IP 白名单

百家号**可能**需要配置 IP 白名单，但百度后台入口不统一。如果发布时提示权限错误，请尝试：
1. 在系统「平台 IP 白名单」页面检测本机 IP
2. 尝试将 IP 添加到百度相关后台（无统一入口）

### 接口说明（基于抓包分析，无官方文档）

| 接口 | 用途 |
|------|------|
| `POST /builderinner/api/content/article/create` | 发布文章 |
| `POST /builderinner/api/content/article/draft/create` | 创建草稿 |
| `GET /builderinner/api/resource/builderfeed/getBuilderFeedList` | 爬取热榜 |

> ⚠️ 百家号无公开 API 文档，以上接口基于抓包分析。接口可能随百度更新而变化，建议先用测试账号验证。

### 代码实现

- Service: `app/services/platforms/baijiahao_service.py`

---

## 四、小红书

### 状态说明

小红书已实现**热榜爬取 + 发布笔记 + 草稿接口**（`app/services/platforms/xiaohongshu_service.py`）。

| 功能 | 状态 | 说明 |
|------|------|------|
| 热榜爬取 | ✅ 支持 | 通过 Cookie 认证，接口可能失效 |
| 发布文章 | ✅ 支持 | `publish_article()`（`xiaohongshu_service.py:129`），发布前先校验凭证有效性 |
| 创建草稿 | ⚠️ 占位 | `create_draft()`（`xiaohongshu_service.py:198`）——小红书无草稿概念，固定返回 `None` |

### 认证凭证

| 字段 | 说明 |
|------|------|
| `xhs_token` | 小红书登录态 Cookie，可在浏览器开发者工具中获取 |

### 限制说明

- 小红书官方发布 API 需要**创作者资质**，个人版本较难申请；`publish_article()` 会先调 `validate_credentials()`（用户信息接口）校验登录态，凭证失效直接返回失败。
- 即使 token 有效，无资质时也需走第三方平台代理发布（新榜、秒播等），或申请 `creators.xiaohongshu.com` 资质。

---

## 五、抖音

### 认证凭证

| 字段 | 说明 |
|------|------|
| `client_key` | 抖音开放平台 → 应用管理 → Client ID |
| `client_secret` | 同上页面获取 |

### 接口说明

Service：`app/services/platforms/douyin_service.py`，使用 `client_credentials` 模式换取 access_token（Redis 缓存 key `douyin:access_token:{account_id}`）。

| 接口 | 用途 |
|------|------|
| `POST /oauth/client_token` | 获取 access_token |
| `GET /douyin/v1/hot_search/bolton/` | 爬取抖音热榜 |
| `POST /douyin/v1/video/upload/` | 发布视频（`publish_article()`，`douyin_service.py:138`） |
| `POST /douyin/v1/message/custom/send/` | 客服消息（`send_customer_message()`） |

### IP 白名单

登录抖音开放平台 → 应用管理 → 安全设置 → IP 白名单，添加本服务器出口 IP。

### 限制说明

- 发布走**视频上传**接口（`video/upload/`），需同时提供视频文件与 `video_data`，纯图文文章不适用。
- `create_draft()`（`douyin_service.py:194`）为骨架实现。

---

## 六、YouTube

### 认证凭证

| 字段 | 说明 |
|------|------|
| `api_key` | Google Cloud Console → APIs & Services → Credentials |
| `channel_id` | 频道 ID（爬取热门时用于按频道过滤） |

### 接口说明

Service：`app/services/platforms/youtube_service.py`，使用 YouTube Data API v3。

| 接口 | 用途 |
|------|------|
| `GET /youtube/v3/videos`（`chart=mostPopular`） | 爬取热门视频热榜 |
| `POST /youtube/v3/videos`（`videos().insert()`） | 发布视频，需要 OAuth 2.0 |

### 限制说明

- **目前仅热榜可用**：`publish_article()`（`youtube_service.py:112`）是预留接口 —— YouTube 主要发布视频，需要 OAuth 2.0 授权与视频文件，未实现 OAuth 流程时直接返回失败提示。
- `create_draft()`（`youtube_service.py:142`）固定返回 `None`（YouTube 无文章草稿概念）。
- `send_customer_message()` 固定返回 `False`（不支持主动消息）。

---

## 七、平台账号配置方法

### 添加账号

1. 进入系统「平台账号管理」页面
2. 点击「添加账号」
3. 选择平台类型（微信公众号 / 今日头条 / 百度百家号 / 小红书 / 抖音 / YouTube）
4. 填入对应凭证（见上方各平台说明）
5. 保存

### 发布文章到指定平台

1. 在「深度文章」页面生成文章
2. 点击文章行的「发布到平台」按钮
3. 在弹窗中选择目标平台和账号
4. 点击「确认发布」
5. 文章通过 Kafka 异步投递，后台消费者完成实际 API 调用
6. 成功后在文章详情中显示 `platform_media_id`

### 多账号发布

支持配置多个同平台账号，实现"不同账号发不同文章"：
- 在「平台账号管理」添加多个账号
- 发布时在弹窗中选择目标账号即可

---

## 八、IP 白名单配置

所有平台发布前，**强烈建议先检测并配置 IP 白名单**，否则可能被平台拒绝。

### 检测本机 IP

进入「平台 IP 白名单」页面 → 点击「检测本机 IP」→ 复制显示的 IP 地址。

### 配置各平台白名单

| 平台 | 配置入口 |
|------|----------|
| 微信公众号 | mp.weixin.qq.com → 设置与开发 → 基本配置 → IP 白名单 |
| 今日头条 | open.toutiao.com → 应用管理 → 安全设置 → IP 白名单 |
| 百度百家号 | 可能需要联系百度客服或在各产品线后台分别配置 |
| 小红书 | 无公开白名单配置入口 |
| 抖音 | open.douyin.com → 应用管理 → 安全设置 → IP 白名单 |
| YouTube | 无公开白名单配置入口 |

---

## 九、架构说明

### 发布流程

```
用户点击「发布到平台」
    ↓
POST /api/v1/articles/{id}/publish-to-platform
    ↓
Kafka: kafka_topic_publish（投递消息）
    ↓
toutiao_publish_consumer 消费（所有带 platform_account_id 的消息）
    ↓
ArticlePublishingService.publish_to_platform()
    ↓
PlatformServiceFactory.get_service(account)
    ↓
WeChatPlatformService / ToutiaoPlatformService / BaijiahaoPlatformService
XiaohongshuPlatformService / DouyinPlatformService / YouTubePlatformService
    ↓
调用平台 API → 回写 platform_media_id
```

### 关键文件

| 文件 | 说明 |
|------|------|
| `app/services/platforms/__init__.py` | 平台服务工厂（注册 6 个平台） |
| `app/services/platforms/wechat_service.py` | 微信公众号 Service（`platform_accounts` 凭据 → `app/services/wechat_core.py` 的薄适配器）|
| `app/services/wechat_core.py` | 微信 HTTP 语义的**唯一实现**（与凭据来源无关，两个微信入口共用）|
| `app/services/wechat_publish.py` | 微信「建草稿 → 群发」统一流程 + 封面解析 + 配额统计 |
| `app/services/platforms/toutiao_service.py` | 头条 Service |
| `app/services/platforms/baijiahao_service.py` | 百家号 Service |
| `app/services/platforms/xiaohongshu_service.py` | 小红书 Service（热榜 + 发布笔记） |
| `app/services/platforms/douyin_service.py` | 抖音 Service |
| `app/services/platforms/youtube_service.py` | YouTube Service（热榜） |
| `app/services/platforms/base.py` | PlatformService 基类 + `PublishResult` / `CrawledTopic` |
| `app/services/platforms/crawlers.py` | 按平台类型选爬虫的 `CrawlerFactory` |
| `app/services/article_publishing.py` | 统一发布 Service（含 `publish_to_platform`） |
| `consumers/toutiao_publish_consumer.py` | 多平台发布 Consumer |
| `app/api/v1/articles.py` | 发布 API 端点 |
| `app/models/platform.py` | `Platform` / `PlatformAccount` / `PlatformType` 模型 |
