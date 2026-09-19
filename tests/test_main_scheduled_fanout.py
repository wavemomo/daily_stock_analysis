"""定时分析去重扇出 + scheduled_analysis 功能额度分配的单元测试。

覆盖：
- `_allocate_scheduled_pools`：跨用户同股去重、按用户额度截断、unlimited 全给、
  disabled/0 跳过，且真实预留额度。
- `_release_unanalyzed_scheduled_quota`：未成功分析的股票按 owner 退还额度。
- `_run_per_user_scheduled_analysis`：编排（大盘复盘一次 + 去重扇出一次 + 失败退还 +
  回测一次），通过 stub `run_full_analysis` 观察传入的 `fanout_owners_by_code`。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import main
from data_provider.base import normalize_stock_code
from src.config import Config
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.feature_quota_service import FeatureQuotaService
from src.storage import DatabaseManager

_FEATURE = 'scheduled_analysis'


@pytest.fixture()
def quota_db(tmp_path, monkeypatch):
    """隔离的真实数据库；scheduled_analysis 策略默认存在（服务按需 seed）。"""
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'scheduled_fanout.db'))
    monkeypatch.setenv('ADMIN_AUTH_ENABLED', 'false')
    monkeypatch.setenv('STOCK_LIST', '600519')
    monkeypatch.setenv('GEMINI_API_KEY', 'test')
    Config.reset_instance()
    DatabaseManager.reset_instance()
    database = DatabaseManager.get_instance()
    try:
        yield database
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def _make_user(database, openid: str) -> int:
    return MiniappUserRepository(database).upsert_user(
        openid=openid, issuer='scheduled-fanout-test'
    ).id


def _pool(user_id: int, codes):
    return {
        'user_id': user_id,
        'codes': [{'stock_code': c, 'stock_name': ''} for c in codes],
    }


def _remaining(service: FeatureQuotaService, user_id: int):
    for item in service.get_entitlements_for_user(user_id):
        if item.feature_code == _FEATURE:
            return item
    raise AssertionError('scheduled_analysis entitlement missing')


def test_allocate_dedups_shared_code_and_respects_quota(quota_db):
    service = FeatureQuotaService(quota_db)
    # 全局默认额度收紧到 2，用于验证按额度截断。
    service.update_policy(_FEATURE, 2, actor_user_id=None)

    capped_user = _make_user(quota_db, 'user-capped')
    unlimited_user = _make_user(quota_db, 'user-unlimited')
    disabled_user = _make_user(quota_db, 'user-disabled')

    service.set_whitelist_entry(
        unlimited_user, feature_code=_FEATURE, actor_user_id=None
    )
    service.set_user_override(
        disabled_user, _FEATURE, daily_limit=0, actor_user_id=None
    )

    pools = [
        # 3 只，额度 2 → 仅前 2 只（按自选顺序）。含与 unlimited_user 共享的 600519。
        _pool(capped_user, ['600519', 'hk00700', '000001']),
        # unlimited → 全给；与 capped_user 共享 600519。
        _pool(unlimited_user, ['600519', 'AAPL']),
        # disabled(0) → 跳过。
        _pool(disabled_user, ['000002']),
    ]

    fanout, granted, _period = main._allocate_scheduled_pools(pools)

    key_600519 = normalize_stock_code('600519')
    key_hk = normalize_stock_code('hk00700')
    key_aapl = normalize_stock_code('AAPL')

    # capped_user 只拿到前 2 只（600519 + hk00700），000001 被额度截断。
    assert granted[capped_user] == [key_600519, key_hk]
    # unlimited_user 拿到全部。
    assert set(granted[unlimited_user]) == {key_600519, key_aapl}
    # disabled_user 完全跳过。
    assert disabled_user not in granted

    # 跨用户同股去重：600519 只有一个分析键，owner 含两位用户。
    assert set(o.user_id for o in fanout[key_600519]) == {capped_user, unlimited_user}
    assert [o.user_id for o in fanout[key_hk]] == [capped_user]
    assert [o.user_id for o in fanout[key_aapl]] == [unlimited_user]
    # 000001 未获配，不出现在扇出集合。
    assert normalize_stock_code('000001') not in fanout

    # 额度已真实预留：capped_user 用掉 2（剩 0）；unlimited_user 不计数。
    assert _remaining(service, capped_user).remaining == 0
    assert _remaining(service, unlimited_user).unlimited is True


def test_release_refunds_only_unanalyzed_codes(quota_db):
    service = FeatureQuotaService(quota_db)
    service.update_policy(_FEATURE, 5, actor_user_id=None)
    user_id = _make_user(quota_db, 'user-release')

    pools = [_pool(user_id, ['600519', 'hk00700', '000001'])]
    _fanout, granted, period = main._allocate_scheduled_pools(pools)
    assert len(granted[user_id]) == 3
    assert _remaining(service, user_id).used_count == 3

    # 仅 600519 成功分析，其余两只（休市/失败）应退还。
    analyzed = {normalize_stock_code('600519')}
    main._release_unanalyzed_scheduled_quota(granted, analyzed, period)

    refreshed = _remaining(service, user_id)
    assert refreshed.used_count == 1
    assert refreshed.remaining == 4


def test_run_per_user_scheduled_analysis_orchestrates_dedup_fanout(quota_db, monkeypatch):
    service = FeatureQuotaService(quota_db)
    service.update_policy(_FEATURE, 10, actor_user_id=None)
    user_a = _make_user(quota_db, 'user-a')
    user_b = _make_user(quota_db, 'user-b')

    pools = [
        _pool(user_a, ['600519', 'hk00700']),
        _pool(user_b, ['600519', 'AAPL']),  # 与 user_a 共享 600519
    ]
    monkeypatch.setattr(
        'src.services.miniapp_watchlist_service.MiniappWatchlistService.iter_scheduled_pools',
        lambda self: pools,
    )

    calls = []

    def fake_run_full_analysis(config, args, stock_codes=None, **kwargs):
        calls.append({'stock_codes': stock_codes, 'kwargs': kwargs})
        fanout = kwargs.get('fanout_owners_by_code')
        analyzed = kwargs.get('analyzed_input_codes')
        if fanout is not None and analyzed is not None:
            # 模拟 AAPL 分析失败，其余成功。
            for code in fanout:
                if code != normalize_stock_code('AAPL'):
                    analyzed.add(code)
        return True

    monkeypatch.setattr(main, 'run_full_analysis', fake_run_full_analysis)

    backtest_calls = []
    monkeypatch.setattr(main, '_run_auto_backtest', lambda config: backtest_calls.append(1))

    config = SimpleNamespace(market_review_enabled=False, backtest_enabled=False)
    args = SimpleNamespace(no_market_review=False)

    result = main._run_per_user_scheduled_analysis(config, args)
    assert result is True

    # 市场复盘关闭 → 仅一次扇出 run_full_analysis。
    fanout_calls = [c for c in calls if c['kwargs'].get('fanout_owners_by_code') is not None]
    assert len(fanout_calls) == 1
    fanout_call = fanout_calls[0]

    key_600519 = normalize_stock_code('600519')
    key_aapl = normalize_stock_code('AAPL')
    key_hk = normalize_stock_code('hk00700')

    # 去重：600519 只提交一次，owner 含两位用户。
    submitted = set(fanout_call['stock_codes'])
    assert submitted == {key_600519, key_hk, key_aapl}
    fanout_map = fanout_call['kwargs']['fanout_owners_by_code']
    assert set(o.user_id for o in fanout_map[key_600519]) == {user_a, user_b}
    assert fanout_call['kwargs']['_per_user_child'] is True

    # AAPL 失败 → 只有 user_b 退还 1；user_a 两只均成功不退。
    assert _remaining(service, user_a).used_count == 2
    assert _remaining(service, user_b).used_count == 1  # 2 预留 - 1 退还

    # 回测全局只跑一次。
    assert backtest_calls == [1]
