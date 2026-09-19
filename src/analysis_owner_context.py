"""当前分析 owner 的运行期上下文（跨深层调用栈传递资源归属）。

多用户隔离里有两类归属传递方式：

1. **显式参数**：仓库/服务层的查询与写入一律显式接收 ``owner``，这是唯一可信真源；
2. **运行期上下文（本模块）**：少数横切关注点位于极深的调用栈里（Agent 记忆注入、
   LLM Token 计费），逐层改构造器既冗长又容易漏。这类场景从本上下文读取当前 owner。

设计约束：

- **fail-closed**：未绑定时 :func:`current_analysis_owner_or_global` 返回 global owner，
  即「只看全局/后台归属数据」，绝不会退化成「不过滤」而跨用户读到他人私有数据；
- 绑定点必须是已完成认证/授权的边界（HTTP 认证中间件、pipeline 构造 owner 之后、
  后台任务按 owner 分发时），业务数据本身绝不可作为绑定来源；
- ``contextvars`` 会被 ``contextvars.copy_context()`` 复制到 Agent 工具线程
  （见 ``src/agent/runner.py``、``src/agent/skills/scheduler.py``），也会被
  ``anyio.to_thread`` 复制到 FastAPI 的同步端点线程，因此绑定一次即可覆盖整条链路。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

from src.analysis_ownership import GLOBAL_ANALYSIS_OWNER, AnalysisOwner

_CURRENT_ANALYSIS_OWNER: ContextVar[Optional[AnalysisOwner]] = ContextVar(
    "current_analysis_owner",
    default=None,
)


def bind_current_analysis_owner(owner: Optional[AnalysisOwner]) -> Token:
    """Bind the trusted owner of the work executing in this context."""
    if owner is not None and not isinstance(owner, AnalysisOwner):
        raise TypeError("owner must be an AnalysisOwner")
    return _CURRENT_ANALYSIS_OWNER.set(owner)


def reset_current_analysis_owner(token: Optional[Token]) -> None:
    """Restore the previously bound owner."""
    if token is None:
        return
    try:
        _CURRENT_ANALYSIS_OWNER.reset(token)
    except ValueError:
        # 跨上下文 reset（例如线程池复制的上下文）不应影响主流程。
        pass


def get_current_analysis_owner() -> Optional[AnalysisOwner]:
    """Return the bound owner, or ``None`` when nothing is bound."""
    return _CURRENT_ANALYSIS_OWNER.get()


def current_analysis_owner_or_global() -> AnalysisOwner:
    """Return the bound owner, falling back to the global owner (fail-closed)."""
    return _CURRENT_ANALYSIS_OWNER.get() or GLOBAL_ANALYSIS_OWNER
