# -*- coding: utf-8 -*-
"""Daily expensive-feature quota endpoints."""

from __future__ import annotations

from typing import Optional, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from api.deps import require_permission
from api.v1.schemas.feature_quota import (
    FeatureQuotaEntitlementListResponse,
    FeatureQuotaPlanCreateRequest,
    FeatureQuotaPlanItem,
    FeatureQuotaPlanListResponse,
    FeatureQuotaPlanAssignmentRequest,
    FeatureQuotaPlanUpdateRequest,
    FeatureQuotaPolicyItem,
    FeatureQuotaPolicyListResponse,
    FeatureQuotaPolicyUpdateRequest,
    FeatureQuotaUserOverrideRequest,
    FeatureQuotaUserProfileResponse,
    FeatureQuotaWhitelistRequest,
)
from src.services.feature_quota_service import FeatureQuotaService
from src.services.wechat_miniapp_auth_service import MiniappPrincipal

router = APIRouter()
miniapp_rbac_router = APIRouter()
_SchemaModel = TypeVar("_SchemaModel", bound=BaseModel)


def _policy_item(payload: dict) -> FeatureQuotaPolicyItem:
    return FeatureQuotaPolicyItem(**payload)


def _plan_item(payload: dict) -> FeatureQuotaPlanItem:
    return FeatureQuotaPlanItem(**payload)


def _profile_item(payload: dict) -> FeatureQuotaUserProfileResponse:
    return FeatureQuotaUserProfileResponse(**payload)


def _limits_by_code(items) -> dict[str, int]:
    return {item.feature_code: item.daily_limit for item in items}


def _raise_management_error(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": str(exc)},
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_request", "message": str(exc)},
        ) from exc
    raise exc


def _list_plans() -> FeatureQuotaPlanListResponse:
    return FeatureQuotaPlanListResponse(
        items=[_plan_item(item) for item in FeatureQuotaService().list_plans()],
    )


def _create_plan(
    request: FeatureQuotaPlanCreateRequest,
    *,
    actor_user_id: int,
) -> FeatureQuotaPlanItem:
    try:
        return _plan_item(FeatureQuotaService().create_plan(
            code=request.code,
            name=request.name,
            description=request.description,
            limits=_limits_by_code(request.limits),
            is_active=request.is_active,
            actor_user_id=actor_user_id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


def _update_plan(
    plan_code: str,
    request: FeatureQuotaPlanUpdateRequest,
    *,
    actor_user_id: int,
) -> FeatureQuotaPlanItem:
    try:
        return _plan_item(FeatureQuotaService().update_plan(
            plan_code,
            name=request.name,
            description=request.description,
            is_active=request.is_active,
            limits=_limits_by_code(request.limits) if request.limits is not None else None,
            actor_user_id=actor_user_id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


def _profile(user_id: int) -> FeatureQuotaUserProfileResponse:
    try:
        return _profile_item(FeatureQuotaService().get_user_quota_profile(user_id))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


def _assign_plan(
    user_id: int,
    request: FeatureQuotaPlanAssignmentRequest,
    *,
    actor_user_id: int,
) -> FeatureQuotaUserProfileResponse:
    try:
        return _profile_item(FeatureQuotaService().assign_plan(
            user_id,
            plan_code=request.plan_code,
            effective_from=request.effective_from,
            effective_until=request.effective_until,
            actor_user_id=actor_user_id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


def _set_override(
    user_id: int,
    feature_code: str,
    request: FeatureQuotaUserOverrideRequest,
    *,
    actor_user_id: int,
) -> FeatureQuotaUserProfileResponse:
    try:
        return _profile_item(FeatureQuotaService().set_user_override(
            user_id,
            feature_code,
            daily_limit=request.daily_limit,
            effective_from=request.effective_from,
            effective_until=request.effective_until,
            reason=request.reason,
            actor_user_id=actor_user_id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


def _revoke_override(
    user_id: int,
    feature_code: str,
    *,
    actor_user_id: int,
) -> FeatureQuotaUserProfileResponse:
    try:
        return _profile_item(FeatureQuotaService().revoke_user_override(
            user_id,
            feature_code,
            actor_user_id=actor_user_id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


def _set_whitelist(
    user_id: int,
    request: FeatureQuotaWhitelistRequest,
    *,
    actor_user_id: int,
) -> FeatureQuotaUserProfileResponse:
    try:
        return _profile_item(FeatureQuotaService().set_whitelist_entry(
            user_id,
            feature_code=request.feature_code,
            effective_from=request.effective_from,
            effective_until=request.effective_until,
            reason=request.reason,
            actor_user_id=actor_user_id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


def _revoke_whitelist(
    user_id: int,
    feature_code: Optional[str],
    *,
    actor_user_id: int,
) -> FeatureQuotaUserProfileResponse:
    try:
        return _profile_item(FeatureQuotaService().revoke_whitelist_entry(
            user_id,
            feature_code=feature_code,
            actor_user_id=actor_user_id,
        ))
    except (LookupError, ValueError) as exc:
        _raise_management_error(exc)


@router.get("/me", response_model=FeatureQuotaEntitlementListResponse)
def get_my_feature_quotas(request: Request) -> FeatureQuotaEntitlementListResponse:
    """Expose the canonical caller's remaining daily quota."""
    return FeatureQuotaEntitlementListResponse(
        items=[item.as_dict() for item in FeatureQuotaService().get_entitlements_for_request(request)],
    )


@miniapp_rbac_router.get("", response_model=FeatureQuotaPolicyListResponse)
def list_feature_quota_policies(
    _principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaPolicyListResponse:
    return FeatureQuotaPolicyListResponse(
        items=[_policy_item(item) for item in FeatureQuotaService().list_policies()],
    )


@miniapp_rbac_router.put("/{feature_code}", response_model=FeatureQuotaPolicyItem)
def update_feature_quota_policy(
    feature_code: str,
    request: FeatureQuotaPolicyUpdateRequest,
    principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaPolicyItem:
    try:
        return _policy_item(FeatureQuotaService().update_policy(
            feature_code,
            request.daily_limit,
            actor_user_id=principal.user.id,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc


@miniapp_rbac_router.get("/plans", response_model=FeatureQuotaPlanListResponse)
def list_feature_quota_plans(
    _principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaPlanListResponse:
    return _list_plans()


@miniapp_rbac_router.post("/plans", response_model=FeatureQuotaPlanItem, status_code=status.HTTP_201_CREATED)
def create_feature_quota_plan(
    request: FeatureQuotaPlanCreateRequest,
    principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaPlanItem:
    return _create_plan(request, actor_user_id=principal.user.id)


@miniapp_rbac_router.put("/plans/{plan_code}", response_model=FeatureQuotaPlanItem)
def update_feature_quota_plan(
    plan_code: str,
    request: FeatureQuotaPlanUpdateRequest,
    principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaPlanItem:
    return _update_plan(plan_code, request, actor_user_id=principal.user.id)


@miniapp_rbac_router.get("/users/{user_id}/quota-profile", response_model=FeatureQuotaUserProfileResponse)
def get_feature_quota_profile(
    user_id: int,
    _principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaUserProfileResponse:
    return _profile(user_id)


@miniapp_rbac_router.put("/users/{user_id}/plan", response_model=FeatureQuotaUserProfileResponse)
def assign_feature_quota_plan(
    user_id: int,
    request: FeatureQuotaPlanAssignmentRequest,
    principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaUserProfileResponse:
    return _assign_plan(user_id, request, actor_user_id=principal.user.id)


@miniapp_rbac_router.put("/users/{user_id}/overrides/{feature_code}", response_model=FeatureQuotaUserProfileResponse)
def set_feature_quota_override(
    user_id: int,
    feature_code: str,
    request: FeatureQuotaUserOverrideRequest,
    principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaUserProfileResponse:
    return _set_override(user_id, feature_code, request, actor_user_id=principal.user.id)


@miniapp_rbac_router.delete("/users/{user_id}/overrides/{feature_code}", response_model=FeatureQuotaUserProfileResponse)
def revoke_feature_quota_override(
    user_id: int,
    feature_code: str,
    principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaUserProfileResponse:
    return _revoke_override(user_id, feature_code, actor_user_id=principal.user.id)


@miniapp_rbac_router.put("/users/{user_id}/whitelist", response_model=FeatureQuotaUserProfileResponse)
def set_feature_quota_whitelist(
    user_id: int,
    request: FeatureQuotaWhitelistRequest,
    principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaUserProfileResponse:
    return _set_whitelist(user_id, request, actor_user_id=principal.user.id)


@miniapp_rbac_router.delete("/users/{user_id}/whitelist", response_model=FeatureQuotaUserProfileResponse)
def revoke_feature_quota_whitelist(
    user_id: int,
    feature_code: Optional[str] = None,
    principal: MiniappPrincipal = Depends(require_permission("rbac.manage")),
) -> FeatureQuotaUserProfileResponse:
    return _revoke_whitelist(user_id, feature_code, actor_user_id=principal.user.id)
