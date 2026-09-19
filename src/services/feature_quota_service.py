# -*- coding: utf-8 -*-
"""可信请求边界上的每日功能额度治理。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

from fastapi import HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.storage import (
    DatabaseManager,
    FeatureQuotaPlanLimitRecord,
    FeatureQuotaPlanRecord,
    FeatureQuotaPolicyRecord,
    FeatureQuotaUsageRecord,
    FeatureQuotaUserOverrideRecord,
    FeatureQuotaUserPlanAssignmentRecord,
    FeatureQuotaWhitelistRecord,
    MiniappUserRecord,
    RbacAuditEventRecord,
    local_naive_now,
)


@dataclass(frozen=True)
class FeatureQuotaDefinition:
    code: str
    name: str
    description: str
    default_daily_limit: int


# 仅列出会产生显著计算、LLM 或外部供应商成本的交互操作。
FEATURE_QUOTA_DEFINITIONS: tuple[FeatureQuotaDefinition, ...] = (
    FeatureQuotaDefinition('stock_analysis', '个股分析', '提交个股或持仓分析任务。', 5),
    FeatureQuotaDefinition('market_review', '大盘复盘', '提交大盘复盘任务。', 2),
    FeatureQuotaDefinition('agent_chat', '问股对话', '发送一次会调用 Agent 的问股消息。', 20),
    FeatureQuotaDefinition('agent_research', '深度研究', '执行一次深度研究任务。', 2),
    FeatureQuotaDefinition('screening', '策略选股', '执行一次策略选股任务。', 3),
    FeatureQuotaDefinition('backtest', '回测', '执行一次历史回测任务。', 3),
    FeatureQuotaDefinition('decision_signal_reassess', '决策信号重评估', '重新评估一份历史报告。', 5),
    FeatureQuotaDefinition('decision_signal_outcomes', '决策信号后验', '批量计算决策信号后验结果。', 3),
    FeatureQuotaDefinition('image_stock_extract', '图片识股', '使用 Vision LLM 从图片提取股票代码。', 10),
    FeatureQuotaDefinition('scheduled_analysis', '定时分析', '每日定时分析一只参与定时的自选股。', 10),
)
FEATURE_QUOTA_BY_CODE: Dict[str, FeatureQuotaDefinition] = {
    definition.code: definition for definition in FEATURE_QUOTA_DEFINITIONS
}
_PLAN_CODE_PATTERN = re.compile(r'^[a-z][a-z0-9_]{1,63}$')


def _local_today() -> date:
    """返回额度计账日期，与数据库时间语义一致，使用本地（北京）时间。"""
    return local_naive_now().date()


@dataclass(frozen=True)
class FeatureQuotaEntitlement:
    feature_code: str
    name: str
    description: str
    daily_limit: Optional[int]
    used_count: int
    remaining: Optional[int]
    unlimited: bool
    disabled: bool
    reset_at: str
    limit_source: str
    plan_code: Optional[str] = None
    effective_until: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            'feature_code': self.feature_code,
            'name': self.name,
            'description': self.description,
            'daily_limit': self.daily_limit,
            'used_count': self.used_count,
            'remaining': self.remaining,
            'unlimited': self.unlimited,
            'disabled': self.disabled,
            'reset_at': self.reset_at,
            'limit_source': self.limit_source,
            'plan_code': self.plan_code,
            'effective_until': self.effective_until,
        }


@dataclass(frozen=True)
class _ResolvedQuotaRule:
    daily_limit: Optional[int]
    limit_source: str
    plan_code: Optional[str] = None
    effective_until: Optional[date] = None


class FeatureQuotaExceededError(Exception):
    """Raised when a normal user has exhausted or been denied an entitlement."""

    def __init__(self, entitlement: FeatureQuotaEntitlement):
        super().__init__(f'feature quota exceeded: {entitlement.feature_code}')
        self.entitlement = entitlement


class FeatureQuotaService:
    """Resolve and persist feature quota rules in one atomic write window.

    Every entry point uses the same rule precedence so a displayed entitlement
    and a charged execution cannot disagree: user override > feature whitelist
    > all-feature whitelist > active plan > global default policy.
    """

    def __init__(
        self,
        database_manager: Optional[DatabaseManager] = None,
        *,
        today_provider: Callable[[], date] = _local_today,
    ) -> None:
        self.db = database_manager or DatabaseManager.get_instance()
        self._today_provider = today_provider

    @staticmethod
    def _definition(feature_code: str) -> FeatureQuotaDefinition:
        definition = FEATURE_QUOTA_BY_CODE.get(feature_code)
        if definition is None:
            raise ValueError(f'未知功能额度码: {feature_code}')
        return definition

    @staticmethod
    def _validate_user_id(user_id: int) -> None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError('user_id 必须是正整数')

    @staticmethod
    def _validate_daily_limit(daily_limit: int) -> None:
        if isinstance(daily_limit, bool) or not isinstance(daily_limit, int) or not 0 <= daily_limit <= 10000:
            raise ValueError('daily_limit 必须是 0 到 10000 的整数')

    @staticmethod
    def _validate_window(effective_from: date, effective_until: Optional[date]) -> None:
        if not isinstance(effective_from, date):
            raise ValueError('effective_from 必须是 date')
        if effective_until is not None and not isinstance(effective_until, date):
            raise ValueError('effective_until 必须是 date 或 null')
        if effective_until is not None and effective_until < effective_from:
            raise ValueError('effective_until 不能早于 effective_from')

    @staticmethod
    def _normalize_plan_code(plan_code: str) -> str:
        normalized = (plan_code or '').strip().lower()
        if not _PLAN_CODE_PATTERN.fullmatch(normalized):
            raise ValueError('套餐编码必须以小写字母开头，仅包含小写字母、数字和下划线，长度 2 到 64')
        return normalized

    @classmethod
    def _normalize_limits(cls, limits: Dict[str, int]) -> Dict[str, int]:
        if not isinstance(limits, dict):
            raise ValueError('limits 必须是功能码到每日次数的对象')
        normalized: Dict[str, int] = {}
        for feature_code, daily_limit in limits.items():
            definition = cls._definition(feature_code)
            cls._validate_daily_limit(daily_limit)
            normalized[definition.code] = daily_limit
        return normalized

    def _today(self) -> date:
        today = self._today_provider()
        if not isinstance(today, date):
            raise RuntimeError('today_provider 必须返回 date')
        return today

    def current_period_start(self) -> date:
        """Return the UTC accounting date to bind a later compensation to."""
        return self._today()

    @staticmethod
    def _reset_at(period_start: date) -> str:
        return datetime.combine(
            period_start + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        ).isoformat().replace('+00:00', 'Z')

    @staticmethod
    def _date_payload(value: Optional[date]) -> Optional[str]:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _seed_policies(session: Session) -> None:
        existing_codes = {
            code for (code,) in session.query(FeatureQuotaPolicyRecord.feature_code).all()
        }
        for definition in FEATURE_QUOTA_DEFINITIONS:
            if definition.code not in existing_codes:
                session.add(
                    FeatureQuotaPolicyRecord(
                        feature_code=definition.code,
                        daily_limit=definition.default_daily_limit,
                    )
                )

    @staticmethod
    def _audit(
        session: Session,
        *,
        action: str,
        target_type: str,
        target_id: str,
        actor_user_id: Optional[int],
        metadata: dict,
    ) -> None:
        session.add(
            RbacAuditEventRecord(
                action=action,
                target_type=target_type,
                target_id=target_id,
                actor_user_id=actor_user_id,
                metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                created_at=local_naive_now(),
            )
        )

    @staticmethod
    def _require_user(session: Session, user_id: int) -> MiniappUserRecord:
        user = session.query(MiniappUserRecord).filter(MiniappUserRecord.id == user_id).one_or_none()
        if user is None:
            raise LookupError(f'用户不存在: {user_id}')
        return user

    @staticmethod
    def _active_window_filters(record_type, period_start: date) -> Iterable:
        return (
            record_type.revoked_at.is_(None),
            record_type.effective_from <= period_start,
            or_(record_type.effective_until.is_(None), record_type.effective_until >= period_start),
        )

    @staticmethod
    def _windows_overlap(
        first_from: date,
        first_until: Optional[date],
        second_from: date,
        second_until: Optional[date],
    ) -> bool:
        return (
            (first_until is None or first_until >= second_from)
            and (second_until is None or second_until >= first_from)
        )

    def _replace_scope_window(
        self,
        records: Iterable,
        *,
        effective_from: date,
        effective_until: Optional[date],
    ) -> list[int]:
        """Safely replace one user's rule scope without silently deleting schedules.

        A future rule takes effect by ending the current rule on the previous UTC
        date. Rules that would need to resume after a finite future window, or
        already-scheduled overlapping rules, cannot be represented by a single
        history row and are rejected instead of being revoked implicitly.
        """
        today = self._today()
        active_records = [record for record in records if record.revoked_at is None]
        replaced_ids: list[int] = []

        if effective_from > today:
            for record in active_records:
                if not self._windows_overlap(
                    record.effective_from,
                    record.effective_until,
                    effective_from,
                    effective_until,
                ):
                    continue
                if record.effective_from >= effective_from:
                    raise ValueError('存在重叠的未来排期，请先撤销或调整现有规则')
                if effective_until is not None and (
                    record.effective_until is None or record.effective_until > effective_until
                ):
                    raise ValueError('有限期限排期会要求现有规则在结束后恢复，当前不支持自动恢复')
                record.effective_until = effective_from - timedelta(days=1)
                replaced_ids.append(record.id)
            return replaced_ids

        if effective_until is not None and effective_until < today:
            return replaced_ids

        for record in active_records:
            if record.effective_from > today and self._windows_overlap(
                record.effective_from,
                record.effective_until,
                effective_from,
                effective_until,
            ):
                raise ValueError('当前规则替换会与未来排期重叠，请先撤销或调整未来规则')

        now = local_naive_now()
        for record in active_records:
            if (
                record.effective_from <= today
                and (record.effective_until is None or record.effective_until >= today)
            ):
                record.revoked_at = now
                replaced_ids.append(record.id)
        return replaced_ids

    @classmethod
    def _entitlement(
        cls,
        definition: FeatureQuotaDefinition,
        *,
        resolved_rule: _ResolvedQuotaRule,
        used_count: int,
        period_start: date,
    ) -> FeatureQuotaEntitlement:
        if resolved_rule.daily_limit is None:
            return FeatureQuotaEntitlement(
                feature_code=definition.code,
                name=definition.name,
                description=definition.description,
                daily_limit=None,
                used_count=0,
                remaining=None,
                unlimited=True,
                disabled=False,
                reset_at=cls._reset_at(period_start),
                limit_source=resolved_rule.limit_source,
                plan_code=resolved_rule.plan_code,
                effective_until=cls._date_payload(resolved_rule.effective_until),
            )
        return FeatureQuotaEntitlement(
            feature_code=definition.code,
            name=definition.name,
            description=definition.description,
            daily_limit=resolved_rule.daily_limit,
            used_count=used_count,
            remaining=max(resolved_rule.daily_limit - used_count, 0),
            unlimited=False,
            disabled=resolved_rule.daily_limit == 0,
            reset_at=cls._reset_at(period_start),
            limit_source=resolved_rule.limit_source,
            plan_code=resolved_rule.plan_code,
            effective_until=cls._date_payload(resolved_rule.effective_until),
        )

    def _with_seeded_policies(self, operation_name: str, operation):
        def write_operation(session: Session):
            self._seed_policies(session)
            session.flush()
            return operation(session)

        return self.db._run_write_transaction(operation_name, write_operation)

    def _global_limits(self, session: Session) -> Dict[str, int]:
        return {
            item.feature_code: item.daily_limit
            for item in session.query(FeatureQuotaPolicyRecord).all()
        }

    def _active_plan_assignment(
        self,
        session: Session,
        user_id: int,
        period_start: date,
    ) -> Optional[tuple[FeatureQuotaUserPlanAssignmentRecord, FeatureQuotaPlanRecord]]:
        return (
            session.query(FeatureQuotaUserPlanAssignmentRecord, FeatureQuotaPlanRecord)
            .join(FeatureQuotaPlanRecord, FeatureQuotaPlanRecord.id == FeatureQuotaUserPlanAssignmentRecord.plan_id)
            .filter(
                FeatureQuotaUserPlanAssignmentRecord.user_id == user_id,
                FeatureQuotaPlanRecord.is_active.is_(True),
                *self._active_window_filters(FeatureQuotaUserPlanAssignmentRecord, period_start),
            )
            .order_by(
                FeatureQuotaUserPlanAssignmentRecord.effective_from.desc(),
                FeatureQuotaUserPlanAssignmentRecord.id.desc(),
            )
            .first()
        )

    def _resolve_effective_rule(
        self,
        session: Session,
        *,
        user_id: int,
        definition: FeatureQuotaDefinition,
        period_start: date,
        global_limits: Dict[str, int],
    ) -> _ResolvedQuotaRule:
        override = (
            session.query(FeatureQuotaUserOverrideRecord)
            .filter(
                FeatureQuotaUserOverrideRecord.user_id == user_id,
                FeatureQuotaUserOverrideRecord.feature_code == definition.code,
                *self._active_window_filters(FeatureQuotaUserOverrideRecord, period_start),
            )
            .order_by(FeatureQuotaUserOverrideRecord.effective_from.desc(), FeatureQuotaUserOverrideRecord.id.desc())
            .first()
        )
        if override is not None:
            return _ResolvedQuotaRule(
                daily_limit=override.daily_limit,
                limit_source='user_override',
                effective_until=override.effective_until,
            )

        for feature_code, source in ((definition.code, 'whitelist_feature'), (None, 'whitelist_all')):
            whitelist = (
                session.query(FeatureQuotaWhitelistRecord)
                .filter(
                    FeatureQuotaWhitelistRecord.user_id == user_id,
                    FeatureQuotaWhitelistRecord.feature_code.is_(None)
                    if feature_code is None
                    else FeatureQuotaWhitelistRecord.feature_code == feature_code,
                    *self._active_window_filters(FeatureQuotaWhitelistRecord, period_start),
                )
                .order_by(FeatureQuotaWhitelistRecord.effective_from.desc(), FeatureQuotaWhitelistRecord.id.desc())
                .first()
            )
            if whitelist is not None:
                return _ResolvedQuotaRule(
                    daily_limit=None,
                    limit_source=source,
                    effective_until=whitelist.effective_until,
                )

        assignment_and_plan = self._active_plan_assignment(session, user_id, period_start)
        if assignment_and_plan is not None:
            assignment, plan = assignment_and_plan
            plan_limit = (
                session.query(FeatureQuotaPlanLimitRecord)
                .filter(
                    FeatureQuotaPlanLimitRecord.plan_id == plan.id,
                    FeatureQuotaPlanLimitRecord.feature_code == definition.code,
                )
                .one_or_none()
            )
            if plan_limit is not None:
                return _ResolvedQuotaRule(
                    daily_limit=plan_limit.daily_limit,
                    limit_source='plan',
                    plan_code=plan.code,
                    effective_until=assignment.effective_until,
                )

        return _ResolvedQuotaRule(
            daily_limit=global_limits[definition.code],
            limit_source='global_policy',
        )

    def _entitlements_for_user_in_session(
        self,
        session: Session,
        user_id: int,
        period_start: date,
    ) -> List[FeatureQuotaEntitlement]:
        global_limits = self._global_limits(session)
        usages = {
            item.feature_code: item.used_count
            for item in session.query(FeatureQuotaUsageRecord)
            .filter(
                FeatureQuotaUsageRecord.user_id == user_id,
                FeatureQuotaUsageRecord.period_start == period_start,
            )
            .all()
        }
        return [
            self._entitlement(
                definition,
                resolved_rule=self._resolve_effective_rule(
                    session,
                    user_id=user_id,
                    definition=definition,
                    period_start=period_start,
                    global_limits=global_limits,
                ),
                used_count=usages.get(definition.code, 0),
                period_start=period_start,
            )
            for definition in FEATURE_QUOTA_DEFINITIONS
        ]

    def list_policies(self) -> List[dict]:
        def operation(session: Session) -> List[dict]:
            policy_by_code = {
                item.feature_code: item
                for item in session.query(FeatureQuotaPolicyRecord).all()
            }
            return [
                {
                    'feature_code': definition.code,
                    'name': definition.name,
                    'description': definition.description,
                    'daily_limit': policy_by_code[definition.code].daily_limit,
                    'updated_at': policy_by_code[definition.code].updated_at.isoformat()
                    if policy_by_code[definition.code].updated_at else None,
                    'updated_by_user_id': policy_by_code[definition.code].updated_by_user_id,
                }
                for definition in FEATURE_QUOTA_DEFINITIONS
            ]

        return self._with_seeded_policies('list_feature_quota_policies', operation)

    def update_policy(
        self,
        feature_code: str,
        daily_limit: int,
        *,
        actor_user_id: Optional[int],
    ) -> dict:
        definition = self._definition(feature_code)
        self._validate_daily_limit(daily_limit)

        def operation(session: Session) -> dict:
            policy = (
                session.query(FeatureQuotaPolicyRecord)
                .filter(FeatureQuotaPolicyRecord.feature_code == definition.code)
                .one()
            )
            previous_limit = policy.daily_limit
            policy.daily_limit = daily_limit
            policy.updated_by_user_id = actor_user_id
            policy.updated_at = local_naive_now()
            self._audit(
                session,
                action='feature_quota.policy_updated',
                target_type='feature_quota_policy',
                target_id=definition.code,
                actor_user_id=actor_user_id,
                metadata={
                    'before_daily_limit': previous_limit,
                    'after_daily_limit': daily_limit,
                },
            )
            session.flush()
            return {
                'feature_code': definition.code,
                'name': definition.name,
                'description': definition.description,
                'daily_limit': policy.daily_limit,
                'updated_at': policy.updated_at.isoformat() if policy.updated_at else None,
                'updated_by_user_id': policy.updated_by_user_id,
            }

        return self._with_seeded_policies('update_feature_quota_policy', operation)

    def _plan_payload(self, session: Session, plan: FeatureQuotaPlanRecord) -> dict:
        limits = (
            session.query(FeatureQuotaPlanLimitRecord)
            .filter(FeatureQuotaPlanLimitRecord.plan_id == plan.id)
            .order_by(FeatureQuotaPlanLimitRecord.feature_code.asc())
            .all()
        )
        return {
            'code': plan.code,
            'name': plan.name,
            'description': plan.description,
            'is_active': plan.is_active,
            'limits': [
                {'feature_code': item.feature_code, 'daily_limit': item.daily_limit}
                for item in limits
            ],
            'created_at': plan.created_at.isoformat() if plan.created_at else None,
            'updated_at': plan.updated_at.isoformat() if plan.updated_at else None,
        }

    def list_plans(self) -> List[dict]:
        def operation(session: Session) -> List[dict]:
            plans = session.query(FeatureQuotaPlanRecord).order_by(FeatureQuotaPlanRecord.code.asc()).all()
            return [self._plan_payload(session, plan) for plan in plans]

        return self._with_seeded_policies('list_feature_quota_plans', operation)

    def create_plan(
        self,
        *,
        code: str,
        name: str,
        description: str,
        limits: Dict[str, int],
        is_active: bool = True,
        actor_user_id: Optional[int],
    ) -> dict:
        normalized_code = self._normalize_plan_code(code)
        normalized_name = (name or '').strip()
        if not normalized_name:
            raise ValueError('套餐名称不能为空')
        if not isinstance(is_active, bool):
            raise ValueError('is_active 必须是布尔值')
        normalized_limits = self._normalize_limits(limits)

        def operation(session: Session) -> dict:
            if session.query(FeatureQuotaPlanRecord).filter(FeatureQuotaPlanRecord.code == normalized_code).one_or_none():
                raise ValueError(f'套餐已存在: {normalized_code}')
            plan = FeatureQuotaPlanRecord(
                code=normalized_code,
                name=normalized_name,
                description=(description or '').strip(),
                is_active=is_active,
            )
            session.add(plan)
            session.flush()
            for feature_code, daily_limit in normalized_limits.items():
                session.add(FeatureQuotaPlanLimitRecord(
                    plan_id=plan.id,
                    feature_code=feature_code,
                    daily_limit=daily_limit,
                ))
            self._audit(
                session,
                action='feature_quota.plan_created',
                target_type='feature_quota_plan',
                target_id=normalized_code,
                actor_user_id=actor_user_id,
                metadata={'is_active': is_active, 'limits': normalized_limits},
            )
            session.flush()
            return self._plan_payload(session, plan)

        return self._with_seeded_policies('create_feature_quota_plan', operation)

    def update_plan(
        self,
        plan_code: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        is_active: Optional[bool] = None,
        limits: Optional[Dict[str, int]] = None,
        actor_user_id: Optional[int],
    ) -> dict:
        normalized_code = self._normalize_plan_code(plan_code)
        normalized_limits = self._normalize_limits(limits) if limits is not None else None
        if name is not None and not name.strip():
            raise ValueError('套餐名称不能为空')
        if is_active is not None and not isinstance(is_active, bool):
            raise ValueError('is_active 必须是布尔值')

        def operation(session: Session) -> dict:
            plan = session.query(FeatureQuotaPlanRecord).filter(FeatureQuotaPlanRecord.code == normalized_code).one_or_none()
            if plan is None:
                raise LookupError(f'套餐不存在: {normalized_code}')
            before = self._plan_payload(session, plan)
            if name is not None:
                plan.name = name.strip()
            if description is not None:
                plan.description = description.strip()
            if is_active is not None:
                plan.is_active = is_active
            if normalized_limits is not None:
                session.query(FeatureQuotaPlanLimitRecord).filter(
                    FeatureQuotaPlanLimitRecord.plan_id == plan.id
                ).delete(synchronize_session=False)
                for feature_code, daily_limit in normalized_limits.items():
                    session.add(FeatureQuotaPlanLimitRecord(
                        plan_id=plan.id,
                        feature_code=feature_code,
                        daily_limit=daily_limit,
                    ))
            plan.updated_at = local_naive_now()
            session.flush()
            payload = self._plan_payload(session, plan)
            self._audit(
                session,
                action='feature_quota.plan_updated',
                target_type='feature_quota_plan',
                target_id=normalized_code,
                actor_user_id=actor_user_id,
                metadata={'before': before, 'after': payload},
            )
            return payload

        return self._with_seeded_policies('update_feature_quota_plan', operation)

    def assign_plan(
        self,
        user_id: int,
        *,
        plan_code: Optional[str],
        effective_from: Optional[date] = None,
        effective_until: Optional[date] = None,
        actor_user_id: Optional[int],
    ) -> dict:
        self._validate_user_id(user_id)
        current_day = effective_from or self._today()
        self._validate_window(current_day, effective_until)
        normalized_plan_code = self._normalize_plan_code(plan_code) if plan_code is not None else None

        def operation(session: Session) -> dict:
            self._require_user(session, user_id)
            active_assignments = session.query(FeatureQuotaUserPlanAssignmentRecord).filter(
                FeatureQuotaUserPlanAssignmentRecord.user_id == user_id,
                FeatureQuotaUserPlanAssignmentRecord.revoked_at.is_(None),
            ).all()
            before_plan_ids = [assignment.plan_id for assignment in active_assignments]
            if normalized_plan_code is None:
                if current_day > self._today():
                    replaced_assignment_ids = self._replace_scope_window(
                        active_assignments,
                        effective_from=current_day,
                        effective_until=effective_until,
                    )
                else:
                    now = local_naive_now()
                    for assignment in active_assignments:
                        assignment.revoked_at = now
                    replaced_assignment_ids = [assignment.id for assignment in active_assignments]
                self._audit(
                    session,
                    action='feature_quota.plan_unassigned',
                    target_type='feature_quota_user',
                    target_id=str(user_id),
                    actor_user_id=actor_user_id,
                        metadata={
                        'before_plan_ids': before_plan_ids,
                        'effective_from': self._date_payload(current_day),
                        'effective_until': self._date_payload(effective_until),
                        'replaced_assignment_ids': replaced_assignment_ids,
                    },
                )
                return self._quota_profile_payload(session, user_id, current_day)
            plan = session.query(FeatureQuotaPlanRecord).filter(
                FeatureQuotaPlanRecord.code == normalized_plan_code
            ).one_or_none()
            if plan is None:
                raise LookupError(f'套餐不存在: {normalized_plan_code}')
            if not plan.is_active:
                raise ValueError('不能分配已停用的套餐')
            replaced_assignment_ids = self._replace_scope_window(
                active_assignments,
                effective_from=current_day,
                effective_until=effective_until,
            )
            session.add(FeatureQuotaUserPlanAssignmentRecord(
                user_id=user_id,
                plan_id=plan.id,
                effective_from=current_day,
                effective_until=effective_until,
                assigned_by_user_id=actor_user_id,
            ))
            self._audit(
                session,
                action='feature_quota.plan_assigned',
                target_type='feature_quota_user',
                target_id=str(user_id),
                actor_user_id=actor_user_id,
                metadata={
                    'plan_code': normalized_plan_code,
                    'effective_from': self._date_payload(current_day),
                    'effective_until': self._date_payload(effective_until),
                    'replaced_assignment_ids': replaced_assignment_ids,
                },
            )
            session.flush()
            return self._quota_profile_payload(session, user_id, current_day)

        return self._with_seeded_policies('assign_feature_quota_plan', operation)

    def set_user_override(
        self,
        user_id: int,
        feature_code: str,
        *,
        daily_limit: int,
        effective_from: Optional[date] = None,
        effective_until: Optional[date] = None,
        reason: str = '',
        actor_user_id: Optional[int],
    ) -> dict:
        self._validate_user_id(user_id)
        definition = self._definition(feature_code)
        self._validate_daily_limit(daily_limit)
        current_day = effective_from or self._today()
        self._validate_window(current_day, effective_until)

        def operation(session: Session) -> dict:
            self._require_user(session, user_id)
            existing = session.query(FeatureQuotaUserOverrideRecord).filter(
                FeatureQuotaUserOverrideRecord.user_id == user_id,
                FeatureQuotaUserOverrideRecord.feature_code == definition.code,
                FeatureQuotaUserOverrideRecord.revoked_at.is_(None),
            ).all()
            replaced_override_ids = self._replace_scope_window(
                existing,
                effective_from=current_day,
                effective_until=effective_until,
            )
            session.add(FeatureQuotaUserOverrideRecord(
                user_id=user_id,
                feature_code=definition.code,
                daily_limit=daily_limit,
                effective_from=current_day,
                effective_until=effective_until,
                reason=(reason or '').strip(),
                assigned_by_user_id=actor_user_id,
            ))
            self._audit(
                session,
                action='feature_quota.user_override_set',
                target_type='feature_quota_user_override',
                target_id=f'{user_id}:{definition.code}',
                actor_user_id=actor_user_id,
                metadata={
                    'daily_limit': daily_limit,
                    'effective_from': self._date_payload(current_day),
                    'effective_until': self._date_payload(effective_until),
                    'reason': (reason or '').strip(),
                    'replaced_override_ids': replaced_override_ids,
                },
            )
            session.flush()
            return self._quota_profile_payload(session, user_id, current_day)

        return self._with_seeded_policies('set_feature_quota_user_override', operation)

    def revoke_user_override(
        self,
        user_id: int,
        feature_code: str,
        *,
        actor_user_id: Optional[int],
    ) -> dict:
        self._validate_user_id(user_id)
        definition = self._definition(feature_code)
        current_day = self._today()

        def operation(session: Session) -> dict:
            self._require_user(session, user_id)
            rows = session.query(FeatureQuotaUserOverrideRecord).filter(
                FeatureQuotaUserOverrideRecord.user_id == user_id,
                FeatureQuotaUserOverrideRecord.feature_code == definition.code,
                FeatureQuotaUserOverrideRecord.revoked_at.is_(None),
            ).all()
            if not rows:
                raise LookupError('用户当前没有该功能的额度覆盖')
            now = local_naive_now()
            for row in rows:
                row.revoked_at = now
            self._audit(
                session,
                action='feature_quota.user_override_revoked',
                target_type='feature_quota_user_override',
                target_id=f'{user_id}:{definition.code}',
                actor_user_id=actor_user_id,
                metadata={},
            )
            return self._quota_profile_payload(session, user_id, current_day)

        return self._with_seeded_policies('revoke_feature_quota_user_override', operation)

    def set_whitelist_entry(
        self,
        user_id: int,
        *,
        feature_code: Optional[str],
        effective_from: Optional[date] = None,
        effective_until: Optional[date] = None,
        reason: str = '',
        actor_user_id: Optional[int],
    ) -> dict:
        self._validate_user_id(user_id)
        normalized_feature_code = self._definition(feature_code).code if feature_code is not None else None
        current_day = effective_from or self._today()
        self._validate_window(current_day, effective_until)

        def operation(session: Session) -> dict:
            self._require_user(session, user_id)
            query = session.query(FeatureQuotaWhitelistRecord).filter(
                FeatureQuotaWhitelistRecord.user_id == user_id,
                FeatureQuotaWhitelistRecord.revoked_at.is_(None),
            )
            query = query.filter(
                FeatureQuotaWhitelistRecord.feature_code.is_(None)
                if normalized_feature_code is None
                else FeatureQuotaWhitelistRecord.feature_code == normalized_feature_code
            )
            existing = query.all()
            replaced_whitelist_ids = self._replace_scope_window(
                existing,
                effective_from=current_day,
                effective_until=effective_until,
            )
            session.add(FeatureQuotaWhitelistRecord(
                user_id=user_id,
                feature_code=normalized_feature_code,
                effective_from=current_day,
                effective_until=effective_until,
                reason=(reason or '').strip(),
                granted_by_user_id=actor_user_id,
            ))
            scope = normalized_feature_code or '*'
            self._audit(
                session,
                action='feature_quota.whitelist_granted',
                target_type='feature_quota_whitelist',
                target_id=f'{user_id}:{scope}',
                actor_user_id=actor_user_id,
                metadata={
                    'feature_code': normalized_feature_code,
                    'effective_from': self._date_payload(current_day),
                    'effective_until': self._date_payload(effective_until),
                    'reason': (reason or '').strip(),
                    'replaced_whitelist_ids': replaced_whitelist_ids,
                },
            )
            session.flush()
            return self._quota_profile_payload(session, user_id, current_day)

        return self._with_seeded_policies('set_feature_quota_whitelist', operation)

    def revoke_whitelist_entry(
        self,
        user_id: int,
        *,
        feature_code: Optional[str],
        actor_user_id: Optional[int],
    ) -> dict:
        self._validate_user_id(user_id)
        normalized_feature_code = self._definition(feature_code).code if feature_code is not None else None
        current_day = self._today()

        def operation(session: Session) -> dict:
            self._require_user(session, user_id)
            query = session.query(FeatureQuotaWhitelistRecord).filter(
                FeatureQuotaWhitelistRecord.user_id == user_id,
                FeatureQuotaWhitelistRecord.revoked_at.is_(None),
            )
            query = query.filter(
                FeatureQuotaWhitelistRecord.feature_code.is_(None)
                if normalized_feature_code is None
                else FeatureQuotaWhitelistRecord.feature_code == normalized_feature_code
            )
            rows = query.all()
            if not rows:
                raise LookupError('用户当前没有该白名单规则')
            now = local_naive_now()
            for row in rows:
                row.revoked_at = now
            scope = normalized_feature_code or '*'
            self._audit(
                session,
                action='feature_quota.whitelist_revoked',
                target_type='feature_quota_whitelist',
                target_id=f'{user_id}:{scope}',
                actor_user_id=actor_user_id,
                metadata={'feature_code': normalized_feature_code},
            )
            return self._quota_profile_payload(session, user_id, current_day)

        return self._with_seeded_policies('revoke_feature_quota_whitelist', operation)

    def _quota_profile_payload(self, session: Session, user_id: int, period_start: date) -> dict:
        # 管理写操作会在返回 profile 前撤销旧规则；显式 flush 保证
        # 后续查询读取的是本次事务的当前状态，而非数据库中的旧记录。
        session.flush()
        assignment_and_plan = self._active_plan_assignment(session, user_id, period_start)
        assignment_payload = None
        if assignment_and_plan is not None:
            assignment, plan = assignment_and_plan
            assignment_payload = {
                'plan_code': plan.code,
                'plan_name': plan.name,
                'effective_from': self._date_payload(assignment.effective_from),
                'effective_until': self._date_payload(assignment.effective_until),
            }
        plan_assignments = (
            session.query(FeatureQuotaUserPlanAssignmentRecord, FeatureQuotaPlanRecord)
            .join(FeatureQuotaPlanRecord, FeatureQuotaPlanRecord.id == FeatureQuotaUserPlanAssignmentRecord.plan_id)
            .filter(
                FeatureQuotaUserPlanAssignmentRecord.user_id == user_id,
                FeatureQuotaUserPlanAssignmentRecord.revoked_at.is_(None),
            )
            .order_by(
                FeatureQuotaUserPlanAssignmentRecord.effective_from.asc(),
                FeatureQuotaUserPlanAssignmentRecord.id.asc(),
            )
            .all()
        )
        plan_assignment_payloads = [
            {
                'plan_code': plan.code,
                'plan_name': plan.name,
                'effective_from': self._date_payload(assignment.effective_from),
                'effective_until': self._date_payload(assignment.effective_until),
            }
            for assignment, plan in plan_assignments
        ]
        overrides = session.query(FeatureQuotaUserOverrideRecord).filter(
            FeatureQuotaUserOverrideRecord.user_id == user_id,
            FeatureQuotaUserOverrideRecord.revoked_at.is_(None),
        ).order_by(FeatureQuotaUserOverrideRecord.feature_code.asc()).all()
        whitelists = session.query(FeatureQuotaWhitelistRecord).filter(
            FeatureQuotaWhitelistRecord.user_id == user_id,
            FeatureQuotaWhitelistRecord.revoked_at.is_(None),
        ).order_by(FeatureQuotaWhitelistRecord.feature_code.asc()).all()
        return {
            'user_id': user_id,
            'plan_assignment': assignment_payload,
            'plan_assignments': plan_assignment_payloads,
            'overrides': [
                {
                    'feature_code': item.feature_code,
                    'daily_limit': item.daily_limit,
                    'effective_from': self._date_payload(item.effective_from),
                    'effective_until': self._date_payload(item.effective_until),
                    'reason': item.reason,
                }
                for item in overrides
            ],
            'whitelist_entries': [
                {
                    'feature_code': item.feature_code,
                    'effective_from': self._date_payload(item.effective_from),
                    'effective_until': self._date_payload(item.effective_until),
                    'reason': item.reason,
                }
                for item in whitelists
            ],
            'entitlements': [item.as_dict() for item in self._entitlements_for_user_in_session(session, user_id, period_start)],
        }

    def get_user_quota_profile(
        self,
        user_id: int,
        *,
        period_start: Optional[date] = None,
    ) -> dict:
        self._validate_user_id(user_id)
        current_day = period_start or self._today()

        def operation(session: Session) -> dict:
            self._require_user(session, user_id)
            return self._quota_profile_payload(session, user_id, current_day)

        return self._with_seeded_policies('get_feature_quota_user_profile', operation)

    def get_entitlements_for_user(
        self,
        user_id: int,
        *,
        period_start: Optional[date] = None,
    ) -> List[FeatureQuotaEntitlement]:
        self._validate_user_id(user_id)
        current_day = period_start or self._today()

        def operation(session: Session) -> List[FeatureQuotaEntitlement]:
            return self._entitlements_for_user_in_session(session, user_id, current_day)

        return self._with_seeded_policies('get_feature_quota_entitlements', operation)

    def reserve_for_user(
        self,
        user_id: int,
        feature_code: str,
        *,
        period_start: Optional[date] = None,
    ) -> FeatureQuotaEntitlement:
        """Reserve one accepted execution for a trusted user."""
        return self.reserve_many_for_user(
            user_id,
            feature_code,
            amount=1,
            period_start=period_start,
        )

    def reserve_many_for_user(
        self,
        user_id: int,
        feature_code: str,
        *,
        amount: int,
        period_start: Optional[date] = None,
    ) -> FeatureQuotaEntitlement:
        """Atomically reserve multiple accepted executions of one feature."""
        definition = self._definition(feature_code)
        self._validate_user_id(user_id)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError('amount 必须是正整数')
        current_day = period_start or self._today()

        def operation(session: Session) -> FeatureQuotaEntitlement:
            global_limits = self._global_limits(session)
            rule = self._resolve_effective_rule(
                session,
                user_id=user_id,
                definition=definition,
                period_start=current_day,
                global_limits=global_limits,
            )
            usage = (
                session.query(FeatureQuotaUsageRecord)
                .filter(
                    FeatureQuotaUsageRecord.user_id == user_id,
                    FeatureQuotaUsageRecord.feature_code == definition.code,
                    FeatureQuotaUsageRecord.period_start == current_day,
                )
                .one_or_none()
            )
            used_count = usage.used_count if usage is not None else 0
            current = self._entitlement(
                definition,
                resolved_rule=rule,
                used_count=used_count,
                period_start=current_day,
            )
            if current.disabled or current.remaining is not None and current.remaining < amount:
                raise FeatureQuotaExceededError(current)
            if current.unlimited:
                return current

            used_count += amount
            if usage is None:
                session.add(FeatureQuotaUsageRecord(
                    user_id=user_id,
                    feature_code=definition.code,
                    period_start=current_day,
                    used_count=used_count,
                ))
            else:
                usage.used_count = used_count
                usage.updated_at = local_naive_now()
            return self._entitlement(
                definition,
                resolved_rule=rule,
                used_count=used_count,
                period_start=current_day,
            )

        return self._with_seeded_policies(f'reserve_feature_quota:{definition.code}', operation)

    def release_for_user(
        self,
        user_id: int,
        feature_code: str,
        *,
        period_start: Optional[date] = None,
    ) -> FeatureQuotaEntitlement:
        """Release one same-day reservation after executor admission failed."""
        return self.release_many_for_user(
            user_id,
            feature_code,
            amount=1,
            period_start=period_start,
        )

    def release_many_for_user(
        self,
        user_id: int,
        feature_code: str,
        *,
        amount: int,
        period_start: Optional[date] = None,
    ) -> FeatureQuotaEntitlement:
        """Compensate a queue-admission reservation in its original UTC period."""
        definition = self._definition(feature_code)
        self._validate_user_id(user_id)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError('amount 必须是正整数')
        current_day = period_start or self._today()

        def operation(session: Session) -> FeatureQuotaEntitlement:
            global_limits = self._global_limits(session)
            rule = self._resolve_effective_rule(
                session,
                user_id=user_id,
                definition=definition,
                period_start=current_day,
                global_limits=global_limits,
            )
            usage = (
                session.query(FeatureQuotaUsageRecord)
                .filter(
                    FeatureQuotaUsageRecord.user_id == user_id,
                    FeatureQuotaUsageRecord.feature_code == definition.code,
                    FeatureQuotaUsageRecord.period_start == current_day,
                )
                .one_or_none()
            )
            used_count = usage.used_count if usage is not None else 0
            released_count = max(used_count - amount, 0)
            if usage is not None and released_count != used_count:
                usage.used_count = released_count
                usage.updated_at = local_naive_now()
            return self._entitlement(
                definition,
                resolved_rule=rule,
                used_count=released_count,
                period_start=current_day,
            )

        return self._with_seeded_policies(f'release_feature_quota:{definition.code}', operation)

    def get_entitlements_for_request(self, request: Request) -> List[FeatureQuotaEntitlement]:
        principal = getattr(request.state, 'miniapp_principal', None)
        if principal is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='微信登录已失效，请重新登录')
        return self.get_entitlements_for_user(principal.user.id)

    def reserve_for_request(
        self,
        request: Optional[Request],
        feature_code: str,
        *,
        period_start: Optional[date] = None,
    ) -> Optional[FeatureQuotaEntitlement]:
        """Reserve only after endpoint-specific validation has accepted the request."""
        return self.reserve_many_for_request(
            request,
            feature_code,
            amount=1,
            period_start=period_start,
        )

    def reserve_many_for_request(
        self,
        request: Optional[Request],
        feature_code: str,
        *,
        amount: int,
        period_start: Optional[date] = None,
    ) -> Optional[FeatureQuotaEntitlement]:
        """Reserve several queue-admitted tasks for a trusted request atomically."""
        if request is None:
            return None
        principal = getattr(request.state, 'miniapp_principal', None)
        if principal is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='微信登录已失效，请重新登录')
        try:
            return self.reserve_many_for_user(
                principal.user.id,
                feature_code,
                amount=amount,
                period_start=period_start,
            )
        except FeatureQuotaExceededError as exc:
            entitlement = exc.entitlement
            if entitlement.disabled:
                message = f'{entitlement.name} 当前已被管理员禁用'
                reason = 'feature_disabled'
            else:
                message = f'{entitlement.name} 今日剩余 {entitlement.remaining or 0} 次，本次请求需要 {amount} 次'
                reason = 'daily_limit_exceeded'
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    'error': 'feature_quota_exceeded',
                    'reason': reason,
                    'message': message,
                    **entitlement.as_dict(),
                },
            ) from exc

    def release_for_request(
        self,
        request: Optional[Request],
        feature_code: str,
        *,
        period_start: Optional[date] = None,
    ) -> Optional[FeatureQuotaEntitlement]:
        """Release one reservation when submission fails before worker admission."""
        return self.release_many_for_request(
            request,
            feature_code,
            amount=1,
            period_start=period_start,
        )

    def release_many_for_request(
        self,
        request: Optional[Request],
        feature_code: str,
        *,
        amount: int,
        period_start: Optional[date] = None,
    ) -> Optional[FeatureQuotaEntitlement]:
        """Compensate a trusted request's reservation in its original UTC period."""
        if request is None:
            return None
        principal = getattr(request.state, 'miniapp_principal', None)
        if principal is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='微信登录已失效，请重新登录')
        return self.release_many_for_user(
            principal.user.id,
            feature_code,
            amount=amount,
            period_start=period_start,
        )
