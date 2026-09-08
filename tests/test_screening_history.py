# -*- coding: utf-8 -*-
"""Persistence and DSA hand-off coverage for the built-in screening engine."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text

from src.analysis_ownership import AnalysisOwner, GLOBAL_ANALYSIS_OWNER
from src.config import Config
from src.services.screening.strategy import list_strategies
from src.services.screening_service import ScreeningService, _build_dsa_candidate_context
from src.storage import DatabaseManager, MiniappUserRecord


class ScreeningHistoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        DatabaseManager.reset_instance()
        self.db = DatabaseManager(db_url="sqlite:///:memory:")
        with self.db.get_session() as session:
            session.add_all(
                [
                    MiniappUserRecord(id=101, openid="screening-history-owner-a"),
                    MiniappUserRecord(id=202, openid="screening-history-owner-b"),
                ]
            )
            session.commit()
        self.config = Config(screening_enabled=True)
        self.owner_a = AnalysisOwner.user(101)
        self.owner_b = AnalysisOwner.user(202)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()

    @staticmethod
    def _payload(run_id: str, *, candidate_count: int = 1) -> dict:
        return {
            "run_id": run_id,
            "strategy": "dual_low",
            "market": "cn",
            "snapshot_source": "sina",
            "snapshot_count": 5000,
            "after_filter_count": 12,
            "candidate_count": candidate_count,
            "llm_ranked": True,
            "daily_enriched": False,
            "source_errors": ["efinance: request timed out"],
            "warnings": ["Snapshot source fallback: efinance: request timed out"],
            "candidates": [
                {
                    "rank": 1,
                    "code": "600519",
                    "name": "贵州茅台",
                    "final_score": 88.5,
                    "ranking_reason": "低估值与流动性通过",
                }
            ],
        }

    def test_completed_screen_run_is_persisted_and_loaded(self) -> None:
        raw_result = self._payload("screen-run-1")
        service = ScreeningService(self.config, db_manager=self.db)

        with (
            patch(
                "src.services.screening_service._get_screening_status_snapshot",
                return_value=({}, True, None),
            ),
            patch(
                "src.services.screening_service._call_screening_screen",
                return_value=raw_result,
            ),
            patch(
                "src.services.screening_service._enrich_candidates_with_dsa",
                side_effect=lambda candidates: (
                    candidates,
                    {
                        "enabled": True,
                        "requested_count": 1,
                        "enriched_count": 0,
                        "warnings": [],
                    },
                ),
            ),
        ):
            response = service.screen(
                strategy="dual_low",
                market="cn",
                max_results=3,
                owner=self.owner_a,
            )

        self.assertEqual(response["run_id"], "screen-run-1")
        stored = self.db.get_screening_run("screen-run-1", owner=self.owner_a)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["candidate_count"], 1)
        self.assertEqual(stored["result"]["candidates"][0]["code"], "600519")
        self.assertNotIn("owner_scope", stored)
        self.assertNotIn("owner_user_id", stored)

        history = service.history(
            owner=self.owner_a,
            limit=10,
            strategy="dual_low",
            market="cn",
        )
        self.assertEqual(history["run_count"], 1)
        self.assertNotIn("result", history["runs"][0])

    def test_screen_maps_pipeline_degradation_into_warning_contract(self) -> None:
        raw_result = self._payload("screen-run-degradation")
        raw_result.update(
            {
                "llm_ranked": False,
                "degradation": [
                    "Snapshot source fallback: efinance: request timed out",
                    "LLM ranking failed: fell back to screen_score",
                ],
            }
        )
        service = ScreeningService(self.config, db_manager=self.db)

        with (
            patch(
                "src.services.screening_service._get_screening_status_snapshot",
                return_value=({}, True, None),
            ),
            patch(
                "src.services.screening_service._call_screening_screen",
                return_value=raw_result,
            ),
            patch(
                "src.services.screening_service._enrich_candidates_with_dsa",
                side_effect=lambda candidates: (
                    candidates,
                    {
                        "enabled": True,
                        "requested_count": 1,
                        "enriched_count": 0,
                        "warnings": [],
                    },
                ),
            ),
        ):
            response = service.screen(
                strategy="dual_low",
                market="cn",
                max_results=3,
                owner=self.owner_a,
            )

        expected_warnings = [
            "Snapshot source fallback: efinance: request timed out",
            "LLM ranking failed: fell back to screen_score",
        ]
        self.assertEqual(response["warnings"], expected_warnings)
        self.assertEqual(response["degradation"], expected_warnings)
        stored = self.db.get_screening_run("screen-run-degradation", owner=self.owner_a)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored["warnings"], expected_warnings)
        self.assertEqual(stored["result"]["warnings"], expected_warnings)
        self.assertEqual(stored["result"]["degradation"], expected_warnings)

        history = service.history(owner=self.owner_a, limit=10, strategy="dual_low", market="cn")
        self.assertEqual(history["runs"][0]["warnings"], expected_warnings)

        source_history = service.source_history(owner=self.owner_a, limit=10)
        self.assertEqual(source_history["fallback_runs"], 1)

    def test_screening_runs_are_strictly_isolated_by_owner(self) -> None:
        self.db.save_screening_run(self._payload("owner-a-run"), owner=self.owner_a)
        self.db.save_screening_run(self._payload("owner-b-run"), owner=self.owner_b)
        self.db.save_screening_run(self._payload("global-run"), owner=GLOBAL_ANALYSIS_OWNER)

        self.assertEqual(
            [row["run_id"] for row in self.db.list_screening_runs(owner=self.owner_a, limit=10)],
            ["owner-a-run"],
        )
        self.assertEqual(
            [row["run_id"] for row in self.db.list_screening_runs(owner=self.owner_b, limit=10)],
            ["owner-b-run"],
        )
        self.assertEqual(
            [row["run_id"] for row in self.db.list_screening_runs(owner=GLOBAL_ANALYSIS_OWNER, limit=10)],
            ["global-run"],
        )
        self.assertIsNone(self.db.get_screening_run("owner-b-run", owner=self.owner_a))
        self.assertIsNone(self.db.get_screening_run("global-run", owner=self.owner_a))
        self.assertIsNone(self.db.get_screening_run("owner-a-run", owner=GLOBAL_ANALYSIS_OWNER))

        service = ScreeningService(self.config, db_manager=self.db)
        self.assertEqual(service.history(owner=self.owner_a, limit=10)["run_count"], 1)
        self.assertEqual(service.source_history(owner=self.owner_b, limit=10)["runs_analyzed"], 1)
        with self.assertRaises(HTTPException) as context:
            service.history_detail("owner-a-run", owner=self.owner_b)
        self.assertEqual(context.exception.status_code, 404)

    def test_cross_owner_run_id_collision_fails_closed_without_overwrite(self) -> None:
        self.db.save_screening_run(self._payload("shared-run", candidate_count=1), owner=self.owner_a)

        with self.assertRaisesRegex(ValueError, "belongs to a different owner"):
            self.db.save_screening_run(self._payload("shared-run", candidate_count=9), owner=self.owner_b)

        owner_a_run = self.db.get_screening_run("shared-run", owner=self.owner_a)
        self.assertIsNotNone(owner_a_run)
        assert owner_a_run is not None
        self.assertEqual(owner_a_run["candidate_count"], 1)
        self.assertIsNone(self.db.get_screening_run("shared-run", owner=self.owner_b))

    def test_legacy_null_owner_row_is_invisible_to_every_scope(self) -> None:
        with self.db.get_session() as session:
            session.execute(
                text(
                    "INSERT INTO screening_runs "
                    "(run_id, strategy, market, candidate_count, result_json, owner_scope, owner_user_id, created_at) "
                    "VALUES (:run_id, :strategy, :market, :candidate_count, :result_json, NULL, NULL, CURRENT_TIMESTAMP)"
                ),
                {
                    "run_id": "legacy-unscoped-run",
                    "strategy": "dual_low",
                    "market": "cn",
                    "candidate_count": 0,
                    "result_json": "{}",
                },
            )
            session.commit()

        for owner in (self.owner_a, self.owner_b, GLOBAL_ANALYSIS_OWNER):
            self.assertIsNone(self.db.get_screening_run("legacy-unscoped-run", owner=owner))
            self.assertEqual(self.db.list_screening_runs(owner=owner, limit=10), [])

    def test_public_screening_persistence_methods_require_an_explicit_owner(self) -> None:
        service = ScreeningService(self.config, db_manager=self.db)
        with self.assertRaises(TypeError):
            self.db.save_screening_run(self._payload("missing-owner"))
        with self.assertRaises(TypeError):
            self.db.list_screening_runs(limit=10)
        with self.assertRaises(TypeError):
            self.db.get_screening_run("missing-owner")
        with self.assertRaises(TypeError):
            service.history(limit=10)
        with self.assertRaises(TypeError):
            service.history_detail("missing-owner")
        with self.assertRaises(TypeError):
            service.source_history(limit=10)

    def test_sqlite_schema_migration_adds_owner_columns_and_index_without_backfill(self) -> None:
        DatabaseManager.reset_instance()
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "legacy-screening.db"
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE screening_runs ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "run_id VARCHAR(64) NOT NULL UNIQUE, "
                    "strategy VARCHAR(64) NOT NULL, "
                    "market VARCHAR(16) NOT NULL, "
                    "snapshot_source VARCHAR(64), "
                    "snapshot_count INTEGER, "
                    "after_filter_count INTEGER, "
                    "candidate_count INTEGER NOT NULL DEFAULT 0, "
                    "llm_ranked BOOLEAN, "
                    "daily_enriched BOOLEAN, "
                    "source_errors_json TEXT, "
                    "warnings_json TEXT, "
                    "result_json TEXT NOT NULL, "
                    "created_at DATETIME NOT NULL"
                    ")"
                )
                connection.exec_driver_sql(
                    "INSERT INTO screening_runs "
                    "(run_id, strategy, market, candidate_count, result_json, created_at) "
                    "VALUES ('legacy-run', 'dual_low', 'cn', 0, '{}', CURRENT_TIMESTAMP)"
                )
            engine.dispose()

            migrated = DatabaseManager(db_url=f"sqlite:///{db_path}")
            inspector = inspect(migrated._engine)
            self.assertTrue({"owner_scope", "owner_user_id"}.issubset(
                {column["name"] for column in inspector.get_columns("screening_runs")}
            ))
            index = next(item for item in inspector.get_indexes("screening_runs") if item["name"] == "ix_screening_run_owner_time")
            self.assertEqual(index["column_names"], ["owner_user_id", "owner_scope", "created_at"])
            self.assertIsNone(migrated.get_screening_run("legacy-run", owner=self.owner_a))
            self.assertIsNone(migrated.get_screening_run("legacy-run", owner=GLOBAL_ANALYSIS_OWNER))
            migrated._engine.dispose()
        DatabaseManager.reset_instance()

    def test_save_is_idempotent_and_source_history_aggregates_failures(self) -> None:
        payload = self._payload("screen-run-2", candidate_count=2)
        payload.update(
            {
                "strategy": "volume_breakout",
                "source_errors": ["efinance: empty response"],
                "warnings": [],
                "degradation": ["Snapshot source fallback: efinance: empty response"],
                "candidates": [],
            }
        )
        self.assertEqual(self.db.save_screening_run(payload, owner=self.owner_a), 1)
        payload["candidate_count"] = 3
        self.assertEqual(self.db.save_screening_run(payload, owner=self.owner_a), 1)

        runs = self.db.list_screening_runs(owner=self.owner_a, limit=10)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["candidate_count"], 3)
        self.assertEqual(runs[0]["warnings"], ["Snapshot source fallback: efinance: empty response"])

        source_history = ScreeningService(self.config, db_manager=self.db).source_history(
            owner=self.owner_a,
            limit=10,
        )
        self.assertEqual(source_history["runs_analyzed"], 1)
        self.assertEqual(source_history["fallback_runs"], 1)
        self.assertEqual(source_history["sources"]["sina"]["selected_runs"], 1)
        self.assertEqual(source_history["sources"]["efinance"]["error_count"], 1)

    def test_screening_strategies_declare_dsa_analysis_skill_handoffs(self) -> None:
        strategies = {item.name: item for item in list_strategies()}
        self.assertEqual(strategies["volume_breakout"].analysis_skills, ["volume_breakout"])
        self.assertEqual(strategies["capital_heat"].analysis_skills, ["hot_theme", "emotion_cycle"])

    def test_fresh_post_rank_context_includes_dsa_events(self) -> None:
        manager = Mock()
        manager.get_stock_name.return_value = "贵州茅台"
        news = {"success": True, "results": [{"title": "贵州茅台经营动态", "url": "https://example.com/news"}]}
        events = {"success": True, "results": [{"title": "贵州茅台发布年度报告", "url": "https://example.com/event"}]}
        candidate = {
            "code": "600519",
            "name": "贵州茅台",
            "dsa_context": {"quote": {"price": 1688.0}, "fundamentals": {"pe_ttm": 24.5}},
        }

        with (
            patch("src.services.screening_service._get_dsa_fetcher_manager", return_value=manager),
            patch("src.services.screening_service.search_dsa_stock_news", return_value=news),
            patch("src.services.screening_service.search_dsa_stock_events", return_value=events) as event_search,
        ):
            enriched = _build_dsa_candidate_context(candidate)

        event_search.assert_called_once_with("600519", "贵州茅台", max_results=3)
        self.assertEqual(enriched["dsa_events"][0]["title"], "贵州茅台发布年度报告")
        self.assertEqual(enriched["dsa_context"]["events"], events)
        self.assertIn("DSA事件", enriched["dsa_analysis_summary"])


if __name__ == "__main__":
    unittest.main()
