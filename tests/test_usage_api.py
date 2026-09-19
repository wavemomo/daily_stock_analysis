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
    def __init__(self):
        # 记录端点传入的归属参数，用于断言平台级与本人视图的区分。
        self.summary_calls = []
        self.record_calls = []
        self.by_owner_calls = []

    def get_llm_usage_by_owner(self, from_dt, to_dt, *, limit=100):
        self.by_owner_calls.append({"limit": limit})
        return [
            {
                "user_id": 42,
                "nickname": "Alice",
                "owner_scope": "user",
                "calls": 3,
                "prompt_tokens": 30,
                "completion_tokens": 70,
                "total_tokens": 100,
                "last_called_at": datetime(2026, 6, 11, 9, 30, 0),
            },
            {
                "user_id": None,
                "nickname": None,
                "owner_scope": "global",
                "calls": 1,
                "prompt_tokens": 5,
                "completion_tokens": 5,
                "total_tokens": 10,
                "last_called_at": None,
            },
        ]

    def get_llm_usage_summary(self, from_dt, to_dt, *, owner=None, include_all_owners=False):
        self.summary_calls.append({"owner": owner, "include_all_owners": include_all_owners})
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

    def get_llm_usage_records(self, from_dt, to_dt, limit=50, *, owner=None, include_all_owners=False):
        self.record_calls.append({"owner": owner, "include_all_owners": include_all_owners})
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
        self.fake_db = FakeUsageDbManager()
        self.app.dependency_overrides[get_database_manager] = lambda: self.fake_db
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

    # ------------------------------------------------------------------
    # 多用户隔离：平台级用量 vs 本人用量
    # ------------------------------------------------------------------

    def test_platform_dashboard_aggregates_all_owners(self):
        """管理员/运营的平台视图必须显式跨全体用户聚合。"""
        response = self.client.get(
            "/api/v1/usage/dashboard?period=today&limit=10",
            headers=self._bearer_headers("usage-test-token"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.fake_db.summary_calls)
        self.assertTrue(all(call["include_all_owners"] for call in self.fake_db.summary_calls))
        self.assertTrue(all(call["include_all_owners"] for call in self.fake_db.record_calls))
        self.assertTrue(all(call["owner"] is None for call in self.fake_db.summary_calls))

    def test_member_can_read_own_usage_scoped_to_self(self):
        """普通成员可读本人用量，且查询必须按其 owner 收敛、不得跨用户聚合。"""
        response = self.client.get(
            "/api/v1/usage/me/dashboard?period=today&limit=10",
            headers=self._bearer_headers("member-token"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.fake_db.summary_calls)
        for call in self.fake_db.summary_calls + self.fake_db.record_calls:
            self.assertFalse(call["include_all_owners"])
            self.assertIsNotNone(call["owner"])
            self.assertEqual(call["owner"].scope, "user")
            self.assertEqual(call["owner"].user_id, self.user.id)

    def test_member_can_read_own_usage_summary(self):
        response = self.client.get(
            "/api/v1/usage/me/summary?period=month",
            headers=self._bearer_headers("member-token"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_tokens"], 100)
        self.assertEqual(self.fake_db.summary_calls[0]["owner"].user_id, self.user.id)

    def test_my_usage_rejects_anonymous_request(self):
        response = self.client.get("/api/v1/usage/me/summary")

        self.assertEqual(response.status_code, 401)

    def test_platform_views_report_platform_scope(self):
        """平台级响应必须自报 scope=platform，前端据此标注当前视图。"""
        for path in ("/api/v1/usage/summary", "/api/v1/usage/dashboard"):
            response = self.client.get(
                f"{path}?period=today",
                headers=self._bearer_headers("usage-test-token"),
            )

            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.json()["scope"], "platform", path)

    def test_self_views_report_self_scope(self):
        """本人视图必须自报 scope=self，避免与全平台聚合混淆。"""
        for path in ("/api/v1/usage/me/summary", "/api/v1/usage/me/dashboard"):
            response = self.client.get(
                f"{path}?period=today",
                headers=self._bearer_headers("member-token"),
            )

            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.json()["scope"], "self", path)

    def test_by_user_lists_each_owner_for_operator(self):
        """管理员可按用户下钻；非用户归属的消耗合并为一条平台条目。"""
        response = self.client.get(
            "/api/v1/usage/by-user?period=month&limit=50",
            headers=self._bearer_headers("usage-test-token"),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["scope"], "platform")
        self.assertEqual(self.fake_db.by_owner_calls, [{"limit": 50}])
        owners = payload["owners"]
        self.assertEqual(len(owners), 2)
        self.assertEqual(owners[0]["user_id"], 42)
        self.assertEqual(owners[0]["nickname"], "Alice")
        self.assertEqual(owners[0]["owner_scope"], "user")
        self.assertEqual(owners[0]["total_tokens"], 100)
        self.assertEqual(owners[0]["last_called_at"], "2026-06-11T09:30:00")
        self.assertIsNone(owners[1]["user_id"])
        self.assertEqual(owners[1]["owner_scope"], "global")
        self.assertIsNone(owners[1]["last_called_at"])

    def test_by_user_rejects_member_without_usage_read(self):
        """按用户下钻是平台级能力，普通成员不得读取他人用量。"""
        response = self.client.get(
            "/api/v1/usage/by-user",
            headers=self._bearer_headers("member-token"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.fake_db.by_owner_calls, [])

    def test_by_user_rejects_anonymous_request(self):
        response = self.client.get("/api/v1/usage/by-user")

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
