"""告警附带的分析上下文必须按规则 owner 隔离。

回归点：`AlertWorker._recent_history_pack_overview` 曾不带 owner 读取 analysis_history，
并且可见性缓存只按股票代码为键，导致 A 用户的告警里会附上 B 用户对同一标的的私有
分析快照。这里验证：按 owner 过滤 + 缓存键含 owner。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.analysis_ownership import AnalysisOwner
from src.config import Config
from src.services.alert_service import AlertService
from src.services.alert_worker import AlertWorker
from src.storage import AnalysisHistory, DatabaseManager, MiniappUserRecord

_OVERVIEW_KEY = "analysis_context_pack_overview"


def _snapshot(marker: str) -> str:
    """构造带可识别标记的 context_snapshot（含合法的 pack overview 结构）。"""
    return json.dumps({
        _OVERVIEW_KEY: {
            # marker 放在 subject.stock_name 上，用于断言"看到的是谁的快照"。
            "subject": {"code": "600519", "stock_name": marker, "market": "cn"},
            "blocks": [{"key": "quote", "label": "行情", "status": "available"}],
        }
    })


@pytest.fixture()
def alert_env():
    temp_dir = tempfile.TemporaryDirectory()
    root = Path(temp_dir.name)
    env_path = root / ".env"
    db_path = root / "alert_isolation.db"
    env_path.write_text(
        "\n".join([
            "STOCK_LIST=600519",
            "GEMINI_API_KEY=test",
            "ADMIN_AUTH_ENABLED=false",
            f"DATABASE_PATH={db_path}",
        ]) + "\n",
        encoding="utf-8",
    )
    os.environ["ENV_FILE"] = str(env_path)
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    service = AlertService()
    try:
        yield service
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        temp_dir.cleanup()


def _create_user(service: AlertService, openid: str) -> int:
    with service.repo.db.get_session() as session:
        user = MiniappUserRecord(openid=openid)
        session.add(user)
        session.commit()
        session.refresh(user)
        return int(user.id)


def _runtime_rule(owner_id: int | None, target: str = "600519") -> SimpleNamespace:
    rule = SimpleNamespace(target_scope="single_symbol", target=target, metadata={})
    return SimpleNamespace(rule=rule, owner_id=owner_id, effective_target=target)


def test_recent_history_overview_is_scoped_to_rule_owner(alert_env):
    service = alert_env
    user_a = _create_user(service, "alert-iso-a")
    user_b = _create_user(service, "alert-iso-b")

    with service.repo.db.get_session() as session:
        session.add(AnalysisHistory(
            code="600519", owner_scope="user", owner_user_id=user_a,
            context_snapshot=_snapshot("owner-a"),
        ))
        session.add(AnalysisHistory(
            code="600519", owner_scope="user", owner_user_id=user_b,
            context_snapshot=_snapshot("owner-b"),
        ))
        session.add(AnalysisHistory(
            code="600519", owner_scope="global", owner_user_id=None,
            context_snapshot=_snapshot("owner-global"),
        ))
        session.commit()

    worker = AlertWorker(
        config_provider=lambda: SimpleNamespace(
            agent_event_monitor_enabled=True, agent_event_alert_rules_json=""
        ),
        service=service,
        notifier=SimpleNamespace(),
    )
    worker._analysis_visibility_cache = {}

    overview_a = worker._recent_history_pack_overview(_runtime_rule(user_a))
    overview_b = worker._recent_history_pack_overview(_runtime_rule(user_b))
    overview_global = worker._recent_history_pack_overview(_runtime_rule(None))

    # 每个 owner 只看到自己的分析快照，缓存不会跨 owner 复用。
    assert overview_a["subject"]["stock_name"] == "owner-a"
    assert overview_b["subject"]["stock_name"] == "owner-b"
    assert overview_global["subject"]["stock_name"] == "owner-global"

    # 缓存键必须包含 owner，否则第二个 owner 会命中第一个 owner 的缓存。
    assert set(worker._analysis_visibility_cache) == {
        f"user:{user_a}|600519",
        f"user:{user_b}|600519",
        "global|600519",
    }


def test_user_rule_does_not_see_other_user_analysis_when_own_history_missing(alert_env):
    """本人无分析历史时返回空，绝不回退到他人或全局快照。"""
    service = alert_env
    user_a = _create_user(service, "alert-iso-only-a")
    user_b = _create_user(service, "alert-iso-only-b")

    with service.repo.db.get_session() as session:
        session.add(AnalysisHistory(
            code="600519", owner_scope="user", owner_user_id=user_b,
            context_snapshot=_snapshot("owner-b"),
        ))
        session.add(AnalysisHistory(
            code="600519", owner_scope="global", owner_user_id=None,
            context_snapshot=_snapshot("owner-global"),
        ))
        session.commit()

    worker = AlertWorker(
        config_provider=lambda: SimpleNamespace(
            agent_event_monitor_enabled=True, agent_event_alert_rules_json=""
        ),
        service=service,
        notifier=SimpleNamespace(),
    )
    worker._analysis_visibility_cache = {}

    assert worker._recent_history_pack_overview(_runtime_rule(user_a)) is None
    assert AnalysisOwner.user(user_a).key == f"user:{user_a}"
