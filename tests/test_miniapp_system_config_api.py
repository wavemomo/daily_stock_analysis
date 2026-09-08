# -*- coding: utf-8 -*-
"""Regression tests for the restricted miniapp system configuration surface."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from src.config import Config
from src.storage import DatabaseManager


@pytest.fixture()
def client_and_principals(tmp_path):
    old_env_file = os.environ.get("ENV_FILE")
    old_database_path = os.environ.get("DATABASE_PATH")
    env_path = tmp_path / ".env"
    db_path = tmp_path / "miniapp_system_config_api.db"
    static_dir = tmp_path / "empty-static"
    static_dir.mkdir()
    env_path.write_text(
        "\n".join(
            [
                "STOCK_LIST=600519",
                "GEMINI_API_KEY=miniapp-test-secret",
                "LOG_LEVEL=INFO",
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

    admin = SimpleNamespace(
        user=SimpleNamespace(id=101),
        roles=("admin",),
        permissions=("system.read", "system.manage"),
    )
    member = SimpleNamespace(
        user=SimpleNamespace(id=102),
        roles=("member",),
        permissions=("portfolio.read",),
    )
    miniapp_auth_patch = patch(
        "api.middlewares.auth.WechatMiniappAuthService.authenticate_token",
        return_value=admin,
    )
    auth_mock = miniapp_auth_patch.start()
    app = create_app(static_dir=Path(static_dir))
    client = TestClient(app)
    try:
        yield client, admin, member, auth_mock
    finally:
        miniapp_auth_patch.stop()
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


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer miniapp-system-config-test-token"}


def test_admin_bearer_can_read_masked_miniapp_system_config(client_and_principals) -> None:
    client, _admin, _member, _auth_patch = client_and_principals

    response = client.get("/api/v1/miniapp/system/config", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    item = next(item for item in payload["items"] if item["key"] == "GEMINI_API_KEY")
    assert item["value"] == payload["mask_token"]
    assert item["is_masked"] is True
    assert item["raw_value_exists"] is True


def test_admin_bearer_can_validate_and_update_miniapp_system_config(client_and_principals) -> None:
    client, _admin, _member, _auth_patch = client_and_principals
    config_response = client.get("/api/v1/miniapp/system/config", headers=_headers())
    config_version = config_response.json()["config_version"]

    validate_response = client.post(
        "/api/v1/miniapp/system/config/validate",
        headers=_headers(),
        json={"items": [{"key": "LOG_LEVEL", "value": "DEBUG"}]},
    )
    update_response = client.put(
        "/api/v1/miniapp/system/config",
        headers=_headers(),
        json={
            "config_version": config_version,
            "mask_token": config_response.json()["mask_token"],
            "reload_now": False,
            "items": [{"key": "LOG_LEVEL", "value": "DEBUG"}],
        },
    )

    assert validate_response.status_code == 200
    assert validate_response.json()["valid"] is True
    assert update_response.status_code == 200
    assert update_response.json()["success"] is True
    assert update_response.json()["updated_keys"] == ["LOG_LEVEL"]


def test_miniapp_system_config_requires_read_and_manage_permissions(client_and_principals) -> None:
    client, _admin, member, auth_mock = client_and_principals
    auth_mock.return_value = member

    read_response = client.get("/api/v1/miniapp/system/config", headers=_headers())
    manage_response = client.post(
        "/api/v1/miniapp/system/config/validate",
        headers=_headers(),
        json={"items": [{"key": "LOG_LEVEL", "value": "DEBUG"}]},
    )

    assert read_response.status_code == 403
    assert read_response.json()["error"] == "forbidden"
    assert read_response.json()["message"] == "Permission denied: system.read"
    assert read_response.json()["required_permission"] == "system.read"
    assert manage_response.status_code == 403
    assert manage_response.json()["error"] == "forbidden"
    assert manage_response.json()["message"] == "Permission denied: system.manage"
    assert manage_response.json()["required_permission"] == "system.manage"


def test_standard_system_config_uses_bearer_principal_permissions(client_and_principals) -> None:
    client, _admin, _member, _auth_patch = client_and_principals

    response = client.get("/api/v1/system/config", headers=_headers())

    assert response.status_code == 200
    assert response.json()["config_version"]


def test_miniapp_system_router_excludes_high_risk_operations(client_and_principals) -> None:
    client, _admin, _member, _auth_patch = client_and_principals
    paths = set(client.app.openapi()["paths"])

    forbidden_paths = {
        "/api/v1/miniapp/system/config/export",
        "/api/v1/miniapp/system/config/import",
        "/api/v1/miniapp/system/config/llm/test-channel",
        "/api/v1/miniapp/system/config/llm/discover-models",
        "/api/v1/miniapp/system/config/notification/test-channel",
        "/api/v1/miniapp/system/scheduler/run-now",
    }
    assert paths.isdisjoint(forbidden_paths)
