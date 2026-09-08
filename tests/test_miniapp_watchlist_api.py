# -*- coding: utf-8 -*-
"""按用户维度个人自选股：服务、端点 owner 隔离与 RBAC 映射回归。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from api.v1.endpoints.miniapp_watchlist import (
    add_watchlist,
    list_watchlist,
    remove_watchlist,
)
from api.v1.schemas.miniapp import MiniappWatchlistMutateRequest
from src.config import Config
from src.services.miniapp_watchlist_service import (
    MiniappWatchlistService,
    WatchlistValidationError,
)
from src.services.rbac_service import MEMBER_PERMISSIONS, RbacService
from src.storage import DatabaseManager, UserRecord


def _principal(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(id=user_id))


class MiniappWatchlistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"
        self.db_path = Path(self.temp_dir.name) / "watchlist_test.db"
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
        self.service = MiniappWatchlistService()
        # miniapp_watchlist.user_id 外键指向 users.id，需先创建真实用户。
        self.uid = {}
        for label in (1, 2, 11, 12):
            self.uid[label] = self._create_user()

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

    # ---- 服务层 ----
    def test_add_list_and_per_user_isolation(self) -> None:
        self.service.add(user_id=self.uid[1], stock_code="600519", stock_name="贵州茅台")
        self.service.add(user_id=self.uid[1], stock_code="AAPL")
        self.service.add(user_id=self.uid[2], stock_code="00700")

        u1 = self.service.list(user_id=self.uid[1])
        u2 = self.service.list(user_id=self.uid[2])
        self.assertEqual(set(u1["stock_codes"]), {"600519", "AAPL"})
        self.assertEqual(u1["total"], 2)
        # 跨用户隔离：用户 2 看不到用户 1 的自选
        self.assertEqual(u2["stock_codes"], ["00700"])

    def test_add_deduplicates_hk_variants(self) -> None:
        self.service.add(user_id=self.uid[1], stock_code="00700")
        self.service.add(user_id=self.uid[1], stock_code="HK00700")
        result = self.service.list(user_id=self.uid[1])
        self.assertEqual(result["total"], 1)

    def test_remove_is_owner_scoped(self) -> None:
        self.service.add(user_id=self.uid[1], stock_code="600519")
        self.service.add(user_id=self.uid[2], stock_code="600519")
        # 用户 2 删除不应影响用户 1
        self.assertTrue(self.service.remove(user_id=self.uid[2], stock_code="600519"))
        self.assertEqual(self.service.list(user_id=self.uid[1])["total"], 1)
        self.assertEqual(self.service.list(user_id=self.uid[2])["total"], 0)

    def test_invalid_code_rejected(self) -> None:
        with self.assertRaises(WatchlistValidationError):
            self.service.add(user_id=self.uid[1], stock_code="not a code!!")

    # ---- 端点 owner 隔离（直接调用，principal 由测试注入）----
    def test_endpoint_scopes_to_principal(self) -> None:
        add_watchlist(
            MiniappWatchlistMutateRequest(stock_code="600519", stock_name="贵州茅台"),
            principal=_principal(self.uid[11]),
        )
        listed = list_watchlist(principal=_principal(self.uid[11]))
        self.assertEqual(listed.total, 1)
        self.assertEqual(listed.items[0].stock_code, "600519")
        # 另一个用户读到空
        self.assertEqual(list_watchlist(principal=_principal(self.uid[12])).total, 0)
        # 删除返回最新列表
        removed = remove_watchlist(
            MiniappWatchlistMutateRequest(stock_code="600519"),
            principal=_principal(self.uid[11]),
        )
        self.assertEqual(removed.total, 0)

    # ---- RBAC ----
    def test_member_has_watchlist_permissions(self) -> None:
        self.assertIn("watchlist.read", MEMBER_PERMISSIONS)
        self.assertIn("watchlist.manage", MEMBER_PERMISSIONS)

    def test_permission_for_request_maps_watchlist_routes(self) -> None:
        self.assertEqual(
            RbacService.permission_for_request("/api/v1/miniapp/watchlist", "GET"),
            "watchlist.read",
        )
        self.assertEqual(
            RbacService.permission_for_request("/api/v1/miniapp/watchlist/add", "POST"),
            "watchlist.manage",
        )
        self.assertEqual(
            RbacService.permission_for_request("/api/v1/miniapp/watchlist/remove", "POST"),
            "watchlist.manage",
        )


if __name__ == "__main__":
    unittest.main()
