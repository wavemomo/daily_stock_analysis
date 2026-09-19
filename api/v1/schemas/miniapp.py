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


class MiniappEmailCodeRequest(BaseModel):
    """请求向目标邮箱发送 Web 登录绑定验证码。"""

    email: str = Field(..., min_length=3, max_length=254)


class MiniappEmailBindRequest(BaseModel):
    """提交邮箱 + 验证码 + 密码，绑定 Web 端邮箱密码登录。"""

    email: str = Field(..., min_length=3, max_length=254)
    code: str = Field(..., min_length=1, max_length=16)
    password: str = Field(..., min_length=8, max_length=128)


class MiniappEmailBindingResponse(BaseModel):
    """当前用户的 Web 邮箱登录绑定状态（仅本人可见）。"""

    email: Optional[str] = None
    email_verified: bool = False
    has_password: bool = False
    report_email_enabled: bool = False


class MiniappReportEmailToggleRequest(BaseModel):
    """切换"生成的报告是否发送到已绑定邮箱"。"""

    enabled: bool


class MiniappEmailCodeSentResponse(BaseModel):
    sent: bool = True


class MiniappIdentityBindApproveRequest(BaseModel):
    """小程序当前用户批准一个 Web 端发起的显式身份绑定挑战。"""

    challenge: str = Field(..., min_length=1, max_length=512)


class MiniappIdentityBindApproveResponse(BaseModel):
    status: str
    expires_at: str


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


class DailyReflectionStatsResponse(BaseModel):
    total: int
    current_streak: int
    longest_streak: int
    today_done: bool
    month: str
    month_count: int
    month_days: List[int] = Field(default_factory=list)


class MiniappWatchlistItem(BaseModel):
    id: int
    stock_code: str
    stock_name: str = ""
    # 是否纳入本人定时分析池（默认纳入）。
    scheduled: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MiniappWatchlistMutateRequest(BaseModel):
    stock_code: str = Field(..., min_length=1, max_length=32)
    stock_name: str = Field("", max_length=64)


class MiniappWatchlistScheduledRequest(BaseModel):
    """勾选/取消某只自选是否参与本人定时分析。"""

    stock_code: str = Field(..., min_length=1, max_length=32)
    scheduled: bool


class MiniappSchedulePrefResponse(BaseModel):
    """本人定时分析参与状态。"""

    scheduled_analysis_enabled: bool = False
    scheduled_count: int = 0


class MiniappSchedulePrefRequest(BaseModel):
    """开启/关闭本人定时分析参与开关。"""

    enabled: bool


class MiniappWatchlistListResponse(BaseModel):
    items: List[MiniappWatchlistItem] = Field(default_factory=list)
    # stock_codes 便于客户端沿用既有自选渲染逻辑（与全局自选返回形状对齐）。
    stock_codes: List[str] = Field(default_factory=list)
    total: int


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
