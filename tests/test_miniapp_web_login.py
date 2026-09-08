from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from api.app import create_app
from src.config import Config
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.rbac_service import RbacService
from src.services.web_user_auth_service import WEB_USER_COOKIE_NAME
from src.storage import DatabaseManager

TRUSTED_ORIGIN = "http://localhost:5173"
OAUTH_BINDING_COOKIE = "dsa_wechat_oauth_binding"
CALLBACK_PATH = "/api/v1/web-auth/wechat/callback"


class _WechatResponse:
    def __init__(self, payload: dict, *, error: Exception | None = None):
        self.payload = payload
        self.error = error

    def raise_for_status(self) -> None:
        if self.error is not None:
            raise self.error

    def json(self) -> dict:
        return self.payload


class MiniappWebLoginApiTestCase(unittest.TestCase):
    """覆盖官方微信网站 OAuth、本地 Web Cookie 与身份确认的真实边界。"""

    _ENV_KEYS = (
        "DATABASE_PATH",
        "STOCK_LIST",
        "GEMINI_API_KEY",
        "WECHAT_OPEN_WEB_APP_ID",
        "WECHAT_OPEN_WEB_APP_SECRET",
        "WECHAT_OPEN_WEB_REDIRECT_URI",
        "WECHAT_OPEN_WEB_STATE_TTL_SECONDS",
        "WEB_USER_SESSION_TTL_SECONDS",
    )

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "web_wechat_oauth.db"
        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ.update({
            "DATABASE_PATH": str(self.db_path),
            "STOCK_LIST": "600519",
            "GEMINI_API_KEY": "test",
            "WECHAT_OPEN_WEB_APP_ID": "web-app-id",
            "WECHAT_OPEN_WEB_APP_SECRET": "web-app-secret",
            "WECHAT_OPEN_WEB_REDIRECT_URI": "https://web.example.test/api/v1/web-auth/wechat/callback",
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
    def _wechat_success(openid: str = "web-openid", unionid: str | None = None) -> list[_WechatResponse]:
        token_payload = {
            "access_token": "upstream-access-token",
            "openid": openid,
        }
        userinfo_payload = {"openid": openid}
        if unionid:
            token_payload["unionid"] = unionid
            userinfo_payload["unionid"] = unionid
        return [_WechatResponse(token_payload), _WechatResponse(userinfo_payload)]

    @staticmethod
    def _member_headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _start_oauth(self, browser: TestClient | None = None) -> tuple[TestClient, str]:
        client = browser or self.browser
        response = client.get("/api/v1/web-auth/wechat/start", follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        location = response.headers["location"]
        parsed = urlsplit(location)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "open.weixin.qq.com")
        self.assertEqual(parsed.path, "/connect/qrconnect")
        self.assertEqual(query["appid"], ["web-app-id"])
        self.assertEqual(query["redirect_uri"], [os.environ["WECHAT_OPEN_WEB_REDIRECT_URI"]])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["scope"], ["snsapi_login"])
        self.assertGreaterEqual(len(query["state"][0]), 43)
        self.assertTrue(location.endswith("#wechat_redirect"))
        self.assertNotIn("web-app-secret", location)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn(f"{OAUTH_BINDING_COOKIE}=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn(f"path={CALLBACK_PATH}", cookie)
        self.assertNotIn("openid", response.text.lower())
        return client, query["state"][0]

    def _complete_oauth(
        self,
        *,
        browser: TestClient | None = None,
        openid: str = "web-openid",
        unionid: str | None = None,
    ) -> tuple[TestClient, object]:
        client, state = self._start_oauth(browser)
        with patch(
            "src.services.wechat_open_web_auth_service.httpx.get",
            side_effect=self._wechat_success(openid, unionid),
        ) as upstream_get:
            response = client.get(
                "/api/v1/web-auth/wechat/callback",
                params={"code": "wechat-code", "state": state},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 302, response.text)
        self.assertEqual(response.headers["location"], "/")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn(WEB_USER_COOKIE_NAME, response.headers["set-cookie"])
        self.assertIn(f"{OAUTH_BINDING_COOKIE}=\"\"", response.headers["set-cookie"])
        self.assertNotIn("wechat-code", response.text)
        self.assertNotIn("upstream-access-token", response.text)
        self.assertNotIn(openid, response.text)
        self.assertNotIn("web-app-secret", response.text)
        self.assertEqual(upstream_get.call_count, 2)
        token_call, userinfo_call = upstream_get.call_args_list
        self.assertEqual(token_call.kwargs["params"]["appid"], "web-app-id")
        self.assertEqual(token_call.kwargs["params"]["secret"], "web-app-secret")
        self.assertEqual(token_call.kwargs["params"]["code"], "wechat-code")
        self.assertEqual(token_call.kwargs["timeout"], 8.0)
        self.assertEqual(userinfo_call.kwargs["params"]["openid"], openid)
        self.assertEqual(userinfo_call.kwargs["timeout"], 8.0)
        return client, response

    def _web_user_and_csrf(self) -> tuple[dict, str]:
        response = self.browser.get("/api/v1/web-auth/me")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("csrf_token", payload)
        return payload["user"], payload["csrf_token"]

    def test_start_requires_complete_configuration_and_redirect_uri_https(self) -> None:
        os.environ["WECHAT_OPEN_WEB_REDIRECT_URI"] = "http://localhost:8000/api/v1/web-auth/wechat/callback"
        Config.reset_instance()
        rejected = self.browser.get("/api/v1/web-auth/wechat/start", follow_redirects=False)
        self.assertEqual(rejected.status_code, 503, rejected.text)
        self.assertEqual(rejected.json()["error"], "wechat_login_failed")
        self.assertIn(f"{OAUTH_BINDING_COOKIE}=\"\"", rejected.headers["set-cookie"])

        os.environ["WECHAT_OPEN_WEB_REDIRECT_URI"] = "https://web.example.test/api/v1/web-auth/wechat/callback"
        os.environ["WECHAT_OPEN_WEB_APP_SECRET"] = ""
        Config.reset_instance()
        missing_config = self.browser.get("/api/v1/web-auth/wechat/start", follow_redirects=False)
        self.assertEqual(missing_config.status_code, 503, missing_config.text)
        self.assertEqual(missing_config.json()["error"], "wechat_login_failed")

    def test_start_rejects_existing_cookie_or_bearer_before_creating_transaction(self) -> None:
        self._complete_oauth(openid="web-user-for-start-conflict")

        with patch(
            "api.v1.endpoints.web_auth.WechatOpenWebAuthService.start_login"
        ) as start_login:
            cookie_response = self.browser.get(
                "/api/v1/web-auth/wechat/start",
                follow_redirects=False,
            )
        self.assertEqual(cookie_response.status_code, 400, cookie_response.text)
        self.assertEqual(cookie_response.json()["error"], "authentication_conflict")
        self.assertNotIn(OAUTH_BINDING_COOKIE, cookie_response.headers.get("set-cookie", ""))
        start_login.assert_not_called()

        anonymous_browser = TestClient(self.app)
        with patch(
            "api.v1.endpoints.web_auth.WechatOpenWebAuthService.start_login"
        ) as start_login:
            bearer_response = anonymous_browser.get(
                "/api/v1/web-auth/wechat/start",
                headers=self._member_headers("other-user-token"),
                follow_redirects=False,
            )
        self.assertEqual(bearer_response.status_code, 400, bearer_response.text)
        self.assertEqual(bearer_response.json()["error"], "authentication_conflict")
        self.assertNotIn(OAUTH_BINDING_COOKIE, bearer_response.headers.get("set-cookie", ""))
        start_login.assert_not_called()

    def test_callback_creates_cookie_only_session_and_exposes_current_user_via_me(self) -> None:
        self._complete_oauth(unionid="trusted-union-id")
        user, csrf_token = self._web_user_and_csrf()
        self.assertIsInstance(user["id"], int)
        self.assertTrue(csrf_token)
        self.assertNotIn("openid", user)
        self.assertNotIn("unionid", user)
        self.assertEqual(self.browser.get("/api/v1/miniapp/auth/me").status_code, 400)

    def test_miniapp_login_rejects_web_cookie_and_bearer_before_token_issuance(self) -> None:
        self._complete_oauth(openid="web-user-for-login-conflict")

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

    def test_miniapp_account_route_cannot_bypass_central_rbac(self) -> None:
        limited = TestClient(self.app).get(
            "/api/v1/miniapp/auth/me",
            headers=self._member_headers("limited-token"),
        )

        self.assertEqual(limited.status_code, 403, limited.text)
        self.assertEqual(limited.json()["error"], "forbidden")
        self.assertEqual(limited.json()["required_permission"], "account.self")

    def test_callback_rejects_missing_or_wrong_binding_without_consuming_valid_state(self) -> None:
        _, state = self._start_oauth()
        binding = self.browser.cookies.get(OAUTH_BINDING_COOKIE)
        self.assertTrue(binding)
        without_cookie = TestClient(self.app).get(
            "/api/v1/web-auth/wechat/callback",
            params={"code": "wechat-code", "state": state},
            follow_redirects=False,
        )
        self.assertEqual(without_cookie.status_code, 400, without_cookie.text)
        self.assertEqual(without_cookie.json()["error"], "wechat_login_failed")

        wrong_state = self.browser.get(
            "/api/v1/web-auth/wechat/callback",
            params={"code": "wechat-code", "state": "different-state"},
            follow_redirects=False,
        )
        self.assertEqual(wrong_state.status_code, 400, wrong_state.text)
        self.assertEqual(wrong_state.json()["error"], "wechat_login_failed")

        with patch(
            "src.services.wechat_open_web_auth_service.httpx.get",
            side_effect=self._wechat_success(),
        ) as upstream_get:
            success = TestClient(self.app).get(
                "/api/v1/web-auth/wechat/callback",
                params={"code": "wechat-code", "state": state},
                headers={"Cookie": f"{OAUTH_BINDING_COOKIE}={binding}"},
                follow_redirects=False,
            )
        self.assertEqual(success.status_code, 302, success.text)
        self.assertEqual(upstream_get.call_count, 2)

    def test_callback_replay_and_existing_bearer_are_rejected_before_upstream_exchange(self) -> None:
        _, state = self._start_oauth()
        binding = self.browser.cookies.get(OAUTH_BINDING_COOKIE)
        self.assertTrue(binding)
        with patch(
            "src.services.wechat_open_web_auth_service.httpx.get",
            side_effect=self._wechat_success(),
        ):
            success = self.browser.get(
                "/api/v1/web-auth/wechat/callback",
                params={"code": "wechat-code", "state": state},
                follow_redirects=False,
            )
        self.assertEqual(success.status_code, 302, success.text)

        replay = TestClient(self.app).get(
            "/api/v1/web-auth/wechat/callback",
            params={"code": "wechat-code", "state": state},
            headers={"Cookie": f"{OAUTH_BINDING_COOKIE}={binding}"},
            follow_redirects=False,
        )
        self.assertEqual(replay.status_code, 400, replay.text)
        self.assertEqual(replay.json()["error"], "wechat_login_failed")
        self.assertNotIn(WEB_USER_COOKIE_NAME, replay.headers.get("set-cookie", ""))

        second_browser = TestClient(self.app)
        _, next_state = self._start_oauth(second_browser)
        with patch("src.services.wechat_open_web_auth_service.httpx.get") as upstream_get:
            existing_bearer = second_browser.get(
                "/api/v1/web-auth/wechat/callback",
                params={"code": "wechat-code", "state": next_state},
                headers=self._member_headers("other-user-token"),
                follow_redirects=False,
            )
        self.assertEqual(existing_bearer.status_code, 400, existing_bearer.text)
        self.assertEqual(existing_bearer.json()["error"], "authentication_conflict")
        upstream_get.assert_not_called()

    def test_upstream_errors_and_identity_mismatch_are_generic_and_do_not_set_session(self) -> None:
        invalid_cases = (
            [_WechatResponse({"errcode": 40029})],
            [_WechatResponse({"access_token": "token", "openid": "web-openid"}), _WechatResponse({"openid": "other-openid"})],
            [_WechatResponse({"access_token": "token", "openid": "web-openid"}), _WechatResponse({"errcode": 40003})],
        )
        for responses in invalid_cases:
            with self.subTest(responses=responses):
                client = TestClient(self.app)
                _, state = self._start_oauth(client)
                with patch(
                    "src.services.wechat_open_web_auth_service.httpx.get",
                    side_effect=responses,
                ):
                    response = client.get(
                        "/api/v1/web-auth/wechat/callback",
                        params={"code": "wechat-code", "state": state},
                        follow_redirects=False,
                    )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["error"], "wechat_login_failed")
                self.assertNotIn(WEB_USER_COOKIE_NAME, response.headers.get("set-cookie", ""))

    def test_identity_bind_requires_web_cookie_csrf_and_same_user_confirmation_is_one_time(self) -> None:
        self._complete_oauth(openid="same-user-openid")
        user, csrf_token = self._web_user_and_csrf()
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
        self._complete_oauth(openid="web-user-for-conflict")
        _, csrf_token = self._web_user_and_csrf()
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
        self._complete_oauth(openid="logout-user")
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
