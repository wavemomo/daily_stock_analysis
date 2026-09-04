# -*- coding: utf-8 -*-
"""Admin auth middleware with WeChat miniapp Bearer-session support."""

from __future__ import annotations

import logging
import re
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.auth import COOKIE_NAME, is_auth_enabled, is_direct_loopback_request, verify_session
from src.services.rbac_service import RbacService
from src.services.wechat_miniapp_auth_service import (
    AVATAR_FILENAME_PATTERN,
    AVATAR_URL_PREFIX,
    WechatMiniappAuthService,
)

logger = logging.getLogger(__name__)
_PUBLIC_AVATAR_PATH_RE = re.compile(
    rf"^{re.escape(AVATAR_URL_PREFIX)}{AVATAR_FILENAME_PATTERN}$"
)

EXEMPT_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/status",
    "/api/v1/miniapp/auth/login",
    "/api/health",
    "/api/v1/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
})


def _path_exempt(path: str, method: str) -> bool:
    """Check if the exact path and HTTP method are exempt from auth."""
    normalized = path.rstrip("/") or "/"
    if method.upper() in {"GET", "HEAD"} and _PUBLIC_AVATAR_PATH_RE.fullmatch(path):
        return True
    return normalized in EXEMPT_PATHS


def _miniapp_bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


class AuthMiddleware(BaseHTTPMiddleware):
    """Require an admin cookie or miniapp Bearer session for protected v1 APIs."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        path = request.url.path
        if request.method.upper() == 'OPTIONS':
            return await call_next(request)
        if _path_exempt(path, request.method) or not path.startswith("/api/v1/"):
            return await call_next(request)
        if path.rstrip('/') == '/api/v1/auth/settings' and not is_auth_enabled():
            if is_direct_loopback_request(request):
                request.state.auth_kind = 'local_bootstrap'
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Initial admin setup is restricted to direct loopback access",
                },
            )

        token = _miniapp_bearer_token(request)

        # 除精确 login 白名单外，所有 miniapp 路由先统一建立 principal；
        # 端点依赖只负责细粒度权限，避免新增路由漏写认证后匿名开放。
        if path.startswith("/api/v1/miniapp/"):
            principal = WechatMiniappAuthService().authenticate_token(token) if token else None
            if principal is None:
                return JSONResponse(
                    status_code=401,
                    content={"error": "unauthorized", "message": "Login required"},
                )
            request.state.auth_kind = 'miniapp'
            request.state.miniapp_principal = principal
            return await call_next(request)

        # 管理后台 Cookie 仅在管理员认证启用且会话有效时作为超级管理员。
        cookie_val = request.cookies.get(COOKIE_NAME)
        if is_auth_enabled() and cookie_val and verify_session(cookie_val):
            request.state.auth_kind = 'admin'
            return await call_next(request)

        principal = WechatMiniappAuthService().authenticate_token(token) if token else None
        if principal is None:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "Login required"},
            )

        required = RbacService.permission_for_request(path, request.method)
        if required is None:
            logger.error("Protected API route has no RBAC policy: %s %s", request.method, path)
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

        request.state.auth_kind = 'miniapp'
        request.state.miniapp_principal = principal
        request.state.required_permission = required
        return await call_next(request)


def add_auth_middleware(app):
    """Add middleware; runtime auth switches remain dynamic."""
    app.add_middleware(AuthMiddleware)
