# -*- coding: utf-8 -*-
"""微信小程序 RBAC：目录、种子、授权与路由策略的唯一真源。"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from src.repositories.rbac_repo import RbacRepository
from src.storage import MiniappUserRecord

PERMISSIONS: Dict[str, str] = {
    'account.self': '查看和维护本人会话',
    'daily_reflections.read': '读取本人每日心得',
    'daily_reflections.manage': '保存和删除本人每日心得',
    'stocks.read': '读取股票与行情', 'stocks.manage': '维护全局默认自选股与导入',
    'watchlist.read': '读取本人自选股', 'watchlist.manage': '维护本人自选股',
    'analysis.read': '读取分析任务', 'analysis.execute': '执行股票与市场分析',
    'history.read': '读取分析历史', 'history.delete': '删除分析历史',
    'agent.read': '读取智能体能力与会话', 'agent.execute': '执行智能体任务', 'agent.manage': '管理智能体会话', 'agent.share': '发送智能体内容到外部通知渠道',
    'screening.read': '读取选股结果', 'screening.execute': '执行选股',
    'portfolio.read': '读取持仓', 'portfolio.manage': '维护和导入持仓',
    'backtest.read': '读取回测结果', 'backtest.execute': '执行回测',
    'alerts.read': '读取告警', 'alerts.manage': '维护和测试告警', 'alerts.notify': '发送告警到外部通知渠道',
    'decision_signals.read': '读取决策信号', 'decision_signals.execute': '执行决策信号成本计算', 'decision_signals.manage': '维护和评估决策信号',
    'intelligence.read': '读取情报源', 'intelligence.manage': '维护和抓取情报源',
    'usage.read': '读取用量', 'data.read': '读取数据能力',
    'system.read': '读取系统配置', 'system.manage': '维护系统配置和调度',
    'rbac.manage': '管理角色与用户授权',
}

# 默认 member 只包含小程序所需的个人资源和低风险共享只读能力。
# 高成本计算、全局数据维护、外部通知与系统运维必须由管理员显式授予。
MEMBER_PERMISSIONS = (
    'account.self',
    'daily_reflections.read', 'daily_reflections.manage',
    'watchlist.read', 'watchlist.manage',
    'stocks.read',
    'agent.read', 'agent.execute', 'agent.manage',
    'analysis.read', 'analysis.execute',
    'history.read', 'history.delete',
    'screening.read', 'screening.execute',
    'backtest.read', 'backtest.execute',
    'portfolio.read', 'portfolio.manage',
    'alerts.read', 'alerts.manage',
    'decision_signals.read',
)
ROLES = {
    'member': {
        'name': '普通成员',
        'description': '微信登录用户的个人功能与共享只读能力',
        'permissions': MEMBER_PERMISSIONS,
    },
    'operator': {
        'name': '运营分析员',
        'description': '受信任的分析与运营人员，可执行共享计算和维护共享资源',
        'permissions': tuple(code for code in PERMISSIONS if code not in {
            'system.manage', 'rbac.manage',
        }),
    },
    'admin': {
        'name': '平台管理者',
        'description': '拥有系统维护与授权管理能力',
        'permissions': tuple(PERMISSIONS),
    },
}

DOMAIN_PREFIXES = (
    # Miniapp routes must use the same middleware policy resolver as Web/Bearer
    # routes.  Keep these entries ahead of their generic counterparts so a new
    # miniapp endpoint cannot silently bypass RBAC when it omits a dependency.
    ('/api/v1/miniapp/rbac', 'rbac'),
    ('/api/v1/miniapp/daily-reflections', 'daily_reflections'),
    ('/api/v1/miniapp/watchlist', 'watchlist'),
    ('/api/v1/miniapp/auth', 'account'),
    ('/api/v1/rbac', 'rbac'),
    # 中性前缀（Web Cookie 与小程序 Bearer 复用同一处理逻辑）。
    ('/api/v1/daily-reflections', 'daily_reflections'),
    ('/api/v1/watchlist', 'watchlist'),
    ('/api/v1/account', 'account'),
    ('/api/v1/decision-signals', 'decision_signals'),
    ('/api/v1/intelligence', 'intelligence'),
    ('/api/v1/portfolio', 'portfolio'),
    ('/api/v1/screening', 'screening'),
    ('/api/v1/backtest', 'backtest'),
    ('/api/v1/analysis', 'analysis'),
    ('/api/v1/history', 'history'),
    ('/api/v1/stocks', 'stocks'),
    ('/api/v1/alerts', 'alerts'),
    ('/api/v1/agent', 'agent'),
    ('/api/v1/usage', 'usage'),
    ('/api/v1/data', 'data'),
    ('/api/v1/miniapp/system', 'system'),
    ('/api/v1/system', 'system'),
)


class RbacService:
    def __init__(self, repository: Optional[RbacRepository] = None):
        self.repository = repository or RbacRepository()
        self.repository.seed(PERMISSIONS, ROLES)

    def ensure_user_access(
        self,
        user: MiniappUserRecord,
        *,
        assign_default: bool = False,
    ) -> Dict[str, List[str]]:
        access = self.repository.get_access(user.id)
        if assign_default or not access['roles']:
            self.repository.ensure_role(user.id, 'member')
        return self.repository.get_access(user.id)

    def resolve(self, user_id: int) -> Dict[str, List[str]]:
        return self.repository.get_access(user_id)

    def is_allowed(self, user_id: int, permission: str) -> bool:
        return permission in self.resolve(user_id)['permissions']

    def list_users(self, *, query: Optional[str], page: int, page_size: int) -> Dict[str, object]:
        return self.repository.list_users(query=query, page=page, page_size=page_size)

    def replace_user_roles(
        self,
        user_id: int,
        role_codes: Sequence[str],
        assigned_by_user_id: Optional[int],
    ) -> Dict[str, List[str]]:
        self.repository.replace_user_roles(user_id, role_codes, assigned_by_user_id)
        return self.resolve(user_id)

    def set_user_active(
        self,
        user_id: int,
        is_active: bool,
        *,
        changed_by_user_id: Optional[int],
    ) -> Dict[str, object]:
        return self.repository.set_user_active(
            user_id,
            is_active,
            changed_by_user_id=changed_by_user_id,
        )

    def create_custom_role(
        self,
        *,
        code: str,
        name: str,
        description: str,
        permissions: Sequence[str],
        created_by_user_id: Optional[int],
    ) -> Dict[str, object]:
        return self.repository.create_custom_role(
            code=code,
            name=name,
            description=description,
            permission_codes=permissions,
            created_by_user_id=created_by_user_id,
        )

    def update_custom_role(
        self,
        role_code: str,
        *,
        name: str,
        description: str,
        permissions: Sequence[str],
        changed_by_user_id: Optional[int],
    ) -> Dict[str, object]:
        return self.repository.update_custom_role(
            role_code,
            name=name,
            description=description,
            permission_codes=permissions,
            changed_by_user_id=changed_by_user_id,
        )

    def delete_custom_role(
        self,
        role_code: str,
        *,
        deleted_by_user_id: Optional[int],
    ) -> None:
        self.repository.delete_custom_role(role_code, deleted_by_user_id=deleted_by_user_id)

    def list_audit_events(self, *, page: int, page_size: int) -> Dict[str, object]:
        return self.repository.list_audit_events(page=page, page_size=page_size)

    @staticmethod
    def permission_for_request(path: str, method: str) -> Optional[str]:
        normalized_method = method.upper()
        if normalized_method == 'GET' and path == '/api/v1/feature-quotas/me':
            return 'account.self'
        # 本人 Token 用量属于个人资源：普通成员可见自己的用量；
        # 平台级 /api/v1/usage/* 仍由 usage.read 收敛到运营/管理员。
        if normalized_method == 'GET' and path.startswith('/api/v1/usage/me'):
            return 'account.self'
        if normalized_method == 'POST' and path in {
            '/api/v1/stocks/extract-from-image',
            '/api/v1/stocks/parse-import',
        }:
            return 'stocks.read'
        if normalized_method == 'POST' and path.startswith('/api/v1/portfolio/positions/') and path.endswith('/analysis'):
            return 'analysis.execute'
        for prefix, domain in DOMAIN_PREFIXES:
            if path == prefix or path.startswith(prefix + '/'):
                if domain in {'usage', 'data'}:
                    return f'{domain}.read'
                if domain == 'account':
                    return 'account.self'
                if domain == 'history':
                    return 'history.delete' if normalized_method == 'DELETE' else 'history.read'
                if domain == 'agent':
                    if normalized_method == 'POST' and path == '/api/v1/agent/chat/send':
                        return 'agent.share'
                    if normalized_method == 'GET': return 'agent.read'
                    if normalized_method == 'DELETE': return 'agent.manage'
                    return 'agent.execute'
                if domain == 'decision_signals':
                    if normalized_method == 'GET':
                        return 'decision_signals.read'
                    if path in {
                        '/api/v1/decision-signals/outcomes/run',
                        '/api/v1/decision-signals/reassess',
                    }:
                        return 'decision_signals.execute'
                    return 'decision_signals.manage'
                if domain == 'analysis':
                    return 'analysis.read' if normalized_method == 'GET' else 'analysis.execute'
                if domain == 'screening':
                    return 'screening.read' if normalized_method == 'GET' else 'screening.execute'
                if domain == 'backtest':
                    return 'backtest.read' if normalized_method == 'GET' else 'backtest.execute'
                if domain == 'rbac':
                    return 'rbac.manage'
                if domain == 'system':
                    return 'system.read' if normalized_method == 'GET' else 'system.manage'
                suffix = 'read' if normalized_method == 'GET' else 'manage'
                return f'{domain}.{suffix}'
        return None
