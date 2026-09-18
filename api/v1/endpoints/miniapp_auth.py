# -*- coding: utf-8 -*-
"""微信小程序登录与本人展示资料端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse

from api.deps import get_current_miniapp_principal, require_permission
from api.v1.schemas.miniapp import (
    MiniappEmailBindRequest,
    MiniappEmailBindingResponse,
    MiniappEmailCodeRequest,
    MiniappEmailCodeSentResponse,
    MiniappReportEmailToggleRequest,
    MiniappLoginRequest,
    MiniappLoginResponse,
    MiniappProfileUpdateRequest,
    MiniappUserItem,
    MiniappIdentityBindApproveRequest,
    MiniappIdentityBindApproveResponse,
)
from src.services.email_password_auth_service import (
    EmailAlreadyBoundError,
    EmailPasswordAuthError,
    EmailPasswordAuthService,
    EmailPasswordConfigurationError,
)
from src.services.web_user_auth_service import WebUserAuthService
from src.services.wechat_miniapp_auth_service import (
    MAX_AVATAR_BYTES,
    MiniappAuthConfigurationError,
    MiniappAuthError,
    MiniappPrincipal,
    WechatMiniappAuthService,
)

router = APIRouter()
# 账户自助端点（本人资料 / Web 邮箱），供小程序 Bearer 与 Web Cookie 复用。
# 通过 router.include_router(account_router) 挂在 /miniapp/auth 下保持小程序兼容，
# 同时在 api/v1/router.py 以中性前缀 /account 挂载供 Web 复用。
account_router = APIRouter()


@router.post("/login", response_model=MiniappLoginResponse, summary="微信小程序登录")
def login(request: MiniappLoginRequest) -> MiniappLoginResponse:
    try:
        result = WechatMiniappAuthService().login(request.code)
        return MiniappLoginResponse(**result)
    except MiniappAuthConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except MiniappAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


@account_router.get("/me", response_model=MiniappUserItem, summary="当前用户资料")
def me(
    principal: MiniappPrincipal = Depends(get_current_miniapp_principal),
) -> MiniappUserItem:
    return MiniappUserItem(
        **WechatMiniappAuthService.serialize_user(
            principal.user,
            {'roles': principal.roles, 'permissions': principal.permissions},
        )
    )


@account_router.patch("/me", response_model=MiniappUserItem, summary="更新当前用户昵称")
def update_me(
    request: MiniappProfileUpdateRequest,
    principal: MiniappPrincipal = Depends(require_permission('account.self')),
) -> MiniappUserItem:
    try:
        payload = WechatMiniappAuthService().update_profile(
            user_id=int(principal.user.id),
            nickname=request.nickname,
        )
        return MiniappUserItem(**payload)
    except MiniappAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@account_router.post(
    "/me/avatar",
    response_model=MiniappUserItem,
    summary="上传当前用户头像",
)
async def upload_me_avatar(
    file: UploadFile = File(...),
    principal: MiniappPrincipal = Depends(require_permission('account.self')),
) -> MiniappUserItem:
    try:
        content = await file.read(MAX_AVATAR_BYTES + 1)
        payload = WechatMiniappAuthService().save_avatar(
            user_id=int(principal.user.id),
            content=content,
        )
        return MiniappUserItem(**payload)
    except MiniappAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        await file.close()


@router.api_route(
    "/public/avatars/{filename}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
    response_class=FileResponse,
)
def get_public_avatar(filename: str) -> FileResponse:
    service = WechatMiniappAuthService()
    path = service.avatar_path(filename)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="头像不存在")
    media_type = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@account_router.get(
    "/email",
    response_model=MiniappEmailBindingResponse,
    summary="当前用户的 Web 邮箱登录绑定状态",
)
def get_email_binding(
    principal: MiniappPrincipal = Depends(require_permission('account.self')),
) -> MiniappEmailBindingResponse:
    status_ = EmailPasswordAuthService().get_binding_status(int(principal.user.id))
    return MiniappEmailBindingResponse(
        email=status_.email,
        email_verified=status_.email_verified,
        has_password=status_.has_password,
        report_email_enabled=status_.report_email_enabled,
    )


@account_router.patch(
    "/email/report-delivery",
    response_model=MiniappEmailBindingResponse,
    summary="切换生成的报告是否发送到已绑定邮箱",
)
def set_report_email_delivery(
    request: MiniappReportEmailToggleRequest,
    principal: MiniappPrincipal = Depends(require_permission('account.self')),
) -> MiniappEmailBindingResponse:
    try:
        binding = EmailPasswordAuthService().set_report_email_enabled(
            user_id=int(principal.user.id),
            enabled=request.enabled,
        )
    except EmailPasswordAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return MiniappEmailBindingResponse(
        email=binding.email,
        email_verified=binding.email_verified,
        has_password=binding.has_password,
        report_email_enabled=binding.report_email_enabled,
    )


@account_router.post(
    "/email/request-code",
    response_model=MiniappEmailCodeSentResponse,
    summary="发送 Web 邮箱登录绑定验证码",
)
def request_email_code(
    request: MiniappEmailCodeRequest,
    principal: MiniappPrincipal = Depends(require_permission('account.self')),
) -> MiniappEmailCodeSentResponse:
    try:
        EmailPasswordAuthService().request_email_verification(
            user_id=int(principal.user.id),
            email=request.email,
        )
    except EmailPasswordConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except EmailAlreadyBoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except EmailPasswordAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return MiniappEmailCodeSentResponse(sent=True)


@account_router.post(
    "/email/bind",
    response_model=MiniappEmailBindingResponse,
    summary="绑定 Web 邮箱密码登录（重复绑定即重置密码）",
)
def bind_email(
    request: MiniappEmailBindRequest,
    principal: MiniappPrincipal = Depends(require_permission('account.self')),
) -> MiniappEmailBindingResponse:
    try:
        binding = EmailPasswordAuthService().bind_email_password(
            user_id=int(principal.user.id),
            email=request.email,
            code=request.code,
            password=request.password,
        )
    except EmailAlreadyBoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except EmailPasswordAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return MiniappEmailBindingResponse(
        email=binding.email,
        email_verified=binding.email_verified,
        has_password=binding.has_password,
        report_email_enabled=binding.report_email_enabled,
    )


@router.post(
    "/identity-bind/approve",
    response_model=MiniappIdentityBindApproveResponse,
    summary="批准 Web 端显式身份绑定",
)
def approve_identity_bind(
    request: MiniappIdentityBindApproveRequest,
    principal: MiniappPrincipal = Depends(require_permission('account.self')),
) -> MiniappIdentityBindApproveResponse:
    expires_at = WebUserAuthService().approve_identity_bind(
        challenge=request.challenge,
        approved_user_id=int(principal.user.id),
    )
    if expires_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_identity_bind", "message": "身份绑定挑战无效、已过期或已处理"},
        )
    return MiniappIdentityBindApproveResponse(
        status="approved",
        expires_at=expires_at.isoformat(),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="退出小程序登录",
)
def logout(
    principal: MiniappPrincipal = Depends(get_current_miniapp_principal),
) -> Response:
    WechatMiniappAuthService().revoke(principal.token_hash)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# 小程序保持在 /miniapp/auth 前缀下访问本人资料 / 邮箱端点（Bearer 认证）。
router.include_router(account_router)
