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
from src.repositories.email_password_repo import EmailPasswordRepository
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.email_password_auth_service import (
    EmailPasswordAuthService,
    hash_password,
    verify_password,
)
from src.services.rbac_service import RbacService
from src.services.web_user_auth_service import WEB_USER_COOKIE_NAME
from src.storage import DatabaseManager


class EmailPasswordAuthApiTestCase(unittest.TestCase):
    """覆盖小程序绑定邮箱密码与 Web 端邮箱密码登录的真实边界。"""

    _ENV_KEYS = (
        "DATABASE_PATH",
        "STOCK_LIST",
        "GEMINI_API_KEY",
        "EMAIL_SENDER",
        "EMAIL_PASSWORD",
        "WEB_USER_SESSION_TTL_SECONDS",
        "EMAIL_VERIFICATION_RESEND_INTERVAL_SECONDS",
    )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "email_password.db"
        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ.update({
            "DATABASE_PATH": str(self.db_path),
            "STOCK_LIST": "600519",
            "GEMINI_API_KEY": "test",
            "EMAIL_SENDER": "sender@example.com",
            "EMAIL_PASSWORD": "smtp-auth-code",
            "WEB_USER_SESSION_TTL_SECONDS": "3600",
            # 关闭发码限频，便于同一测试内多次发码。
            "EMAIL_VERIFICATION_RESEND_INTERVAL_SECONDS": "30",
        })
        Config.reset_instance()
        DatabaseManager.reset_instance()

        self.db = DatabaseManager.get_instance()
        self.user_repo = MiniappUserRepository(self.db)
        self.rbac = RbacService()
        self.user = self.user_repo.upsert_user(openid="miniapp-user-1", issuer="email-pw-test")
        self.other_user = self.user_repo.upsert_user(openid="miniapp-user-2", issuer="email-pw-test")
        for user in (self.user, self.other_user):
            self.rbac.repository.ensure_role(user.id, "member")

        # 小程序 Bearer principal（具备 account.self 权限）。
        self.principals = {
            "user-1-token": SimpleNamespace(
                user=self.user, roles=("member",), permissions=("account.self",),
            ),
            "user-2-token": SimpleNamespace(
                user=self.other_user, roles=("member",), permissions=("account.self",),
            ),
        }
        self.miniapp_auth_patch = patch(
            "src.services.wechat_miniapp_auth_service.WechatMiniappAuthService.authenticate_token",
            side_effect=lambda token: self.principals.get(token),
        )
        self.miniapp_auth_patch.start()

        # 拦截真实邮件发送，捕获验证码。
        self.sent_codes: list[tuple[str, str]] = []

        def fake_send(_self, email, code, ttl_seconds):  # noqa: ANN001
            self.sent_codes.append((email, code))

        self.email_send_patch = patch.object(
            EmailPasswordAuthService, "_send_code_email", fake_send
        )
        self.email_send_patch.start()

        self.static_dir = self.data_dir / "static"
        self.static_dir.mkdir()
        self.app = create_app(static_dir=self.static_dir)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.email_send_patch.stop()
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
    def _bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _bind(self, token: str, email: str, password: str) -> None:
        code_resp = self.client.post(
            "/api/v1/miniapp/auth/email/request-code",
            json={"email": email},
            headers=self._bearer(token),
        )
        self.assertEqual(code_resp.status_code, 200, code_resp.text)
        sent_email, code = self.sent_codes[-1]
        self.assertEqual(sent_email, email.strip().lower())
        bind_resp = self.client.post(
            "/api/v1/miniapp/auth/email/bind",
            json={"email": email, "code": code, "password": password},
            headers=self._bearer(token),
        )
        self.assertEqual(bind_resp.status_code, 200, bind_resp.text)

    # ---------------- 密码哈希单元 ----------------

    def test_password_hash_roundtrip_and_format(self) -> None:
        stored = hash_password("correct horse battery")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn("correct horse battery", stored)
        self.assertTrue(verify_password("correct horse battery", stored))
        self.assertFalse(verify_password("wrong", stored))
        self.assertFalse(verify_password("x", "not-a-valid-hash"))

    # ---------------- 绑定端点边界 ----------------

    def test_bind_requires_bearer_and_account_self(self) -> None:
        anon = self.client.post(
            "/api/v1/miniapp/auth/email/request-code", json={"email": "a@b.com"}
        )
        self.assertEqual(anon.status_code, 401, anon.text)

    def test_request_code_returns_503_when_email_channel_unconfigured(self) -> None:
        os.environ.pop("EMAIL_SENDER", None)
        os.environ.pop("EMAIL_PASSWORD", None)
        Config.reset_instance()
        resp = self.client.post(
            "/api/v1/miniapp/auth/email/request-code",
            json={"email": "user@example.com"},
            headers=self._bearer("user-1-token"),
        )
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(len(self.sent_codes), 0)

    def test_wrong_code_is_rejected_and_correct_code_binds(self) -> None:
        code_resp = self.client.post(
            "/api/v1/miniapp/auth/email/request-code",
            json={"email": "user@example.com"},
            headers=self._bearer("user-1-token"),
        )
        self.assertEqual(code_resp.status_code, 200, code_resp.text)
        _, code = self.sent_codes[-1]

        wrong = self.client.post(
            "/api/v1/miniapp/auth/email/bind",
            json={"email": "user@example.com", "code": "000000", "password": "pw12345678"},
            headers=self._bearer("user-1-token"),
        )
        self.assertEqual(wrong.status_code, 400, wrong.text)

        ok = self.client.post(
            "/api/v1/miniapp/auth/email/bind",
            json={"email": "user@example.com", "code": code, "password": "pw12345678"},
            headers=self._bearer("user-1-token"),
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["email"], "user@example.com")
        self.assertTrue(ok.json()["email_verified"])
        self.assertTrue(ok.json()["has_password"])

    def test_email_cannot_be_bound_by_two_users(self) -> None:
        self._bind("user-1-token", "shared@example.com", "pw12345678")
        conflict = self.client.post(
            "/api/v1/miniapp/auth/email/request-code",
            json={"email": "shared@example.com"},
            headers=self._bearer("user-2-token"),
        )
        self.assertEqual(conflict.status_code, 409, conflict.text)

    # ---------------- Web 密码登录 ----------------

    def test_password_login_sets_cookie_and_exposes_current_user(self) -> None:
        self._bind("user-1-token", "login@example.com", "pw12345678")
        # 独立浏览器（无既有登录态）。
        browser = TestClient(self.app)
        resp = browser.post(
            "/api/v1/web-auth/password/login",
            json={"email": "login@example.com", "password": "pw12345678"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn(WEB_USER_COOKIE_NAME, resp.cookies)
        body = resp.json()
        self.assertEqual(int(body["user"]["id"]), int(self.user.id))
        self.assertTrue(body["csrf_token"])
        # 会话 Cookie 可访问 /me。
        me = browser.get("/api/v1/web-auth/me")
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(int(me.json()["user"]["id"]), int(self.user.id))

    def test_password_login_rejects_wrong_password_and_unknown_email(self) -> None:
        self._bind("user-1-token", "login2@example.com", "pw12345678")
        wrong = TestClient(self.app).post(
            "/api/v1/web-auth/password/login",
            json={"email": "login2@example.com", "password": "wrongpass"},
        )
        self.assertEqual(wrong.status_code, 401, wrong.text)
        self.assertEqual(wrong.json()["error"], "invalid_credentials")

        unknown = TestClient(self.app).post(
            "/api/v1/web-auth/password/login",
            json={"email": "nobody@example.com", "password": "pw12345678"},
        )
        self.assertEqual(unknown.status_code, 401, unknown.text)

    def test_password_login_rejected_when_already_authenticated(self) -> None:
        self._bind("user-1-token", "login3@example.com", "pw12345678")
        browser = TestClient(self.app)
        first = browser.post(
            "/api/v1/web-auth/password/login",
            json={"email": "login3@example.com", "password": "pw12345678"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        # 已登录浏览器再次发起登录被拒绝。
        again = browser.post(
            "/api/v1/web-auth/password/login",
            json={"email": "login3@example.com", "password": "pw12345678"},
        )
        self.assertEqual(again.status_code, 400, again.text)
        self.assertEqual(again.json()["error"], "authentication_conflict")

    def test_rebind_updates_password(self) -> None:
        self._bind("user-1-token", "rotate@example.com", "oldpassword1")
        self._bind("user-1-token", "rotate@example.com", "newpassword2")
        # 旧密码失效，新密码可登录。
        old = TestClient(self.app).post(
            "/api/v1/web-auth/password/login",
            json={"email": "rotate@example.com", "password": "oldpassword1"},
        )
        self.assertEqual(old.status_code, 401, old.text)
        new = TestClient(self.app).post(
            "/api/v1/web-auth/password/login",
            json={"email": "rotate@example.com", "password": "newpassword2"},
        )
        self.assertEqual(new.status_code, 200, new.text)

    def test_unverified_credential_cannot_login(self) -> None:
        # 直接写入未验证凭据（email_verified_at=None），模拟未完成验证的历史数据。
        repo = EmailPasswordRepository(self.db)
        repo.upsert_credential(
            user_id=int(self.user.id),
            email="unverified@example.com",
            password_hash=hash_password("pw12345678"),
            email_verified_at=None,
        )
        resp = TestClient(self.app).post(
            "/api/v1/web-auth/password/login",
            json={"email": "unverified@example.com", "password": "pw12345678"},
        )
        self.assertEqual(resp.status_code, 401, resp.text)


if __name__ == "__main__":
    unittest.main()
