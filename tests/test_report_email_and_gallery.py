from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import timedelta
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
from src.services.email_password_auth_service import hash_password
from src.storage import AnalysisHistory, DatabaseManager, local_naive_now


def _make_env(db_path: Path) -> dict:
    return {
        "DATABASE_PATH": str(db_path),
        "STOCK_LIST": "600519",
        "GEMINI_API_KEY": "test",
    }


class ReportEmailPreferenceRepoTestCase(unittest.TestCase):
    """报告接收邮箱解析 + "报告发送到邮箱"开关的仓储行为。"""

    _ENV_KEYS = ("DATABASE_PATH", "STOCK_LIST", "GEMINI_API_KEY")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "report_email_repo.db"
        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ.update(_make_env(self.db_path))
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.user_repo = MiniappUserRepository(self.db)
        self.repo = EmailPasswordRepository(self.db)
        self.user = self.user_repo.upsert_user(openid="report-email-user", issuer="test")

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def test_report_email_target_defaults_on_and_follows_toggle(self) -> None:
        # 未绑定邮箱：无接收邮箱，且开关切换失败（无凭据）。
        self.assertIsNone(self.repo.get_report_email_target(self.user.id))
        self.assertFalse(self.repo.set_report_email_enabled(user_id=self.user.id, enabled=True))

        # 绑定后默认开启 → 返回邮箱。
        self.assertTrue(
            self.repo.upsert_credential(
                user_id=self.user.id,
                email="owner@example.com",
                password_hash=hash_password("secret-pass"),
                email_verified_at=local_naive_now(),
            )
        )
        self.assertEqual(self.repo.get_report_email_target(self.user.id), "owner@example.com")

        # 关闭开关 → 不再返回邮箱，但邮箱本身仍在（登录不受影响）。
        self.assertTrue(self.repo.set_report_email_enabled(user_id=self.user.id, enabled=False))
        self.assertIsNone(self.repo.get_report_email_target(self.user.id))
        self.assertEqual(self.repo.get_credential_by_user_id(self.user.id).email, "owner@example.com")

        # 重新开启 → 恢复。
        self.assertTrue(self.repo.set_report_email_enabled(user_id=self.user.id, enabled=True))
        self.assertEqual(self.repo.get_report_email_target(self.user.id), "owner@example.com")


class NotificationEmailOverrideTestCase(unittest.TestCase):
    """通知层 email_receivers_override：显式收件人 / 空则跳过。"""

    _ENV_KEYS = ("EMAIL_SENDER", "EMAIL_PASSWORD", "STOCK_LIST", "GEMINI_API_KEY")

    def setUp(self) -> None:
        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ.update({
            "EMAIL_SENDER": "sender@example.com",
            "EMAIL_PASSWORD": "app-password",
            "STOCK_LIST": "600519",
            "GEMINI_API_KEY": "test",
        })
        Config.reset_instance()
        from src.notification import NotificationService

        self.service = NotificationService()

    def tearDown(self) -> None:
        Config.reset_instance()
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_empty_override_skips_email_without_sending(self) -> None:
        from src.notification import NotificationChannel

        with patch.object(self.service, "send_to_email", return_value=True) as send_email:
            ok = self.service._send_to_static_channel(
                NotificationChannel.EMAIL,
                "content",
                image_bytes=None,
                email_stock_codes=None,
                email_send_to_all=False,
                email_receivers_override=[],
            )
        self.assertTrue(ok)  # 跳过视为无需发送，不算失败
        send_email.assert_not_called()

    def test_explicit_override_routes_to_owner_email(self) -> None:
        from src.notification import NotificationChannel

        with patch.object(self.service, "send_to_email", return_value=True) as send_email:
            ok = self.service._send_to_static_channel(
                NotificationChannel.EMAIL,
                "content",
                image_bytes=None,
                email_stock_codes=None,
                email_send_to_all=False,
                email_receivers_override=["owner@example.com"],
            )
        self.assertTrue(ok)
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.kwargs.get("receivers"), ["owner@example.com"])


class ReportGalleryApiTestCase(unittest.TestCase):
    """报告展览端点：当天、排除大盘复盘、搜索、分页、生成者昵称。"""

    _ENV_KEYS = ("DATABASE_PATH", "STOCK_LIST", "GEMINI_API_KEY")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "report_gallery.db"
        self._original_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        os.environ.update(_make_env(self.db_path))
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.user_repo = MiniappUserRepository(self.db)
        self.alice = self.user_repo.upsert_user(openid="alice", issuer="test")
        self.user_repo.update_profile(user_id=self.alice.id, nickname="Alice", avatar_url=None)
        self.bob = self.user_repo.upsert_user(openid="bob", issuer="test")

        self._seed_reports()

        self.principal = SimpleNamespace(
            user=self.bob,
            roles=("member",),
            permissions=("analysis.read",),
        )
        self.auth_patch = patch(
            "src.services.wechat_miniapp_auth_service.WechatMiniappAuthService.authenticate_token",
            side_effect=lambda token: self.principal if token == "member-token" else None,
        )
        self.auth_patch.start()

        self.static_dir = self.data_dir / "static"
        self.static_dir.mkdir()
        self.app = create_app(static_dir=self.static_dir)
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

    def _add_report(self, **kwargs) -> None:
        now = local_naive_now()
        defaults = dict(
            query_id=f"q-{kwargs.get('code', 'x')}-{kwargs.get('owner_user_id', 0)}",
            owner_scope="user",
            report_type="simple",
            name=None,
            sentiment_score=60.0,
            operation_advice="观望",
            analysis_summary="摘要",
            created_at=now,
        )
        defaults.update(kwargs)
        with self.db.get_session() as session:
            session.add(AnalysisHistory(**defaults))
            session.commit()

    def _seed_reports(self) -> None:
        # 今天：Alice 两只、Bob 一只（个股）
        self._add_report(code="SH600519", name="贵州茅台", owner_user_id=self.alice.id)
        self._add_report(code="SZ000001", name="平安银行", owner_user_id=self.alice.id)
        self._add_report(code="SH601318", name="中国平安", owner_user_id=self.bob.id)
        # 今天：大盘复盘（应被排除）
        self._add_report(
            code="MARKET",
            name="大盘复盘",
            owner_scope="global",
            owner_user_id=None,
            report_type="market_review",
        )
        # 昨天：个股（应被排除，非当天）
        self._add_report(
            code="SH600000",
            name="浦发银行",
            owner_user_id=self.alice.id,
            created_at=local_naive_now() - timedelta(days=1),
        )

    def _headers(self) -> dict:
        return {"Authorization": "Bearer member-token"}

    def test_gallery_lists_only_today_individual_reports_with_owner(self) -> None:
        resp = self.client.get("/api/v1/analysis/gallery", headers=self._headers())
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total"], 3)  # 排除大盘复盘 + 昨天
        codes = {item["stock_code"] for item in body["items"]}
        self.assertNotIn("MARKET", codes)
        self.assertNotIn("SH600000", codes)  # 昨天
        owners = {item["owner_name"] for item in body["items"]}
        self.assertIn("Alice", owners)

    def test_gallery_search_filters_by_code_or_name(self) -> None:
        by_name = self.client.get(
            "/api/v1/analysis/gallery", params={"search": "茅台"}, headers=self._headers()
        )
        self.assertEqual(by_name.status_code, 200, by_name.text)
        name_items = by_name.json()["items"]
        self.assertEqual(len(name_items), 1)
        self.assertEqual(name_items[0]["stock_name"], "贵州茅台")

        by_code = self.client.get(
            "/api/v1/analysis/gallery", params={"search": "601318"}, headers=self._headers()
        )
        self.assertEqual(by_code.status_code, 200, by_code.text)
        self.assertEqual(len(by_code.json()["items"]), 1)

    def test_gallery_paginates(self) -> None:
        page1 = self.client.get(
            "/api/v1/analysis/gallery",
            params={"page": 1, "limit": 2},
            headers=self._headers(),
        ).json()
        self.assertEqual(page1["total"], 3)
        self.assertEqual(len(page1["items"]), 2)
        page2 = self.client.get(
            "/api/v1/analysis/gallery",
            params={"page": 2, "limit": 2},
            headers=self._headers(),
        ).json()
        self.assertEqual(len(page2["items"]), 1)

    def test_gallery_detail_rejects_market_review_and_unknown(self) -> None:
        # 大盘复盘不在展览范围
        with self.db.get_session() as session:
            market = session.query(AnalysisHistory).filter(
                AnalysisHistory.code == "MARKET"
            ).one()
            market_id = market.id
        rejected = self.client.get(
            f"/api/v1/analysis/gallery/{market_id}", headers=self._headers()
        )
        self.assertEqual(rejected.status_code, 404, rejected.text)

        missing = self.client.get(
            "/api/v1/analysis/gallery/99999999", headers=self._headers()
        )
        self.assertEqual(missing.status_code, 404, missing.text)


if __name__ == "__main__":
    unittest.main()
