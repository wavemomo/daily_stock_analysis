# -*- coding: utf-8 -*-
"""邮箱 + 密码认证服务（个人主体绕开微信开放平台扫码登录）。

流程：
1. 小程序内已通过微信认证的用户，输入邮箱 → ``request_email_verification`` 发送验证码。
2. 用户提交邮箱 + 验证码 + 密码 → ``bind_email_password`` 校验验证码后写入凭据。
3. Web 端凭邮箱 + 密码 → ``login`` 校验后由调用方签发 Web 会话 Cookie。

安全约束：
- 密码只保存 pbkdf2-hmac-sha256 派生摘要（标准库，无第三方依赖），带每用户随机 salt。
- 验证码限时、限次、限频；只保存摘要。
- 邮箱仅作登录标识/找回入口，不参与账号自动合并。
- 登录失败对外统一文案，不暴露"邮箱是否存在"。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Optional

from src.config import Config, get_config
from src.repositories.email_password_repo import EmailPasswordRepository

logger = logging.getLogger(__name__)

_PWD_ALGO = "pbkdf2_sha256"
_PWD_ITERATIONS = 600_000
_PWD_SALT_BYTES = 16
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
EMAIL_MAX_LENGTH = 254
_CODE_DIGITS = 6
_VERIFY_PURPOSE_BIND = "bind_email"
# 保守但足够的邮箱格式校验：本地部分 + @ + 含点域名，拒绝空格与多 @。
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailPasswordAuthError(Exception):
    """邮箱密码绑定或登录失败（对外可展示的业务错误）。"""


class EmailPasswordConfigurationError(EmailPasswordAuthError):
    """服务端未配置邮件发送通道，无法下发验证码。"""


class EmailAlreadyBoundError(EmailPasswordAuthError):
    """目标邮箱已被其他用户绑定。"""


@dataclass(frozen=True)
class EmailBindingStatus:
    """当前用户的邮箱密码绑定概览（仅暴露给本人）。"""

    email: Optional[str]
    email_verified: bool
    has_password: bool


def hash_password(password: str) -> str:
    """返回 ``pbkdf2_sha256$iterations$salt_b64$hash_b64`` 格式的密码摘要。"""
    salt = secrets.token_bytes(_PWD_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PWD_ITERATIONS
    )
    return "{algo}${iters}${salt}${hash}".format(
        algo=_PWD_ALGO,
        iters=_PWD_ITERATIONS,
        salt=base64.b64encode(salt).decode("ascii"),
        hash=base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验密码；格式非法或不匹配一律返回 False。"""
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$", 3)
        if algo != _PWD_ALGO:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(derived, expected)
    except (ValueError, TypeError, binascii.Error):
        return False


def _hash_code(code: str, *, user_id: int) -> str:
    """按用户 ID 加盐的验证码摘要；仅存摘要不存明文验证码。"""
    return hashlib.sha256(f"{user_id}:{code}".encode("utf-8")).hexdigest()


class EmailPasswordAuthService:
    """邮箱验证码下发、绑定与密码登录。"""

    def __init__(
        self,
        repository: Optional[EmailPasswordRepository] = None,
        config: Optional[Config] = None,
    ):
        self.repository = repository or EmailPasswordRepository()
        self.config = config or get_config()

    # ---------------- 校验工具 ----------------

    @staticmethod
    def normalize_email(email: str) -> str:
        normalized = (email or "").strip().lower()
        if not normalized or len(normalized) > EMAIL_MAX_LENGTH or not _EMAIL_RE.match(normalized):
            raise EmailPasswordAuthError("邮箱格式不正确")
        return normalized

    @staticmethod
    def _validate_password(password: str) -> str:
        value = password or ""
        if len(value) < PASSWORD_MIN_LENGTH:
            raise EmailPasswordAuthError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 位")
        if len(value) > PASSWORD_MAX_LENGTH:
            raise EmailPasswordAuthError(f"密码不能超过 {PASSWORD_MAX_LENGTH} 位")
        return value

    def _email_channel_ready(self) -> bool:
        return bool((self.config.email_sender or "").strip()) and bool(
            (self.config.email_password or "").strip()
        )

    # ---------------- 绑定状态 ----------------

    def get_binding_status(self, user_id: int) -> EmailBindingStatus:
        credential = self.repository.get_credential_by_user_id(user_id)
        if credential is None:
            return EmailBindingStatus(email=None, email_verified=False, has_password=False)
        return EmailBindingStatus(
            email=credential.email,
            email_verified=credential.email_verified_at is not None,
            has_password=bool(credential.password_hash),
        )

    # ---------------- 验证码 ----------------

    def request_email_verification(self, *, user_id: int, email: str) -> None:
        """向目标邮箱发送绑定验证码。

        - 邮箱已被其他用户绑定 → ``EmailAlreadyBoundError``。
        - 邮件通道未配置 → ``EmailPasswordConfigurationError``（映射 503）。
        - 发送过于频繁 → ``EmailPasswordAuthError``。
        """
        normalized_email = self.normalize_email(email)

        owner_id = self.repository.find_email_owner_id(normalized_email)
        if owner_id is not None and owner_id != user_id:
            raise EmailAlreadyBoundError("该邮箱已被其他账号绑定")

        if not self._email_channel_ready():
            raise EmailPasswordConfigurationError(
                "服务端未配置邮件发送通道（EMAIL_SENDER/EMAIL_PASSWORD），暂时无法发送验证码"
            )

        resend_interval = self.config.email_verification_resend_interval_seconds
        latest = self.repository.latest_active_code_created_at(
            user_id=user_id, email=normalized_email, purpose=_VERIFY_PURPOSE_BIND
        )
        if latest is not None:
            from src.storage import local_naive_now

            elapsed = (local_naive_now() - latest).total_seconds()
            if elapsed < resend_interval:
                wait = int(resend_interval - elapsed) + 1
                raise EmailPasswordAuthError(f"验证码发送过于频繁，请 {wait} 秒后再试")

        code = "".join(secrets.choice("0123456789") for _ in range(_CODE_DIGITS))
        ttl_seconds = self.config.email_verification_code_ttl_seconds
        from datetime import timedelta

        from src.storage import local_naive_now

        expires_at = local_naive_now() + timedelta(seconds=ttl_seconds)
        self.repository.create_code(
            user_id=user_id,
            email=normalized_email,
            purpose=_VERIFY_PURPOSE_BIND,
            code_hash=_hash_code(code, user_id=user_id),
            expires_at=expires_at,
        )

        self._send_code_email(normalized_email, code, ttl_seconds)

    def _send_code_email(self, email: str, code: str, ttl_seconds: int) -> None:
        # 延迟导入，避免在无邮件依赖场景引入 smtplib 相关初始化开销。
        from src.notification_sender.email_sender import EmailSender

        minutes = max(1, ttl_seconds // 60)
        subject = "【主升浪】Web 端登录验证码"
        content = (
            f"你正在为主升浪 Web 端登录绑定邮箱。\n\n"
            f"验证码：{code}\n\n"
            f"验证码 {minutes} 分钟内有效，请勿泄露给他人。若非本人操作请忽略本邮件。"
        )
        try:
            sent = EmailSender(self.config).send_to_email(
                content, subject=subject, receivers=[email]
            )
        except Exception as exc:  # noqa: BLE001 - 邮件底层异常统一转业务错误
            logger.warning("邮箱验证码发送失败: %s", exc)
            raise EmailPasswordAuthError("验证码发送失败，请稍后重试") from exc
        if not sent:
            raise EmailPasswordAuthError("验证码发送失败，请稍后重试")

    # ---------------- 绑定 ----------------

    def bind_email_password(
        self, *, user_id: int, email: str, code: str, password: str
    ) -> EmailBindingStatus:
        """校验验证码后写入邮箱 + 密码凭据（重复绑定即重置密码）。"""
        normalized_email = self.normalize_email(email)
        validated_password = self._validate_password(password)

        owner_id = self.repository.find_email_owner_id(normalized_email)
        if owner_id is not None and owner_id != user_id:
            raise EmailAlreadyBoundError("该邮箱已被其他账号绑定")

        normalized_code = (code or "").strip()
        if not normalized_code:
            raise EmailPasswordAuthError("验证码不能为空")

        outcome = self.repository.consume_code(
            user_id=user_id,
            email=normalized_email,
            purpose=_VERIFY_PURPOSE_BIND,
            code_hash=_hash_code(normalized_code, user_id=user_id),
            max_attempts=self.config.email_verification_max_attempts,
        )
        if outcome == "too_many_attempts":
            raise EmailPasswordAuthError("验证码错误次数过多，请重新获取验证码")
        if outcome != "ok":
            raise EmailPasswordAuthError("验证码无效或已过期")

        from src.storage import local_naive_now

        updated = self.repository.upsert_credential(
            user_id=user_id,
            email=normalized_email,
            password_hash=hash_password(validated_password),
            email_verified_at=local_naive_now(),
        )
        if not updated:
            # 用户已停用，或并发下邮箱被他人抢占。
            raise EmailAlreadyBoundError("邮箱绑定失败，请确认账号状态或更换邮箱")
        return self.get_binding_status(user_id)

    # ---------------- 登录 ----------------

    def login(self, *, email: str, password: str) -> Optional[int]:
        """校验邮箱 + 密码，成功返回 canonical user id，失败返回 None（不区分原因）。"""
        try:
            normalized_email = self.normalize_email(email)
        except EmailPasswordAuthError:
            return None
        if not password:
            return None
        credential = self.repository.get_active_credential_by_email(normalized_email)
        if credential is None or not credential.password_hash:
            # 统一做一次哈希消耗，缓解通过响应时间探测邮箱是否存在。
            verify_password(password, hash_password(secrets.token_urlsafe(16)))
            return None
        if credential.email_verified_at is None:
            return None
        if not verify_password(password, credential.password_hash):
            return None
        return int(credential.user_id)
