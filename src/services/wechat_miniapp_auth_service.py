# -*- coding: utf-8 -*-
"""微信小程序登录、本地会话与可选展示资料服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
import hashlib
from pathlib import Path
import re
import secrets
from typing import Any, Dict, Optional
import warnings

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from src.config import Config, get_config
from src.repositories.auth_identity_repo import AuthIdentityConflictError
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.rbac_service import RbacService
from src.storage import MiniappUserRecord

CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
AVATAR_URL_PREFIX = "/api/v1/miniapp/auth/public/avatars/"
AVATAR_FILENAME_PATTERN = r"[A-Za-z0-9_-]{24,64}\.(?:jpg|png|webp)"
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_AVATAR_DIMENSION = 4096
MAX_AVATAR_PIXELS = 16_000_000
_AVATAR_FILENAME_RE = re.compile(rf"^{AVATAR_FILENAME_PATTERN}$")


class MiniappAuthError(Exception):
    """小程序认证或本人资料更新失败。"""


class MiniappAuthConfigurationError(MiniappAuthError):
    """服务端未完成微信小程序配置。"""


@dataclass(frozen=True)
class MiniappPrincipal:
    """已认证的小程序用户、当前会话及实时 RBAC 权限。"""

    user: MiniappUserRecord
    token_hash: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]


class WechatMiniappAuthService:
    """使用微信临时 code 建立可撤销的本地 Bearer 会话。"""

    def __init__(
        self,
        repository: Optional[MiniappUserRepository] = None,
        config: Optional[Config] = None,
    ):
        self.repository = repository or MiniappUserRepository()
        self.config = config or get_config()

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def login(self, code: str) -> Dict[str, Any]:
        normalized_code = (code or "").strip()
        if not normalized_code:
            raise MiniappAuthError("微信登录凭证不能为空")

        payload = self._exchange_code(normalized_code)
        openid = str(payload.get("openid") or "").strip()
        if not openid:
            raise MiniappAuthError("微信登录未返回用户标识")

        unionid = str(payload.get("unionid") or "").strip() or None
        try:
            user, created = self.repository.upsert_user_with_status(
                openid=openid,
                unionid=unionid,
                issuer=self.config.wechat_miniapp_app_id,
            )
        except AuthIdentityConflictError as exc:
            # Do not reveal whether another account owns an identity mapping.
            raise MiniappAuthError("当前微信身份暂时无法登录，请稍后重试") from exc
        access = RbacService().ensure_user_access(user, assign_default=created)
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(
            hours=self.config.wechat_miniapp_session_ttl_hours
        )
        self.repository.create_session(
            user_id=user.id,
            token_hash=self.hash_token(raw_token),
            expires_at=expires_at,
        )
        return {
            "access_token": raw_token,
            "expires_at": expires_at.isoformat(),
            "user": self.serialize_user(user, access),
        }

    def authenticate_token(self, token: str) -> Optional[MiniappPrincipal]:
        normalized = (token or "").strip()
        if not normalized:
            return None
        token_hash = self.hash_token(normalized)
        user = self.repository.get_user_by_session_hash(token_hash)
        if user is None:
            return None
        access = RbacService().ensure_user_access(user)
        return MiniappPrincipal(
            user=user,
            token_hash=token_hash,
            roles=tuple(access['roles']),
            permissions=tuple(access['permissions']),
        )

    def revoke(self, token_hash: str) -> bool:
        return self.repository.revoke_session(token_hash)

    def update_profile(self, *, user_id: int, nickname: Optional[str]) -> Dict[str, Any]:
        """更新当前用户昵称；身份和权限不由展示资料决定。"""
        normalized_nickname = (nickname or "").strip() or None
        user = self.repository.update_profile(
            user_id=user_id,
            nickname=normalized_nickname,
            avatar_url=None,
        )
        if user is None:
            raise MiniappAuthError("当前微信用户不存在或已停用")
        return self.serialize_user(user)

    def save_avatar(self, *, user_id: int, content: bytes) -> Dict[str, Any]:
        """完整解码并重编码当前用户通过 chooseAvatar 选择的头像。"""
        extension, normalized_content = self._normalize_avatar(content)
        avatar_dir = self._avatar_directory()
        avatar_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{secrets.token_urlsafe(24)}.{extension}"
        target = avatar_dir / filename
        target.write_bytes(normalized_content)
        try:
            previous = self.repository.get_user_by_id(user_id)
            user = self.repository.update_profile(
                user_id=user_id,
                nickname=None,
                avatar_url=f"{AVATAR_URL_PREFIX}{filename}",
            )
            if user is None:
                raise MiniappAuthError("当前微信用户不存在或已停用")
        except Exception:
            target.unlink(missing_ok=True)
            raise

        if previous and previous.avatar_url:
            old_name = previous.avatar_url.removeprefix(AVATAR_URL_PREFIX)
            if old_name != filename and _AVATAR_FILENAME_RE.fullmatch(old_name):
                (avatar_dir / old_name).unlink(missing_ok=True)
        return self.serialize_user(user)

    def avatar_path(self, filename: str) -> Optional[Path]:
        """解析公开头像的不透明文件名，拒绝目录穿越。"""
        if not _AVATAR_FILENAME_RE.fullmatch(filename or ""):
            return None
        candidate = self._avatar_directory() / filename
        return candidate if candidate.is_file() else None

    def _avatar_directory(self) -> Path:
        database_path = Path(self.config.database_path).expanduser().resolve()
        return database_path.parent / "miniapp_avatars"

    @staticmethod
    def _normalize_avatar(content: bytes) -> tuple[str, bytes]:
        if not content:
            raise MiniappAuthError("微信头像文件为空")
        if len(content) > MAX_AVATAR_BYTES:
            raise MiniappAuthError("微信头像不能超过 2MB")

        allowed_formats = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as inspected:
                    source_format = str(inspected.format or "").upper()
                    if source_format not in allowed_formats:
                        raise MiniappAuthError("微信头像仅支持 JPEG、PNG 或 WebP")
                    width, height = inspected.size
                    if (
                        width < 1
                        or height < 1
                        or width > MAX_AVATAR_DIMENSION
                        or height > MAX_AVATAR_DIMENSION
                        or width * height > MAX_AVATAR_PIXELS
                    ):
                        raise MiniappAuthError(
                            "微信头像尺寸不能超过 4096×4096 或 1600 万像素"
                        )
                    if getattr(inspected, "is_animated", False):
                        raise MiniappAuthError("微信头像仅支持单帧图片")
                    inspected.verify()

                with Image.open(BytesIO(content)) as source:
                    source.load()
                    normalized = ImageOps.exif_transpose(source)
                    normalized.load()
                    has_alpha = normalized.mode in {"RGBA", "LA"} or (
                        "transparency" in normalized.info
                    )
                    output = BytesIO()
                    if source_format == "JPEG":
                        normalized.convert("RGB").save(
                            output,
                            format="JPEG",
                            quality=90,
                            optimize=True,
                        )
                    elif source_format == "PNG":
                        normalized.convert("RGBA" if has_alpha else "RGB").save(
                            output,
                            format="PNG",
                            optimize=True,
                        )
                    else:
                        normalized.convert("RGBA" if has_alpha else "RGB").save(
                            output,
                            format="WEBP",
                            quality=90,
                            method=4,
                        )
        except MiniappAuthError:
            raise
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            UnidentifiedImageError,
            OSError,
            SyntaxError,
            ValueError,
        ) as exc:
            raise MiniappAuthError("微信头像文件损坏或无法解码") from exc

        normalized_content = output.getvalue()
        if not normalized_content or len(normalized_content) > MAX_AVATAR_BYTES:
            raise MiniappAuthError("处理后的微信头像不能超过 2MB")
        return allowed_formats[source_format], normalized_content

    @staticmethod
    def serialize_user(
        user: MiniappUserRecord,
        access: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        resolved = access or RbacService().ensure_user_access(user)
        return {
            "id": int(user.id),
            "nickname": user.nickname,
            "avatar_url": user.avatar_url,
            "profile_updated_at": (
                user.profile_updated_at.isoformat()
                if user.profile_updated_at else None
            ),
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "roles": list(resolved['roles']),
            "permissions": list(resolved['permissions']),
        }

    def _exchange_code(self, code: str) -> Dict[str, Any]:
        app_id = (self.config.wechat_miniapp_app_id or "").strip()
        app_secret = (self.config.wechat_miniapp_app_secret or "").strip()
        if not app_id or not app_secret:
            raise MiniappAuthConfigurationError(
                "服务端未配置 WECHAT_MINIAPP_APP_ID/WECHAT_MINIAPP_APP_SECRET"
            )

        try:
            response = httpx.get(
                CODE2SESSION_URL,
                params={
                    "appid": app_id,
                    "secret": app_secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
                timeout=self.config.wechat_miniapp_code2session_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MiniappAuthError("微信登录服务暂时不可用") from exc

        if not isinstance(payload, dict):
            raise MiniappAuthError("微信登录响应格式无效")
        if payload.get("errcode"):
            raise MiniappAuthError(
                str(payload.get("errmsg") or "微信登录凭证无效")
            )
        return payload
