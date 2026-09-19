# -*- coding: utf-8 -*-
"""Schemas for LLM usage tracking API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CallTypeBreakdown(BaseModel):
    call_type: str = Field(..., description="'analysis' | 'agent' | 'market_review'")
    calls: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int


class ModelBreakdown(BaseModel):
    model: str
    calls: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int
    max_total_tokens: int = 0


class UsageCallRecord(BaseModel):
    id: int
    called_at: str = Field(..., description="ISO datetime string")
    call_type: str
    model: str
    stock_code: Optional[str] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OwnerUsageBreakdown(BaseModel):
    """Per-user token consumption for the platform-level admin drill-down."""

    user_id: Optional[int] = Field(
        None,
        description="Owning user id; null for the aggregated platform bucket.",
    )
    nickname: Optional[str] = Field(None, description="User display name when available")
    owner_scope: str = Field(
        ...,
        description="'user' for a single user, 'global' for platform/background usage",
    )
    calls: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int
    last_called_at: Optional[str] = Field(None, description="ISO datetime string")


class UsageSummaryResponse(BaseModel):
    period: str = Field(..., description="'today' | 'month' | 'all'")
    from_date: str = Field(..., description="ISO date string")
    to_date: str = Field(..., description="ISO date string")
    scope: str = Field(
        ...,
        description="'self' for the caller's own usage, 'platform' for the cross-user aggregate",
    )
    total_calls: int
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int
    by_call_type: List[CallTypeBreakdown]
    by_model: List[ModelBreakdown]


class UsageDashboardResponse(UsageSummaryResponse):
    recent_calls: List[UsageCallRecord]


class UsageByUserResponse(BaseModel):
    """Platform-level per-user usage drill-down (requires ``usage.read``)."""

    period: str = Field(..., description="'today' | 'month' | 'all'")
    from_date: str = Field(..., description="ISO date string")
    to_date: str = Field(..., description="ISO date string")
    scope: str = Field("platform", description="Always 'platform' for this view")
    owners: List[OwnerUsageBreakdown]
