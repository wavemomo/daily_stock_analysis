# -*- coding: utf-8 -*-
"""LLM usage tracking endpoint."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from api.deps import get_database_manager, get_request_analysis_owner_context
from api.v1.schemas.usage import UsageDashboardResponse, UsageSummaryResponse
from src.storage import DatabaseManager, local_naive_now

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_PERIODS = {"today", "month", "all"}


def _date_range(period: str):
    """Return (from_dt, to_dt) as naive datetimes in Beijing time (UTC+8)."""
    now = local_naive_now()
    if period == "today":
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        from_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # all
        from_dt = datetime(2000, 1, 1)
    return from_dt, now


def _normalize_period(period: str) -> str:
    return period if period in _VALID_PERIODS else "month"


def _enrich_call_record(row: dict[str, Any]) -> dict[str, Any]:
    called_at = row.get("called_at")
    if isinstance(called_at, datetime):
        called_at_value = called_at.isoformat()
    else:
        called_at_value = str(called_at or "")
    return {
        **row,
        "called_at": called_at_value,
    }


def _build_summary_payload(period: str, from_dt: datetime, to_dt: datetime, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "period": period,
        "from_date": from_dt.date().isoformat(),
        "to_date": to_dt.date().isoformat(),
        "total_calls": data.get("total_calls", 0),
        "total_prompt_tokens": data.get("total_prompt_tokens", 0),
        "total_completion_tokens": data.get("total_completion_tokens", 0),
        "total_tokens": data.get("total_tokens", 0),
        "by_call_type": data.get("by_call_type", []),
        "by_model": data.get("by_model", []),
    }


@router.get(
    "/summary",
    response_model=UsageSummaryResponse,
    summary="LLM token usage summary",
    description="Aggregate token consumption by period, call type, and model.",
)
def get_usage_summary(
    period: str = Query("month", description="'today' | 'month' | 'all'"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> UsageSummaryResponse:
    """平台级用量（跨全体用户）。RBAC 限制为 ``usage.read``（运营/管理员）。"""
    normalized_period = _normalize_period(period)
    from_dt, to_dt = _date_range(normalized_period)
    data = db_manager.get_llm_usage_summary(from_dt, to_dt, include_all_owners=True)
    return UsageSummaryResponse(**_build_summary_payload(normalized_period, from_dt, to_dt, data))


@router.get(
    "/me/summary",
    response_model=UsageSummaryResponse,
    summary="本人 LLM token 用量统计",
    description="仅统计当前登录用户自己触发的 LLM 调用，普通成员可见。",
)
def get_my_usage_summary(
    http_request: Request,
    period: str = Query("month", description="'today' | 'month' | 'all'"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> UsageSummaryResponse:
    normalized_period = _normalize_period(period)
    from_dt, to_dt = _date_range(normalized_period)
    data = db_manager.get_llm_usage_summary(
        from_dt,
        to_dt,
        owner=get_request_analysis_owner_context(http_request),
    )
    return UsageSummaryResponse(**_build_summary_payload(normalized_period, from_dt, to_dt, data))


@router.get(
    "/me/dashboard",
    response_model=UsageDashboardResponse,
    summary="本人 LLM token 用量看板",
    description="仅返回当前登录用户自己的用量汇总与最近调用记录，普通成员可见。",
)
def get_my_usage_dashboard(
    http_request: Request,
    period: str = Query("month", description="'today' | 'month' | 'all'"),
    limit: int = Query(50, ge=1, le=200, description="Recent call records to include"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> UsageDashboardResponse:
    normalized_period = _normalize_period(period)
    from_dt, to_dt = _date_range(normalized_period)
    owner = get_request_analysis_owner_context(http_request)
    data = db_manager.get_llm_usage_summary(from_dt, to_dt, owner=owner)
    records = db_manager.get_llm_usage_records(from_dt, to_dt, limit=limit, owner=owner)
    payload = _build_summary_payload(normalized_period, from_dt, to_dt, data)
    payload["recent_calls"] = [_enrich_call_record(row) for row in records]
    return UsageDashboardResponse(**payload)


@router.get(
    "/dashboard",
    response_model=UsageDashboardResponse,
    summary="LLM token usage monitoring dashboard",
    description="Return token totals, model breakdowns, and recent LLM call records.",
)
def get_usage_dashboard(
    period: str = Query("month", description="'today' | 'month' | 'all'"),
    limit: int = Query(50, ge=1, le=200, description="Recent call records to include"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> UsageDashboardResponse:
    """平台级用量看板（跨全体用户）。RBAC 限制为 ``usage.read``（运营/管理员）。"""
    normalized_period = _normalize_period(period)
    from_dt, to_dt = _date_range(normalized_period)
    data = db_manager.get_llm_usage_summary(from_dt, to_dt, include_all_owners=True)
    records = db_manager.get_llm_usage_records(from_dt, to_dt, limit=limit, include_all_owners=True)
    payload = _build_summary_payload(normalized_period, from_dt, to_dt, data)
    payload["recent_calls"] = [_enrich_call_record(row) for row in records]
    return UsageDashboardResponse(**payload)
