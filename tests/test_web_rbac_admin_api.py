"""Unified Web Cookie and miniapp Bearer RBAC regression tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
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
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.rbac_service import RbacService
from src.services.web_user_auth_service import WEB_USER_COOKIE_NAME, WebUserAuthService
from src.storage import DatabaseManager

TRUSTED_ORIGIN = "http://localhost:5173"


class UnifiedWebRbacApiTestCase(unittest.TestCase):
    """Exercise Web Cookie and miniapp Bearer management boundaries."""

    _ENV_KEYS = (
        "DATABASE_PATH",
        "STOCK_LIST",
        "GEMINI_API_KEY",
        "WEB_USER_SESSION_TTL_SECONDS",
    )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "unified_web_rbac_api.db"
        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ.update({
            "DATABASE_PATH": str(self.db_path),
            "STOCK_LIST": "600519",
            "GEMINI_API_KEY": "test",
            "WEB_USER_SESSION_TTL_SECONDS": "3600",
        })
        Config.reset_instance()
        DatabaseManager.reset_instance()

        self.db = DatabaseManager.get_instance()
        self.user_repo = MiniappUserRepository(self.db)
        self.rbac = RbacService()
        self.miniapp_admin = self.user_repo.upsert_user(
            openid="miniapp-rbac-admin", issuer="web-rbac-api-test"
        )
        self.target = self.user_repo.upsert_user(
            openid="miniapp-rbac-target", issuer="web-rbac-api-test"
        )
        self.rbac.repository.ensure_role(self.miniapp_admin.id, "admin")
        self.rbac.repository.ensure_role(self.target.id, "member")
        self.principals = {
            "miniapp-admin-token": self._principal_for(self.miniapp_admin),
            "miniapp-target-token": self._principal_for(self.target),
        }
        self.miniapp_auth_patch = patch(
            "src.services.wechat_miniapp_auth_service.WechatMiniappAuthService.authenticate_token",
            side_effect=lambda token: self.principals.get(token),
        )
        self.miniapp_auth_patch.start()

        self.static_dir = self.data_dir / "static"
        self.static_dir.mkdir()
        self.app = create_app(static_dir=self.static_dir)
        self.browser = TestClient(self.app)

    def tearDown(self) -> None:
        self.miniapp_auth_patch.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def _principal_for(self, user) -> SimpleNamespace:
        access = self.rbac.resolve(user.id)
        return SimpleNamespace(
            user=user,
            roles=tuple(access["roles"]),
            permissions=tuple(access["permissions"]),
        )

    @staticmethod
    def _bearer_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _role_payload() -> dict[str, object]:
        return {
            "code": "rbac_manager",
            "name": "权限经理",
            "description": "唯一权限维护者",
            "permissions": ["rbac.manage"],
        }

    def _establish_web_session(self, *, openid: str = "web-rbac-user") -> tuple[dict, str]:
        """直接为 canonical user 铸造独立浏览器会话并加载到测试客户端。"""
        user = self.user_repo.upsert_user(openid=openid, issuer="web-rbac-api-test")
        issue = WebUserAuthService().create_session_for_user(
            user_id=user.id, assign_default_role=True
        )
        self.assertIsNotNone(issue)
        self.browser.cookies.set(WEB_USER_COOKIE_NAME, issue.session_value)
        session = self.browser.get("/api/v1/web-auth/me")
        self.assertEqual(session.status_code, 200, session.text)
        return session.json()["user"], session.json()["csrf_token"]

    @staticmethod
    def _web_write_headers(csrf_token: str) -> dict[str, str]:
        return {"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token}

    def _grant_web_management_access(self, web_user_id: int) -> None:
        self.rbac.repository.ensure_role(web_user_id, "admin")

    def test_web_auth_routes_expose_password_login_and_no_wechat_oauth(self) -> None:
        paths = set(self.app.openapi()["paths"])
        self.assertNotIn("/api/v1/web-auth/wechat/start", paths)
        self.assertNotIn("/api/v1/web-auth/wechat/callback", paths)
        self.assertIn("/api/v1/web-auth/password/login", paths)
        self.assertIn("/api/v1/web-auth/me", paths)
        self.assertIn("/api/v1/web-auth/logout", paths)
        self.assertNotIn("/api/v1/auth/login", paths)
        self.assertNotIn("/api/v1/auth/status", paths)
        self.assertNotIn("/api/v1/auth/logout", paths)
        self.assertNotIn("/api/v1/admin/rbac/catalog", paths)
        self.assertIn("/api/v1/rbac/catalog", paths)
        self.assertIn("/api/v1/miniapp/rbac/catalog", paths)

    def test_web_oauth_and_miniapp_bearer_are_isolated_and_enforce_rbac(self) -> None:
        anonymous = TestClient(self.app)
        self.assertEqual(anonymous.get("/api/v1/rbac/catalog").status_code, 401)
        self.assertEqual(
            anonymous.get(
                "/api/v1/rbac/catalog",
                headers=self._bearer_headers("miniapp-admin-token"),
            ).status_code,
            200,
        )

        web_user, csrf_token = self._establish_web_session()
        denied = self.browser.get("/api/v1/rbac/catalog")
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["required_permission"], "rbac.manage")

        self._grant_web_management_access(web_user["id"])
        catalog = self.browser.get("/api/v1/rbac/catalog")
        self.assertEqual(catalog.status_code, 200, catalog.text)
        self.assertIn("permissions", catalog.json())
        self.assertIn("roles", catalog.json())
        self.assertTrue(csrf_token)

        conflict = self.browser.get(
            "/api/v1/rbac/catalog",
            headers=self._bearer_headers("miniapp-admin-token"),
        )
        self.assertEqual(conflict.status_code, 400, conflict.text)
        self.assertEqual(conflict.json()["error"], "authentication_conflict")
        miniapp_conflict = self.browser.get("/api/v1/miniapp/rbac/catalog")
        self.assertEqual(miniapp_conflict.status_code, 400, miniapp_conflict.text)
        self.assertEqual(miniapp_conflict.json()["error"], "authentication_conflict")

    def test_web_rbac_writes_require_csrf_and_audit_with_canonical_actor(self) -> None:
        web_user, csrf_token = self._establish_web_session()
        self._grant_web_management_access(web_user["id"])
        payload = self._role_payload()

        for headers in ({}, {"X-CSRF-Token": csrf_token}, {"Origin": "https://untrusted.example", "X-CSRF-Token": csrf_token}):
            rejected = self.browser.post("/api/v1/rbac/roles", headers=headers, json=payload)
            self.assertEqual(rejected.status_code, 403, rejected.text)
            self.assertEqual(rejected.json()["error"], "csrf_failed")

        headers = self._web_write_headers(csrf_token)
        created = self.browser.post("/api/v1/rbac/roles", headers=headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        assigned = self.browser.put(
            f"/api/v1/rbac/users/{self.target.id}/roles",
            headers=headers,
            json={"roles": ["rbac_manager"]},
        )
        self.assertEqual(assigned.status_code, 200, assigned.text)
        self.assertEqual(assigned.json()["permissions"], ["rbac.manage"])

        audit = self.browser.get("/api/v1/rbac/audit", params={"page_size": 100})
        self.assertEqual(audit.status_code, 200, audit.text)
        events = audit.json()["items"]
        for action, target_id in (("role.created", "rbac_manager"), ("user.roles_replaced", str(self.target.id))):
            event = next(item for item in events if item["action"] == action and item["target_id"] == target_id)
            self.assertEqual(event["actor_user_id"], web_user["id"])
            self.assertNotIn("actor_kind", event["metadata"])

    def test_quota_management_uses_canonical_web_actor_and_matches_bearer_route(self) -> None:
        web_user, csrf_token = self._establish_web_session()
        self._grant_web_management_access(web_user["id"])
        headers = self._web_write_headers(csrf_token)

        listed = self.browser.get("/api/v1/rbac/feature-quotas")
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(
            TestClient(self.app).get(
                "/api/v1/miniapp/rbac/feature-quotas",
                headers=self._bearer_headers("miniapp-admin-token"),
            ).status_code,
            200,
        )

        updated = self.browser.put(
            "/api/v1/rbac/feature-quotas/stock_analysis",
            headers=headers,
            json={"daily_limit": 1},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["updated_by_user_id"], web_user["id"])

        audit = self.browser.get("/api/v1/rbac/audit", params={"page_size": 100})
        self.assertEqual(audit.status_code, 200, audit.text)
        event = next(
            item
            for item in audit.json()["items"]
            if item["action"] == "feature_quota.policy_updated" and item["target_id"] == "stock_analysis"
        )
        self.assertEqual(event["actor_user_id"], web_user["id"])
        self.assertNotIn("actor_kind", event["metadata"])

    def test_rbac_manage_does_not_bypass_portfolio_owner_scope(self) -> None:
        target_account = TestClient(self.app).post(
            "/api/v1/portfolio/accounts",
            headers=self._bearer_headers("miniapp-target-token"),
            json={
                "name": "Target account",
                "broker": "Demo",
                "market": "cn",
                "base_currency": "CNY",
                "owner_id": "forged-owner",
            },
        )
        self.assertEqual(target_account.status_code, 200, target_account.text)
        account_id = target_account.json()["id"]

        web_user, csrf_token = self._establish_web_session(openid="web-owner-scope")
        self._grant_web_management_access(web_user["id"])
        own_accounts = self.browser.get("/api/v1/portfolio/accounts")
        self.assertEqual(own_accounts.status_code, 200, own_accounts.text)
        self.assertNotIn(account_id, [item["id"] for item in own_accounts.json()["accounts"]])

        cross_owner_update = self.browser.put(
            f"/api/v1/portfolio/accounts/{account_id}",
            headers=self._web_write_headers(csrf_token),
            json={"name": "stolen"},
        )
        self.assertEqual(cross_owner_update.status_code, 404, cross_owner_update.text)


if __name__ == "__main__":
    unittest.main()
