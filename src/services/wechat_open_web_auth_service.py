# -*- coding: utf-8 -*-
"""微信开放平台网站扫码 OAuth 的纯服务端认证适配层。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import secrets
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from src.config import Config, get_config
from src.repositories.auth_identity_repo import AuthIdentityConflictError
from src.repositories.web_user_auth_repo import WebUserAuthRepository
from src.services.identity_service import IdentityService
from src.storage import local_naive_now

WECHAT_OPEN_WEB_AUTHORIZE_URL = "https://open.weixin.qq.com/connect/qrconnect"
WECHAT_OPEN_WEB_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
WECHAT_OPEN_WEB_USERINFO_URL = "https://api.weixin.qq.com/sns/userinfo"


class WechatOpenWebAuthError(Exception):
    """网站微信登录失败；对外仅暴露泛化错误。"""


class WechatOpenWebAuthConfigurationError(WechatOpenWebAuthError):
    """网站微信 OAuth 的服务端配置缺失。"""


@dataclass(frozen=True)
class WebWechatLoginStart:
    authorization_url: str
    browser_binding: str
    expires_at: datetime


@dataclass(frozen=True)
class WebWechatIdentity:
    user_id: int
    created: bool


class WechatOpenWebAuthService:
    """生成官方扫码 URL，并在 callback 中完成 state 与 code 的服务端处理。"""

    def __init__(
        self,
        repository: Optional[WebUserAuthRepository] = None,
        identity_service: Optional[IdentityService] = None,
        config: Optional[Config] = None,
    ):
        self.repository = repository or WebUserAuthRepository()
        self.identity_service = identity_service or IdentityService()
        self.config = config or get_config()

    @staticmethod
    def hash_value(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def start_login(self) -> WebWechatLoginStart:
        app_id, _, redirect_uri = self._oauth_config()
        state = secrets.token_urlsafe(32)
        browser_binding = secrets.token_urlsafe(32)
        expires_at = local_naive_now() + timedelta(
            seconds=self.config.wechat_open_web_state_ttl_seconds
        )
        self.repository.create_wechat_login_transaction(
            state_hash=self.hash_value(state),
            browser_binding_hash=self.hash_value(browser_binding),
            redirect_uri=redirect_uri,
            expires_at=expires_at,
        )
        authorization_params = {
            "appid": app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "snsapi_login",
            "state": state,
        }
        authorization_url = (
            f"{WECHAT_OPEN_WEB_AUTHORIZE_URL}?{urlencode(authorization_params)}"
            "#wechat_redirect"
        )
        return WebWechatLoginStart(
            authorization_url=authorization_url,
            browser_binding=browser_binding,
            expires_at=expires_at,
        )

    def complete_callback(
        self,
        *,
        code: str,
        state: str,
        browser_binding: str,
    ) -> WebWechatIdentity:
        normalized_code = (code or "").strip()
        normalized_state = (state or "").strip()
        normalized_binding = (browser_binding or "").strip()
        if not normalized_code or not normalized_state or not normalized_binding:
            raise WechatOpenWebAuthError("微信网站登录验证失败")
        app_id, _, configured_redirect_uri = self._oauth_config()
        callback_redirect_uri = self.repository.consume_wechat_login_transaction(
            state_hash=self.hash_value(normalized_state),
            browser_binding_hash=self.hash_value(normalized_binding),
        )
        if callback_redirect_uri != configured_redirect_uri:
            raise WechatOpenWebAuthError("微信网站登录验证失败")
        payload = self._exchange_code(normalized_code)
        openid = str(payload.get("openid") or "").strip()
        if not openid:
            raise WechatOpenWebAuthError("微信网站登录验证失败")
        unionid = str(payload.get("unionid") or "").strip() or None
        try:
            user, created = self.identity_service.resolve_open_web_user(
                app_id=app_id,
                openid=openid,
                unionid=unionid,
            )
        except (AuthIdentityConflictError, ValueError) as exc:
            raise WechatOpenWebAuthError("当前微信身份暂时无法登录，请稍后重试") from exc
        return WebWechatIdentity(user_id=int(user.id), created=created)

    def _oauth_config(self) -> tuple[str, str, str]:
        app_id = (self.config.wechat_open_web_app_id or "").strip()
        app_secret = (self.config.wechat_open_web_app_secret or "").strip()
        redirect_uri = (self.config.wechat_open_web_redirect_uri or "").strip()
        if not app_id or not app_secret or not redirect_uri:
            raise WechatOpenWebAuthConfigurationError(
                "服务端未完成微信网站扫码登录配置"
            )
        if not redirect_uri.startswith("https://"):
            raise WechatOpenWebAuthConfigurationError(
                "微信网站扫码回调地址必须使用 HTTPS"
            )
        return app_id, app_secret, redirect_uri

    def _exchange_code(self, code: str) -> dict[str, Any]:
        app_id, app_secret, _ = self._oauth_config()
        try:
            token_response = httpx.get(
                WECHAT_OPEN_WEB_TOKEN_URL,
                params={
                    "appid": app_id,
                    "secret": app_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
                timeout=8.0,
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            if not isinstance(token_payload, dict) or token_payload.get("errcode"):
                raise ValueError("invalid token response")
            access_token = str(token_payload.get("access_token") or "").strip()
            openid = str(token_payload.get("openid") or "").strip()
            if not access_token or not openid:
                raise ValueError("missing token identity")

            userinfo_response = httpx.get(
                WECHAT_OPEN_WEB_USERINFO_URL,
                params={
                    "access_token": access_token,
                    "openid": openid,
                    "lang": "zh_CN",
                },
                timeout=8.0,
            )
            userinfo_response.raise_for_status()
            userinfo_payload = userinfo_response.json()
            if not isinstance(userinfo_payload, dict) or userinfo_payload.get("errcode"):
                raise ValueError("invalid userinfo response")
            returned_openid = str(userinfo_payload.get("openid") or "").strip()
            if returned_openid != openid:
                raise ValueError("userinfo identity mismatch")
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise WechatOpenWebAuthError("微信网站登录服务暂时不可用") from exc

        # UnionID may arrive in either response. It is only a trusted bridge when
        # returned by this server-side OAuth exchange, never from browser input.
        return {
            "openid": openid,
            "unionid": (
                str(userinfo_payload.get("unionid") or token_payload.get("unionid") or "").strip()
                or None
            ),
        }
