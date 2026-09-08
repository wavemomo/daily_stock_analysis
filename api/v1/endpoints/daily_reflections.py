# -*- coding: utf-8 -*-
"""“渡劫”每日心得端点。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import require_permission
from api.v1.schemas.miniapp import (
    DailyReflectionDeleteResponse,
    DailyReflectionItem,
    DailyReflectionListResponse,
    DailyReflectionStatsResponse,
    DailyReflectionUpsertRequest,
)
from src.services.daily_reflection_service import (
    DailyReflectionError,
    DailyReflectionNotFoundError,
    DailyReflectionService,
)
from src.services.wechat_miniapp_auth_service import MiniappPrincipal

router = APIRouter()


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get("", response_model=DailyReflectionListResponse, summary="列出当前用户心得")
def list_reflections(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    principal: MiniappPrincipal = Depends(require_permission('daily_reflections.read')),
) -> DailyReflectionListResponse:
    payload = DailyReflectionService().list(
        user_id=principal.user.id,
        page=page,
        page_size=page_size,
    )
    return DailyReflectionListResponse(**payload)


@router.get("/by-date/{reflection_date}", response_model=Optional[DailyReflectionItem], summary="按日期查询心得")
def get_reflection_by_date(
    reflection_date: date,
    principal: MiniappPrincipal = Depends(require_permission('daily_reflections.read')),
) -> Optional[DailyReflectionItem]:
    payload = DailyReflectionService().get_by_date(
        user_id=principal.user.id,
        reflection_date=reflection_date,
    )
    return DailyReflectionItem(**payload) if payload is not None else None


@router.put("", response_model=DailyReflectionItem, summary="保存当日心得")
def upsert_reflection(
    request: DailyReflectionUpsertRequest,
    principal: MiniappPrincipal = Depends(require_permission('daily_reflections.manage')),
) -> DailyReflectionItem:
    try:
        payload = DailyReflectionService().upsert(
            user_id=principal.user.id,
            reflection_date=request.reflection_date,
            title=request.title,
            content=request.content,
        )
        return DailyReflectionItem(**payload)
    except DailyReflectionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/stats", response_model=DailyReflectionStatsResponse, summary="连续打卡与月度回顾统计")
def get_reflection_stats(
    reference_date: Optional[date] = Query(None, description="客户端本地今天(YYYY-MM-DD)"),
    month: Optional[str] = Query(None, description="回顾月份(YYYY-MM)"),
    principal: MiniappPrincipal = Depends(require_permission('daily_reflections.read')),
) -> DailyReflectionStatsResponse:
    payload = DailyReflectionService().stats(
        user_id=principal.user.id,
        reference_date=reference_date,
        month=month,
    )
    return DailyReflectionStatsResponse(**payload)


@router.get("/{reflection_id}", response_model=DailyReflectionItem, summary="读取一条心得")
def get_reflection(
    reflection_id: int,
    principal: MiniappPrincipal = Depends(require_permission('daily_reflections.read')),
) -> DailyReflectionItem:
    try:
        payload = DailyReflectionService().get(
            user_id=principal.user.id,
            reflection_id=reflection_id,
        )
        return DailyReflectionItem(**payload)
    except DailyReflectionNotFoundError as exc:
        raise _not_found(exc)


@router.delete("/{reflection_id}", response_model=DailyReflectionDeleteResponse, summary="删除一条心得")
def delete_reflection(
    reflection_id: int,
    principal: MiniappPrincipal = Depends(require_permission('daily_reflections.manage')),
) -> DailyReflectionDeleteResponse:
    try:
        DailyReflectionService().delete(
            user_id=principal.user.id,
            reflection_id=reflection_id,
        )
        return DailyReflectionDeleteResponse(deleted=1)
    except DailyReflectionNotFoundError as exc:
        raise _not_found(exc)
