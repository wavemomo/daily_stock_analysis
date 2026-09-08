"""Trusted Portfolio ownership scopes.

``None`` used to mean both an administrator's legacy rows and an internal
unscoped call.  Keep those meanings explicit so HTTP callers cannot acquire
unscoped Portfolio access through an omitted owner value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

PortfolioScopeKind = Literal["user", "legacy_global", "unscoped"]


@dataclass(frozen=True, slots=True)
class PortfolioScope:
    """Validated, trusted access scope for Portfolio accounts and events."""

    kind: PortfolioScopeKind
    owner_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind == "user":
            owner_id = (self.owner_id or "").strip()
            if not owner_id:
                raise ValueError("user-scoped portfolio access requires owner_id")
            object.__setattr__(self, "owner_id", owner_id)
            return
        if self.kind in {"legacy_global", "unscoped"}:
            if self.owner_id is not None:
                raise ValueError(f"{self.kind} portfolio scope must not include owner_id")
            return
        raise ValueError(f"unsupported portfolio scope: {self.kind}")

    @classmethod
    def user(cls, owner_id: str) -> "PortfolioScope":
        return cls("user", owner_id)

    @classmethod
    def legacy_global(cls) -> "PortfolioScope":
        """Administrator HTTP scope: legacy Portfolio rows with NULL owner only."""
        return cls("legacy_global")

    @classmethod
    def unscoped(cls) -> "PortfolioScope":
        """Explicit direct Python/CLI compatibility scope; never construct in HTTP."""
        return cls("unscoped")

    def matches(self, other: "PortfolioScope | None") -> bool:
        return other is not None and self == other


LEGACY_GLOBAL_PORTFOLIO_SCOPE = PortfolioScope.legacy_global()
UNSCOPED_PORTFOLIO_SCOPE = PortfolioScope.unscoped()


class PortfolioScopeRequiredError(ValueError):
    """Raised when a Portfolio data access omits its trusted scope."""


class _UnsetPortfolioScope:
    """Private sentinel that distinguishes an omitted scope from explicit access."""

    def __repr__(self) -> str:
        return "UNSET_PORTFOLIO_SCOPE"


UNSET_PORTFOLIO_SCOPE = _UnsetPortfolioScope()


def scope_from_legacy_owner(value: object) -> PortfolioScope:
    """Adapt trusted legacy owner strings while rejecting omitted scope values.

    ``str`` remains a temporary compatibility input for trusted Python callers.
    New boundaries must pass a :class:`PortfolioScope` explicitly.  Neither an
    omitted value nor ``None`` grants access because unscoped access is only
    valid when the caller intentionally supplies ``UNSCOPED_PORTFOLIO_SCOPE``.
    """
    if isinstance(value, PortfolioScope):
        return value
    if value is UNSET_PORTFOLIO_SCOPE or value is None:
        raise PortfolioScopeRequiredError("portfolio access scope is required")
    return PortfolioScope.user(str(value))


def storage_owner_id(value: object) -> Optional[str]:
    """Resolve an account's stored owner for an explicit creation scope."""
    scope = scope_from_legacy_owner(value)
    return scope.owner_id if scope.kind == "user" else None
