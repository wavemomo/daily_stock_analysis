# 统一微信身份、会话与 RBAC 实施规格

**状态：** 当前实现基线（测试阶段的破坏性迁移）  
**范围：** `daily_stock_analysis` Web、微信小程序、FastAPI API 与持久化身份/RBAC 数据。

## 1. 设计目标

1. Web 仅通过微信开放平台网站应用扫码 OAuth 登录。
2. 小程序继续通过 `wx.login` 和服务端 `code2session` 登录。
3. 两端解析为 canonical business user，并加载同一套角色、权限、功能额度与 owner scope。
4. 小程序使用 Bearer session；Web 使用 HttpOnly `dsa_user_session` Cookie，并对 Cookie 写操作执行可信 Origin 与 CSRF 校验。
5. 管理能力由权限码判定，例如 `rbac.manage`；`admin` 是 RBAC 角色，不是密码登录或独立身份域。
6. 不按昵称、头像、手机号或裸 OpenID 自动合并账号；需要关联时只提供受控的 challenge、批准与冲突处理。

不保留传统用户名密码、`dsa_session`、`/api/v1/auth/*`、`/api/v1/admin/rbac/*` 或 browser-to-miniapp web-link 登录 API。系统不签发服务主体、API key 或 bot token 用于业务 API。

## 2. 身份与会话

| 通道 | 微信凭据 | DSA 会话 | API 边界 |
| --- | --- | --- | --- |
| 小程序 | `wx.login` code，经 `code2session` 交换 | 随机 Bearer token；数据库仅存 hash | `/api/v1/miniapp/*` 严格 Bearer-only |
| Web | 微信开放平台 OAuth `code` | `dsa_user_session` HttpOnly Cookie；数据库仅存 hash | 通用 `/api/v1/*` 的 Web principal |

身份记录按 `(provider, issuer, subject)` 区分：provider 是 `wechat_miniapp` 或 `wechat_open_web`，issuer 是对应 AppID，subject 是 OpenID。OpenID、UnionID、code、access token、refresh token 和 AppSecret 不得回传给前端或写入日志。

小程序和 Web 都加载同一个 canonical user 的 roles、permissions、账号状态、资源归属和 entitlement。请求携带有效 Web Cookie 与 Bearer token 时，通用 API 必须返回 `400 authentication_conflict`，而不能任意挑选其中之一。

## 3. Web 微信 OAuth

1. 浏览器请求 `GET /api/v1/web-auth/wechat/start`。
2. 服务端创建短期 state/binding transaction，写入临时浏览器 binding Cookie，并 302 到微信开放平台扫码授权地址。
3. 微信回调 `GET /api/v1/web-auth/wechat/callback?code=...&state=...`。
4. 服务端验证 state、浏览器 binding、有效期和单次消费状态；成功交换 code 后解析或创建 canonical user。
5. 服务端建立 `dsa_user_session`，清理临时 binding，并固定 `302 /`。

`start` 不接受 `return_path`。回调失败、state/binding 缺失、过期、篡改、重放或已经消费时不能建立会话，且不得泄露微信 provider 响应。

认证后的 Web API：

| 路由 | 认证/约束 | 作用 |
| --- | --- | --- |
| `GET /api/v1/web-auth/me` | Web Cookie | 返回 `{user:{id,nickname,avatar_url,roles,permissions}, csrf_token}` |
| `POST /api/v1/web-auth/logout` | Web Cookie + exact Origin + `X-CSRF-Token` | 撤销当前 Web session |
| `POST /api/v1/web-auth/identity-bind/start` | Web Cookie | 创建受控绑定 challenge |
| `POST /api/v1/web-auth/identity-bind/consume` | Web Cookie | 消费已批准的 challenge，或返回冲突/状态结果 |

Cookie 属性以实际运行时实现为准；文档不把未经实现验证的 `Strict`、path-scoped 或其他属性写成契约。所有 OAuth 响应应使用 `Cache-Control: no-store`。

## 4. 小程序登录与绑定

小程序将一次性 `wx.login()` code 发送到 `POST /api/v1/miniapp/auth/login`。服务端以小程序 AppID/AppSecret 调用 `code2session`，解析身份后创建或复用 canonical user、签发 Bearer session，并在 `GET /api/v1/miniapp/auth/me` 返回用户摘要、roles 与 permissions。

身份关联优先使用可验证且唯一的 UnionID；UnionID 缺失、歧义或身份已经归属另一用户时必须 fail closed。`identity-bind` 流程只负责 challenge、当前小程序用户显式批准和 Web 端消费/冲突处理：它不会自动合并两个已有用户，也不会静默转移资源、角色、额度或审计历史。

`POST /api/v1/miniapp/auth/identity-bind/approve` 只接受当前小程序 Bearer principal。客户端不得指定目标 user ID，也不得以 provider key、OpenID 或自定义 Bearer 替代会话。

## 5. RBAC、系统管理与资源范围

统一 principal 至少包含 `user_id`、`auth_channel`、`session_id`、roles 和 permissions。认证中间件只负责解析凭据与写入 request state；路由从 principal 执行 `require_permission(...)` 和 owner-scope 校验。

- `/api/v1/rbac/*` 和 `/api/v1/miniapp/rbac/*` 都要求 `rbac.manage`。
- `/api/v1/miniapp/*` 不接受 Web Cookie。
- 通用 `/api/v1/*` 按路由的 RBAC policy 接受合法 Web Cookie 或 Bearer principal。
- `admin` 不绕过 `owner_user_id == principal.user_id`。跨用户个人资源应 fail closed，通常以 `404` 避免暴露存在性。
- 初始管理员只能通过受控的一次性 seed/角色授予流程给已存在用户，记录审计事件；不使用 OpenID 环境变量自动回授。
- 高风险系统和密钥操作须有单独、明确的权限策略；不能因“Web 管理员 Cookie”获得隐式放行。

功能额度独立于 RBAC：实际来源仅为 `user_override`、`whitelist_feature`、`whitelist_all`、`plan` 或 `global_policy`。无限额度只来自白名单；角色或 Cookie 都不能成为额度豁免来源。

## 6. 配置与部署

```dotenv
WECHAT_OPEN_WEB_APP_ID=
WECHAT_OPEN_WEB_APP_SECRET=
WECHAT_OPEN_WEB_REDIRECT_URI=https://example.com/api/v1/web-auth/wechat/callback
WECHAT_OPEN_WEB_STATE_TTL_SECONDS=300
WEB_USER_SESSION_TTL_SECONDS=604800
WECHAT_MINIAPP_APP_ID=
WECHAT_MINIAPP_APP_SECRET=
WECHAT_MINIAPP_CODE2SESSION_TIMEOUT_SECONDS=8
WECHAT_MINIAPP_SESSION_TTL_HOURS=720
CORS_ORIGINS=https://example.com
```

`WECHAT_OPEN_WEB_REDIRECT_URI` 必须是微信开放平台登记的公开 HTTPS 回调地址。`CORS_ORIGINS` 只控制浏览器 API Origin，不替代 OAuth 回调配置。所有 AppSecret 仅可出现在后端部署环境；不得进入 Web 构建、客户端、日志或错误响应。只有在单层且完全可信的反向代理拓扑中才设置 `TRUST_X_FORWARDED_FOR=true`。

## 7. 验收矩阵

- Web state/binding 缺失、过期、篡改、重放或 exchange 失败时不得建立 session。
- Web callback 只能跳转 `/`，不泄露 OAuth 凭据。
- 小程序 Bearer 不能访问 Web Cookie CSRF surface；Web Cookie 不能访问 `/api/v1/miniapp/*`。
- 受保护通用路由上的有效 Cookie + Bearer 返回 `400 authentication_conflict`。
- Web Cookie 写操作必须校验 exact Origin 和 CSRF；小程序写操作必须校验 Bearer 与 RBAC。
- 无 `rbac.manage` 的用户不能访问角色/成员管理；拥有该权限也不能越过个人 owner scope。
- 身份绑定不会自动合并两个用户；冲突始终显式返回。
- 不存在密码登录、`dsa_session`、旧 `/api/v1/auth/*`、旧 `/api/v1/admin/rbac/*`、web-link 入口或 OpenID 自动升权。
