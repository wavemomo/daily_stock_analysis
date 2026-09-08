"""可信分析任务与历史记录的 owner 值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

AnalysisOwnerScope = Literal["user", "global"]


@dataclass(frozen=True, slots=True)
class AnalysisOwner:
    """A validated, trusted owner context for analysis-owned resources.

    该对象只能由 API 认证边界、后台任务或 CLI 组合根构造；业务数据
    本身不携带可由客户端伪造的 owner 字段。``None`` 保留给极少数
    直接调用的历史兼容读取，不是一个可持久化的 owner。
    """

    scope: AnalysisOwnerScope
    user_id: Optional[int] = None

    def __post_init__(self) -> None:
        if self.scope == "user":
            if (
                not isinstance(self.user_id, int)
                or isinstance(self.user_id, bool)
                or self.user_id <= 0
            ):
                raise ValueError("user-scoped analysis owner requires a positive user_id")
            return
        if self.scope == "global":
            if self.user_id is not None:
                raise ValueError("global analysis owner must not include a user_id")
            return
        raise ValueError(f"unsupported analysis owner scope: {self.scope}")

    @classmethod
    def user(cls, user_id: int) -> "AnalysisOwner":
        """Create a user-bound owner from a trusted positive integer ID."""
        return cls("user", user_id)

    @classmethod
    def global_owner(cls) -> "AnalysisOwner":
        """Create the explicit administrator/background global owner."""
        return cls("global")

    @classmethod
    def from_legacy(
        cls,
        owner_scope: Optional[str],
        owner_user_id: Optional[int] = None,
        *,
        allow_unscoped: bool = False,
    ) -> Optional["AnalysisOwner"]:
        """Adapt the established ``owner_scope``/``owner_user_id`` contract.

        ``allow_unscoped=True`` is intentionally limited to internal direct-call
        compatibility paths. HTTP handlers must never use it for unauthenticated
        requests; they construct a trusted owner before entering the application.
        """
        if owner_scope is None:
            if allow_unscoped:
                return None
            raise ValueError("analysis owner scope is required")
        if owner_scope == "user":
            return cls.user(owner_user_id)  # type: ignore[arg-type]
        if owner_scope == "global":
            return cls.global_owner()
        raise ValueError(f"unsupported analysis owner scope: {owner_scope}")

    @property
    def key(self) -> str:
        """Stable in-memory partition key for queue deduplication."""
        return f"user:{self.user_id}" if self.scope == "user" else "global"

    @property
    def storage_kwargs(self) -> dict[str, Optional[int] | str]:
        """Keyword arguments for owner-aware storage methods."""
        return {"owner_scope": self.scope, "owner_user_id": self.user_id}

    def matches(self, other: Optional["AnalysisOwner"]) -> bool:
        """Return whether another trusted context identifies the same owner."""
        return other is not None and self == other


GLOBAL_ANALYSIS_OWNER = AnalysisOwner.global_owner()
