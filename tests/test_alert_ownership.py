"""Owner-scope regression tests for alert rules and history."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from fastapi import HTTPException

from api.v1.endpoints import alerts as alert_endpoints
from api.v1.schemas.alerts import AlertRuleUpdateRequest
from src.config import Config
from src.portfolio_ownership import LEGACY_GLOBAL_PORTFOLIO_SCOPE, PortfolioScope
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.alert_service import AlertNotFoundError, AlertService, AlertServiceError
from src.services.portfolio_service import PortfolioService
from src.storage import DatabaseManager, MiniappUserRecord


class AlertOwnershipTestCase(unittest.TestCase):
    """Exercise owner isolation against a real file-backed SQLite database."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "alert_ownership.db"
        self.env_path = self.data_dir / ".env"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    f"DATABASE_PATH={self.db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        self.db = DatabaseManager(db_url=f"sqlite:///{self.db_path}")
        self.service = AlertService(self.db)
        with self.db.get_session() as session:
            owner_a = MiniappUserRecord(openid="alert-owner-a")
            owner_b = MiniappUserRecord(openid="alert-owner-b")
            session.add_all([owner_a, owner_b])
            session.commit()
            session.refresh(owner_a)
            session.refresh(owner_b)
            self.owner_a = int(owner_a.id)
            self.owner_b = int(owner_b.id)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    @staticmethod
    def _payload(name: str, target: str) -> dict:
        return {
            "name": name,
            "target_scope": "single_symbol",
            "target": target,
            "alert_type": "price_cross",
            "parameters": {"direction": "above", "price": 10},
            "severity": "warning",
            "enabled": True,
        }

    def _create_rule(self, name: str, target: str, user_id: int | None) -> dict:
        return self.service.create_rule(self._payload(name, target), user_id=user_id)

    def _create_history(self, rule: dict, *, channel: str = "custom") -> tuple[int, int]:
        trigger = self.service.repo.create_trigger(
            {
                "rule_id": rule["id"],
                "target": rule["target"],
                "status": "triggered",
                "reason": f"trigger for {rule['name']}",
            }
        )
        notification = self.service.repo.record_notification_attempt(
            {
                "trigger_id": trigger.id,
                "channel": channel,
                "attempt": 1,
                "success": True,
                "retryable": False,
            }
        )
        return int(trigger.id), int(notification.id)

    def test_request_owner_requires_canonical_authenticated_principal(self) -> None:
        miniapp_request = SimpleNamespace(
            state=SimpleNamespace(
                miniapp_principal=SimpleNamespace(user=SimpleNamespace(id=self.owner_a)),
                auth_kind="miniapp",
            )
        )
        unauthenticated_request = SimpleNamespace(
            state=SimpleNamespace(miniapp_principal=None)
        )

        self.assertEqual(alert_endpoints._request_user_id(miniapp_request), self.owner_a)
        with self.assertRaises(HTTPException) as context:
            alert_endpoints._request_user_id(unauthenticated_request)
        self.assertEqual(context.exception.status_code, 401)

    def test_create_forces_trusted_owner_and_rule_lists_hide_foreign_and_legacy_rows(self) -> None:
        payload = self._payload("Owner A rule", "600519")
        payload["user_id"] = self.owner_b
        owner_a_rule = self.service.create_rule(payload, user_id=self.owner_a)
        owner_b_rule = self._create_rule("Owner B rule", "000001", self.owner_b)
        legacy_rule = self._create_rule("Legacy rule", "000002", None)

        self.assertNotIn("user_id", owner_a_rule)
        self.assertEqual(
            self.service.repo.get_rule(owner_a_rule["id"]).user_id,
            self.owner_a,
        )

        owner_a_list = self.service.list_rules(user_id=self.owner_a, page_size=1)
        self.assertEqual(owner_a_list["total"], 1)
        self.assertEqual([item["id"] for item in owner_a_list["items"]], [owner_a_rule["id"]])

        owner_b_list = self.service.list_rules(user_id=self.owner_b, page_size=100)
        self.assertEqual(owner_b_list["total"], 1)
        self.assertEqual([item["id"] for item in owner_b_list["items"]], [owner_b_rule["id"]])

        admin_list = self.service.list_rules(user_id=None, page_size=100)
        self.assertEqual(admin_list["total"], 3)
        self.assertEqual(
            {item["id"] for item in admin_list["items"]},
            {owner_a_rule["id"], owner_b_rule["id"], legacy_rule["id"]},
        )
        self.assertEqual(
            {row.id for row in self.service.repo.list_enabled_rules()},
            {owner_a_rule["id"], owner_b_rule["id"], legacy_rule["id"]},
        )

        with self.assertRaises(AlertNotFoundError):
            self.service.get_rule(legacy_rule["id"], user_id=self.owner_a)

    def test_portfolio_alert_targets_and_runtime_payloads_are_owner_scoped(self) -> None:
        portfolio_service = PortfolioService(repo=PortfolioRepository(self.db))
        owner_a_scope = PortfolioScope.user(str(self.owner_a))
        owner_b_scope = PortfolioScope.user(str(self.owner_b))
        owner_a_account = portfolio_service.create_account(
            name="Owner A portfolio",
            broker="Demo",
            market="cn",
            base_currency="CNY",
            portfolio_scope=owner_a_scope,
        )
        owner_b_account = portfolio_service.create_account(
            name="Owner B portfolio",
            broker="Demo",
            market="cn",
            base_currency="CNY",
            portfolio_scope=owner_b_scope,
        )
        legacy_account = portfolio_service.create_account(
            name="Legacy portfolio",
            broker="Demo",
            market="cn",
            base_currency="CNY",
            portfolio_scope=LEGACY_GLOBAL_PORTFOLIO_SCOPE,
        )

        def payload(target: str) -> dict:
            return {
                "name": f"Portfolio {target}",
                "target_scope": "portfolio_account",
                "target": target,
                "alert_type": "portfolio_concentration",
                "parameters": {},
                "severity": "warning",
                "enabled": True,
            }

        own_rule = self.service.create_rule(
            payload(str(owner_a_account["id"])),
            user_id=self.owner_a,
        )
        with self.assertRaises(AlertServiceError):
            self.service.create_rule(
                payload(str(owner_b_account["id"])),
                user_id=self.owner_a,
            )
        with self.assertRaises(AlertServiceError):
            self.service.create_rule(
                payload(str(legacy_account["id"])),
                user_id=self.owner_a,
            )

        all_rule = self.service.create_rule(payload("all"), user_id=self.owner_a)
        all_row = self.service.repo.get_rule(all_rule["id"], user_id=self.owner_a)
        runtime_payload = self.service.build_runtime_payloads(all_row)[0]
        self.assertEqual(runtime_payload.rule.portfolio_scope, owner_a_scope)

        admin_rule = self.service.create_rule(
            payload(str(legacy_account["id"])),
            user_id=None,
        )
        admin_row = self.service.repo.get_rule(admin_rule["id"], user_id=None)
        admin_payload = self.service.build_runtime_payloads(admin_row)[0]
        self.assertEqual(admin_payload.rule.portfolio_scope, LEGACY_GLOBAL_PORTFOLIO_SCOPE)
        self.assertIsNotNone(admin_rule["id"])
        self.assertIsNotNone(own_rule["id"])

    def test_foreign_detail_update_delete_enable_and_test_are_not_found(self) -> None:
        foreign_rule = self._create_rule("Owner B protected", "000001", self.owner_b)
        rule_id = foreign_rule["id"]

        with self.assertRaises(AlertNotFoundError):
            self.service.get_rule(rule_id, user_id=self.owner_a)
        with self.assertRaises(AlertNotFoundError):
            self.service.update_rule(rule_id, {"name": "hijacked"}, user_id=self.owner_a)
        with self.assertRaises(AlertNotFoundError):
            self.service.enable_rule(rule_id, False, user_id=self.owner_a)
        with self.assertRaises(AlertNotFoundError):
            self.service.test_rule(rule_id, user_id=self.owner_a)
        self.assertFalse(self.service.delete_rule(rule_id, user_id=self.owner_a))

        untouched = self.service.get_rule(rule_id, user_id=self.owner_b)
        self.assertEqual(untouched["name"], "Owner B protected")
        self.assertTrue(untouched["enabled"])

    def test_foreign_endpoint_operations_map_to_not_found(self) -> None:
        foreign_rule = self._create_rule("Owner B endpoint", "000001", self.owner_b)
        request = SimpleNamespace(
            state=SimpleNamespace(
                miniapp_principal=SimpleNamespace(user=SimpleNamespace(id=self.owner_a)),
                auth_kind="miniapp",
            )
        )
        operations = [
            lambda: alert_endpoints.get_rule(request, foreign_rule["id"]),
            lambda: alert_endpoints.update_rule(
                request,
                foreign_rule["id"],
                AlertRuleUpdateRequest(name="hijacked"),
            ),
            lambda: alert_endpoints.delete_rule(request, foreign_rule["id"]),
            lambda: alert_endpoints.enable_rule(request, foreign_rule["id"]),
            lambda: alert_endpoints.test_rule(request, foreign_rule["id"]),
        ]

        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(HTTPException) as context:
                    operation()
                self.assertEqual(context.exception.status_code, 404)

    def test_trigger_and_notification_lists_follow_rule_owner_chain(self) -> None:
        owner_a_rule = self._create_rule("Owner A history", "600519", self.owner_a)
        owner_b_rule = self._create_rule("Owner B history", "000001", self.owner_b)
        legacy_rule = self._create_rule("Legacy history", "000002", None)
        owner_a_trigger, owner_a_notification = self._create_history(owner_a_rule, channel="a")
        owner_b_trigger, owner_b_notification = self._create_history(owner_b_rule, channel="b")
        legacy_trigger, legacy_notification = self._create_history(legacy_rule, channel="legacy")

        owner_a_triggers = self.service.list_triggers(user_id=self.owner_a, page_size=100)
        self.assertEqual(owner_a_triggers["total"], 1)
        self.assertEqual([item["id"] for item in owner_a_triggers["items"]], [owner_a_trigger])
        foreign_triggers = self.service.list_triggers(
            user_id=self.owner_a,
            rule_id=owner_b_rule["id"],
            page_size=100,
        )
        self.assertEqual(foreign_triggers, {"items": [], "total": 0, "page": 1, "page_size": 100})

        owner_a_notifications = self.service.list_notifications(user_id=self.owner_a, page_size=100)
        self.assertEqual(owner_a_notifications["total"], 1)
        self.assertEqual(
            [item["id"] for item in owner_a_notifications["items"]],
            [owner_a_notification],
        )
        foreign_notifications = self.service.list_notifications(
            user_id=self.owner_a,
            trigger_id=owner_b_trigger,
            page_size=100,
        )
        self.assertEqual(
            foreign_notifications,
            {"items": [], "total": 0, "page": 1, "page_size": 100},
        )

        admin_triggers = self.service.list_triggers(user_id=None, page_size=100)
        admin_notifications = self.service.list_notifications(user_id=None, page_size=100)
        self.assertEqual(admin_triggers["total"], 3)
        self.assertEqual(
            {item["id"] for item in admin_triggers["items"]},
            {owner_a_trigger, owner_b_trigger, legacy_trigger},
        )
        self.assertEqual(admin_notifications["total"], 3)
        self.assertEqual(
            {item["id"] for item in admin_notifications["items"]},
            {owner_a_notification, owner_b_notification, legacy_notification},
        )


if __name__ == "__main__":
    unittest.main()
