# -*- coding: utf-8 -*-
"""微信小程序认证与“渡劫”每日心得 API schema。"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field


class MiniappLoginRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=256)


class MiniappUserItem(BaseModel):
    id: int
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None
    profile_updated_at: Optional[str] = None
    created_at: Optional[str] = None
    last_login_at: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)


class MiniappLoginResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_at: str
    user: MiniappUserItem


class MiniappProfileUpdateRequest(BaseModel):
    nickname: Optional[str] = Field(None, max_length=64)


class DailyReflectionUpsertRequest(BaseModel):
    reflection_date: date
    title: str = Field("", max_length=80)
    content: str = Field(..., min_length=1, max_length=10000)


class DailyReflectionItem(BaseModel):
    id: int
    reflection_date: str
    title: str
    content: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DailyReflectionListResponse(BaseModel):
    items: List[DailyReflectionItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class DailyReflectionDeleteResponse(BaseModel):
    deleted: int


class MiniappRoleAssignmentRequest(BaseModel):
    roles: List[str] = Field(default_factory=list, max_length=32)


class MiniappAdminUserItem(MiniappUserItem):
    """管理员用户目录条目；不包含 OpenID、UnionID 或任何会话信息。"""

    is_active: bool


class MiniappAdminUserListResponse(BaseModel):
    items: List[MiniappAdminUserItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class MiniappUserActiveRequest(BaseModel):
    is_active: bool


class MiniappUserActiveResponse(BaseModel):
    id: int
    is_active: bool


class MiniappRoleRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field('', max_length=255)
    permissions: List[str] = Field(default_factory=list, max_length=128)


class MiniappRoleUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field('', max_length=255)
    permissions: List[str] = Field(default_factory=list, max_length=128)


class MiniappRoleItem(BaseModel):
    code: str
    name: str
    description: str
    is_system: bool
    permissions: List[str] = Field(default_factory=list)


class MiniappRbacAuditItem(BaseModel):
    id: int
    action: str
    target_type: str
    target_id: str
    actor_user_id: Optional[int] = None
    metadata: dict = Field(default_factory=dict)
    created_at: Optional[str] = None


class MiniappRbacAuditListResponse(BaseModel):
    items: List[MiniappRbacAuditItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
