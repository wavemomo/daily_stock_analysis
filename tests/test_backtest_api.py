# -*- coding: utf-8 -*-
"""Regression coverage for backtest API request validation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from api.app import create_app
from src.config import Config
from src.storage import DatabaseManager


def test_invalid_backtest_code_does_not_reserve_quota(tmp_path) -> None:
    old_env_file = os.environ.get("ENV_FILE")
    old_database_path = os.environ.get("DATABASE_PATH")
    env_path = tmp_path / ".env"
    db_path = tmp_path / "backtest_api.db"
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    env_path.write_text(
        "\n".join(
            [
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=test",
                f"DATABASE_PATH={db_path}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.environ["ENV_FILE"] = str(env_path)
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()

    try:
        principal = SimpleNamespace(
            user=SimpleNamespace(id=1),
            roles=("admin",),
            permissions=("backtest.execute",),
        )
        with patch(
            "api.middlewares.auth.WechatMiniappAuthService.authenticate_token",
            return_value=principal,
        ):
            client = TestClient(
                create_app(static_dir=Path(static_dir)),
                headers={"Authorization": "Bearer backtest-test-token"},
            )
            with patch(
                "api.v1.endpoints.backtest.FeatureQuotaService.reserve_for_request"
            ) as reserve_for_request:
                response = client.post("/api/v1/backtest/run", json={"code": "not-a-stock-code"})

        assert response.status_code == 400, response.text
        assert response.json()["error"] == "invalid_params"
        reserve_for_request.assert_not_called()
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old_env_file is None:
            os.environ.pop("ENV_FILE", None)
        else:
            os.environ["ENV_FILE"] = old_env_file
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path
