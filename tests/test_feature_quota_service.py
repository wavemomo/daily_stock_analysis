"""每日高成本功能配额的真实 SQLite 回归测试。"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.config import Config
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.feature_quota_service import (
    FEATURE_QUOTA_BY_CODE,
    FeatureQuotaExceededError,
    FeatureQuotaService,
)
from src.storage import DatabaseManager, RbacAuditEventRecord


@pytest.fixture()
def quota_context(tmp_path, monkeypatch):
    """Provide an isolated real database and an authenticated miniapp user."""
    database_path = tmp_path / 'feature_quota_service.db'
    monkeypatch.setenv('DATABASE_PATH', str(database_path))
    monkeypatch.setenv('ADMIN_AUTH_ENABLED', 'false')
    monkeypatch.setenv('STOCK_LIST', '600519')
    monkeypatch.setenv('GEMINI_API_KEY', 'test')
    Config.reset_instance()
    DatabaseManager.reset_instance()
    database = DatabaseManager.get_instance()
    user = MiniappUserRepository(database).upsert_user(
        openid='feature-quota-user', issuer='feature-quota-test'
    )
    try:
        yield database, user
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _entitlement(service: FeatureQuotaService, user_id: int, feature_code: str, day: date):
    return next(
        item
        for item in service.get_entitlements_for_user(user_id, period_start=day)
        if item.feature_code == feature_code
    )


def test_policy_seed_update_and_audit_are_persisted(quota_context):
    database, user = quota_context
    service = FeatureQuotaService(database, today_provider=lambda: date(2026, 9, 6))

    policies = service.list_policies()

    assert {policy['feature_code'] for policy in policies} == set(FEATURE_QUOTA_BY_CODE)
    assert next(policy for policy in policies if policy['feature_code'] == 'stock_analysis')['daily_limit'] == 5

    updated = service.update_policy('stock_analysis', 2, actor_user_id=user.id)

    assert updated['daily_limit'] == 2
    assert updated['updated_by_user_id'] == user.id
    with database.get_session() as session:
        audit_event = (
            session.query(RbacAuditEventRecord)
            .filter(
                RbacAuditEventRecord.action == 'feature_quota.policy_updated',
                RbacAuditEventRecord.target_id == 'stock_analysis',
            )
            .one()
        )
        assert audit_event.actor_user_id == user.id
        assert json.loads(audit_event.metadata_json) == {
            'after_daily_limit': 2,
            'before_daily_limit': 5,
        }


def test_batch_reservation_is_atomic_and_usage_resets_each_day(quota_context):
    database, user = quota_context
    service = FeatureQuotaService(database, today_provider=lambda: date(2026, 9, 6))
    first_day = date(2026, 9, 6)
    second_day = date(2026, 9, 7)
    service.update_policy('screening', 3, actor_user_id=None)

    accepted = service.reserve_many_for_user(
        user.id,
        'screening',
        amount=2,
        period_start=first_day,
    )

    assert accepted.used_count == 2
    assert accepted.remaining == 1
    with pytest.raises(FeatureQuotaExceededError):
        service.reserve_many_for_user(
            user.id,
            'screening',
            amount=2,
            period_start=first_day,
        )

    first_day_entitlement = _entitlement(service, user.id, 'screening', first_day)
    second_day_entitlement = _entitlement(service, user.id, 'screening', second_day)
    assert (first_day_entitlement.used_count, first_day_entitlement.remaining) == (2, 1)
    assert (second_day_entitlement.used_count, second_day_entitlement.remaining) == (0, 3)


def test_all_canonical_users_consume_quota_and_request_adapter_returns_429_when_disabled(quota_context):
    database, user = quota_context
    service = FeatureQuotaService(database, today_provider=lambda: date(2026, 9, 6))
    day = date(2026, 9, 6)

    entitlement = service.reserve_for_user(user.id, 'stock_analysis', period_start=day)
    assert entitlement.unlimited is False
    assert (entitlement.used_count, entitlement.remaining) == (1, 4)

    service.update_policy('screening', 0, actor_user_id=None)
    request = SimpleNamespace(
        state=SimpleNamespace(
            auth_kind=None,
            miniapp_principal=SimpleNamespace(user=user, roles=()),
        )
    )
    with pytest.raises(HTTPException) as exc_info:
        service.reserve_many_for_request(request, 'screening', amount=1)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail['error'] == 'feature_quota_exceeded'
    assert exc_info.value.detail['reason'] == 'feature_disabled'
    assert exc_info.value.detail['disabled'] is True


def test_request_adapter_requires_principal_and_rbac_admin_uses_own_quota(quota_context):
    database, user = quota_context
    day = date(2026, 9, 6)
    service = FeatureQuotaService(database, today_provider=lambda: day)
    service.update_policy('stock_analysis', 1, actor_user_id=None)

    unauthenticated_request = SimpleNamespace(
        state=SimpleNamespace(auth_kind='admin', miniapp_principal=None)
    )
    with pytest.raises(HTTPException) as unauthenticated_exc:
        service.reserve_many_for_request(
            unauthenticated_request,
            'stock_analysis',
            amount=1,
            period_start=day,
        )
    assert unauthenticated_exc.value.status_code == 401

    rbac_admin_request = SimpleNamespace(
        state=SimpleNamespace(
            auth_kind='web_user',
            miniapp_principal=SimpleNamespace(user=user, roles=('admin',)),
        )
    )
    reserved = service.reserve_many_for_request(
        rbac_admin_request,
        'stock_analysis',
        amount=1,
        period_start=day,
    )
    assert reserved is not None
    assert reserved.unlimited is False
    assert (reserved.used_count, reserved.remaining) == (1, 0)

    released = service.release_many_for_request(
        rbac_admin_request,
        'stock_analysis',
        amount=1,
        period_start=day,
    )
    assert released is not None
    assert (released.used_count, released.remaining) == (0, 1)

    service.reserve_many_for_request(
        rbac_admin_request,
        'stock_analysis',
        amount=1,
        period_start=day,
    )
    with pytest.raises(HTTPException) as exc_info:
        service.reserve_many_for_request(
            rbac_admin_request,
            'stock_analysis',
            amount=1,
            period_start=day,
        )
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail['reason'] == 'daily_limit_exceeded'


def test_release_compensates_same_day_reservation_without_underflow_and_uses_utc_reset(quota_context):
    database, user = quota_context
    day = date(2026, 9, 6)
    service = FeatureQuotaService(database, today_provider=lambda: day)
    service.update_policy('stock_analysis', 3, actor_user_id=None)

    reserved = service.reserve_many_for_user(user.id, 'stock_analysis', amount=2)
    assert (reserved.used_count, reserved.remaining, reserved.reset_at) == (
        2,
        1,
        '2026-09-07T00:00:00Z',
    )

    released = service.release_many_for_user(user.id, 'stock_analysis', amount=2)
    assert (released.used_count, released.remaining, released.reset_at) == (
        0,
        3,
        '2026-09-07T00:00:00Z',
    )

    # Compensation is defensive: duplicate failure callbacks cannot create
    # negative persisted usage for any canonical user.
    repeated_release = service.release_many_for_user(user.id, 'stock_analysis', amount=2)
    assert (repeated_release.used_count, repeated_release.remaining) == (0, 3)
    assert _entitlement(service, user.id, 'stock_analysis', day).used_count == 0


def test_request_compensation_releases_the_captured_utc_period(quota_context):
    database, user = quota_context
    first_day = date(2026, 9, 6)
    second_day = date(2026, 9, 7)
    current_day = [first_day]
    service = FeatureQuotaService(database, today_provider=lambda: current_day[0])
    request = SimpleNamespace(
        state=SimpleNamespace(
            auth_kind=None,
            miniapp_principal=SimpleNamespace(user=user, roles=()),
        )
    )

    reservation_period_start = service.current_period_start()
    service.reserve_many_for_request(
        request,
        'stock_analysis',
        amount=1,
        period_start=reservation_period_start,
    )
    current_day[0] = second_day
    service.reserve_many_for_request(request, 'stock_analysis', amount=1)

    released = service.release_many_for_request(
        request,
        'stock_analysis',
        amount=1,
        period_start=reservation_period_start,
    )

    assert released.reset_at == '2026-09-07T00:00:00Z'
    assert _entitlement(service, user.id, 'stock_analysis', first_day).used_count == 0
    assert _entitlement(service, user.id, 'stock_analysis', second_day).used_count == 1


def test_rule_priority_and_profile_updates_follow_one_resolution_path(quota_context):
    database, user = quota_context
    day = date(2026, 9, 6)
    service = FeatureQuotaService(database, today_provider=lambda: day)
    service.update_policy('stock_analysis', 1, actor_user_id=None)
    service.create_plan(
        code='pro_plan',
        name='专业版',
        description='测试套餐',
        limits={'stock_analysis': 2},
        actor_user_id=user.id,
    )

    assigned = service.assign_plan(user.id, plan_code='pro_plan', actor_user_id=user.id)
    assert assigned['plan_assignment']['plan_code'] == 'pro_plan'
    assert _entitlement(service, user.id, 'stock_analysis', day).limit_source == 'plan'
    assert _entitlement(service, user.id, 'stock_analysis', day).daily_limit == 2

    service.set_whitelist_entry(user.id, feature_code=None, actor_user_id=user.id)
    assert _entitlement(service, user.id, 'stock_analysis', day).limit_source == 'whitelist_all'
    assert _entitlement(service, user.id, 'stock_analysis', day).unlimited is True

    service.set_whitelist_entry(user.id, feature_code='stock_analysis', actor_user_id=user.id)
    assert _entitlement(service, user.id, 'stock_analysis', day).limit_source == 'whitelist_feature'

    profile = service.set_user_override(
        user.id,
        'stock_analysis',
        daily_limit=0,
        reason='人工停用',
        actor_user_id=user.id,
    )
    assert profile['overrides'][0]['daily_limit'] == 0
    current = _entitlement(service, user.id, 'stock_analysis', day)
    assert (current.limit_source, current.disabled, current.remaining) == ('user_override', True, 0)
    with pytest.raises(FeatureQuotaExceededError):
        service.reserve_for_user(user.id, 'stock_analysis', period_start=day)

    profile = service.revoke_user_override(user.id, 'stock_analysis', actor_user_id=user.id)
    assert profile['overrides'] == []
    assert _entitlement(service, user.id, 'stock_analysis', day).limit_source == 'whitelist_feature'

    service.revoke_whitelist_entry(user.id, feature_code='stock_analysis', actor_user_id=user.id)
    assert _entitlement(service, user.id, 'stock_analysis', day).limit_source == 'whitelist_all'
    service.revoke_whitelist_entry(user.id, feature_code=None, actor_user_id=user.id)
    assert _entitlement(service, user.id, 'stock_analysis', day).limit_source == 'plan'

    profile = service.assign_plan(user.id, plan_code=None, actor_user_id=user.id)
    assert profile['plan_assignment'] is None
    assert _entitlement(service, user.id, 'stock_analysis', day).limit_source == 'global_policy'

    with database.get_session() as session:
        actions = {
            action for (action,) in session.query(RbacAuditEventRecord.action).filter(
                RbacAuditEventRecord.action.like('feature_quota.%')
            )
        }
    assert {
        'feature_quota.plan_created',
        'feature_quota.plan_assigned',
        'feature_quota.user_override_set',
        'feature_quota.user_override_revoked',
        'feature_quota.whitelist_granted',
        'feature_quota.whitelist_revoked',
        'feature_quota.plan_unassigned',
    } <= actions


def test_plan_windows_fallback_and_429_reason_include_effective_source(quota_context):
    database, user = quota_context
    day = date(2026, 9, 6)
    service = FeatureQuotaService(database, today_provider=lambda: day)
    service.update_policy('stock_analysis', 1, actor_user_id=None)
    service.create_plan(
        code='timed_plan',
        name='限时套餐',
        description='',
        limits={'stock_analysis': 3},
        actor_user_id=None,
    )
    service.assign_plan(
        user.id,
        plan_code='timed_plan',
        effective_from=date(2026, 9, 7),
        effective_until=date(2026, 9, 7),
        actor_user_id=None,
    )

    assert _entitlement(service, user.id, 'stock_analysis', day).limit_source == 'global_policy'
    active = _entitlement(service, user.id, 'stock_analysis', date(2026, 9, 7))
    assert (active.limit_source, active.daily_limit, active.effective_until) == ('plan', 3, '2026-09-07')
    assert _entitlement(service, user.id, 'stock_analysis', date(2026, 9, 8)).limit_source == 'global_policy'

    request = SimpleNamespace(
        state=SimpleNamespace(
            auth_kind=None,
            miniapp_principal=SimpleNamespace(user=user, roles=()),
        )
    )
    service.reserve_for_user(user.id, 'stock_analysis', period_start=day)
    with pytest.raises(HTTPException) as exc_info:
        service.reserve_many_for_request(request, 'stock_analysis', amount=1, period_start=day)

    detail = exc_info.value.detail
    assert exc_info.value.status_code == 429
    assert detail['reason'] == 'daily_limit_exceeded'
    assert detail['limit_source'] == 'global_policy'
    assert detail['remaining'] == 0


def test_future_schedule_preserves_current_rules_until_the_effective_day(quota_context):
    database, user = quota_context
    today = date(2026, 9, 6)
    switch_day = date(2026, 9, 7)
    service = FeatureQuotaService(database, today_provider=lambda: today)
    service.update_policy('stock_analysis', 1, actor_user_id=None)
    service.create_plan(
        code='basic_plan',
        name='基础套餐',
        description='',
        limits={'stock_analysis': 2},
        actor_user_id=None,
    )
    service.create_plan(
        code='pro_plan',
        name='专业套餐',
        description='',
        limits={'stock_analysis': 4},
        actor_user_id=None,
    )
    service.assign_plan(user.id, plan_code='basic_plan', actor_user_id=None)
    plan_profile = service.assign_plan(
        user.id,
        plan_code='pro_plan',
        effective_from=switch_day,
        actor_user_id=None,
    )
    assert [item['plan_code'] for item in plan_profile['plan_assignments']] == ['basic_plan', 'pro_plan']
    assert _entitlement(service, user.id, 'stock_analysis', today).daily_limit == 2
    assert _entitlement(service, user.id, 'stock_analysis', switch_day).daily_limit == 4

    service.set_user_override(user.id, 'stock_analysis', daily_limit=3, actor_user_id=None)
    service.set_user_override(
        user.id,
        'stock_analysis',
        daily_limit=5,
        effective_from=switch_day,
        actor_user_id=None,
    )
    assert _entitlement(service, user.id, 'stock_analysis', today).daily_limit == 3
    assert _entitlement(service, user.id, 'stock_analysis', switch_day).daily_limit == 5

    whitelist_user = MiniappUserRepository(database).upsert_user(
        openid='feature-quota-whitelist-user', issuer='feature-quota-test'
    )
    service.set_whitelist_entry(whitelist_user.id, feature_code='stock_analysis', actor_user_id=None)
    service.set_whitelist_entry(
        whitelist_user.id,
        feature_code='stock_analysis',
        effective_from=switch_day,
        actor_user_id=None,
    )
    assert _entitlement(service, whitelist_user.id, 'stock_analysis', today).effective_until == '2026-09-06'
    assert _entitlement(service, whitelist_user.id, 'stock_analysis', switch_day).limit_source == 'whitelist_feature'


def test_future_plan_cancellation_preserves_current_entitlement_until_switch_day(quota_context):
    database, user = quota_context
    today = date(2026, 9, 6)
    switch_day = date(2026, 9, 7)
    service = FeatureQuotaService(database, today_provider=lambda: today)
    service.update_policy('stock_analysis', 1, actor_user_id=None)
    service.create_plan(
        code='basic_plan',
        name='基础套餐',
        description='',
        limits={'stock_analysis': 2},
        actor_user_id=None,
    )
    service.assign_plan(user.id, plan_code='basic_plan', actor_user_id=None)

    profile = service.assign_plan(
        user.id,
        plan_code=None,
        effective_from=switch_day,
        actor_user_id=None,
    )

    assert profile['plan_assignment'] is None
    assert profile['plan_assignments'] == [
        {
            'plan_code': 'basic_plan',
            'plan_name': '基础套餐',
            'effective_from': '2026-09-06',
            'effective_until': '2026-09-06',
        }
    ]
    assert (
        _entitlement(service, user.id, 'stock_analysis', today).limit_source,
        _entitlement(service, user.id, 'stock_analysis', today).plan_code,
        _entitlement(service, user.id, 'stock_analysis', today).daily_limit,
    ) == ('plan', 'basic_plan', 2)
    assert (
        _entitlement(service, user.id, 'stock_analysis', switch_day).limit_source,
        _entitlement(service, user.id, 'stock_analysis', switch_day).plan_code,
        _entitlement(service, user.id, 'stock_analysis', switch_day).daily_limit,
    ) == ('global_policy', None, 1)


def test_future_plan_cancellation_rejects_conflicting_plan_schedule(quota_context):
    database, user = quota_context
    today = date(2026, 9, 6)
    switch_day = date(2026, 9, 7)
    service = FeatureQuotaService(database, today_provider=lambda: today)
    service.create_plan(
        code='basic_plan',
        name='基础套餐',
        description='',
        limits={'stock_analysis': 2},
        actor_user_id=None,
    )
    service.create_plan(
        code='pro_plan',
        name='专业套餐',
        description='',
        limits={'stock_analysis': 4},
        actor_user_id=None,
    )
    service.assign_plan(user.id, plan_code='basic_plan', actor_user_id=None)
    service.assign_plan(
        user.id,
        plan_code='pro_plan',
        effective_from=switch_day,
        actor_user_id=None,
    )

    with pytest.raises(ValueError, match='重叠的未来排期'):
        service.assign_plan(
            user.id,
            plan_code=None,
            effective_from=switch_day,
            actor_user_id=None,
        )

    assert _entitlement(service, user.id, 'stock_analysis', today).daily_limit == 2
    assert _entitlement(service, user.id, 'stock_analysis', switch_day).daily_limit == 4
    profile = service.get_user_quota_profile(user.id, period_start=today)
    assert profile['plan_assignments'] == [
        {
            'plan_code': 'basic_plan',
            'plan_name': '基础套餐',
            'effective_from': '2026-09-06',
            'effective_until': '2026-09-06',
        },
        {
            'plan_code': 'pro_plan',
            'plan_name': '专业套餐',
            'effective_from': '2026-09-07',
            'effective_until': None,
        },
    ]


def test_future_schedule_rejects_ambiguous_overlaps_and_finite_replacement(quota_context):
    database, user = quota_context
    today = date(2026, 9, 6)
    switch_day = date(2026, 9, 7)
    service = FeatureQuotaService(database, today_provider=lambda: today)
    service.set_user_override(
        user.id,
        'stock_analysis',
        daily_limit=3,
        effective_from=switch_day,
        actor_user_id=None,
    )
    with pytest.raises(ValueError, match='重叠的未来排期'):
        service.set_user_override(
            user.id,
            'stock_analysis',
            daily_limit=4,
            effective_from=switch_day,
            actor_user_id=None,
        )

    second_user = MiniappUserRepository(database).upsert_user(
        openid='feature-quota-finite-window-user', issuer='feature-quota-test'
    )
    service.set_user_override(second_user.id, 'stock_analysis', daily_limit=3, actor_user_id=None)
    with pytest.raises(ValueError, match='当前不支持自动恢复'):
        service.set_user_override(
            second_user.id,
            'stock_analysis',
            daily_limit=4,
            effective_from=switch_day,
            effective_until=switch_day,
            actor_user_id=None,
        )


def test_inactive_plan_is_persisted_but_cannot_be_assigned(quota_context):
    database, user = quota_context
    service = FeatureQuotaService(database, today_provider=lambda: date(2026, 9, 6))

    plan = service.create_plan(
        code='inactive_plan',
        name='已停用套餐',
        description='',
        limits={'stock_analysis': 2},
        is_active=False,
        actor_user_id=None,
    )

    assert plan['is_active'] is False
    assert next(item for item in service.list_plans() if item['code'] == 'inactive_plan')['is_active'] is False
    with pytest.raises(ValueError, match='已停用'):
        service.assign_plan(user.id, plan_code='inactive_plan', actor_user_id=None)
