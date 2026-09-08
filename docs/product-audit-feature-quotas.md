# 产品审计：用户高成本功能与每日配额

> 审计日期：2026-09-06  
> 范围：`daily_stock_analysis` 后端、Web、微信小程序及其统一身份/RBAC 契约。

## 1. 治理模型

系统把访问控制、每日可用次数和业务例外分开处理：

- **RBAC** 决定当前 canonical user 是否可以调用某项能力；角色 `admin` 是一组权限，不是独立 Cookie 身份，也不会绕过资源 owner scope。
- **每日配额** 在无副作用的业务准入通过后，由服务端按用户、功能和 UTC 自然日原子预留。客户端的隐藏按钮、剩余次数或本地状态都不是安全边界。
- **Entitlement** 按 **单用户覆盖 > 功能级白名单 > 全功能白名单 > 有效套餐 > 全局策略** 解析。`0` 始终表示禁用；不限次数只由功能级或全功能白名单产生。

所有经过认证的 Web Cookie 用户和小程序 Bearer 用户均按同一用户 entitlement 计量并写入 usage ledger。Web Cookie 写操作还需要精确 `Origin` 与 `X-CSRF-Token`；配额管理接口同时要求 `rbac.manage`。

## 2. 功能与默认策略

| 功能 | 主要成本/风险 | 默认每日配额 | 服务端控制 |
| --- | --- | ---: | --- |
| 个股分析、持仓分析 | LLM、数据源、异步 worker | 5 | 原子预留与批量 admission 控制 |
| 市场复盘 | LLM、市场数据、运行锁 | 2 | 获取运行锁后预留，提交失败释放 |
| 问股（含 SSE） | Agent/LLM、工具调用 | 20 | accepted 前预留，结构化额度错误 |
| 深度研究 | 多轮 Agent、搜索/LLM | 2 | 执行前预留 |
| 策略选股 | 批量行情与计算 | 3 | 入队前预留 |
| 历史回测 | 历史数据与计算 | 3 | 参数校验后预留 |
| 决策信号重评估 | LLM/策略计算 | 5 | 服务端预留 |
| 决策信号后验批处理 | 批处理与历史数据 | 3 | 服务端预留 |
| 图片识股 | Vision LLM | 10 | 文件安全校验后预留 |

`daily_limit=0` 的含义是显式禁止，不能表达无限额。单用户覆盖和套餐限制范围均为 `0..10000`；未配置的套餐功能回退全局策略。白名单可带 UTC 生效窗口，窗口到期后立即回退至下一层规则。

## 3. API 与来源字段

已登录用户通过 `GET /api/v1/feature-quotas/me` 获取每项实际 entitlement：`daily_limit`、`used_count`、`remaining`、`disabled`、`unlimited`、`reset_at`、`limit_source`、`plan_code` 与 `effective_until`。

`limit_source` 的稳定枚举仅为：

- `user_override`
- `whitelist_feature`
- `whitelist_all`
- `plan`
- `global_policy`

没有角色、Cookie 或 `admin` 专属来源。服务端拒绝预留时返回 `429 feature_quota_exceeded`：`reason=feature_disabled` 表示规则显式关闭，`reason=daily_limit_exceeded` 表示当天次数耗尽。客户端必须把两类结果分别解释，不能将其误报为网络失败。

Web 使用 `/api/v1/rbac/feature-quotas/*`，小程序使用 `/api/v1/miniapp/rbac/feature-quotas/*`；二者均基于当前 principal 和 `rbac.manage`。小程序专属接口严格 Bearer-only，Web 使用 Cookie 时仍需 Origin/CSRF 防护。

## 4. 数据与审计

| 数据表 | 用途 | 关键约束 |
| --- | --- | --- |
| `feature_quota_policies` | 功能默认每日额度 | `feature_code` 唯一；`daily_limit >= 0` |
| `feature_quota_usages` | 用户按功能、自然日的累计用量 | `(user_id, feature_code, period_start)` 唯一 |
| `feature_quota_plans` / `feature_quota_plan_limits` | 可启停套餐及其功能限制 | 未配置功能回退全局策略 |
| `feature_quota_user_plan_assignments` | 套餐分配及有效期 | 历史分配可审计 |
| `feature_quota_user_overrides` | 单用户、单功能覆盖 | `0` 为明确禁用 |
| `feature_quota_whitelists` | 功能或全功能不限次例外 | 功能级优先于全功能 |
| `rbac_audit_events` | 脱敏的角色与配额管理审计 | 记录 `feature_quota.*` 变更 |

策略、套餐、分配、覆盖与白名单变更必须记录操作者和非敏感变更内容。运营看板应从 usage ledger 汇总日活、调用/拒绝、额度耗尽率、有效例外、估算成本与失败率，而不是从 LLM provider 日志推断用户权益。

## 5. 体验、风险与验收

- Web 与小程序均显示服务端 entitlement；展示和按钮禁用仅改善体验，不能替代服务端预留。
- 高权限用户也只能访问本人 owner-scoped 资源。跨用户数据访问不应因 `rbac.manage` 或角色 `admin` 自动放行。
- 每日额度不能阻止短时并发突发；上线前应结合用户/功能维度的速率限制、队列并发、预算告警与审批流程。
- 执行器提交失败仅补偿尚未被 executor 接受的预留；worker 已接受后的失败、取消或第三方失败默认不退款。

验收时应确认：超额请求稳定返回可解释的 `429`；`0` 限额稳定返回 `feature_disabled`；五层 entitlement 优先级与 UTC 到期行为正确；`GET /api/v1/feature-quotas/me` 立即反映管理变更；白名单是唯一的不限次来源；刷新、重登或重试均不能超过服务端 usage ledger。
