# -*- coding: utf-8 -*-
"""Feature quota API schemas."""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


FeatureQuotaLimitSource = Literal[
    'admin',
    'user_override',
    'whitelist_feature',
    'whitelist_all',
    'plan',
    'global_policy',
]


class FeatureQuotaEntitlementItem(BaseModel):
    feature_code: str
    name: str
    description: str
    daily_limit: Optional[int] = None
    used_count: int
    remaining: Optional[int] = None
    unlimited: bool
    disabled: bool
    reset_at: str
    limit_source: FeatureQuotaLimitSource
    plan_code: Optional[str] = None
    effective_until: Optional[str] = None


class FeatureQuotaEntitlementListResponse(BaseModel):
    items: List[FeatureQuotaEntitlementItem] = Field(default_factory=list)


class FeatureQuotaPolicyItem(BaseModel):
    feature_code: str
    name: str
    description: str
    daily_limit: int = Field(ge=0)
    updated_at: Optional[str] = None
    updated_by_user_id: Optional[int] = None


class FeatureQuotaPolicyListResponse(BaseModel):
    items: List[FeatureQuotaPolicyItem] = Field(default_factory=list)


class FeatureQuotaPolicyUpdateRequest(BaseModel):
    daily_limit: int = Field(..., ge=0, le=10000)


class FeatureQuotaPlanLimitItem(BaseModel):
    feature_code: str
    daily_limit: int = Field(ge=0, le=10000)


class FeatureQuotaPlanItem(BaseModel):
    code: str
    name: str
    description: str = ''
    is_active: bool
    limits: List[FeatureQuotaPlanLimitItem] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class FeatureQuotaPlanListResponse(BaseModel):
    items: List[FeatureQuotaPlanItem] = Field(default_factory=list)


class FeatureQuotaPlanCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default='', max_length=1000)
    is_active: bool = True
    limits: List[FeatureQuotaPlanLimitItem] = Field(default_factory=list)


class FeatureQuotaPlanUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=1000)
    is_active: Optional[bool] = None
    limits: Optional[List[FeatureQuotaPlanLimitItem]] = None


class FeatureQuotaPlanAssignmentRequest(BaseModel):
    plan_code: Optional[str] = Field(default=None, min_length=2, max_length=64)
    effective_from: Optional[date] = None
    effective_until: Optional[date] = None


class FeatureQuotaUserOverrideRequest(BaseModel):
    daily_limit: int = Field(..., ge=0, le=10000)
    effective_from: Optional[date] = None
    effective_until: Optional[date] = None
    reason: str = Field(default='', max_length=1000)


class FeatureQuotaWhitelistRequest(BaseModel):
    feature_code: Optional[str] = Field(default=None, max_length=64)
    effective_from: Optional[date] = None
    effective_until: Optional[date] = None
    reason: str = Field(default='', max_length=1000)


class FeatureQuotaPlanAssignmentItem(BaseModel):
    plan_code: str
    plan_name: str
    effective_from: str
    effective_until: Optional[str] = None


class FeatureQuotaUserOverrideItem(BaseModel):
    feature_code: str
    daily_limit: int = Field(ge=0, le=10000)
    effective_from: str
    effective_until: Optional[str] = None
    reason: str = ''


class FeatureQuotaWhitelistItem(BaseModel):
    feature_code: Optional[str] = None
    effective_from: str
    effective_until: Optional[str] = None
    reason: str = ''


class FeatureQuotaUserProfileResponse(BaseModel):
    user_id: int
    plan_assignment: Optional[FeatureQuotaPlanAssignmentItem] = None
    plan_assignments: List[FeatureQuotaPlanAssignmentItem] = Field(default_factory=list)
    overrides: List[FeatureQuotaUserOverrideItem] = Field(default_factory=list)
    whitelist_entries: List[FeatureQuotaWhitelistItem] = Field(default_factory=list)
    entitlements: List[FeatureQuotaEntitlementItem] = Field(default_factory=list)
