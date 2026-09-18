# -*- coding: utf-8 -*-
"""邮箱 + 密码凭据与邮箱验证码的数据访问层。

用于个人主体绕开微信开放平台扫码登录：小程序内已认证用户绑定邮箱/密码，
Web 端凭邮箱密码登录同一 canonical user。所有写操作走 DatabaseManager 的
串行写事务，避免 SQLite 并发写。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select

from src.storage import (
    DatabaseManager,
    EmailVerificationCodeRecord,
    MiniappUserRecord,
    WebPasswordCredentialRecord,
    local_naive_now,
)


class EmailPasswordRepository:
    """管理邮箱密码凭据与限时验证码。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ---------------- 凭据 ----------------

    def get_credential_by_user_id(self, user_id: int) -> Optional[WebPasswordCredentialRecord]:
        with self.db.get_session() as session:
            return session.execute(
                select(WebPasswordCredentialRecord)
                .where(WebPasswordCredentialRecord.user_id == user_id)
                .limit(1)
            ).scalar_one_or_none()

    def get_active_credential_by_email(
        self,
        email: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[WebPasswordCredentialRecord]:
        """按邮箱返回活跃用户的凭据；停用用户的凭据不可用于登录。"""
        current = now or local_naive_now()  # noqa: F841 - reserved for future TTL checks
        with self.db.get_session() as session:
            return session.execute(
                select(WebPasswordCredentialRecord)
                .join(
                    MiniappUserRecord,
                    MiniappUserRecord.id == WebPasswordCredentialRecord.user_id,
                )
                .where(
                    WebPasswordCredentialRecord.email == email,
                    MiniappUserRecord.is_active.is_(True),
                )
                .limit(1)
            ).scalar_one_or_none()

    def get_report_email_target(self, user_id: int) -> Optional[str]:
        """返回该用户"应接收报告的邮箱"：已绑定且开关开启时为邮箱，否则 None。

        用于报告完成后按 owner 路由邮件收件人；纯微信登录（无凭据）或用户关闭
        开关时返回 None，调用方据此跳过邮件发送。
        """
        with self.db.get_session() as session:
            row = session.execute(
                select(WebPasswordCredentialRecord)
                .where(WebPasswordCredentialRecord.user_id == user_id)
                .limit(1)
            ).scalar_one_or_none()
            if row is None or not row.report_email_enabled:
                return None
            email = (row.email or "").strip()
            return email or None

    def set_report_email_enabled(self, *, user_id: int, enabled: bool) -> bool:
        """切换该用户"报告发送到邮箱"开关；无凭据（未绑定邮箱）返回 False。"""
        now = local_naive_now()

        def write(session):
            row = session.execute(
                select(WebPasswordCredentialRecord)
                .where(WebPasswordCredentialRecord.user_id == user_id)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return False
            row.report_email_enabled = bool(enabled)
            row.updated_at = now
            return True

        return bool(self.db._run_write_transaction("set_report_email_enabled", write))

    def find_email_owner_id(self, email: str) -> Optional[int]:
        """返回已绑定该邮箱的用户 ID；用于拒绝把同一邮箱绑给不同用户。"""
        with self.db.get_session() as session:
            return session.execute(
                select(WebPasswordCredentialRecord.user_id)
                .where(WebPasswordCredentialRecord.email == email)
                .limit(1)
            ).scalar_one_or_none()

    def upsert_credential(
        self,
        *,
        user_id: int,
        email: str,
        password_hash: str,
        email_verified_at: Optional[datetime],
    ) -> bool:
        """为活跃用户创建或更新邮箱密码凭据。

        返回 False 表示用户不存在/已停用，或邮箱已被其他用户占用。调用方据此
        映射为 404 / 409，而不静默覆盖他人邮箱。
        """
        now = local_naive_now()

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

            conflict = session.execute(
                select(WebPasswordCredentialRecord)
                .where(
                    WebPasswordCredentialRecord.email == email,
                    WebPasswordCredentialRecord.user_id != user_id,
                )
                .limit(1)
            ).scalar_one_or_none()
            if conflict is not None:
                return False

            row = session.execute(
                select(WebPasswordCredentialRecord)
                .where(WebPasswordCredentialRecord.user_id == user_id)
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                session.add(
                    WebPasswordCredentialRecord(
                        user_id=user_id,
                        email=email,
                        password_hash=password_hash,
                        email_verified_at=email_verified_at,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                row.email = email
                row.password_hash = password_hash
                row.email_verified_at = email_verified_at
                row.updated_at = now
            return True

        return bool(self.db._run_write_transaction("upsert_web_password_credential", write))

    # ---------------- 验证码 ----------------

    def latest_active_code_created_at(
        self,
        *,
        user_id: int,
        email: str,
        purpose: str,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """返回当前用户+邮箱+用途下最近一条未过期未消费验证码的创建时间。

        用于发送频率限制（避免验证码轰炸）。
        """
        current = now or local_naive_now()
        with self.db.get_session() as session:
            return session.execute(
                select(EmailVerificationCodeRecord.created_at)
                .where(
                    EmailVerificationCodeRecord.user_id == user_id,
                    EmailVerificationCodeRecord.email == email,
                    EmailVerificationCodeRecord.purpose == purpose,
                    EmailVerificationCodeRecord.consumed_at.is_(None),
                    EmailVerificationCodeRecord.expires_at > current,
                )
                .order_by(EmailVerificationCodeRecord.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

    def create_code(
        self,
        *,
        user_id: int,
        email: str,
        purpose: str,
        code_hash: str,
        expires_at: datetime,
    ) -> None:
        """写入新验证码，并顺带清理该用户+用途下的过期/历史记录。"""
        now = local_naive_now()

        def write(session):
            session.execute(
                delete(EmailVerificationCodeRecord).where(
                    EmailVerificationCodeRecord.user_id == user_id,
                    EmailVerificationCodeRecord.purpose == purpose,
                )
            )
            session.add(
                EmailVerificationCodeRecord(
                    user_id=user_id,
                    email=email,
                    purpose=purpose,
                    code_hash=code_hash,
                    attempts=0,
                    expires_at=expires_at,
                    created_at=now,
                )
            )

        self.db._run_write_transaction("create_email_verification_code", write)

    def consume_code(
        self,
        *,
        user_id: int,
        email: str,
        purpose: str,
        code_hash: str,
        max_attempts: int,
        now: Optional[datetime] = None,
    ) -> str:
        """校验并消费验证码。

        返回状态字符串：
        - ``"ok"``：验证码正确，已标记消费。
        - ``"invalid"``：无有效验证码（不存在/已过期/已消费），或验证码不匹配。
        - ``"too_many_attempts"``：错误次数超过上限，验证码作废。
        错误尝试会累加计数，达到上限即消费该验证码，防止暴力猜测。
        """
        current = now or local_naive_now()

        def write(session):
            row = session.execute(
                select(EmailVerificationCodeRecord)
                .where(
                    EmailVerificationCodeRecord.user_id == user_id,
                    EmailVerificationCodeRecord.email == email,
                    EmailVerificationCodeRecord.purpose == purpose,
                    EmailVerificationCodeRecord.consumed_at.is_(None),
                    EmailVerificationCodeRecord.expires_at > current,
                )
                .order_by(EmailVerificationCodeRecord.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return "invalid"
            if row.attempts >= max_attempts:
                row.consumed_at = current
                return "too_many_attempts"
            if row.code_hash != code_hash:
                row.attempts = int(row.attempts) + 1
                if row.attempts >= max_attempts:
                    row.consumed_at = current
                return "invalid"
            row.consumed_at = current
            return "ok"

        return str(self.db._run_write_transaction("consume_email_verification_code", write))
