# -*- coding: utf-8 -*-
"""
===================================
API 依赖注入模块
===================================

职责：
1. 提供数据库 Session 依赖
2. 提供配置依赖
3. 提供服务层依赖
4. 提供微信小程序用户认证依赖
"""

from typing import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.storage import DatabaseManager
from src.config import get_config, Config
from src.services.system_config_service import SystemConfigService
from src.services.runtime_scheduler import RuntimeSchedulerService
from src.services.agent_chat_session_service import AgentChatSessionService
from src.services.wechat_miniapp_auth_service import (
    MiniappPrincipal,
    WechatMiniappAuthService,
)


def get_db() -> Generator[Session, None, None]:
    """获取数据库 Session，并在请求结束后关闭。"""
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def get_config_dep() -> Config:
    """获取配置单例。"""
    return get_config()


def get_database_manager() -> DatabaseManager:
    """获取数据库管理器单例。"""
    return DatabaseManager.get_instance()


def get_agent_chat_session_service() -> AgentChatSessionService:
    """Build an Agent Chat session service for the current database manager."""
    return AgentChatSessionService(DatabaseManager.get_instance())


def get_system_config_service(request: Request) -> SystemConfigService:
    """Get app-lifecycle shared SystemConfigService instance."""
    service = getattr(request.app.state, "system_config_service", None)
    if service is None:
        service = SystemConfigService()
        request.app.state.system_config_service = service
    return service


def get_runtime_scheduler_service(request: Request) -> RuntimeSchedulerService:
    """Get app-lifecycle shared RuntimeSchedulerService instance."""
    service = getattr(request.app.state, "runtime_scheduler_service", None)
    if service is None:
        service = RuntimeSchedulerService()
        request.app.state.runtime_scheduler_service = service
    return service


def get_current_miniapp_principal(request: Request) -> MiniappPrincipal:
    """校验 Bearer token 并返回当前微信小程序用户。"""
    established = getattr(request.state, 'miniapp_principal', None)
    if established is not None:
        return established
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="微信登录已失效，请重新登录",
        )
    principal = WechatMiniappAuthService().authenticate_token(token)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="微信登录已失效，请重新登录",
        )
    return principal


def require_permission(permission: str):
    """声明端点权限；未登录返回 401，已登录但无权限返回 403。"""
    def dependency(
        principal: MiniappPrincipal = Depends(get_current_miniapp_principal),
    ) -> MiniappPrincipal:
        if permission not in principal.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"缺少权限: {permission}",
            )
        return principal

    return dependency


def get_request_resource_owner(request: Request) -> str | None:
    """Return the trusted owner id for a miniapp request.

    A miniapp caller is always scoped to its authenticated user.  The legacy
    administrator-cookie session intentionally returns ``None`` so existing
    operational tools can still access global and historical resources.  API
    request bodies must never decide this value.
    """
    principal = getattr(request.state, "miniapp_principal", None)
    if principal is not None:
        return str(principal.user.id)
    if getattr(request.state, "auth_kind", None) == "admin":
        return None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="微信登录已失效，请重新登录",
    )
