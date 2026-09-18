# 微信小程序接入

`upupup/` 小程序与 Web 共享同一个 canonical business user、RBAC、个人资源 owner scope 和服务端功能额度。小程序通过微信 `wx.login`/`code2session` 建立 Bearer session，并仅访问 `/api/v1/miniapp/*`；Web 通过邮箱密码登录建立 HttpOnly `dsa_user_session` Cookie。两类会话不可互换：小程序路径严格不接收 Web Cookie；通用 `/api/v1/*` 由路由 RBAC policy 接受其中一种合法 principal，若同时携带有效 Web Cookie 与 Bearer token，返回 `400 authentication_conflict`。

管理能力由权限码判定，例如 `rbac.manage`。`admin` 只是 RBAC 角色：它不是独立登录域，也不绕过个人资源的 owner scope。

## 后端配置

在后端部署环境中配置：

```dotenv
WEB_USER_SESSION_TTL_SECONDS=604800
WECHAT_OPEN_WEB_STATE_TTL_SECONDS=300
WECHAT_MINIAPP_APP_ID=
WECHAT_MINIAPP_APP_SECRET=
WECHAT_MINIAPP_CODE2SESSION_TIMEOUT_SECONDS=8
WECHAT_MINIAPP_SESSION_TTL_HOURS=720
CORS_ORIGINS=https://example.com
```

`CORS_ORIGINS` 控制浏览器 API 的可信 Origin。`WECHAT_OPEN_WEB_STATE_TTL_SECONDS` 仅用于「显式身份绑定挑战（identity-bind）」的短期 state 有效期，与登录方式无关。微信 AppSecret 只能保存在后端部署环境，不能进入小程序源码、Web 构建、日志或错误响应。只有在单层且完全可信的反向代理拓扑中才设置 `TRUST_X_FORWARDED_FOR=true`。

小程序调用 `wx.login` 获取一次性 code，后端使用微信 `code2session` 解析身份后创建或复用 canonical user，并签发随机 Bearer session。数据库只保存会话令牌 hash，不保存原始 token，也不持久化微信 `session_key`。

身份记录按 `(provider, issuer, subject)` 区分：小程序 provider 为 `wechat_miniapp`，issuer 为对应 AppID，subject 为 OpenID；持有可信 UnionID 时以 `(wechat_unionid, wechat, unionid)` 作为跨端桥接身份。服务端再将身份解析到 canonical user，并以该 user ID 执行 owner-scope 校验。OpenID、UnionID、code、access token、refresh token、AppSecret 与原始 Bearer token 不得回传给客户端或写入日志；昵称和头像只用于展示，不参与身份、RBAC 或资源归属判断。

微信隐私规则不允许在 `onLaunch`/`onLoad` 中静默读取昵称和头像。首次身份登录仍会自动完成；若用户资料为空，登录后可使用微信官方头像昵称填写能力补充资料，也可跳过，之后可在“我的 → 更新微信资料”重新填写。头像临时文件通过认证上传端点保存到 SQLite 数据文件同目录的 `miniapp_avatars/`；API 只接受不超过 2MB、最大 4096×4096 且总像素不超过 1600 万的单帧 JPEG、PNG 或 WebP，服务端完整解码并重新编码后才公开读取。

## Web 登录方式

Web 端通过**邮箱 + 密码登录**建立 `dsa_user_session` Cookie 会话并走与小程序同一套 RBAC。小程序内已登录用户先在「个人设置 → Web 登录邮箱」用邮件验证码绑定邮箱和密码，再在 Web 端凭邮箱密码登录。邮箱仅作登录标识与找回入口，不参与账号自动合并；密码只保存 pbkdf2-hmac-sha256 派生摘要。

### 邮箱密码绑定与登录

- 小程序绑定（Bearer + `account.self`）：
  - `GET /api/v1/miniapp/auth/email`：返回当前用户绑定状态 `{email, email_verified, has_password, report_email_enabled}`（仅本人可见，不返回密码相关摘要）。
  - `PATCH /api/v1/miniapp/auth/email/report-delivery`：提交 `{enabled}`，切换"生成的报告是否发送到已绑定邮箱"（未绑定邮箱返回 `400`）。
  - `POST /api/v1/miniapp/auth/email/request-code`：提交 `{email}`，向该邮箱发送验证码。邮件通道未配置（缺 `EMAIL_SENDER`/`EMAIL_PASSWORD`）返回 `503`；邮箱已被其他账号绑定返回 `409`；发送过于频繁返回 `400`。验证码复用邮件通知通道，限时、限次、限频，只保存摘要。
  - `POST /api/v1/miniapp/auth/email/bind`：提交 `{email, code, password}`，校验验证码后写入邮箱+密码凭据；对同一用户重复绑定即重置密码。密码长度 8-128。
- 报告邮件投递：用户在小程序生成的个股分析报告，其邮件通知只发送到该用户绑定的邮箱（受"报告发送到邮箱"开关控制）；未绑定邮箱或关闭开关时跳过邮件，且不回退到全局收件人。定时任务/大盘复盘等无用户归属（global）的报告仍按全局 `EMAIL_RECEIVERS` 投递。其它通知渠道（企业微信/飞书等）不受此影响，仍按现有配置广播。

- Web 登录（无既有登录态的浏览器）：
  - `POST /api/v1/web-auth/password/login`：提交 `{email, password}`，成功签发 `dsa_user_session` 并返回 `{user, csrf_token}`；失败统一返回 `401 {error:"invalid_credentials"}`，不区分邮箱不存在、密码错误、邮箱未验证或账号停用；浏览器已有登录态时返回 `400 authentication_conflict`。

## 认证后的 Web API

所有 Web 认证响应使用 `Cache-Control: no-store`：

| 路由 | 认证/约束 | 作用 |
| --- | --- | --- |
| `GET /api/v1/web-auth/me` | Web Cookie | 返回 `{user:{id,nickname,avatar_url,roles,permissions}, csrf_token}` |
| `POST /api/v1/web-auth/logout` | Web Cookie + exact Origin + `X-CSRF-Token` | 撤销当前 Web session |
| `POST /api/v1/web-auth/identity-bind/start` | Web Cookie | 创建受控身份绑定 challenge |
| `POST /api/v1/web-auth/identity-bind/consume` | Web Cookie | 消费已批准的 challenge，或返回冲突/状态结果 |

Web Cookie 的不安全请求（`POST`、`PUT`、`PATCH`、`DELETE`）必须同时携带会话对应的 `X-CSRF-Token` 和与默认本地开发地址或 `CORS_ORIGINS` 精确匹配的 `Origin`。`*` 不是可信 Origin。Cookie 属性以实际运行时实现为准；不要把未经实现验证的 `Strict`、path-scoped 等属性写成契约。

## 小程序 API 与认证边界

- `POST /api/v1/miniapp/auth/login`：提交 `{ "code": "wx.login code" }`，返回 Bearer token、expiry、用户摘要、roles 与 permissions。
- `GET /api/v1/miniapp/auth/me`：返回当前用户摘要，包括可选的 `nickname`、`avatar_url`、roles 和 permissions。
- `PATCH /api/v1/miniapp/auth/me`：当前用户更新自己的可选昵称；固定使用 Bearer principal 的用户 ID，不接受 OpenID、角色、权限或 owner 字段。
- `POST /api/v1/miniapp/auth/me/avatar`：认证上传当前用户通过 `chooseAvatar` 选择的头像。
- `GET /api/v1/miniapp/auth/email`：返回当前用户的 Web 邮箱登录绑定状态 `{email, email_verified, has_password, report_email_enabled}`。
- `PATCH /api/v1/miniapp/auth/email/report-delivery`：以 `{enabled}` 切换"报告发送到邮箱"开关（Web Cookie 会话亦可访问，需 `account.self` + CSRF）。
- `POST /api/v1/miniapp/auth/email/request-code`：向 `{email}` 发送 Web 登录绑定验证码（邮件通道未配置返回 `503`，邮箱被占用返回 `409`）。
- `POST /api/v1/miniapp/auth/email/bind`：以 `{email, code, password}` 绑定或重置 Web 登录邮箱密码。
- `GET|HEAD /api/v1/miniapp/auth/public/avatars/{opaque_filename}`：按合法、不可预测的文件名读取公开头像；不列出文件、不接受写方法，也不使用可枚举的用户 ID。
- `POST /api/v1/miniapp/auth/logout`：撤销当前会话。
- `POST /api/v1/miniapp/auth/identity-bind/approve`：以当前小程序 Bearer principal 批准 Web 创建的绑定 challenge。
- `GET /api/v1/miniapp/watchlist`：列出当前用户的个人自选股（owner-scope，返回 `items` 与 `stock_codes`）。
- `POST /api/v1/miniapp/watchlist/add`：将 `{ stock_code, stock_name? }` 加入当前用户自选；代码非法返回 `400`，HK 等价变体按归一 key 去重，单用户上限 200。
- `POST /api/v1/miniapp/watchlist/remove`：从当前用户自选移除 `{ stock_code }`，返回移除后的最新列表。
- `GET /api/v1/analysis/gallery`：报告展览——跨用户列出当天生成的个股分析报告（排除大盘复盘 `code=MARKET`/`report_type=market_review`），支持 `search`（股票代码或名称）、`page`、`limit`（≤50）；仅返回摘要与生成者昵称，不含 openid/unionid。需 `analysis.read`（普通成员可见）。
- `GET /api/v1/analysis/gallery/{record_id}`：报告展览详情——按主键返回当天个股报告的 Markdown 全文（跨用户可见，排除大盘复盘）。
- `GET /api/v1/miniapp/daily-reflections`：分页列出当前用户心得。
- `GET /api/v1/miniapp/daily-reflections/stats`：连续打卡与月度回顾统计（owner-scope）。可选 `reference_date`（客户端本地今天）与 `month`（YYYY-MM），返回 `total`、`current_streak`、`longest_streak`、`today_done`、`month`、`month_count`、`month_days`。连续天数以客户端日历为准；今日未记但昨日已记时按昨日起算，避免跨时区误断。
- `GET /api/v1/miniapp/daily-reflections/by-date/{YYYY-MM-DD}`：读取指定日期心得，不存在时返回 `null`。
- `PUT /api/v1/miniapp/daily-reflections`：按日期新增或覆盖当前用户心得。
- `GET /api/v1/miniapp/daily-reflections/{id}`：读取当前用户的一条心得。
- `DELETE /api/v1/miniapp/daily-reflections/{id}`：删除当前用户的一条心得。

除登录端点、health 与合法不透明头像文件的公开 `GET|HEAD` 外，`/api/v1/miniapp/*` 必须携带微信登录得到的 `Authorization: Bearer <token>`，且严格不接受 Web Cookie。未认证返回 `401`，已认证但不满足路由权限返回 `403`；未登记权限策略的通用业务路由 fail closed。小程序客户端不得指定目标 user ID，也不得以 provider key、OpenID 或自定义 Bearer 替代会话。

小程序设置页只调用受限的 `/api/v1/miniapp/system/*` surface，提供经掩码的配置读取、schema/状态查询、校验与受版本保护的非 raw 更新。高风险系统、密钥、导入导出、调度、外部渠道测试和模型发现必须由各自独立、明确的权限策略保护；它们不因 Cookie、角色名或登录通道获得隐式放行。页面可按权限显示只读或可编辑状态，但后端仍执行最终权限校验。

## RBAC 与功能额度

登录和 `/me` 的用户摘要包含 `roles` 与 `permissions`。内置角色语义如下：

- `member`：新用户默认角色；可维护自己的会话、心得、个人自选股、持仓、告警和 Agent 会话，可使用被授予的分析、选股、回测、决策信号只读与历史（本人读/删，`history.read`/`history.delete`）能力；高成本能力受服务端每日功能额度限制。默认开放常规功能栏目，仅排除管理员专属能力——系统设置（`system.read`/`system.manage`）、权限管理（`rbac.manage`）、Token 用量（`usage.read`）、情报源（`intelligence.read`/`intelligence.manage`），以及外发通知（`alerts.notify`/`agent.share`）、全局数据维护（`stocks.manage`）等全局管控动作不授予普通成员。个人自选由 `watchlist.read`/`watchlist.manage` 控制，按 owner scope 隔离，与管理员维护的全局 `STOCK_LIST`（`stocks.manage`，驱动每日自动分析）相互独立。
- `operator`：受信任的运营分析员；拥有除 `system.manage`、`rbac.manage` 外的全部权限，包含 `alerts.notify` 与 `agent.share`。
- `admin`：拥有全部权限，包含 `alerts.notify`、`system.manage` 与 `rbac.manage`；仍不绕过个人资源 owner scope。

`/api/v1/rbac/*` 与 `/api/v1/miniapp/rbac/*` 都要求 `rbac.manage`。初始管理员只能通过受控的一次性 seed/角色授予流程赋予已存在 canonical user，并记录审计事件；不使用 OpenID 环境变量自动升权或回授。日常权限管理入口是小程序“我的 → 权限管理”：角色和成员管理会拒绝空/未知角色、删除仍被分配的自定义角色、停用当前操作者、移除当前操作者的 `rbac.manage`，以及停用或降权最后一位拥有该权限的活跃用户。

RBAC 决定用户能否调用功能；entitlement 决定已获授权用户当天可用次数。服务端只在无副作用业务准入校验通过后原子预留额度；客户端按钮、剩余次数或本地状态不能作为成本控制边界。实际额度严格按以下顺序解析：**`user_override` > `whitelist_feature` > `whitelist_all` > `plan` > `global_policy`**。无限额度仅来自功能级或全功能白名单；角色、`admin`、Cookie 或登录通道都不是额度豁免来源。

| 功能码 | 默认每日次数 |
| --- | ---: |
| `stock_analysis` | 5 |
| `market_review` | 2 |
| `agent_chat` | 20 |
| `agent_research` | 2 |
| `screening` | 3 |
| `backtest` | 3 |
| `decision_signal_reassess` | 5 |
| `decision_signal_outcomes` | 3 |
| `image_stock_extract` | 10 |

全局策略的 `0` 表示禁用，不表示无限制。单用户覆盖允许 `0..10000`；套餐、覆盖与白名单都可选使用 UTC 日期窗口，窗口结束后自动失效。`GET /api/v1/feature-quotas/me` 返回 `daily_limit`、`used_count`、`remaining`、`disabled`、`unlimited`、`reset_at`、`limit_source`、`plan_code` 与 `effective_until`；`limit_source` 仅为 `user_override`、`whitelist_feature`、`whitelist_all`、`plan` 或 `global_policy`。拒绝预留时响应为 `429 feature_quota_exceeded`：`reason="feature_disabled"` 表示规则显式禁用，`reason="daily_limit_exceeded"` 表示当日额度已用尽，客户端必须给出不同的可解释提示。

配额按 UTC 自然日结算；`period_start` 与 `reset_at` 以 UTC 表示，`reset_at` 为下一 UTC 日的 `00:00:00Z`。异步任务只在 executor 接受前失败时补偿尚未被接受的预留；运行失败、取消或外部数据/LLM 失败不会自动退款。

## 个人资源与行级隔离

RBAC 控制“能否使用能力”，owner scope 控制“能访问哪一条数据”，两者必须同时满足。每日心得、持仓、告警、Agent Chat、分析任务和分析历史都按可信 `principal.user_id` 过滤。请求体、模型工具参数或客户端传入的用户字段不能扩大资源可见范围。

个人资源只在 `owner_user_id == principal.user_id` 时可见和可改；跨用户请求 fail closed，通常返回 `404`，避免泄露资源是否存在。`rbac.manage`、`admin` 角色和 Web Cookie 都不自动扩大个人资源范围。仅受信内部维护调用可在明确设计的独立 scope 下执行跨用户维护；任何缺失或未绑定 scope 都必须 fail closed。

## 数据与迁移

首次启动后端会在 `DATABASE_PATH` 指定的 SQLite 文件中创建用户、session、RBAC、审计、心得和功能额度相关表。既有 SQLite 启动迁移保持幂等：不会自动提升用户角色、自动合并 canonical user、迁移或暴露身份凭据，也不会把历史资源自动归属给某个用户。