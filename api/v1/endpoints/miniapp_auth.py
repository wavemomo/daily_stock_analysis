# -*- coding: utf-8 -*-
"""微信小程序登录与本人展示资料端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse

from api.deps import get_current_miniapp_principal, require_permission
from api.v1.schemas.miniapp import (
    MiniappLoginRequest,
    MiniappLoginResponse,
    MiniappProfileUpdateRequest,
    MiniappUserItem,
)
from src.services.wechat_miniapp_auth_service import (
    MAX_AVATAR_BYTES,
    MiniappAuthConfigurationError,
    MiniappAuthError,
    MiniappPrincipal,
    WechatMiniappAuthService,
)

router = APIRouter()


@router.post("/login", response_model=MiniappLoginResponse, summary="微信小程序登录")
def login(request: MiniappLoginRequest) -> MiniappLoginResponse:
    try:
        result = WechatMiniappAuthService().login(request.code)
        return MiniappLoginResponse(**result)
    except MiniappAuthConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except MiniappAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


@router.get("/me", response_model=MiniappUserItem, summary="当前小程序用户")
def me(
    principal: MiniappPrincipal = Depends(get_current_miniapp_principal),
) -> MiniappUserItem:
    return MiniappUserItem(
        **WechatMiniappAuthService.serialize_user(
            principal.user,
            {'roles': principal.roles, 'permissions': principal.permissions},
        )
    )


@router.patch("/me", response_model=MiniappUserItem, summary="更新当前小程序用户昵称")
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


@router.post(
    "/me/avatar",
    response_model=MiniappUserItem,
    summary="上传当前小程序用户头像",
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
