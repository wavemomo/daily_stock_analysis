# -*- coding: utf-8 -*-
"""Cross-user isolation coverage for the DecisionSignal API.

Decision signals are derived from analysis reports, so they follow the same
``AnalysisOwner`` contract as ``analysis_history``: every read and write is bound
to the canonical authenticated user, and another tenant's signal must be
indistinguishable from a signal that does not exist.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

try:  # pragma: no cover - import guard mirrors the other API suites
    import litellm  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    sys.modules["litellm"] = MagicMock()

from fastapi.testclient import TestClient

from api.app import create_app
from src.analysis_ownership import AnalysisOwner
from src.config import Config
from src.services.decision_signal_service import DecisionSignalService
from src.storage import DatabaseManager, MiniappUserRecord

_PERMISSIONS = (
    "decision_signals.read",
    "decision_signals.execute",
    "decision_signals.manage",
)


class DecisionSignalOwnershipTestCase(unittest.TestCase):
    """Two users must never observe or mutate each other's decision signals."""

    OWNER_A_ID = 4101
    OWNER_B_ID = 4202

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.env_path = data_dir / ".env"
        self.db_path = data_dir / "decision_signal_ownership.db"
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
        self.db = DatabaseManager.get_instance()
        with self.db.get_session() as session:
            session.add_all(
                [
                    MiniappUserRecord(id=self.OWNER_A_ID, openid="decision-signal-owner-a"),
                    MiniappUserRecord(id=self.OWNER_B_ID, openid="decision-signal-owner-b"),
                ]
            )
            session.commit()

        self.current_user_id = self.OWNER_A_ID
        self.auth_patch = patch(
            "api.middlewares.auth.WechatMiniappAuthService.authenticate_token",
            side_effect=lambda *args, **kwargs: SimpleNamespace(
                user=SimpleNamespace(id=self.current_user_id),
                roles=("admin",),
                permissions=_PERMISSIONS,
            ),
        )
        self.auth_patch.start()
        static_dir = data_dir / "empty-static"
        static_dir.mkdir()
        self.client = TestClient(
            create_app(static_dir=static_dir),
            headers={"Authorization": "Bearer decision-signal-ownership-token"},
        )

    def tearDown(self) -> None:
        self.auth_patch.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _act_as(self, user_id: int) -> None:
        self.current_user_id = user_id

    def _seed_signal(self, owner_user_id: int, *, action: str = "buy", suffix: str = "a") -> dict:
        payload = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "market": "cn",
            "source_type": "manual",
            "trace_id": f"trace-owner-{owner_user_id}-{suffix}",
            "trigger_source": "api",
            "action": action,
            "reason": "ownership regression fixture",
            "status": "active",
        }
        return DecisionSignalService().create_signal(
            payload,
            owner=AnalysisOwner.user(owner_user_id),
        )["item"]

    def test_list_only_returns_signals_owned_by_the_request_user(self) -> None:
        self._seed_signal(self.OWNER_A_ID, suffix="a")
        self._seed_signal(self.OWNER_B_ID, suffix="b")

        self._act_as(self.OWNER_A_ID)
        response = self.client.get("/api/v1/decision-signals")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(
            {item["trace_id"] for item in body["items"]},
            {f"trace-owner-{self.OWNER_A_ID}-a"},
        )

    def test_detail_of_another_users_signal_is_not_found(self) -> None:
        other = self._seed_signal(self.OWNER_B_ID, suffix="b")

        self._act_as(self.OWNER_A_ID)
        response = self.client.get(f"/api/v1/decision-signals/{other['id']}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["error"], "not_found")

    def test_status_update_cannot_reach_another_users_signal(self) -> None:
        other = self._seed_signal(self.OWNER_B_ID, suffix="b")

        self._act_as(self.OWNER_A_ID)
        response = self.client.patch(
            f"/api/v1/decision-signals/{other['id']}/status",
            json={"status": "archived"},
        )
        self.assertEqual(response.status_code, 404, response.text)

        self._act_as(self.OWNER_B_ID)
        owner_view = self.client.get(f"/api/v1/decision-signals/{other['id']}")
        self.assertEqual(owner_view.status_code, 200, owner_view.text)
        self.assertEqual(owner_view.json()["status"], "active")

    def test_feedback_cannot_be_written_on_another_users_signal(self) -> None:
        other = self._seed_signal(self.OWNER_B_ID, suffix="b")

        self._act_as(self.OWNER_A_ID)
        write = self.client.put(
            f"/api/v1/decision-signals/{other['id']}/feedback",
            json={"feedback_value": "useful"},
        )
        self.assertEqual(write.status_code, 404, write.text)

        read = self.client.get(f"/api/v1/decision-signals/{other['id']}/feedback")
        self.assertEqual(read.status_code, 404, read.text)

    def test_latest_active_lookup_is_owner_scoped(self) -> None:
        self._seed_signal(self.OWNER_B_ID, suffix="b")

        self._act_as(self.OWNER_A_ID)
        response = self.client.get("/api/v1/decision-signals/latest/600519")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 0)

        self._act_as(self.OWNER_B_ID)
        owner_response = self.client.get("/api/v1/decision-signals/latest/600519")
        self.assertEqual(owner_response.status_code, 200, owner_response.text)
        self.assertEqual(owner_response.json()["total"], 1)

    def test_outcome_stats_do_not_aggregate_across_owners(self) -> None:
        self._seed_signal(self.OWNER_B_ID, suffix="b")

        self._act_as(self.OWNER_A_ID)
        response = self.client.get("/api/v1/decision-signals/outcomes/stats")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 0)

    def test_signals_of_different_owners_are_not_deduplicated_together(self) -> None:
        """Same stock and source identity must stay two independent rows."""
        payload_kwargs = {"action": "buy", "suffix": "shared"}
        first = DecisionSignalService().create_signal(
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "cn",
                "source_type": "manual",
                "trace_id": "trace-shared-identity",
                "trigger_source": "api",
                "action": payload_kwargs["action"],
                "reason": "shared identity",
                "status": "active",
            },
            owner=AnalysisOwner.user(self.OWNER_A_ID),
        )
        second = DecisionSignalService().create_signal(
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "market": "cn",
                "source_type": "manual",
                "trace_id": "trace-shared-identity",
                "trigger_source": "api",
                "action": payload_kwargs["action"],
                "reason": "shared identity",
                "status": "active",
            },
            owner=AnalysisOwner.user(self.OWNER_B_ID),
        )
        self.assertTrue(first["created"] if isinstance(first, dict) and "created" in first else True)
        self.assertNotEqual(first["item"]["id"], second["item"]["id"])
        self.assertTrue(second["created"])

    def test_unauthenticated_request_fails_closed(self) -> None:
        self.auth_patch.stop()
        try:
            anonymous = TestClient(create_app(static_dir=Path(self.temp_dir.name) / "empty-static"))
            response = anonymous.get("/api/v1/decision-signals")
            self.assertEqual(response.status_code, 401, response.text)
        finally:
            self.auth_patch.start()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
