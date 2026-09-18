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


class MiniappWebLoginApiTestCase(unittest.TestCase):
    """覆盖 Web 独立 Cookie 会话、小程序 Bearer 边界与显式身份确认。"""

    _ENV_KEYS = (
        "DATABASE_PATH",
        "STOCK_LIST",
        "GEMINI_API_KEY",
        "WECHAT_OPEN_WEB_STATE_TTL_SECONDS",
        "WEB_USER_SESSION_TTL_SECONDS",
    )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "miniapp_web_login.db"
        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ.update({
            "DATABASE_PATH": str(self.db_path),
            "STOCK_LIST": "600519",
            "GEMINI_API_KEY": "test",
            "WECHAT_OPEN_WEB_STATE_TTL_SECONDS": "300",
            "WEB_USER_SESSION_TTL_SECONDS": "3600",
        })
        Config.reset_instance()
        DatabaseManager.reset_instance()

        self.db = DatabaseManager.get_instance()
        self.user_repo = MiniappUserRepository(self.db)
        self.rbac = RbacService()
        self.other_user = self.user_repo.upsert_user(
            openid="existing-miniapp-user",
            issuer="miniapp-web-login-test",
        )
        self.rbac.repository.ensure_role(self.other_user.id, "member")
        self.principals: dict[str, SimpleNamespace] = {
            "limited-token": SimpleNamespace(
                user=self.other_user,
                roles=("member",),
                permissions=(),
            ),
            "other-user-token": SimpleNamespace(
                user=self.other_user,
                roles=("member",),
                permissions=("account.self",),
            ),
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

    @staticmethod
    def _member_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _web_user_and_csrf(self) -> tuple[dict, str]:
        response = self.browser.get("/api/v1/web-auth/me")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("csrf_token", payload)
        return payload["user"], payload["csrf_token"]

    def _establish_web_session(self, *, openid: str = "web-session-user") -> tuple[dict, str]:
        """直接为 canonical user 铸造独立浏览器会话并加载到测试客户端。"""
        user = self.user_repo.upsert_user(openid=openid, issuer="miniapp-web-login-test")
        issue = WebUserAuthService().create_session_for_user(
            user_id=user.id, assign_default_role=True
        )
        self.assertIsNotNone(issue)
        self.browser.cookies.set(WEB_USER_COOKIE_NAME, issue.session_value)
        return self._web_user_and_csrf()

    def test_web_session_exposes_current_user_via_me_without_leaking_identity(self) -> None:
        self._establish_web_session(openid="me-user")
        user, csrf_token = self._web_user_and_csrf()
        self.assertIsInstance(user["id"], int)
        self.assertTrue(csrf_token)
        self.assertNotIn("openid", user)
        self.assertNotIn("unionid", user)
        self.assertEqual(self.browser.get("/api/v1/miniapp/auth/me").status_code, 400)

    def test_miniapp_login_rejects_web_cookie_and_bearer_before_token_issuance(self) -> None:
        self._establish_web_session(openid="web-user-for-login-conflict")

        with patch(
            "src.services.wechat_miniapp_auth_service.WechatMiniappAuthService.login"
        ) as issue_token:
            response = self.browser.post(
                "/api/v1/miniapp/auth/login",
                headers=self._member_headers("other-user-token"),
                json={"code": "miniapp-code"},
            )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"], "authentication_conflict")
        issue_token.assert_not_called()

    def test_miniapp_identity_requires_issuer_and_is_scoped_by_issuer(self) -> None:
        with self.assertRaisesRegex(ValueError, "issuer 不能为空"):
            self.user_repo.upsert_user(openid="shared-openid", issuer="")

        first = self.user_repo.upsert_user(
            openid="shared-openid", issuer="miniapp-a"
        )
        repeated = self.user_repo.upsert_user(
            openid="shared-openid", issuer="miniapp-a"
        )
        second = self.user_repo.upsert_user(
            openid="shared-openid", issuer="miniapp-b"
        )

        self.assertEqual(first.id, repeated.id)
        self.assertNotEqual(first.id, second.id)

    def test_neutral_account_and_reflection_routes_reuse_backend_for_web_cookie(self) -> None:
        """中性前缀 /account、/daily-reflections 复用同一处理逻辑，接受 Web Cookie（写操作需 CSRF）。"""
        _, csrf_token = self._establish_web_session(openid="neutral-web-user")

        me = self.browser.get("/api/v1/account/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(self.browser.get("/api/v1/account/email").status_code, 200)
        self.assertEqual(self.browser.get("/api/v1/daily-reflections").status_code, 200)
        self.assertEqual(self.browser.get("/api/v1/daily-reflections/stats").status_code, 200)

        no_csrf = self.browser.put(
            "/api/v1/daily-reflections",
            json={"reflection_date": "2026-01-01", "content": "hello"},
        )
        self.assertEqual(no_csrf.status_code, 403, no_csrf.text)
        self.assertEqual(no_csrf.json()["error"], "csrf_failed")

        saved = self.browser.put(
            "/api/v1/daily-reflections",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"reflection_date": "2026-01-01", "title": "day1", "content": "hello"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["content"], "hello")

    def test_same_origin_cookie_write_passes_csrf_without_cors_allowlist(self) -> None:
        """同源写请求（Origin host == 请求 Host）无需 CORS_ORIGINS 白名单即可通过 CSRF。"""
        _, csrf_token = self._establish_web_session(openid="same-origin-user")
        saved = self.browser.patch(
            "/api/v1/account/me",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_token},
            json={"nickname": "renamed"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)

    def test_cross_origin_cookie_write_still_rejected(self) -> None:
        """跨源写请求即便带有效 CSRF token 仍被拒绝。"""
        _, csrf_token = self._establish_web_session(openid="cross-origin-user")
        rejected = self.browser.patch(
            "/api/v1/account/me",
            headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf_token},
            json={"nickname": "renamed"},
        )
        self.assertEqual(rejected.status_code, 403, rejected.text)
        self.assertEqual(rejected.json()["error"], "csrf_failed")

    def test_neutral_account_route_accepts_miniapp_bearer(self) -> None:
        """中性前缀同样接受小程序 Bearer（同一处理逻辑对两端复用）。"""
        response = TestClient(self.app).get(
            "/api/v1/account/me",
            headers=self._member_headers("other-user-token"),
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_neutral_account_route_still_enforces_central_rbac(self) -> None:
        """中性前缀不绕过 RBAC：无 account.self 的 Bearer 被拒。"""
        limited = TestClient(self.app).get(
            "/api/v1/account/me",
            headers=self._member_headers("limited-token"),
        )
        self.assertEqual(limited.status_code, 403, limited.text)
        self.assertEqual(limited.json()["required_permission"], "account.self")

    def test_miniapp_account_route_cannot_bypass_central_rbac(self) -> None:
        limited = TestClient(self.app).get(
            "/api/v1/miniapp/auth/me",
            headers=self._member_headers("limited-token"),
        )

        self.assertEqual(limited.status_code, 403, limited.text)
        self.assertEqual(limited.json()["error"], "forbidden")
        self.assertEqual(limited.json()["required_permission"], "account.self")

    def test_identity_bind_requires_web_cookie_csrf_and_same_user_confirmation_is_one_time(self) -> None:
        user, csrf_token = self._establish_web_session(openid="same-user-openid")
        self.principals["same-user-token"] = SimpleNamespace(
            user=self.user_repo.get_user_by_id(user["id"]),
            roles=("member",),
            permissions=("account.self",),
        )

        rejected = self.browser.post("/api/v1/web-auth/identity-bind/start")
        self.assertEqual(rejected.status_code, 403, rejected.text)
        self.assertEqual(rejected.json()["error"], "csrf_failed")

        started = self.browser.post(
            "/api/v1/web-auth/identity-bind/start",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token},
        )
        self.assertEqual(started.status_code, 200, started.text)
        challenge = started.json()["challenge"]
        self.assertNotIn("hash", started.text.lower())

        anonymous = TestClient(self.app).post(
            "/api/v1/miniapp/auth/identity-bind/approve",
            json={"challenge": challenge},
        )
        self.assertEqual(anonymous.status_code, 401, anonymous.text)
        limited = TestClient(self.app).post(
            "/api/v1/miniapp/auth/identity-bind/approve",
            headers=self._member_headers("limited-token"),
            json={"challenge": challenge},
        )
        self.assertEqual(limited.status_code, 403, limited.text)

        approved = TestClient(self.app).post(
            "/api/v1/miniapp/auth/identity-bind/approve",
            headers=self._member_headers("same-user-token"),
            json={"challenge": challenge},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "approved")

        consumed = self.browser.post(
            "/api/v1/web-auth/identity-bind/consume",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"challenge": challenge},
        )
        self.assertEqual(consumed.status_code, 200, consumed.text)
        self.assertEqual(consumed.json(), {"status": "approved"})
        replay = self.browser.post(
            "/api/v1/web-auth/identity-bind/consume",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"challenge": challenge},
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json(), {"status": "invalid"})

    def test_identity_bind_refuses_to_merge_two_existing_canonical_users(self) -> None:
        _, csrf_token = self._establish_web_session(openid="web-user-for-conflict")
        started = self.browser.post(
            "/api/v1/web-auth/identity-bind/start",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token},
        )
        self.assertEqual(started.status_code, 200, started.text)
        challenge = started.json()["challenge"]
        approved = TestClient(self.app).post(
            "/api/v1/miniapp/auth/identity-bind/approve",
            headers=self._member_headers("other-user-token"),
            json={"challenge": challenge},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        consumed = self.browser.post(
            "/api/v1/web-auth/identity-bind/consume",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token},
            json={"challenge": challenge},
        )
        self.assertEqual(consumed.status_code, 200, consumed.text)
        self.assertEqual(consumed.json(), {"status": "conflict"})

    def test_logout_requires_csrf_and_revokes_the_web_session(self) -> None:
        self._establish_web_session(openid="logout-user")
        _, csrf_token = self._web_user_and_csrf()
        session_value = self.browser.cookies.get(WEB_USER_COOKIE_NAME)
        self.assertTrue(session_value)
        rejected = self.browser.post(
            "/api/v1/web-auth/logout",
            headers={"Origin": TRUSTED_ORIGIN},
        )
        self.assertEqual(rejected.status_code, 403, rejected.text)
        logged_out = self.browser.post(
            "/api/v1/web-auth/logout",
            headers={"Origin": TRUSTED_ORIGIN, "X-CSRF-Token": csrf_token},
        )
        self.assertEqual(logged_out.status_code, 204, logged_out.text)
        self.assertEqual(logged_out.headers["cache-control"], "no-store")
        self.assertIn(WEB_USER_COOKIE_NAME, logged_out.headers["set-cookie"])
        self.assertEqual(self.browser.get("/api/v1/web-auth/me").status_code, 401)
        replay = TestClient(self.app).get(
            "/api/v1/web-auth/me",
            headers={"Cookie": f"{WEB_USER_COOKIE_NAME}={session_value}"},
        )
        self.assertEqual(replay.status_code, 401, replay.text)


if __name__ == "__main__":
    unittest.main()
