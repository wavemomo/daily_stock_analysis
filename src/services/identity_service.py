# -*- coding: utf-8 -*-
"""统一身份解析服务。"""

from __future__ import annotations

from typing import Optional

from src.repositories.auth_identity_repo import (
    AuthIdentityConflictError,
    AuthIdentityRepository,
)
from src.storage import UserRecord

WECHAT_MINIAPP_PROVIDER = "wechat_miniapp"


class IdentityService:
    """将微信端身份解析为唯一的内部 UserRecord。"""

    def __init__(self, repository: Optional[AuthIdentityRepository] = None):
        self.repository = repository or AuthIdentityRepository()

    def resolve_miniapp_user(
        self,
        *,
        app_id: str,
        openid: str,
        unionid: Optional[str],
    ) -> tuple[UserRecord, bool]:
        """以小程序 AppID 为 issuer，绝不按裸 OpenID 跨应用匹配。"""
        return self.repository.resolve_or_create(
            provider=WECHAT_MINIAPP_PROVIDER,
            issuer=app_id,
            subject=openid,
            unionid=unionid,
            # The legacy users.openid cache is globally unique and therefore
            # cannot be the source of truth for issuer-scoped identities.
            legacy_openid=None,
        )


__all__ = [
    "AuthIdentityConflictError",
    "IdentityService",
    "WECHAT_MINIAPP_PROVIDER",
]
