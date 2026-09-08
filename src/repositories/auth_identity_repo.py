# -*- coding: utf-8 -*-
"""统一用户与外部认证身份的数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from src.storage import AuthIdentityRecord, DatabaseManager, UserRecord


class AuthIdentityConflictError(Exception):
    """可信身份已经归属其他用户，禁止静默合并。"""


class AuthIdentityRepository:
    """以 provider / issuer / subject 为唯一键管理身份映射。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def resolve_or_create(
        self,
        *,
        provider: str,
        issuer: str,
        subject: str,
        unionid: Optional[str] = None,
        legacy_openid: Optional[str] = None,
    ) -> tuple[UserRecord, bool]:
        """返回唯一用户；仅由同一可信 UnionID 进行受控关联。"""
        normalized_provider = self._required(provider, "provider")
        normalized_issuer = self._required(issuer, "issuer")
        normalized_subject = self._required(subject, "subject")
        normalized_unionid = self._optional(unionid)
        normalized_legacy_openid = self._optional(legacy_openid)
        now = datetime.utcnow()

        def write(session):
            primary = self._find_identity(
                session,
                provider=normalized_provider,
                issuer=normalized_issuer,
                subject=normalized_subject,
            )
            union = (
                self._find_identity(
                    session,
                    provider="wechat_unionid",
                    issuer="wechat",
                    subject=normalized_unionid,
                )
                if normalized_unionid
                else None
            )
            if primary is not None and union is not None and primary.user_id != union.user_id:
                raise AuthIdentityConflictError("微信身份映射存在冲突，拒绝自动关联账户")

            user_id = primary.user_id if primary is not None else (union.user_id if union is not None else None)
            created = user_id is None
            if user_id is None:
                user = UserRecord(
                    # The legacy cache columns are not used to resolve identity.
                    # They remain populated for new miniapp users only so older
                    # non-auth readers retain their existing display behavior.
                    openid=normalized_legacy_openid,
                    unionid=normalized_unionid,
                    created_at=now,
                    updated_at=now,
                    last_login_at=now,
                )
                session.add(user)
                session.flush()
            else:
                user = session.execute(
                    select(UserRecord)
                    .where(UserRecord.id == user_id, UserRecord.is_active.is_(True))
                    .limit(1)
                ).scalar_one_or_none()
                if user is None:
                    raise AuthIdentityConflictError("认证身份关联的用户不存在或已停用")
                if normalized_legacy_openid and not user.openid:
                    user.openid = normalized_legacy_openid
                if normalized_unionid and not user.unionid:
                    user.unionid = normalized_unionid
                user.updated_at = now
                user.last_login_at = now

            if primary is None:
                primary = AuthIdentityRecord(
                    user_id=user.id,
                    provider=normalized_provider,
                    issuer=normalized_issuer,
                    subject=normalized_subject,
                    created_at=now,
                    updated_at=now,
                    last_authenticated_at=now,
                )
                session.add(primary)
            else:
                primary.updated_at = now
                primary.last_authenticated_at = now

            if normalized_unionid:
                if union is None:
                    session.add(AuthIdentityRecord(
                        user_id=user.id,
                        provider="wechat_unionid",
                        issuer="wechat",
                        subject=normalized_unionid,
                        created_at=now,
                        updated_at=now,
                        last_authenticated_at=now,
                    ))
                else:
                    union.updated_at = now
                    union.last_authenticated_at = now
            session.flush()
            # _run_write_transaction commits after this callback. Refresh and
            # detach before that commit so callers receive a usable user record
            # rather than an expired ORM instance.
            session.refresh(user)
            session.expunge(user)
            return user, created

        return self.db._run_write_transaction("resolve_or_create_auth_identity", write)

    def get_user_by_id(self, user_id: int) -> Optional[UserRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(UserRecord)
                .where(UserRecord.id == user_id, UserRecord.is_active.is_(True))
                .limit(1)
            ).scalar_one_or_none()

    @staticmethod
    def _find_identity(session, *, provider: str, issuer: str, subject: str) -> Optional[AuthIdentityRecord]:
        return session.execute(
            select(AuthIdentityRecord)
            .where(
                AuthIdentityRecord.provider == provider,
                AuthIdentityRecord.issuer == issuer,
                AuthIdentityRecord.subject == subject,
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _required(value: str, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} 不能为空")
        return normalized

    @staticmethod
    def _optional(value: Optional[str]) -> Optional[str]:
        normalized = str(value or "").strip()
        return normalized or None
