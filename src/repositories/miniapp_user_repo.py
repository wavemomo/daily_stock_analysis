# -*- coding: utf-8 -*-
"""微信小程序用户与会话的数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import select

from src.storage import DatabaseManager, MiniappSessionRecord, MiniappUserRecord


class MiniappUserRepository:
    """管理微信用户与可撤销的本地会话。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def upsert_user_with_status(
        self,
        *,
        openid: str,
        unionid: Optional[str] = None,
    ) -> tuple[MiniappUserRecord, bool]:
        """创建或更新用户，并明确返回本次是否新建。"""
        now = datetime.utcnow()
        created = False
        with self.db.get_session() as session:
            row = session.execute(
                select(MiniappUserRecord)
                .where(MiniappUserRecord.openid == openid)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                created = True
                row = MiniappUserRecord(
                    openid=openid,
                    unionid=unionid,
                    created_at=now,
                    updated_at=now,
                    last_login_at=now,
                )
                session.add(row)
            else:
                if unionid:
                    row.unionid = unionid
                row.updated_at = now
                row.last_login_at = now
            session.commit()
            session.refresh(row)
            return row, created

    def upsert_user(self, *, openid: str, unionid: Optional[str] = None) -> MiniappUserRecord:
        """兼容原调用方，仅返回用户。"""
        return self.upsert_user_with_status(openid=openid, unionid=unionid)[0]

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
                now = datetime.utcnow()
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
                created_at=datetime.utcnow(),
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
        current = now or datetime.utcnow()
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
            row.revoked_at = datetime.utcnow()
            session.commit()
            return True
