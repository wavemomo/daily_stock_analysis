# -*- coding: utf-8 -*-
"""仅 ``rbac.manage`` 管理员可用的小程序 RBAC 管理端点。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from api.deps import require_permission
from api.v1.schemas.miniapp import (
    MiniappAdminUserListResponse,
    MiniappRbacAuditListResponse,
    MiniappRoleAssignmentRequest,
    MiniappRoleItem,
    MiniappRoleRequest,
    MiniappRoleUpdateRequest,
    MiniappUserActiveRequest,
    MiniappUserActiveResponse,
)
from src.services.rbac_service import RbacService
from src.services.wechat_miniapp_auth_service import MiniappPrincipal

router = APIRouter()


def _raise_management_error(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise exc


@router.get("/catalog", summary="列出角色与权限目录")
def catalog(
    _: MiniappPrincipal = Depends(require_permission('rbac.manage')),
):
    service = RbacService()
    return {
        'permissions': service.repository.list_permissions(),
        'roles': service.repository.list_roles(),
    }


@router.get("/users", response_model=MiniappAdminUserListResponse, summary="分页列出小程序用户")
def list_users(
    query: Optional[str] = Query(None, max_length=64),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: MiniappPrincipal = Depends(require_permission('rbac.manage')),
) -> MiniappAdminUserListResponse:
    return MiniappAdminUserListResponse(**RbacService().list_users(
        query=query,
        page=page,
        page_size=page_size,
    ))


@router.put("/users/{user_id}/roles", summary="替换用户角色")
def replace_user_roles(
    user_id: int,
    request: MiniappRoleAssignmentRequest,
    principal: MiniappPrincipal = Depends(require_permission('rbac.manage')),
):
    try:
        return RbacService().replace_user_roles(
            user_id=user_id,
            role_codes=request.roles,
            assigned_by_user_id=principal.user.id,
        )
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


@router.patch(
    "/users/{user_id}/active",
    response_model=MiniappUserActiveResponse,
    summary="启用或停用小程序用户",
)
def set_user_active(
    user_id: int,
    request: MiniappUserActiveRequest,
    principal: MiniappPrincipal = Depends(require_permission('rbac.manage')),
) -> MiniappUserActiveResponse:
    try:
        return MiniappUserActiveResponse(**RbacService().set_user_active(
            user_id=user_id,
            is_active=request.is_active,
            changed_by_user_id=principal.user.id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


@router.post("/roles", response_model=MiniappRoleItem, status_code=status.HTTP_201_CREATED, summary="创建自定义角色")
def create_role(
    request: MiniappRoleRequest,
    principal: MiniappPrincipal = Depends(require_permission('rbac.manage')),
) -> MiniappRoleItem:
    try:
        return MiniappRoleItem(**RbacService().create_custom_role(
            code=request.code,
            name=request.name,
            description=request.description,
            permissions=request.permissions,
            created_by_user_id=principal.user.id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


@router.put("/roles/{role_code}", response_model=MiniappRoleItem, summary="更新自定义角色")
def update_role(
    role_code: str,
    request: MiniappRoleUpdateRequest,
    principal: MiniappPrincipal = Depends(require_permission('rbac.manage')),
) -> MiniappRoleItem:
    try:
        return MiniappRoleItem(**RbacService().update_custom_role(
            role_code,
            name=request.name,
            description=request.description,
            permissions=request.permissions,
            changed_by_user_id=principal.user.id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


@router.delete("/roles/{role_code}", status_code=status.HTTP_204_NO_CONTENT, summary="删除未分配的自定义角色")
def delete_role(
    role_code: str,
    principal: MiniappPrincipal = Depends(require_permission('rbac.manage')),
) -> Response:
    try:
        RbacService().delete_custom_role(role_code, deleted_by_user_id=principal.user.id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


@router.get("/audit", response_model=MiniappRbacAuditListResponse, summary="列出权限管理审计记录")
def list_audit_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: MiniappPrincipal = Depends(require_permission('rbac.manage')),
) -> MiniappRbacAuditListResponse:
    return MiniappRbacAuditListResponse(**RbacService().list_audit_events(
        page=page,
        page_size=page_size,
    ))
