# -*- coding: utf-8 -*-
"""普通 Web 用户会话与显式身份绑定服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
from typing import Optional

from src.config import Config, get_config
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.repositories.web_user_auth_repo import IdentityBindState, WebUserAuthRepository
from src.services.rbac_service import RbacService
from src.services.wechat_miniapp_auth_service import MiniappPrincipal
from src.storage import local_naive_now

WEB_USER_COOKIE_NAME = "dsa_user_session"


@dataclass(frozen=True)
class WebSessionIssue:
    """服务器签发的独立 Web session；原始值仅用于 Set-Cookie。"""

    session_value: str
    principal: MiniappPrincipal
    expires_at: datetime


@dataclass(frozen=True)
class IdentityBindChallenge:
    """可展示给已登录小程序的短期、不透明身份绑定挑战。"""

    challenge: str
    expires_at: datetime


class WebUserAuthService:
    """为统一 users 身份签发、验证及撤销独立浏览器会话。"""

    def __init__(
        self,
        repository: Optional[WebUserAuthRepository] = None,
        miniapp_repository: Optional[MiniappUserRepository] = None,
        config: Optional[Config] = None,
    ):
        self.repository = repository or WebUserAuthRepository()
        self.miniapp_repository = miniapp_repository or MiniappUserRepository()
        self.config = config or get_config()

    @staticmethod
    def hash_value(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create_session_for_user(
        self,
        *,
        user_id: int,
        assign_default_role: bool = False,
    ) -> Optional[WebSessionIssue]:
        """为活跃 canonical user 创建浏览器会话并加载实时 RBAC。"""
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            return None
        user = self.miniapp_repository.get_user_by_id(user_id)
        if user is None or not user.is_active:
            return None
        access = RbacService().ensure_user_access(user, assign_default=assign_default_role)
        session_value = secrets.token_urlsafe(32)
        expires_at = local_naive_now() + timedelta(
            seconds=self.config.web_user_session_ttl_seconds
        )
        token_hash = self.hash_value(session_value)
        if not self.repository.create_web_session_for_user(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        ):
            return None
        return WebSessionIssue(
            session_value=session_value,
            principal=MiniappPrincipal(
                user=user,
                token_hash=token_hash,
                roles=tuple(access["roles"]),
                permissions=tuple(access["permissions"]),
            ),
            expires_at=expires_at,
        )

    def start_identity_bind(self, *, requested_user_id: int) -> Optional[IdentityBindChallenge]:
        """创建必须由小程序当前 Bearer 身份确认的绑定挑战。"""
        if (
            not isinstance(requested_user_id, int)
            or isinstance(requested_user_id, bool)
            or requested_user_id <= 0
        ):
            return None
        challenge = secrets.token_urlsafe(32)
        expires_at = local_naive_now() + timedelta(
            seconds=self.config.wechat_open_web_state_ttl_seconds
        )
        created = self.repository.create_identity_bind_transaction(
            challenge_hash=self.hash_value(challenge),
            requested_user_id=requested_user_id,
            expires_at=expires_at,
        )
        if not created:
            return None
        return IdentityBindChallenge(challenge=challenge, expires_at=expires_at)

    def approve_identity_bind(
        self,
        *,
        challenge: str,
        approved_user_id: int,
    ) -> Optional[datetime]:
        normalized = (challenge or "").strip()
        if not normalized or not isinstance(approved_user_id, int) or isinstance(approved_user_id, bool):
            return None
        return self.repository.approve_identity_bind_transaction(
            challenge_hash=self.hash_value(normalized),
            approved_user_id=approved_user_id,
        )

    def consume_identity_bind(
        self,
        *,
        challenge: str,
        requested_user_id: int,
    ) -> IdentityBindState:
        normalized = (challenge or "").strip()
        if not normalized:
            return "invalid"
        return self.repository.consume_identity_bind_transaction(
            challenge_hash=self.hash_value(normalized),
            requested_user_id=requested_user_id,
        )

    def authenticate_session(self, session_value: str) -> Optional[MiniappPrincipal]:
        normalized = (session_value or "").strip()
        if not normalized:
            return None
        session_hash = self.hash_value(normalized)
        user = self.repository.get_user_by_session_hash(session_hash)
        if user is None:
            return None
        access = RbacService().ensure_user_access(user)
        return MiniappPrincipal(
            user=user,
            token_hash=session_hash,
            roles=tuple(access["roles"]),
            permissions=tuple(access["permissions"]),
        )

    def revoke_session(self, session_value: str) -> bool:
        normalized = (session_value or "").strip()
        return bool(normalized) and self.repository.revoke_session(self.hash_value(normalized))

    @staticmethod
    def create_csrf_token(session_value: str) -> str:
        """将 CSRF token 绑定到 HttpOnly 随机会话凭据。"""
        return hmac.new(
            (session_value or "").encode("utf-8"),
            b"dsa-web-user-csrf-v1",
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def verify_csrf_token(cls, session_value: str, submitted_token: str) -> bool:
        expected = cls.create_csrf_token(session_value)
        return bool(submitted_token) and hmac.compare_digest(expected, submitted_token)
