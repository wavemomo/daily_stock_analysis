# -*- coding: utf-8 -*-
"""渡劫每日心得连续打卡与月度回顾统计回归。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from api.v1.endpoints.daily_reflections import get_reflection_stats
from src.config import Config
from src.services.daily_reflection_service import DailyReflectionService
from src.storage import DatabaseManager, UserRecord


def _principal(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(id=user_id))


class DailyReflectionStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.db_path = Path(self.temp_dir.name) / "reflection_stats_test.db"
        self.env_path.write_text(
            "\n".join(["STOCK_LIST=600519", "GEMINI_API_KEY=test", f"DATABASE_PATH={self.db_path}"]) + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()
        self.service = DailyReflectionService()
        self.uid = {label: self._create_user() for label in (1, 2)}

    def _create_user(self) -> int:
        with self.db.get_session() as session:
            user = UserRecord(is_active=True)
            session.add(user)
            session.commit()
            session.refresh(user)
            return int(user.id)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    def _add(self, user_id: int, day: date) -> None:
        self.service.upsert(user_id=user_id, reflection_date=day, title="", content="x")

    def test_current_streak_counts_from_today(self) -> None:
        today = date(2026, 3, 10)
        for offset in (0, 1, 2):  # 今天、昨天、前天
            self._add(self.uid[1], today - timedelta(days=offset))
        stats = self.service.stats(user_id=self.uid[1], reference_date=today)
        self.assertTrue(stats["today_done"])
        self.assertEqual(stats["current_streak"], 3)
        self.assertEqual(stats["longest_streak"], 3)
        self.assertEqual(stats["total"], 3)

    def test_current_streak_holds_from_yesterday_when_today_missing(self) -> None:
        today = date(2026, 3, 10)
        for offset in (1, 2):  # 昨天、前天，今天未记
            self._add(self.uid[1], today - timedelta(days=offset))
        stats = self.service.stats(user_id=self.uid[1], reference_date=today)
        self.assertFalse(stats["today_done"])
        # 今天未记但昨天已记，连续未中断，从昨天起算
        self.assertEqual(stats["current_streak"], 2)

    def test_current_streak_zero_when_gap_over_a_day(self) -> None:
        today = date(2026, 3, 10)
        self._add(self.uid[1], today - timedelta(days=3))  # 仅前三天
        stats = self.service.stats(user_id=self.uid[1], reference_date=today)
        self.assertEqual(stats["current_streak"], 0)

    def test_longest_streak_across_gaps(self) -> None:
        base = date(2026, 3, 1)
        for offset in (0, 1, 2, 5, 6):  # 连续3 + 连续2
            self._add(self.uid[1], base + timedelta(days=offset))
        stats = self.service.stats(user_id=self.uid[1], reference_date=date(2026, 3, 20))
        self.assertEqual(stats["longest_streak"], 3)

    def test_month_days_and_isolation(self) -> None:
        self._add(self.uid[1], date(2026, 3, 3))
        self._add(self.uid[1], date(2026, 3, 15))
        self._add(self.uid[1], date(2026, 4, 1))  # 其他月份
        self._add(self.uid[2], date(2026, 3, 9))  # 其他用户

        march = self.service.stats(user_id=self.uid[1], reference_date=date(2026, 3, 20), month="2026-03")
        self.assertEqual(march["month"], "2026-03")
        self.assertEqual(march["month_days"], [3, 15])
        self.assertEqual(march["month_count"], 2)
        # 用户 2 看不到用户 1 的记录
        u2 = self.service.stats(user_id=self.uid[2], reference_date=date(2026, 3, 20), month="2026-03")
        self.assertEqual(u2["month_days"], [9])
        self.assertEqual(u2["total"], 1)

    def test_endpoint_scopes_to_principal(self) -> None:
        self._add(self.uid[1], date(2026, 3, 10))
        resp = get_reflection_stats(
            reference_date=date(2026, 3, 10),
            month="2026-03",
            principal=_principal(self.uid[1]),
        )
        self.assertTrue(resp.today_done)
        self.assertEqual(resp.current_streak, 1)
        self.assertEqual(resp.month_days, [10])
        # 另一个用户为空
        other = get_reflection_stats(
            reference_date=date(2026, 3, 10),
            month="2026-03",
            principal=_principal(self.uid[2]),
        )
        self.assertEqual(other.total, 0)
        self.assertFalse(other.today_done)


if __name__ == "__main__":
    unittest.main()
