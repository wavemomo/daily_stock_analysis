# -*- coding: utf-8 -*-
"""微信小程序用户与会话的数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from src.repositories.auth_identity_repo import AuthIdentityRepository
from src.services.identity_service import IdentityService
from src.storage import DatabaseManager, MiniappSessionRecord, MiniappUserRecord, local_naive_now


class MiniappUserRepository:
    """管理微信用户与可撤销的本地会话。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def upsert_user_with_status(
        self,
        *,
        openid: str,
        unionid: Optional[str] = None,
        issuer: str,
    ) -> tuple[MiniappUserRecord, bool]:
        """创建或更新 issuer-scoped canonical user，并返回是否新建。

        小程序身份只能由 provider/issuer/subject 三元组解析。禁止按裸
        OpenID 查询，避免不同小程序的同值 OpenID 被静默合并。
        """
        normalized_issuer = str(issuer or "").strip()
        if not normalized_issuer:
            raise ValueError("issuer 不能为空")
        return IdentityService(
            AuthIdentityRepository(self.db)
        ).resolve_miniapp_user(
            app_id=normalized_issuer,
            openid=openid,
            unionid=unionid,
        )

    def upsert_user(
        self,
        *,
        openid: str,
        unionid: Optional[str] = None,
        issuer: str,
    ) -> MiniappUserRecord:
        """仅返回 issuer-scoped canonical user。"""
        return self.upsert_user_with_status(
            openid=openid,
            unionid=unionid,
            issuer=issuer,
        )[0]

    def get_user_by_id(self, user_id: int) -> Optional[MiniappUserRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(MiniappUserRecord)
                .where(MiniappUserRecord.id == user_id)
                .limit(1)
            ).scalar_one_or_none()

    def update_profile(
        self,
        *,
        user_id: int,
        nickname: Optional[str],
        avatar_url: Optional[str],
    ) -> Optional[MiniappUserRecord]:
        """只按已认证用户 ID 更新展示资料，不接受身份或权限字段。"""
        with self.db.get_session() as session:
            row = session.execute(
                select(MiniappUserRecord)
                .where(
                    MiniappUserRecord.id == user_id,
                    MiniappUserRecord.is_active.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            changed = False
            if nickname is not None:
                row.nickname = nickname
                changed = True
            if avatar_url is not None:
                row.avatar_url = avatar_url
                changed = True
            if changed:
                now = local_naive_now()
                row.profile_updated_at = now
                row.updated_at = now
                session.commit()
                session.refresh(row)
            return row

    def create_session(
        self,
        *,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
    ) -> MiniappSessionRecord:
        with self.db.get_session() as session:
            row = MiniappSessionRecord(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
                created_at=local_naive_now(),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_user_by_session_hash(
        self,
        token_hash: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[MiniappUserRecord]:
        current = now or local_naive_now()
        with self.db.get_session() as session:
            return session.execute(
                select(MiniappUserRecord)
                .join(MiniappSessionRecord, MiniappSessionRecord.user_id == MiniappUserRecord.id)
                .where(
                    MiniappSessionRecord.token_hash == token_hash,
                    MiniappSessionRecord.revoked_at.is_(None),
                    MiniappSessionRecord.expires_at > current,
                    MiniappUserRecord.is_active.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()

    def revoke_session(self, token_hash: str) -> bool:
        with self.db.get_session() as session:
            row = session.execute(
                select(MiniappSessionRecord)
                .where(MiniappSessionRecord.token_hash == token_hash)
                .limit(1)
            ).scalar_one_or_none()
            if row is None or row.revoked_at is not None:
                return False
            row.revoked_at = local_naive_now()
            session.commit()
            return True
