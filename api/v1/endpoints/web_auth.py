# -*- coding: utf-8 -*-
"""普通 Web 用户的微信开放平台扫码 OAuth 与独立会话端点。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from api.deps import get_current_web_user_principal
from api.middlewares.auth import web_wechat_login_credentials_response
from api.v1.schemas.web_auth import (
    IdentityBindConsumeRequest,
    IdentityBindConsumeResponse,
    IdentityBindStartResponse,
    WebUserSessionResponse,
)
from src.config import get_config
from src.services.web_user_auth_service import WEB_USER_COOKIE_NAME, WebUserAuthService
from src.services.wechat_miniapp_auth_service import MiniappPrincipal, WechatMiniappAuthService
from src.services.wechat_open_web_auth_service import (
    WechatOpenWebAuthConfigurationError,
    WechatOpenWebAuthError,
    WechatOpenWebAuthService,
)

router = APIRouter()
_WEB_WECHAT_BINDING_COOKIE_NAME = "dsa_wechat_oauth_binding"
_WEB_WECHAT_CALLBACK_PATH = "/api/v1/web-auth/wechat/callback"


def _cookie_params(request: Request, *, max_age: int) -> dict:
    secure = request.url.scheme == "https"
    if os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true":
        secure = request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": "/",
        "max_age": max_age,
    }


def _oauth_binding_cookie_params(request: Request, *, max_age: int) -> dict:
    """允许微信跨站顶级回跳时携带的 callback 专属 HttpOnly 绑定 cookie。"""
    secure = request.url.scheme == "https"
    if os.getenv("TRUST_X_FORWARDED_FOR", "false").lower() == "true":
        secure = request.headers.get("X-Forwarded-Proto", "").lower() == "https"
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": secure,
        "path": _WEB_WECHAT_CALLBACK_PATH,
        "max_age": max_age,
    }


def _no_store_response(content: dict, *, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    return JSONResponse(
        content=content,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _oauth_error_response(request: Request, *, status_code: int) -> JSONResponse:
    response = _no_store_response(
        {"error": "wechat_login_failed", "message": "微信扫码登录暂时不可用，请稍后重试。"},
        status_code=status_code,
    )
    response.delete_cookie(key=_WEB_WECHAT_BINDING_COOKIE_NAME, path=_WEB_WECHAT_CALLBACK_PATH)
    return response


def _serialize_web_user(principal: MiniappPrincipal) -> dict:
    return WechatMiniappAuthService.serialize_user(
        principal.user,
        {"roles": principal.roles, "permissions": principal.permissions},
    )


@router.get("/wechat/start", summary="跳转至微信开放平台网站扫码授权")
def start_wechat_login(request: Request) -> Response:
    credentials_response = web_wechat_login_credentials_response(request)
    if credentials_response is not None:
        return credentials_response
    try:
        challenge = WechatOpenWebAuthService().start_login()
    except WechatOpenWebAuthConfigurationError:
        return _oauth_error_response(request, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    response = RedirectResponse(
        url=challenge.authorization_url,
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        key=_WEB_WECHAT_BINDING_COOKIE_NAME,
        value=challenge.browser_binding,
        **_oauth_binding_cookie_params(
            request,
            max_age=get_config().wechat_open_web_state_ttl_seconds,
        ),
    )
    return response


@router.get("/wechat/callback", summary="处理微信扫码 OAuth 回调")
def complete_wechat_login(
    request: Request,
    code: str = "",
    state: str = "",
) -> Response:
    credentials_response = web_wechat_login_credentials_response(request)
    if credentials_response is not None:
        return credentials_response
    try:
        identity = WechatOpenWebAuthService().complete_callback(
            code=code,
            state=state,
            browser_binding=request.cookies.get(_WEB_WECHAT_BINDING_COOKIE_NAME, ""),
        )
        issue = WebUserAuthService().create_session_for_user(
            user_id=identity.user_id,
            assign_default_role=identity.created,
        )
    except WechatOpenWebAuthConfigurationError:
        return _oauth_error_response(request, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    except WechatOpenWebAuthError:
        return _oauth_error_response(request, status_code=status.HTTP_400_BAD_REQUEST)
    if issue is None:
        return _oauth_error_response(request, status_code=status.HTTP_400_BAD_REQUEST)

    # OAuth codes/tokens never reach the SPA: only the local session Cookie does.
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        key=WEB_USER_COOKIE_NAME,
        value=issue.session_value,
        **_cookie_params(
            request,
            max_age=WebUserAuthService().config.web_user_session_ttl_seconds,
        ),
    )
    response.delete_cookie(key=_WEB_WECHAT_BINDING_COOKIE_NAME, path=_WEB_WECHAT_CALLBACK_PATH)
    return response


@router.get("/me", response_model=WebUserSessionResponse, summary="当前普通 Web 用户")
def me(
    request: Request,
    principal: MiniappPrincipal = Depends(get_current_web_user_principal),
) -> JSONResponse:
    session_value = request.cookies.get(WEB_USER_COOKIE_NAME, "")
    return _no_store_response({
        "user": _serialize_web_user(principal),
        "csrf_token": WebUserAuthService.create_csrf_token(session_value),
    })


@router.post(
    "/identity-bind/start",
    response_model=IdentityBindStartResponse,
    summary="创建需要小程序确认的显式身份绑定挑战",
)
def start_identity_bind(
    principal: MiniappPrincipal = Depends(get_current_web_user_principal),
) -> JSONResponse:
    challenge = WebUserAuthService().start_identity_bind(
        requested_user_id=int(principal.user.id)
    )
    if challenge is None:
        return _no_store_response(
            {"error": "identity_bind_failed", "message": "身份绑定挑战创建失败，请重新登录后再试。"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return _no_store_response({
        "challenge": challenge.challenge,
        "expires_at": challenge.expires_at.isoformat(),
    })


@router.post(
    "/identity-bind/consume",
    response_model=IdentityBindConsumeResponse,
    summary="消费已由小程序确认的显式身份绑定挑战",
)
def consume_identity_bind(
    payload: IdentityBindConsumeRequest,
    principal: MiniappPrincipal = Depends(get_current_web_user_principal),
) -> JSONResponse:
    outcome = WebUserAuthService().consume_identity_bind(
        challenge=payload.challenge,
        requested_user_id=int(principal.user.id),
    )
    return _no_store_response({"status": outcome})


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="退出普通 Web 用户")
def logout(
    request: Request,
    _: MiniappPrincipal = Depends(get_current_web_user_principal),
) -> Response:
    WebUserAuthService().revoke_session(request.cookies.get(WEB_USER_COOKIE_NAME, ""))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(key=WEB_USER_COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response
