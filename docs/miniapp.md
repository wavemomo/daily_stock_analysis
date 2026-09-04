# 微信小程序接入

`upupup/` 小程序通过 `/api/v1/miniapp/*` 使用独立的微信用户身份，不复用 Web 管理后台的密码 Cookie。

## 后端配置

在后端 `.env` 中配置：

```dotenv
WECHAT_MINIAPP_APP_ID=wx...
WECHAT_MINIAPP_APP_SECRET=...
WECHAT_MINIAPP_CODE2SESSION_TIMEOUT_SECONDS=8
WECHAT_MINIAPP_SESSION_TTL_HOURS=720
RBAC_BOOTSTRAP_ADMIN_OPENIDS=openid-a,openid-b
```

`APP_SECRET` 只能保存在后端，不能写入小程序源码。小程序调用 `wx.login` 获取一次性 code，后端使用微信 `jscode2session` 换取 `openid`/`unionid`，随后在 SQLite 中创建或更新用户，并签发随机 Bearer 会话。数据库只保存会话令牌的 SHA-256 摘要，不保存原始令牌，也不持久化微信 `session_key`。`openid` 是服务端身份和 owner 绑定依据；昵称、头像仅是可选展示资料，不参与身份识别、RBAC 或资源归属。

微信隐私规则不允许在 `onLaunch`/`onLoad` 中静默读取昵称和头像。首次身份登录仍会自动完成；若用户资料为空且该设备上的当前用户尚未完成或跳过资料引导，登录页会在身份验证后显示微信官方头像昵称填写能力：`button open-type="chooseAvatar"` 选择头像、`input type="nickname"` 填写或使用微信昵称，也可跳过。完成或跳过状态按用户 ID 保存在当前设备，之后可在“我的 → 更新微信资料”重新填写。头像临时文件通过认证上传端点保存到 SQLite 数据文件同目录的 `miniapp_avatars/`；API 只接受不超过 2MB、最大 4096×4096 且总像素不超过 1600 万的单帧 JPEG、PNG 或 WebP，服务端完整解码并重新编码后才公开读取。

小程序的后端服务地址在 `upupup/utils/config.js` 的 `BACKEND_BASE_URL` 中配置，URL 可直接包含端口，例如 `http://127.0.0.1:8000`；页面不提供运行时修改入口。后端监听端口优先使用 `python3 main.py --serve-only --port <端口>` 的 CLI 参数，未传时读取 `.env` 的 `WEBUI_PORT`（默认 `8000`）；监听地址同理由 `--host` 或 `WEBUI_HOST` 控制。真机联调需让后端监听 `0.0.0.0`，并把 `BACKEND_BASE_URL` 改为电脑局域网 IP；真机、体验版和正式版必须使用 HTTPS，并在微信公众平台配置 request 合法域名。

## API

- `POST /api/v1/miniapp/auth/login`：提交 `{ "code": "wx.login code" }`，返回 Bearer token 和用户摘要。
- `GET /api/v1/miniapp/auth/me`：返回当前用户摘要，包括可选的 `nickname`、`avatar_url` 和 RBAC 权限。
- `PATCH /api/v1/miniapp/auth/me`：当前用户更新自己的可选昵称；固定使用 Bearer principal 的用户 ID，不接受 openid、角色、权限或 owner 字段，要求 `account.self`。
- `POST /api/v1/miniapp/auth/me/avatar`：认证上传当前用户通过 `chooseAvatar` 选择的头像；输入限制为不超过 2MB、最大 4096×4096 且总像素不超过 1600 万的单帧 JPEG、PNG 或 WebP，完整解码并重新编码后保存，要求 `account.self`。
- `GET|HEAD /api/v1/miniapp/auth/public/avatars/{opaque_filename}`：仅按一个合法、不可预测的文件名读取头像，供小程序 `<image>` 使用；该地址公开但不列出文件、不接受写方法，也不使用可枚举的用户 ID。
- `POST /api/v1/miniapp/auth/logout`：撤销当前会话。
- `GET /api/v1/miniapp/daily-reflections`：分页列出当前用户心得。
- `GET /api/v1/miniapp/daily-reflections/by-date/{YYYY-MM-DD}`：读取指定日期心得，不存在时返回 `null`。
- `PUT /api/v1/miniapp/daily-reflections`：按日期新增或覆盖当前用户心得。
- `GET /api/v1/miniapp/daily-reflections/{id}`：读取当前用户的一条心得。
- `DELETE /api/v1/miniapp/daily-reflections/{id}`：删除当前用户的一条心得。

除登录端点、health 以及合法不透明文件名的公开头像 `GET|HEAD` 外，请求必须携带 `Authorization: Bearer <token>`；管理员 Web Cookie 仅在 `ADMIN_AUTH_ENABLED=true` 且会话有效时作为超级管理员。未登录固定返回 `401`，已登录但缺权限固定返回 `403`。普通 `/api/v1/*` 路由若未登记权限策略会 fail closed 返回 `403`。`ADMIN_AUTH_ENABLED=false` 只表示不启用 Web 密码管理员入口，不会关闭小程序 Bearer/RBAC，也不会让业务 API 匿名放行。

Web 管理后台的 Auth Settings/初始密码设置与小程序微信登录相互独立。首次设置入口只接受 direct ASGI client 为 loopback 的请求，不采信 `X-Forwarded-For` 来取得本地资格。远程部署应在服务主机本地完成、通过 SSH 隧道直连服务的 loopback 地址，或使用项目已有的 `python -m src.auth reset_password` CLI 设置/重置密码；不要通过伪造转发头开放首次设置。

登录和 `/me` 的用户摘要包含 `roles` 与 `permissions`。系统内置角色如下：

- `member`：新用户默认角色。可维护本人会话、心得、持仓、告警和 Agent 会话；可读取股票、决策信号及情报；可执行 Agent 对话。包含 `alerts.manage`，但不包含 `alerts.notify`：用户规则仍会被评估并记录触发结果，但不会向外部通知渠道发送。不能执行共享分析/选股/回测任务，不能维护共享决策信号或情报，不能查看用量/数据能力，不能发送 Agent 内容到外部通知渠道，也不能管理系统或 RBAC。
- `operator`：受信任的运营分析员。拥有除 `system.manage`、`rbac.manage` 外的全部权限，包含 `alerts.notify`，可执行共享计算、维护共享资源，并拥有 `agent.share`。
- `admin`：全部权限，包含 `alerts.notify`、`system.manage` 与 `rbac.manage`。

小程序按钮和编辑控件按登录或 `/me` 返回的 `permissions` 显隐或只读；这只是交互层提示，后端仍对每个请求执行最终权限校验。`alerts.notify` 只控制告警外发能力，不能替代 `alerts.manage` 来创建、修改、启停、删除或测试规则。

`POST /api/v1/agent/chat/send` 单独要求 `agent.share`，避免普通成员触发外部通知副作用。具体权限码与路由映射的唯一真源为 `src/services/rbac_service.py`；角色可组合，最终权限取并集。

`RBAC_BOOTSTRAP_ADMIN_OPENIDS` 仅用于首位或紧急恢复管理员的幂等授予；从环境变量删除 OpenID 不会自动撤销数据库角色，反之，若数据库已回收 `admin` 但 OpenID 仍在该变量中，用户下次认证仍会被自动回授并记录不包含 OpenID 的审计事件。日常授权入口为小程序“我的 → 权限管理”（`pages/rbac/index`）：菜单按权限过滤、路由策略要求 `rbac.manage`，并由后端 `require_permission('rbac.manage')` 作最终校验。管理员可通过 `GET /api/v1/miniapp/rbac/catalog` 查询角色和权限目录，`GET /api/v1/miniapp/rbac/users` 分页查询不含 OpenID、UnionID、token hash、Bearer token 或 `session_key` 的用户目录，`PUT /api/v1/miniapp/rbac/users/{user_id}/roles` 替换完整角色集合，`PATCH /api/v1/miniapp/rbac/users/{user_id}/active` 启停账号，`POST|PUT|DELETE /api/v1/miniapp/rbac/roles` 管理自定义角色，以及 `GET /api/v1/miniapp/rbac/audit` 查询审计记录。所有管理接口均要求 `rbac.manage`；服务端拒绝空或未知角色、删除仍被分配的自定义角色、停用当前操作者、移除当前操作者的 `rbac.manage`，以及停用或降权最后一位拥有该权限的活跃用户。`member`、`operator`、`admin` 是由服务端权限目录维护的系统角色，页面只读；自定义角色及其有效权限码、用户角色关系、账号启停状态和审计记录均保存于数据库。

## 个人资源与行级隔离

RBAC 控制“能否使用某项能力”，行级所有权控制“能访问哪一条数据”，两者必须同时满足。当前个人资源包括：

- 每日心得：以 `(user_id, reflection_date)` 保证每日唯一，详情、删除和列表均按当前用户过滤。
- 持仓：账户使用服务端从 Bearer principal 得到的 `owner_id`；请求体中的 `owner_id` 不可信且不会决定归属。账户、交易、现金、公司行动、快照、风险报告、CSV 导入和组合告警目标均沿同一 owner 过滤。
- 告警：规则使用服务端可信 `user_id`；规则 CRUD、测试、触发历史和通知历史均按 owner 过滤。组合持仓展开和风险计算继续使用规则 owner，不会回落到全局账户。缺少 `alerts.notify` 时 worker 仍评估用户规则并记录触发结果，但跳过外部通知派发。管理员新建全局规则时必须明确写入 `owner_scope=global`，不能依赖空 owner 推断全局归属。
- Agent Chat：小程序会话 ID 固定使用 `miniapp:{user_id}:` 前缀；列表、详情、删除和流取消均校验 owner。miniapp 传入的任意 `user_id` 筛选值会被忽略；管理员可在 `GET /api/v1/agent/chat/sessions?user_id=<legacy-prefix>` 中筛选旧会话。

跨用户资源统一表现为 `404`，避免泄露资源是否存在。历史告警中同时满足 `user_id IS NULL` 且 `owner_scope IS NULL` 的 legacy 规则不进入 worker；历史持仓账户中 `owner_id IS NULL` 的记录不会自动归给任何小程序用户，只有有效管理员 Cookie 的全局后台范围可见。管理员新建的全局告警必须显式使用 `owner_scope=global`。分析历史、回测、决策信号等未列入上述个人资源的既有业务数据仍按共享域处理，角色权限不等于多租户数据隔离。

## 数据表

首次启动后端时，SQLAlchemy `create_all` 会在 `DATABASE_PATH` 指定的 SQLite 文件中创建：

- `miniapp_users`
- `miniapp_sessions`
- `daily_reflections`
- `rbac_roles`
- `rbac_permissions`
- `rbac_role_permissions`
- `miniapp_user_roles`
- `rbac_audit_events`：记录用户角色、账号状态和自定义角色权限的非敏感管理审计事件

旧 SQLite 库启动时会幂等为 `alert_rules` 补充 nullable `user_id` 与索引；已有行保留 `NULL`。持仓继续复用 `portfolio_accounts.owner_id`，不会自动回填历史账户。迁移不修改既有分析数据或管理员认证数据。
