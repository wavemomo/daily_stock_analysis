# -*- coding: utf-8 -*-
"""小程序 RBAC 持久化适配器。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import String, cast, delete, func, or_, select, text

from src.storage import (
    DatabaseManager,
    MiniappUserRecord,
    MiniappUserRoleRecord,
    RbacAuditEventRecord,
    RbacPermissionRecord,
    RbacRolePermissionRecord,
    RbacRoleRecord,
)

_ROLE_CODE_PATTERN = re.compile(r'^[a-z][a-z0-9_-]{1,63}$')


class RbacRepository:
    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def seed(self, permissions: Dict[str, str], roles: Dict[str, Dict[str, object]]) -> None:
        """幂等创建代码定义的权限和系统角色。

        系统角色是稳定的安全基线，因此其权限集合仍由代码定义同步；管理员
        可在数据库中创建、编辑并分配自定义角色，避免系统升级后内置安全语义
        被页面配置意外破坏。
        """
        now = datetime.utcnow()
        with self.db.get_session() as session:
            permission_rows = {
                row.code: row for row in session.execute(select(RbacPermissionRecord)).scalars()
            }
            for code, description in permissions.items():
                row = permission_rows.get(code)
                if row is None:
                    row = RbacPermissionRecord(
                        code=code,
                        group_code=code.split('.', 1)[0],
                        description=description,
                        created_at=now,
                    )
                    session.add(row)
                    permission_rows[code] = row
                elif row.description != description:
                    row.description = description

            role_rows = {row.code: row for row in session.execute(select(RbacRoleRecord)).scalars()}
            for code, definition in roles.items():
                row = role_rows.get(code)
                if row is None:
                    row = RbacRoleRecord(
                        code=code,
                        name=str(definition['name']),
                        description=str(definition.get('description') or ''),
                        is_system=True,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                    role_rows[code] = row
                else:
                    row.name = str(definition['name'])
                    row.description = str(definition.get('description') or '')
                    row.is_system = True
            session.flush()

            for code, definition in roles.items():
                role = role_rows[code]
                desired_ids = {permission_rows[item].id for item in definition.get('permissions') or ()}
                current_ids = {
                    item.permission_id
                    for item in session.execute(
                        select(RbacRolePermissionRecord).where(
                            RbacRolePermissionRecord.role_id == role.id
                        )
                    ).scalars()
                }
                if current_ids != desired_ids:
                    session.execute(
                        delete(RbacRolePermissionRecord).where(
                            RbacRolePermissionRecord.role_id == role.id
                        )
                    )
                    session.add_all([
                        RbacRolePermissionRecord(
                            role_id=role.id,
                            permission_id=permission_id,
                            created_at=now,
                        )
                        for permission_id in desired_ids
                    ])
            session.commit()

    def ensure_role(
        self,
        user_id: int,
        role_code: str,
        assigned_by_user_id: Optional[int] = None,
        *,
        audit_action: Optional[str] = None,
        audit_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """确保用户拥有角色；仅首次写入时返回 ``True`` 并可原子记录审计。"""
        with self.db.get_session() as session:
            role_id = session.execute(
                select(RbacRoleRecord.id).where(RbacRoleRecord.code == role_code)
            ).scalar_one()
            exists = session.execute(
                select(MiniappUserRoleRecord.id).where(
                    MiniappUserRoleRecord.user_id == user_id,
                    MiniappUserRoleRecord.role_id == role_id,
                )
            ).scalar_one_or_none()
            if exists is not None:
                return False
            session.add(MiniappUserRoleRecord(
                user_id=user_id,
                role_id=role_id,
                assigned_by_user_id=assigned_by_user_id,
                created_at=datetime.utcnow(),
            ))
            if audit_action:
                self._record_audit(
                    session,
                    action=audit_action,
                    target_type='user',
                    target_id=str(user_id),
                    actor_user_id=None,
                    metadata=audit_metadata or {'role': role_code},
                )
            session.commit()
            return True

    def get_access(self, user_id: int) -> Dict[str, List[str]]:
        with self.db.get_session() as session:
            roles = list(session.execute(
                select(RbacRoleRecord.code)
                .join(MiniappUserRoleRecord, MiniappUserRoleRecord.role_id == RbacRoleRecord.id)
                .where(MiniappUserRoleRecord.user_id == user_id)
                .order_by(RbacRoleRecord.code)
            ).scalars())
            permissions = list(session.execute(
                select(RbacPermissionRecord.code)
                .join(RbacRolePermissionRecord, RbacRolePermissionRecord.permission_id == RbacPermissionRecord.id)
                .join(MiniappUserRoleRecord, MiniappUserRoleRecord.role_id == RbacRolePermissionRecord.role_id)
                .where(MiniappUserRoleRecord.user_id == user_id)
                .distinct()
                .order_by(RbacPermissionRecord.code)
            ).scalars())
            return {'roles': roles, 'permissions': permissions}

    def list_permissions(self) -> List[Dict[str, object]]:
        with self.db.get_session() as session:
            return [
                {'code': row.code, 'group_code': row.group_code, 'description': row.description}
                for row in session.execute(
                    select(RbacPermissionRecord).order_by(RbacPermissionRecord.code)
                ).scalars()
            ]

    def list_roles(self) -> List[Dict[str, object]]:
        with self.db.get_session() as session:
            rows = session.execute(select(RbacRoleRecord).order_by(RbacRoleRecord.code)).scalars()
            return [
                self._serialize_role(session, row)
                for row in rows
            ]

    def list_users(self, *, query: Optional[str], page: int, page_size: int) -> Dict[str, object]:
        """返回管理目录所需的安全字段，绝不返回 openid、unionid 或会话数据。"""
        with self.db.get_session() as session:
            filters = []
            normalized_query = (query or '').strip()
            if normalized_query:
                pattern = f'%{normalized_query}%'
                filters.append(or_(
                    MiniappUserRecord.nickname.ilike(pattern),
                    cast(MiniappUserRecord.id, String).like(pattern),
                ))
            statement = select(MiniappUserRecord)
            count_statement = select(func.count(MiniappUserRecord.id))
            if filters:
                statement = statement.where(*filters)
                count_statement = count_statement.where(*filters)
            total = int(session.execute(count_statement).scalar_one() or 0)
            users = list(session.execute(
                statement.order_by(MiniappUserRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars())
            user_ids = [row.id for row in users]
            access_by_user = self._access_for_users(session, user_ids)
            return {
                'items': [
                    self._serialize_user(row, access_by_user.get(row.id, {'roles': [], 'permissions': []}))
                    for row in users
                ],
                'total': total,
                'page': page,
                'page_size': page_size,
            }

    def replace_user_roles(
        self,
        user_id: int,
        role_codes: Sequence[str],
        assigned_by_user_id: int,
    ) -> None:
        normalized = sorted(set(role_codes))
        if not normalized:
            raise ValueError('用户至少需要一个角色')
        with self.db.get_session() as session:
            self._begin_write(session)
            target = session.get(MiniappUserRecord, user_id)
            if target is None:
                raise LookupError('用户不存在')
            roles = list(session.execute(
                select(RbacRoleRecord).where(RbacRoleRecord.code.in_(normalized))
            ).scalars())
            if len(roles) != len(normalized):
                raise ValueError('包含不存在的角色')
            current_codes = self._user_role_codes(session, user_id)
            current_has_management = self._user_has_permission(session, user_id, 'rbac.manage')
            next_has_management = self._roles_grant_permission(
                session,
                [role.id for role in roles],
                'rbac.manage',
            )
            self._ensure_management_permission_removal_is_safe(
                session,
                user_id=user_id,
                target_is_active=bool(target.is_active),
                current_has_management=current_has_management,
                next_has_management=next_has_management,
                actor_user_id=assigned_by_user_id,
            )
            session.execute(delete(MiniappUserRoleRecord).where(MiniappUserRoleRecord.user_id == user_id))
            session.add_all([
                MiniappUserRoleRecord(
                    user_id=user_id,
                    role_id=role.id,
                    assigned_by_user_id=assigned_by_user_id,
                    created_at=datetime.utcnow(),
                )
                for role in roles
            ])
            self._record_audit(
                session,
                action='user.roles_replaced',
                target_type='user',
                target_id=str(user_id),
                actor_user_id=assigned_by_user_id,
                metadata={'before_roles': sorted(current_codes), 'after_roles': normalized},
            )
            session.commit()

    def set_user_active(
        self,
        user_id: int,
        is_active: bool,
        *,
        changed_by_user_id: int,
    ) -> Dict[str, object]:
        with self.db.get_session() as session:
            self._begin_write(session)
            target = session.get(MiniappUserRecord, user_id)
            if target is None:
                raise LookupError('用户不存在')
            if not is_active and user_id == changed_by_user_id:
                raise ValueError('不能停用当前管理员账户')
            if (
                not is_active
                and target.is_active
                and self._user_has_permission(session, user_id, 'rbac.manage')
                and self._active_management_user_count(session) <= 1
            ):
                raise ValueError('不能停用最后一个可管理权限的活跃账户')
            before = bool(target.is_active)
            target.is_active = is_active
            target.updated_at = datetime.utcnow()
            self._record_audit(
                session,
                action='user.status_changed',
                target_type='user',
                target_id=str(user_id),
                actor_user_id=changed_by_user_id,
                metadata={'before_active': before, 'after_active': is_active},
            )
            session.commit()
            return {'id': user_id, 'is_active': is_active}

    def create_custom_role(
        self,
        *,
        code: str,
        name: str,
        description: str,
        permission_codes: Sequence[str],
        created_by_user_id: int,
    ) -> Dict[str, object]:
        normalized_code = self._normalize_role_code(code)
        normalized_permissions = sorted(set(permission_codes))
        with self.db.get_session() as session:
            self._begin_write(session)
            if session.execute(select(RbacRoleRecord.id).where(RbacRoleRecord.code == normalized_code)).scalar_one_or_none() is not None:
                raise ValueError('角色编码已存在')
            permissions = self._get_permissions(session, normalized_permissions)
            role = RbacRoleRecord(
                code=normalized_code,
                name=name.strip(),
                description=description.strip(),
                is_system=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            session.add(role)
            session.flush()
            session.add_all([
                RbacRolePermissionRecord(
                    role_id=role.id,
                    permission_id=permission.id,
                    created_at=datetime.utcnow(),
                )
                for permission in permissions
            ])
            self._record_audit(
                session,
                action='role.created',
                target_type='role',
                target_id=role.code,
                actor_user_id=created_by_user_id,
                metadata={'permissions': normalized_permissions},
            )
            session.commit()
            return {
                'code': role.code,
                'name': role.name,
                'description': role.description,
                'is_system': False,
                'permissions': normalized_permissions,
            }

    def update_custom_role(
        self,
        role_code: str,
        *,
        name: str,
        description: str,
        permission_codes: Sequence[str],
        changed_by_user_id: int,
    ) -> Dict[str, object]:
        normalized_permissions = sorted(set(permission_codes))
        with self.db.get_session() as session:
            self._begin_write(session)
            role = self._get_custom_role(session, role_code)
            before = self._role_permissions(session, role.id)
            permissions = self._get_permissions(session, normalized_permissions)
            self._ensure_custom_role_management_permission_update_is_safe(
                session,
                role_id=role.id,
                before_permissions=before,
                next_permissions=normalized_permissions,
                actor_user_id=changed_by_user_id,
            )
            role.name = name.strip()
            role.description = description.strip()
            role.updated_at = datetime.utcnow()
            session.execute(delete(RbacRolePermissionRecord).where(RbacRolePermissionRecord.role_id == role.id))
            session.add_all([
                RbacRolePermissionRecord(
                    role_id=role.id,
                    permission_id=permission.id,
                    created_at=datetime.utcnow(),
                )
                for permission in permissions
            ])
            self._record_audit(
                session,
                action='role.updated',
                target_type='role',
                target_id=role.code,
                actor_user_id=changed_by_user_id,
                metadata={
                    'before_permissions': before,
                    'after_permissions': normalized_permissions,
                },
            )
            session.commit()
            return {
                'code': role.code,
                'name': role.name,
                'description': role.description,
                'is_system': False,
                'permissions': normalized_permissions,
            }

    def delete_custom_role(self, role_code: str, *, deleted_by_user_id: int) -> None:
        with self.db.get_session() as session:
            self._begin_write(session)
            role = self._get_custom_role(session, role_code)
            assignment_count = int(session.execute(
                select(func.count(MiniappUserRoleRecord.id)).where(
                    MiniappUserRoleRecord.role_id == role.id
                )
            ).scalar_one() or 0)
            if assignment_count:
                raise ValueError('仍有用户分配此角色，不能删除')
            self._record_audit(
                session,
                action='role.deleted',
                target_type='role',
                target_id=role.code,
                actor_user_id=deleted_by_user_id,
                metadata={'permissions': self._role_permissions(session, role.id)},
            )
            session.delete(role)
            session.commit()

    def list_audit_events(self, *, page: int, page_size: int) -> Dict[str, object]:
        with self.db.get_session() as session:
            total = int(session.execute(select(func.count(RbacAuditEventRecord.id))).scalar_one() or 0)
            rows = session.execute(
                select(RbacAuditEventRecord)
                .order_by(RbacAuditEventRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).scalars()
            return {
                'items': [
                    {
                        'id': row.id,
                        'action': row.action,
                        'target_type': row.target_type,
                        'target_id': row.target_id,
                        'actor_user_id': row.actor_user_id,
                        'metadata': self._safe_json_object(row.metadata_json),
                        'created_at': row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in rows
                ],
                'total': total,
                'page': page,
                'page_size': page_size,
            }

    @staticmethod
    def _serialize_role(session, row: RbacRoleRecord) -> Dict[str, object]:
        return {
            'code': row.code,
            'name': row.name,
            'description': row.description,
            'is_system': bool(row.is_system),
            'permissions': RbacRepository._role_permissions(session, row.id),
        }

    @staticmethod
    def _serialize_user(row: MiniappUserRecord, access: Dict[str, List[str]]) -> Dict[str, object]:
        return {
            'id': row.id,
            'nickname': row.nickname,
            'avatar_url': row.avatar_url,
            'profile_updated_at': row.profile_updated_at.isoformat() if row.profile_updated_at else None,
            'created_at': row.created_at.isoformat() if row.created_at else None,
            'last_login_at': row.last_login_at.isoformat() if row.last_login_at else None,
            'is_active': bool(row.is_active),
            'roles': access['roles'],
            'permissions': access['permissions'],
        }

    @staticmethod
    def _role_permissions(session, role_id: int) -> List[str]:
        return list(session.execute(
            select(RbacPermissionRecord.code)
            .join(RbacRolePermissionRecord, RbacRolePermissionRecord.permission_id == RbacPermissionRecord.id)
            .where(RbacRolePermissionRecord.role_id == role_id)
            .order_by(RbacPermissionRecord.code)
        ).scalars())

    @staticmethod
    def _safe_json_object(value: str) -> Dict[str, Any]:
        try:
            result = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return result if isinstance(result, dict) else {}

    def _access_for_users(self, session, user_ids: Sequence[int]) -> Dict[int, Dict[str, List[str]]]:
        access = {user_id: {'roles': [], 'permissions': []} for user_id in user_ids}
        if not user_ids:
            return access
        role_rows = session.execute(
            select(MiniappUserRoleRecord.user_id, RbacRoleRecord.code)
            .join(RbacRoleRecord, RbacRoleRecord.id == MiniappUserRoleRecord.role_id)
            .where(MiniappUserRoleRecord.user_id.in_(user_ids))
            .order_by(MiniappUserRoleRecord.user_id, RbacRoleRecord.code)
        ).all()
        permission_rows = session.execute(
            select(MiniappUserRoleRecord.user_id, RbacPermissionRecord.code)
            .join(RbacRolePermissionRecord, RbacRolePermissionRecord.role_id == MiniappUserRoleRecord.role_id)
            .join(RbacPermissionRecord, RbacPermissionRecord.id == RbacRolePermissionRecord.permission_id)
            .where(MiniappUserRoleRecord.user_id.in_(user_ids))
            .distinct()
            .order_by(MiniappUserRoleRecord.user_id, RbacPermissionRecord.code)
        ).all()
        for user_id, code in role_rows:
            access[user_id]['roles'].append(code)
        for user_id, code in permission_rows:
            access[user_id]['permissions'].append(code)
        return access

    def _get_permissions(self, session, codes: Sequence[str]) -> List[RbacPermissionRecord]:
        rows = list(session.execute(
            select(RbacPermissionRecord).where(RbacPermissionRecord.code.in_(codes))
        ).scalars()) if codes else []
        if len(rows) != len(codes):
            raise ValueError('包含不存在的权限')
        return rows

    @staticmethod
    def _normalize_role_code(value: str) -> str:
        code = value.strip().lower()
        if not _ROLE_CODE_PATTERN.fullmatch(code):
            raise ValueError('角色编码仅支持小写字母、数字、连字符和下划线，且长度为 2-64')
        return code

    @staticmethod
    def _get_custom_role(session, role_code: str) -> RbacRoleRecord:
        role = session.execute(
            select(RbacRoleRecord).where(RbacRoleRecord.code == role_code)
        ).scalar_one_or_none()
        if role is None:
            raise LookupError('角色不存在')
        if role.is_system:
            raise ValueError('系统角色不能通过权限管理页面修改或删除')
        return role

    @staticmethod
    def _user_role_codes(session, user_id: int) -> set[str]:
        return set(session.execute(
            select(RbacRoleRecord.code)
            .join(MiniappUserRoleRecord, MiniappUserRoleRecord.role_id == RbacRoleRecord.id)
            .where(MiniappUserRoleRecord.user_id == user_id)
        ).scalars())

    @staticmethod
    def _begin_write(session) -> None:
        if getattr(session.bind.dialect, 'name', None) == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))

    @staticmethod
    def _active_management_user_count(session) -> int:
        return int(session.execute(
            select(func.count(func.distinct(MiniappUserRoleRecord.user_id)))
            .join(RbacRolePermissionRecord, RbacRolePermissionRecord.role_id == MiniappUserRoleRecord.role_id)
            .join(RbacPermissionRecord, RbacPermissionRecord.id == RbacRolePermissionRecord.permission_id)
            .join(MiniappUserRecord, MiniappUserRecord.id == MiniappUserRoleRecord.user_id)
            .where(
                RbacPermissionRecord.code == 'rbac.manage',
                MiniappUserRecord.is_active.is_(True),
            )
        ).scalar_one() or 0)

    @staticmethod
    def _user_has_permission(session, user_id: int, permission_code: str) -> bool:
        return session.execute(
            select(MiniappUserRoleRecord.id)
            .join(RbacRolePermissionRecord, RbacRolePermissionRecord.role_id == MiniappUserRoleRecord.role_id)
            .join(RbacPermissionRecord, RbacPermissionRecord.id == RbacRolePermissionRecord.permission_id)
            .where(
                MiniappUserRoleRecord.user_id == user_id,
                RbacPermissionRecord.code == permission_code,
            )
            .limit(1)
        ).scalar_one_or_none() is not None

    @staticmethod
    def _user_has_permission_excluding_role(
        session,
        user_id: int,
        permission_code: str,
        excluded_role_id: int,
    ) -> bool:
        return session.execute(
            select(MiniappUserRoleRecord.id)
            .join(RbacRolePermissionRecord, RbacRolePermissionRecord.role_id == MiniappUserRoleRecord.role_id)
            .join(RbacPermissionRecord, RbacPermissionRecord.id == RbacRolePermissionRecord.permission_id)
            .where(
                MiniappUserRoleRecord.user_id == user_id,
                MiniappUserRoleRecord.role_id != excluded_role_id,
                RbacPermissionRecord.code == permission_code,
            )
            .limit(1)
        ).scalar_one_or_none() is not None

    @staticmethod
    def _roles_grant_permission(session, role_ids: Sequence[int], permission_code: str) -> bool:
        if not role_ids:
            return False
        return session.execute(
            select(RbacRolePermissionRecord.id)
            .join(RbacPermissionRecord, RbacPermissionRecord.id == RbacRolePermissionRecord.permission_id)
            .where(
                RbacRolePermissionRecord.role_id.in_(role_ids),
                RbacPermissionRecord.code == permission_code,
            )
            .limit(1)
        ).scalar_one_or_none() is not None

    def _ensure_management_permission_removal_is_safe(
        self,
        session,
        *,
        user_id: int,
        target_is_active: bool,
        current_has_management: bool,
        next_has_management: bool,
        actor_user_id: int,
    ) -> None:
        if not current_has_management or next_has_management:
            return
        if user_id == actor_user_id:
            raise ValueError('不能移除当前操作者的 rbac.manage 权限')
        if target_is_active and self._active_management_user_count(session) <= 1:
            raise ValueError('不能移除最后一个可管理权限的活跃账户')

    def _ensure_custom_role_management_permission_update_is_safe(
        self,
        session,
        *,
        role_id: int,
        before_permissions: Sequence[str],
        next_permissions: Sequence[str],
        actor_user_id: int,
    ) -> None:
        if 'rbac.manage' not in before_permissions or 'rbac.manage' in next_permissions:
            return
        active_user_ids = list(session.execute(
            select(MiniappUserRoleRecord.user_id)
            .join(MiniappUserRecord, MiniappUserRecord.id == MiniappUserRoleRecord.user_id)
            .where(
                MiniappUserRoleRecord.role_id == role_id,
                MiniappUserRecord.is_active.is_(True),
            )
        ).scalars())
        losing_user_ids = [
            user_id
            for user_id in active_user_ids
            if not self._user_has_permission_excluding_role(session, user_id, 'rbac.manage', role_id)
        ]
        if not losing_user_ids:
            return
        if actor_user_id in losing_user_ids:
            raise ValueError('不能移除当前操作者的 rbac.manage 权限')
        if self._active_management_user_count(session) <= len(losing_user_ids):
            raise ValueError('不能移除最后一个可管理权限的活跃账户')

    @staticmethod
    def _record_audit(
        session,
        *,
        action: str,
        target_type: str,
        target_id: str,
        actor_user_id: Optional[int],
        metadata: Dict[str, Any],
    ) -> None:
        session.add(RbacAuditEventRecord(
            action=action,
            target_type=target_type,
            target_id=target_id,
            actor_user_id=actor_user_id,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            created_at=datetime.utcnow(),
        ))
