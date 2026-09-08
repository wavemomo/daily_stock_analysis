# -*- coding: utf-8 -*-
"""普通 Web 用户 OAuth、身份绑定与可撤销会话的数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlalchemy import delete, select, update

from src.storage import (
    DatabaseManager,
    IdentityBindTransactionRecord,
    MiniappUserRecord,
    WebUserSessionRecord,
    WebWechatLoginTransactionRecord,
)

IdentityBindState = Literal["pending", "approved", "conflict", "invalid"]


class WebUserAuthRepository:
    """管理网站 OAuth state、显式绑定挑战和独立浏览器会话。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def create_wechat_login_transaction(
        self,
        *,
        state_hash: str,
        browser_binding_hash: str,
        redirect_uri: str,
        expires_at: datetime,
    ) -> None:
        """创建短期 state；仅保存摘要，且清理过期记录。"""
        now = datetime.utcnow()

        def write(session):
            session.execute(
                delete(WebWechatLoginTransactionRecord).where(
                    WebWechatLoginTransactionRecord.expires_at <= now
                )
            )
            session.add(
                WebWechatLoginTransactionRecord(
                    state_hash=state_hash,
                    browser_binding_hash=browser_binding_hash,
                    redirect_uri=redirect_uri,
                    expires_at=expires_at,
                    created_at=now,
                )
            )

        self.db._run_write_transaction("create_web_wechat_login_transaction", write)

    def consume_wechat_login_transaction(
        self,
        *,
        state_hash: str,
        browser_binding_hash: str,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        """原子校验并消费 OAuth state，返回当初保存的 callback URI。"""
        current = now or datetime.utcnow()

        def write(session):
            statement = (
                update(WebWechatLoginTransactionRecord)
                .where(
                    WebWechatLoginTransactionRecord.state_hash == state_hash,
                    WebWechatLoginTransactionRecord.browser_binding_hash
                    == browser_binding_hash,
                    WebWechatLoginTransactionRecord.consumed_at.is_(None),
                    WebWechatLoginTransactionRecord.expires_at > current,
                )
                .values(consumed_at=current)
                .returning(WebWechatLoginTransactionRecord.redirect_uri)
            )
            return session.execute(statement).scalar_one_or_none()

        return self.db._run_write_transaction("consume_web_wechat_login_transaction", write)

    def create_web_session_for_user(
        self,
        *,
        user_id: int,
        token_hash: str,
        expires_at: datetime,
        now: Optional[datetime] = None,
    ) -> bool:
        """仅为活跃 canonical user 创建浏览器会话。"""
        current = now or datetime.utcnow()

        def write(session):
            user = session.execute(
                select(MiniappUserRecord)
                .where(
                    MiniappUserRecord.id == user_id,
                    MiniappUserRecord.is_active.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()
            if user is None:
                return False
            session.add(
                WebUserSessionRecord(
                    user_id=user.id,
                    token_hash=token_hash,
                    expires_at=expires_at,
                    created_at=current,
                )
            )
            return True

        return bool(self.db._run_write_transaction("create_web_user_session", write))

    def create_identity_bind_transaction(
        self,
        *,
        challenge_hash: str,
        requested_user_id: int,
        expires_at: datetime,
    ) -> bool:
        """创建由当前 Web user 发起的短期显式绑定挑战。"""
        now = datetime.utcnow()

        def write(session):
            session.execute(
                delete(IdentityBindTransactionRecord).where(
                    IdentityBindTransactionRecord.expires_at <= now
                )
            )
            user = session.execute(
                select(MiniappUserRecord)
                .where(
                    MiniappUserRecord.id == requested_user_id,
                    MiniappUserRecord.is_active.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()
            if user is None:
                return False
            session.add(
                IdentityBindTransactionRecord(
                    challenge_hash=challenge_hash,
                    requested_user_id=user.id,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
            return True

        return bool(self.db._run_write_transaction("create_identity_bind_transaction", write))

    def approve_identity_bind_transaction(
        self,
        *,
        challenge_hash: str,
        approved_user_id: int,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """将挑战与 Bearer 当前用户原子绑定；客户端不得指定请求用户。"""
        current = now or datetime.utcnow()

        def write(session):
            approved_user = session.execute(
                select(MiniappUserRecord)
                .where(
                    MiniappUserRecord.id == approved_user_id,
                    MiniappUserRecord.is_active.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()
            if approved_user is None:
                return None
            transaction = session.execute(
                select(IdentityBindTransactionRecord)
                .where(
                    IdentityBindTransactionRecord.challenge_hash == challenge_hash,
                    IdentityBindTransactionRecord.approved_at.is_(None),
                    IdentityBindTransactionRecord.consumed_at.is_(None),
                    IdentityBindTransactionRecord.revoked_at.is_(None),
                    IdentityBindTransactionRecord.expires_at > current,
                )
                .limit(1)
            ).scalar_one_or_none()
            if transaction is None:
                return None
            transaction.approved_user_id = approved_user.id
            transaction.approved_at = current
            return transaction.expires_at

        return self.db._run_write_transaction("approve_identity_bind_transaction", write)

    def consume_identity_bind_transaction(
        self,
        *,
        challenge_hash: str,
        requested_user_id: int,
        now: Optional[datetime] = None,
    ) -> IdentityBindState:
        """消费已批准 challenge，拒绝对两个 canonical users 静默合并。"""
        current = now or datetime.utcnow()

        def write(session):
            transaction = session.execute(
                select(IdentityBindTransactionRecord)
                .where(
                    IdentityBindTransactionRecord.challenge_hash == challenge_hash,
                    IdentityBindTransactionRecord.requested_user_id == requested_user_id,
                    IdentityBindTransactionRecord.approved_at.is_not(None),
                    IdentityBindTransactionRecord.approved_user_id.is_not(None),
                    IdentityBindTransactionRecord.consumed_at.is_(None),
                    IdentityBindTransactionRecord.revoked_at.is_(None),
                    IdentityBindTransactionRecord.expires_at > current,
                )
                .limit(1)
            ).scalar_one_or_none()
            if transaction is None:
                return "invalid"
            transaction.consumed_at = current
            if transaction.approved_user_id != transaction.requested_user_id:
                # A challenge approval alone never authorizes hidden resource/RBAC
                # migration between two already canonical accounts.
                return "conflict"
            return "approved"

        return self.db._run_write_transaction("consume_identity_bind_transaction", write)

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
                .join(WebUserSessionRecord, WebUserSessionRecord.user_id == MiniappUserRecord.id)
                .where(
                    WebUserSessionRecord.token_hash == token_hash,
                    WebUserSessionRecord.revoked_at.is_(None),
                    WebUserSessionRecord.expires_at > current,
                    MiniappUserRecord.is_active.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()

    def revoke_session(self, token_hash: str, *, now: Optional[datetime] = None) -> bool:
        current = now or datetime.utcnow()

        def write(session):
            row = session.execute(
                select(WebUserSessionRecord)
                .where(
                    WebUserSessionRecord.token_hash == token_hash,
                    WebUserSessionRecord.revoked_at.is_(None),
                )
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return False
            row.revoked_at = current
            return True

        return self.db._run_write_transaction("revoke_web_user_session", write)
