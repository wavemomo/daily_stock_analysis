"""Integration coverage for owner-scoped analysis tasks and history."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
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
from src.analysis_ownership import AnalysisOwner, GLOBAL_ANALYSIS_OWNER
from src.config import Config
from src.services.task_queue import AnalysisTaskQueue
from src.storage import DatabaseManager, MiniappUserRecord


def _profile_payload() -> dict:
    return {
        "requested_code": "600519",
        "canonical_code": "600519",
        "market": "cn",
        "as_of": "2026-09-05T20:53:14+08:00",
        "quote": {"status": "unavailable", "data": None, "limitations": ["offline"]},
        "history": {"status": "unavailable", "data": [], "limitations": ["offline"]},
        "research": {
            "status": "unavailable",
            "data": {"latest_report": None, "recent_reports": [], "structured_report": None},
            "limitations": ["offline"],
        },
        "intelligence": {"status": "unavailable", "items": [], "limitations": ["offline"]},
        "portfolio": {
            "status": "unavailable",
            "data": {"held": False, "matched_markets": []},
            "limitations": ["offline"],
        },
        "monitors": {
            "status": "unavailable",
            "data": {"total_rule_count": 0, "enabled_rule_count": 0, "rule_ids": []},
            "limitations": ["offline"],
        },
        "evidence_quality": {
            "status": "unavailable",
            "blocks": {
                "quote": "unavailable",
                "history": "unavailable",
                "research": "unavailable",
                "intelligence": "unavailable",
                "portfolio": "unavailable",
                "monitors": "unavailable",
            },
            "limitations": ["offline"],
        },
    }


def _result() -> SimpleNamespace:
    return SimpleNamespace(
        code="600519",
        name="贵州茅台",
        sentiment_score=70,
        operation_advice="观望",
        trend_prediction="震荡",
        analysis_summary="仅用于归属隔离回归测试",
        get_sniper_points=lambda: {},
        to_dict=lambda: {"success": True},
    )


class AnalysisOwnershipTestCase(unittest.TestCase):
    """Exercise user-facing task/history isolation against a real SQLite database."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "analysis_ownership.db"
        self.env_path = self.data_dir / ".env"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    f"DATABASE_PATH={self.db_path}",
                ]
            ) + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        AnalysisTaskQueue._instance = None
        self.db = DatabaseManager.get_instance()
        with self.db.get_session() as session:
            session.add_all(
                [
                    MiniappUserRecord(id=101, openid="analysis-owner-a"),
                    MiniappUserRecord(id=202, openid="analysis-owner-b"),
                    MiniappUserRecord(id=303, openid="analysis-owner-admin"),
                ]
            )
            session.commit()

        self.principals = {
            "owner-a-token": SimpleNamespace(
                user=SimpleNamespace(id=101),
                roles=("member",),
                permissions=(
                    "analysis.execute",
                    "analysis.read",
                    "stocks.read",
                    "history.read",
                    "history.manage",
                    "history.delete",
                ),
            ),
            "owner-b-token": SimpleNamespace(
                user=SimpleNamespace(id=202),
                roles=("member",),
                permissions=(
                    "analysis.execute",
                    "analysis.read",
                    "stocks.read",
                    "history.read",
                    "history.manage",
                    "history.delete",
                ),
            ),
            "admin-token": SimpleNamespace(
                user=SimpleNamespace(id=303),
                roles=("admin",),
                permissions=(
                    "analysis.execute",
                    "analysis.read",
                    "stocks.read",
                    "history.read",
                    "history.manage",
                    "history.delete",
                ),
            ),
        }
        self.auth_patch = patch(
            "api.middlewares.auth.WechatMiniappAuthService.authenticate_token",
            side_effect=lambda token: self.principals.get(token),
        )
        self.auth_patch.start()
        self.client = TestClient(create_app(static_dir=self.data_dir / "empty-static"))
        self.db = DatabaseManager.get_instance()
        self.queue = AnalysisTaskQueue()

    def tearDown(self) -> None:
        self.auth_patch.stop()
        self.queue.shutdown()
        AnalysisTaskQueue._instance = None
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    @staticmethod
    def _headers(owner: str) -> dict[str, str]:
        return {"Authorization": f"Bearer owner-{owner}-token"}

    def _save_owned_history(self, *, owner_id: int, query_id: str) -> int:
        saved = self.db.save_analysis_history(
            result=_result(),
            query_id=query_id,
            report_type="detailed",
            news_content=None,
            owner_scope="user",
            owner_user_id=owner_id,
        )
        self.assertGreater(saved, 0)
        return saved

    def test_history_list_detail_and_delete_are_scoped_to_authenticated_owner(self) -> None:
        record_a = self._save_owned_history(owner_id=101, query_id="owner-a-history")
        record_b = self._save_owned_history(owner_id=202, query_id="owner-b-history")

        list_a = self.client.get("/api/v1/history", headers=self._headers("a"))
        self.assertEqual(list_a.status_code, 200, list_a.text)
        self.assertEqual([item["id"] for item in list_a.json()["items"]], [record_a])

        foreign_detail = self.client.get(
            f"/api/v1/history/{record_b}", headers=self._headers("a")
        )
        self.assertEqual(foreign_detail.status_code, 404, foreign_detail.text)

        foreign_delete = self.client.request(
            "DELETE",
            "/api/v1/history",
            headers=self._headers("a"),
            json={"record_ids": [record_b]},
        )
        self.assertEqual(foreign_delete.status_code, 200, foreign_delete.text)
        self.assertEqual(foreign_delete.json()["deleted"], 0)
        self.assertIsNotNone(self.db.get_analysis_history_by_id(record_b))

    def test_task_status_list_and_stream_snapshot_do_not_expose_another_owner(self) -> None:
        release = threading.Event()
        task = self.queue.submit_background_task(
            lambda: (release.wait(timeout=5), {"result": "done"})[1],
            stock_code="market_review",
            stock_name="大盘复盘",
            report_type="market_review",
            owner=AnalysisOwner.user(101),
        )

        foreign_status = self.client.get(
            f"/api/v1/analysis/status/{task.task_id}", headers=self._headers("b")
        )
        self.assertEqual(foreign_status.status_code, 404, foreign_status.text)

        foreign_tasks = self.client.get("/api/v1/analysis/tasks", headers=self._headers("b"))
        self.assertEqual(foreign_tasks.status_code, 200, foreign_tasks.text)
        self.assertEqual(foreign_tasks.json()["total"], 0)
        self.assertEqual(foreign_tasks.json()["tasks"], [])

        own_status = self.client.get(
            f"/api/v1/analysis/status/{task.task_id}", headers=self._headers("a")
        )
        self.assertEqual(own_status.status_code, 200, own_status.text)

        async def assert_sse_delivery_is_scoped() -> None:
            owner_events: asyncio.Queue = asyncio.Queue()
            foreign_events: asyncio.Queue = asyncio.Queue()
            self.queue.subscribe(owner_events, owner=AnalysisOwner.user(101))
            self.queue.subscribe(foreign_events, owner=AnalysisOwner.user(202))
            try:
                self.queue.update_task_progress(task.task_id, 25, "owner-only progress")
                event = await asyncio.wait_for(owner_events.get(), timeout=1)
                self.assertEqual(event["data"]["task_id"], task.task_id)
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(foreign_events.get(), timeout=0.1)
            finally:
                self.queue.unsubscribe(owner_events)
                self.queue.unsubscribe(foreign_events)

        asyncio.run(assert_sse_delivery_is_scoped())
        release.set()

    def test_admin_principal_cannot_read_global_or_another_owner_resources(self) -> None:
        global_record = self.db.save_analysis_history(
            result=_result(),
            query_id="global-history",
            report_type="detailed",
            news_content=None,
            **GLOBAL_ANALYSIS_OWNER.storage_kwargs,
        )
        self.assertGreater(global_record, 0)
        admin_record = self._save_owned_history(owner_id=303, query_id="admin-history")

        release = threading.Event()
        global_task = self.queue.submit_background_task(
            lambda: (release.wait(timeout=5), {"result": "done"})[1],
            stock_code="market_review",
            stock_name="大盘复盘",
            report_type="market_review",
            owner=GLOBAL_ANALYSIS_OWNER,
        )
        admin_task = self.queue.submit_background_task(
            lambda: (release.wait(timeout=5), {"result": "done"})[1],
            stock_code="market_review",
            stock_name="管理员任务",
            report_type="market_review",
            owner=AnalysisOwner.user(303),
        )
        try:
            admin_history = self.client.get("/api/v1/history", headers={"Authorization": "Bearer admin-token"})
            self.assertEqual(admin_history.status_code, 200, admin_history.text)
            self.assertEqual([item["id"] for item in admin_history.json()["items"]], [admin_record])

            self.assertEqual(
                self.client.get(
                    f"/api/v1/analysis/status/{global_task.task_id}",
                    headers={"Authorization": "Bearer admin-token"},
                ).status_code,
                404,
            )
            self.assertEqual(
                self.client.get(
                    f"/api/v1/analysis/status/{admin_task.task_id}",
                    headers={"Authorization": "Bearer admin-token"},
                ).status_code,
                200,
            )

            owner_history = self.client.get("/api/v1/history", headers=self._headers("a"))
            self.assertEqual(owner_history.status_code, 200, owner_history.text)
            self.assertEqual(owner_history.json()["items"], [])
            self.assertEqual(
                self.client.get(
                    f"/api/v1/analysis/status/{admin_task.task_id}",
                    headers=self._headers("a"),
                ).status_code,
                404,
            )
        finally:
            release.set()
    def test_stock_profile_uses_authenticated_owner_for_research_history(self) -> None:
        with patch("api.v1.endpoints.stocks.StockProfileService") as profile_service:
            profile_service.return_value.get_profile.return_value = _profile_payload()

            owner_a = self.client.get(
                "/api/v1/stocks/600519/profile", headers=self._headers("a")
            )
            owner_b = self.client.get(
                "/api/v1/stocks/600519/profile", headers=self._headers("b")
            )

        self.assertEqual(owner_a.status_code, 200, owner_a.text)
        self.assertEqual(owner_b.status_code, 200, owner_b.text)
        history_owners = [
            call.kwargs["history_service"].owner
            for call in profile_service.call_args_list
        ]
        self.assertEqual(
            history_owners,
            [AnalysisOwner.user(101), AnalysisOwner.user(202)],
        )


if __name__ == "__main__":
    unittest.main()
