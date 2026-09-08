# -*- coding: utf-8 -*-
"""Tests for LLM usage dashboard API."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import get_database_manager
from src.config import Config
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.rbac_service import RbacService
from src.services.wechat_miniapp_auth_service import MiniappPrincipal
from src.storage import DatabaseManager


class FakeUsageDbManager:
    def get_llm_usage_summary(self, from_dt, to_dt):
        return {
            "total_calls": 2,
            "total_prompt_tokens": 30,
            "total_completion_tokens": 70,
            "total_tokens": 100,
            "by_call_type": [
                {
                    "call_type": "analysis",
                    "calls": 2,
                    "prompt_tokens": 30,
                    "completion_tokens": 70,
                    "total_tokens": 100,
                }
            ],
            "by_model": [
                {
                    "model": "openai/gpt-test",
                    "calls": 2,
                    "prompt_tokens": 30,
                    "completion_tokens": 70,
                    "total_tokens": 100,
                    "max_total_tokens": 60,
                }
            ],
        }

    def get_llm_usage_records(self, from_dt, to_dt, limit=50):
        return [
            {
                "id": 7,
                "called_at": datetime(2026, 6, 11, 9, 30, 0),
                "call_type": "analysis",
                "model": "openai/gpt-test",
                "stock_code": "600519",
                "provider": "openai",
                "language": "zh",
                "market_group": "cn",
                "analysis_mode": "stock_analysis",
                "legacy_prompt_mode": "skill_aware",
                "skill_config_hmac": "a" * 64,
                "transport": "litellm",
                "message_count": 2,
                "estimated_total_prompt_tokens": 2000,
                "approx_common_prefix_chars": 120,
                "approx_common_prefix_tokens": 40,
                "known_dynamic_marker_positions": '[{"marker_name":"stock_code","message_role":"user","char_offset":12}]',
                "prompt_tokens": 10,
                "completion_tokens": 50,
                "total_tokens": 60,
            }
        ]


class UsageDashboardApiTestCase(unittest.TestCase):
    """Usage dashboard stays protected by the canonical Bearer + RBAC boundary."""

    _ENV_KEYS = ("DATABASE_PATH",)

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "usage-api.db"
        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        self.database_manager = DatabaseManager.get_instance()
        self.user = MiniappUserRepository(self.database_manager).upsert_user(
            openid="usage-api-user",
            issuer="usage-api-test",
        )
        rbac = RbacService()
        rbac.repository.ensure_role(self.user.id, "member")
        member_access = rbac.resolve(self.user.id)
        rbac.repository.ensure_role(self.user.id, "operator")
        operator_access = rbac.resolve(self.user.id)
        self.principals = {
            "usage-test-token": MiniappPrincipal(
                user=self.user,
                token_hash="usage-test-token-hash",
                roles=tuple(operator_access["roles"]),
                permissions=tuple(operator_access["permissions"]),
            ),
            "member-token": MiniappPrincipal(
                user=self.user,
                token_hash="member-token-hash",
                roles=tuple(member_access["roles"]),
                permissions=tuple(member_access["permissions"]),
            ),
        }
        self.auth_patch = patch(
            "src.services.wechat_miniapp_auth_service.WechatMiniappAuthService.authenticate_token",
            side_effect=lambda token: self.principals.get(token),
        )
        self.auth_patch.start()

        self.app = create_app(static_dir=Path(self.temp_dir.name))
        self.app.dependency_overrides[get_database_manager] = lambda: FakeUsageDbManager()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.auth_patch.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    @staticmethod
    def _bearer_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_dashboard_rejects_anonymous_request(self):
        response = self.client.get("/api/v1/usage/dashboard?period=today&limit=10")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "unauthorized")

    def test_dashboard_rejects_member_without_usage_read_permission(self):
        response = self.client.get(
            "/api/v1/usage/dashboard?period=today&limit=10",
            headers=self._bearer_headers("member-token"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["required_permission"], "usage.read")

    def test_dashboard_returns_token_summary_and_recent_calls(self):
        response = self.client.get(
            "/api/v1/usage/dashboard?period=today&limit=10",
            headers=self._bearer_headers("usage-test-token"),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["period"], "today")
        self.assertEqual(body["total_tokens"], 100)
        self.assertEqual(body["by_model"][0]["model"], "openai/gpt-test")
        self.assertEqual(body["by_model"][0]["max_total_tokens"], 60)
        self.assertNotIn("provider", body["by_model"][0])
        self.assertNotIn("context_window", body["by_model"][0])
        self.assertNotIn("context_usage_ratio", body["by_model"][0])
        self.assertEqual(body["recent_calls"][0]["stock_code"], "600519")
        p05a_internal_fields = {
            "provider",
            "language",
            "market_group",
            "analysis_mode",
            "legacy_prompt_mode",
            "skill_config_hmac",
            "transport",
            "message_count",
            "estimated_total_prompt_tokens",
            "approx_common_prefix_chars",
            "approx_common_prefix_tokens",
            "known_dynamic_marker_positions",
        }
        self.assertTrue(p05a_internal_fields.isdisjoint(body["recent_calls"][0]))
        self.assertNotIn("context_window", body["recent_calls"][0])
        self.assertNotIn("context_usage_ratio", body["recent_calls"][0])


if __name__ == "__main__":
    unittest.main()
