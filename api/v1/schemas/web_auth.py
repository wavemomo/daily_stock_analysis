# -*- coding: utf-8 -*-
"""普通 Web 用户认证 API schema。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from api.v1.schemas.miniapp import MiniappUserItem


class WebUserSessionResponse(BaseModel):
    user: MiniappUserItem
    csrf_token: str


class IdentityBindStartResponse(BaseModel):
    """仅向已认证 Web user 返回的不透明绑定挑战。"""

    challenge: str = Field(..., min_length=1)
    expires_at: str


class IdentityBindConsumeRequest(BaseModel):
    challenge: str = Field(..., min_length=1, max_length=512)


class IdentityBindConsumeResponse(BaseModel):
    status: Literal["approved", "conflict", "invalid"]
