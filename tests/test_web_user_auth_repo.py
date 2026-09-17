"""Web OAuth transaction repository 的原子消费回归测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.config import Config
from src.repositories.web_user_auth_repo import WebUserAuthRepository
from src.storage import DatabaseManager, local_naive_now


@pytest.fixture()
def auth_repository(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "web_user_auth_repo.db"))
    Config.reset_instance()
    DatabaseManager.reset_instance()
    try:
        yield WebUserAuthRepository(DatabaseManager.get_instance())
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()


def test_wechat_login_transaction_is_consumed_by_single_conditional_update(auth_repository):
    now = local_naive_now()
    auth_repository.create_wechat_login_transaction(
        state_hash="state-hash",
        browser_binding_hash="binding-hash",
        redirect_uri="https://web.example.test/api/v1/web-auth/wechat/callback",
        expires_at=now + timedelta(minutes=5),
    )

    first = auth_repository.consume_wechat_login_transaction(
        state_hash="state-hash",
        browser_binding_hash="binding-hash",
        now=now,
    )
    replay = auth_repository.consume_wechat_login_transaction(
        state_hash="state-hash",
        browser_binding_hash="binding-hash",
        now=now,
    )

    assert first == "https://web.example.test/api/v1/web-auth/wechat/callback"
    assert replay is None
