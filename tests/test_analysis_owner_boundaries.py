from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.analysis_ownership import AnalysisOwner
from src.config import Config
from src.repositories.analysis_repo import AnalysisRepository
from src.repositories.miniapp_user_repo import MiniappUserRepository
from src.services.history_service import HistoryService
from src.services.task_queue import AnalysisTaskQueue, DuplicateTaskError
from src.storage import DatabaseManager


class AnalysisOwnerBoundaryTestCase(unittest.TestCase):
    """Owner 值对象和 owner-bound 数据/任务边界的回归测试。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "owner_boundaries.db"
        self.env_path = self.data_dir / ".env"
        self.env_path.write_text(
            f"DATABASE_PATH={self.db_path}\nSTOCK_LIST=600519\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        AnalysisTaskQueue._instance = None
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        queue = AnalysisTaskQueue._instance
        if queue is not None:
            queue.shutdown()
        AnalysisTaskQueue._instance = None
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def test_owner_value_object_rejects_invalid_user_scope(self) -> None:
        self.assertEqual(AnalysisOwner.user(101).storage_kwargs, {
            "owner_scope": "user",
            "owner_user_id": 101,
        })
        self.assertEqual(AnalysisOwner.global_owner().key, "global")

        for invalid_id in (None, False, 0, -1):
            with self.subTest(owner_user_id=invalid_id):
                with self.assertRaises(ValueError):
                    AnalysisOwner("user", invalid_id)

        with self.assertRaises(ValueError):
            AnalysisOwner("global", 101)

    def test_owner_bound_fundamental_snapshots_are_not_cross_readable(self) -> None:
        users = MiniappUserRepository(self.db)
        owner_a = AnalysisOwner.user(
            users.upsert_user(openid="snapshot-owner-a", issuer="analysis-owner-test").id
        )
        owner_b = AnalysisOwner.user(
            users.upsert_user(openid="snapshot-owner-b", issuer="analysis-owner-test").id
        )
        repo_a = AnalysisRepository(self.db, owner=owner_a)
        repo_b = AnalysisRepository(self.db, owner=owner_b)
        global_repo = AnalysisRepository(self.db, owner=AnalysisOwner.global_owner())

        self.assertEqual(
            repo_a.save_fundamental_snapshot(
                query_id="shared-query",
                code="600519",
                payload={"owner": "a"},
            ),
            1,
        )
        self.assertEqual(
            repo_b.save_fundamental_snapshot(
                query_id="shared-query",
                code="600519",
                payload={"owner": "b"},
            ),
            1,
        )

        self.assertEqual(
            repo_a.get_latest_fundamental_snapshot(
                query_id="shared-query", code="600519"
            ),
            {"owner": "a"},
        )
        self.assertEqual(
            repo_b.get_latest_fundamental_snapshot(
                query_id="shared-query", code="600519"
            ),
            {"owner": "b"},
        )
        self.assertIsNone(
            global_repo.get_latest_fundamental_snapshot(
                query_id="shared-query", code="600519"
            )
        )

    def test_task_deduplication_is_scoped_by_owner(self) -> None:
        queue = AnalysisTaskQueue(max_workers=1)
        owner_a = AnalysisOwner.user(101)
        owner_b = AnalysisOwner.user(202)
        held_futures: list[Future] = []

        def hold_submission(*_args, **_kwargs) -> Future:
            future = Future()
            held_futures.append(future)
            return future

        # 只验证入队与 owner 去重，不启动真实行情/LLM/通知流水线。
        with patch.object(queue.executor, "submit", side_effect=hold_submission):
            task_a = queue.submit_task("600519", owner=owner_a)
            task_b = queue.submit_task("600519", owner=owner_b)
            self.assertNotEqual(task_a.task_id, task_b.task_id)

            with self.assertRaises(DuplicateTaskError):
                queue.submit_task("600519", owner=owner_a)

        for future in held_futures:
            future.cancel()

    def test_history_service_forwards_bound_owner_to_snapshot_read(self) -> None:
        db = MagicMock()
        owner = AnalysisOwner.user(303)

        HistoryService(db, owner=owner).get_latest_fundamental_snapshot(
            query_id="snapshot-query",
            stock_code="600519",
        )

        db.get_latest_fundamental_snapshot.assert_called_once_with(
            query_id="snapshot-query",
            code="600519",
            owner_scope="user",
            owner_user_id=303,
        )

    def test_task_worker_passes_task_owner_to_analysis_service(self) -> None:
        queue = AnalysisTaskQueue(max_workers=1)
        owner = AnalysisOwner.user(404)
        held_futures: list[Future] = []

        def hold_submission(*_args, **_kwargs) -> Future:
            future = Future()
            held_futures.append(future)
            return future

        try:
            with patch.object(queue.executor, "submit", side_effect=hold_submission):
                task = queue.submit_task("600519", owner=owner)

            service = MagicMock()
            service.analyze_stock.return_value = {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
            }
            with patch(
                "src.services.analysis_service.AnalysisService",
                return_value=service,
            ):
                result = queue._execute_task(
                    task.task_id,
                    task.stock_code,
                    task.report_type,
                    False,
                )

            self.assertIsNotNone(result)
            self.assertIs(service.analyze_stock.call_args.kwargs["owner"], owner)
        finally:
            for future in held_futures:
                future.cancel()
            queue.shutdown()

    def test_legacy_scope_kwargs_are_rejected_by_strict_owner_contract(self) -> None:
        owner = AnalysisOwner.user(505)

        with self.assertRaisesRegex(
            TypeError,
            r"unexpected keyword argument 'owner_scope'",
        ):
            HistoryService(
                self.db,
                owner=owner,
                owner_scope="user",
                owner_user_id=606,
            )

        queue = AnalysisTaskQueue(max_workers=1)
        try:
            with self.assertRaisesRegex(
                TypeError,
                r"unexpected keyword argument 'owner_scope'",
            ):
                queue.submit_task(
                    "600519",
                    owner=owner,
                    owner_scope="user",
                    owner_user_id=606,
                )
        finally:
            queue.shutdown()


if __name__ == "__main__":
    unittest.main()
