# -*- coding: utf-8 -*-
"""按用户维度的个人自选股端点。

owner-scope 固定使用 Bearer principal 的 user_id，不接受客户端传入的目标用户。
与 /api/v1/stocks/watchlist（全局 STOCK_LIST，管理员维护）相互独立。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import require_permission
from api.v1.schemas.miniapp import (
    MiniappWatchlistItem,
    MiniappWatchlistListResponse,
    MiniappWatchlistMutateRequest,
)
from src.services.miniapp_watchlist_service import (
    MiniappWatchlistService,
    WatchlistValidationError,
)
from src.services.wechat_miniapp_auth_service import MiniappPrincipal

router = APIRouter()


@router.get("", response_model=MiniappWatchlistListResponse, summary="列出当前用户自选股")
def list_watchlist(
    principal: MiniappPrincipal = Depends(require_permission('watchlist.read')),
) -> MiniappWatchlistListResponse:
    payload = MiniappWatchlistService().list(user_id=principal.user.id)
    return MiniappWatchlistListResponse(**payload)


@router.post("/add", response_model=MiniappWatchlistItem, summary="加入当前用户自选股")
def add_watchlist(
    request: MiniappWatchlistMutateRequest,
    principal: MiniappPrincipal = Depends(require_permission('watchlist.manage')),
) -> MiniappWatchlistItem:
    try:
        payload = MiniappWatchlistService().add(
            user_id=principal.user.id,
            stock_code=request.stock_code,
            stock_name=request.stock_name,
        )
        return MiniappWatchlistItem(**payload)
    except WatchlistValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/remove", response_model=MiniappWatchlistListResponse, summary="从当前用户自选股删除")
def remove_watchlist(
    request: MiniappWatchlistMutateRequest,
    principal: MiniappPrincipal = Depends(require_permission('watchlist.manage')),
) -> MiniappWatchlistListResponse:
    service = MiniappWatchlistService()
    try:
        service.remove(user_id=principal.user.id, stock_code=request.stock_code)
    except WatchlistValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    payload = service.list(user_id=principal.user.id)
    return MiniappWatchlistListResponse(**payload)
