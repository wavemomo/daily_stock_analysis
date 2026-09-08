# -*- coding: utf-8 -*-
"""API dependency injection and trusted request identity boundaries."""

from typing import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from src.analysis_ownership import AnalysisOwner
from src.portfolio_ownership import PortfolioScope
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


def get_current_web_user_principal(request: Request) -> MiniappPrincipal:
    """Return the canonical user established from the Web Cookie session."""
    if getattr(request.state, "auth_kind", None) != "web_user":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Web 登录已失效，请重新使用微信扫码登录",
        )
    principal = getattr(request.state, "miniapp_principal", None)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Web 登录已失效，请重新使用微信扫码登录",
        )
    return principal


def get_current_miniapp_principal(request: Request) -> MiniappPrincipal:
    """Return the principal established by middleware for either supported client."""
    established = getattr(request.state, "miniapp_principal", None)
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


def _request_principal(request: Request) -> MiniappPrincipal:
    principal = getattr(request.state, "miniapp_principal", None)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="微信登录已失效，请重新登录",
        )
    user_id = principal.user.id
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="微信登录已失效，请重新登录",
        )
    return principal


def get_request_analysis_owner_context(request: Request) -> AnalysisOwner:
    """Bind every HTTP analysis request to its canonical authenticated user."""
    return AnalysisOwner.user(_request_principal(request).user.id)


def get_request_analysis_owner(request: Request) -> tuple[str, int | None]:
    """Return legacy storage parameters derived from the canonical request user."""
    owner = get_request_analysis_owner_context(request)
    return owner.scope, owner.user_id


def get_request_portfolio_scope(request: Request) -> PortfolioScope:
    """Bind every HTTP portfolio request to its canonical authenticated user."""
    return PortfolioScope.user(str(_request_principal(request).user.id))


def get_request_resource_owner(request: Request) -> str:
    """Return the canonical request user id for agent-session ownership."""
    return str(_request_principal(request).user.id)
