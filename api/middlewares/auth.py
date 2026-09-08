# -*- coding: utf-8 -*-
"""Web Cookie 与小程序 Bearer 的统一认证和授权边界。"""

from __future__ import annotations

import logging
import os
import re
from typing import Callable, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.services.rbac_service import RbacService
from src.services.web_user_auth_service import WEB_USER_COOKIE_NAME, WebUserAuthService
from src.services.wechat_miniapp_auth_service import (
    AVATAR_FILENAME_PATTERN,
    AVATAR_URL_PREFIX,
    WechatMiniappAuthService,
)

logger = logging.getLogger(__name__)
_PUBLIC_AVATAR_PATH_RE = re.compile(rf"^{re.escape(AVATAR_URL_PREFIX)}{AVATAR_FILENAME_PATTERN}$")
_WEB_WECHAT_CALLBACK_PATH = "/api/v1/web-auth/wechat/callback"
_WEB_USER_PROTECTED_PATHS = frozenset({
    "/api/v1/web-auth/me",
    "/api/v1/web-auth/logout",
    "/api/v1/web-auth/identity-bind/start",
    "/api/v1/web-auth/identity-bind/consume",
})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_WEB_ORIGINS = frozenset({
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
})

EXEMPT_PATHS = frozenset({
    "/api/v1/miniapp/auth/login",
    "/api/v1/web-auth/wechat/start",
    "/api/v1/web-auth/wechat/callback",
    "/api/health",
    "/api/v1/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
})


def _trusted_web_origins() -> frozenset[str]:
    """Return exact configured origins trusted for Cookie-authenticated writes."""
    extra_origins = os.environ.get("CORS_ORIGINS", "")
    configured = {
        origin.strip()
        for origin in extra_origins.split(",")
        if origin.strip() and origin.strip() != "*"
    }
    return _DEFAULT_WEB_ORIGINS | frozenset(configured)


def _is_web_wechat_callback(path: str, method: str) -> bool:
    """OAuth callback may be anonymous only before any current credential exists."""
    normalized = path.rstrip("/") or "/"
    return method.upper() == "GET" and normalized == _WEB_WECHAT_CALLBACK_PATH


def _path_exempt(path: str, method: str) -> bool:
    """Check publicly reachable paths; callback conflicts are handled separately."""
    normalized = path.rstrip("/") or "/"
    if method.upper() in {"GET", "HEAD"} and _PUBLIC_AVATAR_PATH_RE.fullmatch(path):
        return True
    return normalized in EXEMPT_PATHS


def _miniapp_bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _csrf_failure() -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"error": "csrf_failed", "message": "Invalid Origin or CSRF token"},
    )


def _authentication_conflict() -> JSONResponse:
    """Reject a valid browser session combined with a miniapp Bearer credential."""
    return JSONResponse(
        status_code=400,
        content={
            "error": "authentication_conflict",
            "message": "Cookie 会话不能与小程序 Bearer 凭证同时使用",
        },
    )


def web_wechat_login_credentials_response(request: Request) -> Optional[JSONResponse]:
    """拒绝用既有本地登录态重新发起或完成微信扫码登录。"""
    web_session_value = request.cookies.get(WEB_USER_COOKIE_NAME, "")
    web_principal = (
        WebUserAuthService().authenticate_session(web_session_value)
        if web_session_value
        else None
    )
    if web_principal is not None or _miniapp_bearer_token(request):
        return JSONResponse(
            status_code=400,
            content={
                "error": "authentication_conflict",
                "message": "微信扫码登录只能由未登录的授权浏览器发起",
            },
        )
    return None


def _authorize_principal(request: Request, principal, *, auth_kind: str) -> Optional[JSONResponse]:
    required = RbacService.permission_for_request(request.url.path, request.method)
    if required is None:
        logger.error("Protected API route has no RBAC policy: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "API permission policy missing"},
        )
    if required not in principal.permissions:
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "message": f"Permission denied: {required}",
                "required_permission": required,
            },
        )
    request.state.auth_kind = auth_kind
    request.state.miniapp_principal = principal
    request.state.required_permission = required
    return None


def _validate_web_csrf(request: Request, session_value: str) -> Optional[JSONResponse]:
    if request.method.upper() not in _UNSAFE_METHODS:
        return None
    if (
        request.headers.get("origin", "") not in _trusted_web_origins()
        or not WebUserAuthService.verify_csrf_token(
            session_value,
            request.headers.get("x-csrf-token", ""),
        )
    ):
        return _csrf_failure()
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    """Keep Web Cookie and miniapp Bearer credentials isolated and user-scoped."""

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        normalized_path = path.rstrip("/") or "/"
        if request.method.upper() == "OPTIONS" or not path.startswith("/api/v1/"):
            return await call_next(request)
        if _is_web_wechat_callback(path, request.method):
            credentials_response = web_wechat_login_credentials_response(request)
            if credentials_response is not None:
                return credentials_response
            return await call_next(request)
        if normalized_path == "/api/v1/miniapp/auth/login":
            token = _miniapp_bearer_token(request)
            web_session_value = request.cookies.get(WEB_USER_COOKIE_NAME, "")
            web_principal = (
                WebUserAuthService().authenticate_session(web_session_value)
                if web_session_value
                else None
            )
            if web_principal is not None and token:
                return _authentication_conflict()
            return await call_next(request)
        if _path_exempt(path, request.method):
            return await call_next(request)

        token = _miniapp_bearer_token(request)
        web_session_value = request.cookies.get(WEB_USER_COOKIE_NAME, "")
        web_principal = (
            WebUserAuthService().authenticate_session(web_session_value)
            if web_session_value
            else None
        )
        if web_principal is not None and token:
            return _authentication_conflict()

        # Miniapp endpoints intentionally accept only wx.login-issued Bearer sessions.
        if path.startswith("/api/v1/miniapp/"):
            if web_principal is not None:
                return _authentication_conflict()
            principal = WechatMiniappAuthService().authenticate_token(token) if token else None
            if principal is None:
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "message": "Login required"},
                )
            response = _authorize_principal(request, principal, auth_kind="miniapp")
            if response is not None:
                return response
            return await call_next(request)

        # Web identity inspection, logout, and binding are Cookie-only protocol endpoints.
        if normalized_path in _WEB_USER_PROTECTED_PATHS:
            if web_principal is None:
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "message": "Web login required"},
                )
            csrf_response = _validate_web_csrf(request, web_session_value)
            if csrf_response is not None:
                return csrf_response
            request.state.auth_kind = "web_user"
            request.state.miniapp_principal = web_principal
            return await call_next(request)

        if web_principal is not None:
            response = _authorize_principal(request, web_principal, auth_kind="web_user")
            if response is not None:
                return response
            csrf_response = _validate_web_csrf(request, web_session_value)
            if csrf_response is not None:
                return csrf_response
            return await call_next(request)

        principal = WechatMiniappAuthService().authenticate_token(token) if token else None
        if principal is None:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "Login required"},
            )
        response = _authorize_principal(request, principal, auth_kind="miniapp")
        if response is not None:
            return response
        return await call_next(request)


def add_auth_middleware(app):
    """Add middleware; runtime access is derived from live sessions and RBAC."""
    app.add_middleware(AuthMiddleware)
