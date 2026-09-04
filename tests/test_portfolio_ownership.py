# -*- coding: utf-8 -*-
"""Owner-scope integration tests for portfolio resources."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

import src.auth as auth
from api.app import create_app
from src.config import Config
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.portfolio_import_service import PortfolioImportService
from src.services.portfolio_service import PortfolioNotFoundError, PortfolioService
from src.storage import DatabaseManager


def _reset_auth_globals() -> None:
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


class PortfolioOwnershipTestCase(unittest.TestCase):
    """Exercise two miniapp owners and legacy NULL ownership on real SQLite."""

    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "portfolio_ownership.db"
        self.env_path = self.data_dir / ".env"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=600519",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    "PORTFOLIO_RISK_LOOKBACK_DAYS=1",
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

        self.principals = {
            "owner-a-token": SimpleNamespace(
                user=SimpleNamespace(id=101),
                permissions=("portfolio.read", "portfolio.manage", "analysis.execute"),
            ),
            "owner-b-token": SimpleNamespace(
                user=SimpleNamespace(id=202),
                permissions=("portfolio.read", "portfolio.manage", "analysis.execute"),
            ),
        }
        self.auth_patch = patch(
            "src.services.wechat_miniapp_auth_service.WechatMiniappAuthService.authenticate_token",
            side_effect=lambda token: self.principals.get(token),
        )
        self.auth_patch.start()
        app = create_app(static_dir=self.data_dir / "empty-static")
        self.client = TestClient(app)
        self.db = DatabaseManager.get_instance()
        self.repo = PortfolioRepository(self.db)
        self.service = PortfolioService(repo=self.repo)

    def tearDown(self) -> None:
        self.auth_patch.stop()
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.temp_dir.cleanup()

    @staticmethod
    def _headers(owner: str) -> dict[str, str]:
        return {"Authorization": f"Bearer owner-{owner}-token"}

    def _create_account(self, owner: str, name: str, forged_owner: str) -> dict:
        response = self.client.post(
            "/api/v1/portfolio/accounts",
            headers=self._headers(owner),
            json={
                "name": name,
                "broker": "Demo",
                "market": "cn",
                "base_currency": "CNY",
                "owner_id": forged_owner,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _save_close(self, symbol: str, on_date: date, close: float) -> None:
        self.db.save_daily_data(
            pd.DataFrame(
                [
                    {
                        "date": on_date,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "volume": 1.0,
                        "amount": close,
                        "pct_chg": 0.0,
                    }
                ]
            ),
            code=symbol,
            data_source="portfolio-owner-test",
        )

    def test_trusted_owner_account_lists_and_legacy_compatibility(self) -> None:
        account_a = self._create_account("a", "Owner A", forged_owner="202")
        account_b = self._create_account("b", "Owner B", forged_owner="101")
        legacy = self.service.create_account(
            name="Legacy",
            broker="Demo",
            market="cn",
            base_currency="CNY",
        )

        self.assertEqual(account_a["owner_id"], "101")
        self.assertEqual(account_b["owner_id"], "202")
        self.assertIsNone(legacy["owner_id"])

        list_a = self.client.get(
            "/api/v1/portfolio/accounts",
            headers=self._headers("a"),
        )
        list_b = self.client.get(
            "/api/v1/portfolio/accounts",
            headers=self._headers("b"),
        )
        self.assertEqual([item["id"] for item in list_a.json()["accounts"]], [account_a["id"]])
        self.assertEqual([item["id"] for item in list_b.json()["accounts"]], [account_b["id"]])
        self.assertEqual(
            {item["id"] for item in self.service.list_accounts()},
            {account_a["id"], account_b["id"], legacy["id"]},
        )

        update = self.client.put(
            f"/api/v1/portfolio/accounts/{account_a['id']}",
            headers=self._headers("a"),
            json={"name": "Owner A Updated", "owner_id": "202"},
        )
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(update.json()["owner_id"], "101")

        foreign_update = self.client.put(
            f"/api/v1/portfolio/accounts/{account_a['id']}",
            headers=self._headers("b"),
            json={"name": "stolen"},
        )
        foreign_delete = self.client.delete(
            f"/api/v1/portfolio/accounts/{account_a['id']}",
            headers=self._headers("b"),
        )
        self.assertEqual(foreign_update.status_code, 404, foreign_update.text)
        self.assertEqual(foreign_delete.status_code, 404, foreign_delete.text)
        self.assertIsNotNone(self.repo.get_account(account_a["id"], owner_id="101"))

    def test_cross_owner_events_snapshot_risk_and_import_are_hidden(self) -> None:
        today = date.today()
        account_a = self._create_account("a", "Owner A", forged_owner="202")
        account_b = self._create_account("b", "Owner B", forged_owner="101")
        legacy = self.service.create_account(
            name="Legacy",
            broker="Demo",
            market="cn",
            base_currency="CNY",
        )
        self._save_close("600519", today, 110.0)

        trade_payload = {
            "account_id": account_a["id"],
            "symbol": "600519",
            "trade_date": today.isoformat(),
            "side": "buy",
            "quantity": 10,
            "price": 100,
            "market": "cn",
            "currency": "CNY",
        }
        created_trade = self.client.post(
            "/api/v1/portfolio/trades",
            headers=self._headers("a"),
            json=trade_payload,
        )
        self.assertEqual(created_trade.status_code, 200, created_trade.text)
        trade_id = created_trade.json()["id"]
        created_cash = self.client.post(
            "/api/v1/portfolio/cash-ledger",
            headers=self._headers("a"),
            json={
                "account_id": account_a["id"],
                "event_date": today.isoformat(),
                "direction": "in",
                "amount": 100,
                "currency": "CNY",
            },
        )
        created_action = self.client.post(
            "/api/v1/portfolio/corporate-actions",
            headers=self._headers("a"),
            json={
                "account_id": account_a["id"],
                "symbol": "600519",
                "effective_date": today.isoformat(),
                "action_type": "cash_dividend",
                "cash_dividend_per_share": 1,
                "market": "cn",
                "currency": "CNY",
            },
        )
        self.assertEqual(created_cash.status_code, 200, created_cash.text)
        self.assertEqual(created_action.status_code, 200, created_action.text)
        cash_id = created_cash.json()["id"]
        action_id = created_action.json()["id"]

        for path, payload in (
            ("/api/v1/portfolio/trades", {**trade_payload, "trade_uid": "foreign"}),
            (
                "/api/v1/portfolio/cash-ledger",
                {
                    "account_id": account_a["id"],
                    "event_date": today.isoformat(),
                    "direction": "in",
                    "amount": 100,
                    "currency": "CNY",
                },
            ),
            (
                "/api/v1/portfolio/corporate-actions",
                {
                    "account_id": account_a["id"],
                    "symbol": "600519",
                    "effective_date": today.isoformat(),
                    "action_type": "cash_dividend",
                    "cash_dividend_per_share": 1,
                    "market": "cn",
                    "currency": "CNY",
                },
            ),
        ):
            response = self.client.post(path, headers=self._headers("b"), json=payload)
            self.assertEqual(response.status_code, 404, response.text)

        for event_path, event_id in (
            ("trades", trade_id),
            ("cash-ledger", cash_id),
            ("corporate-actions", action_id),
        ):
            foreign_delete = self.client.delete(
                f"/api/v1/portfolio/{event_path}/{event_id}",
                headers=self._headers("b"),
            )
            self.assertEqual(foreign_delete.status_code, 404, foreign_delete.text)

        list_a = self.client.get("/api/v1/portfolio/trades", headers=self._headers("a"))
        list_b = self.client.get("/api/v1/portfolio/trades", headers=self._headers("b"))
        self.assertEqual(list_a.json()["total"], 1)
        self.assertEqual(list_b.json()["total"], 0)
        for event_path in ("cash-ledger", "corporate-actions"):
            owner_list = self.client.get(
                f"/api/v1/portfolio/{event_path}",
                headers=self._headers("a"),
            )
            foreign_list = self.client.get(
                f"/api/v1/portfolio/{event_path}",
                headers=self._headers("b"),
            )
            self.assertEqual(owner_list.json()["total"], 1)
            self.assertEqual(foreign_list.json()["total"], 0)

        snapshot_a = self.client.get(
            "/api/v1/portfolio/snapshot",
            headers=self._headers("a"),
            params={"as_of": today.isoformat(), "include_realtime": "false"},
        )
        snapshot_b_foreign = self.client.get(
            "/api/v1/portfolio/snapshot",
            headers=self._headers("b"),
            params={
                "account_id": account_a["id"],
                "as_of": today.isoformat(),
                "include_realtime": "false",
            },
        )
        self.assertEqual(snapshot_a.status_code, 200, snapshot_a.text)
        self.assertEqual(snapshot_a.json()["account_count"], 1)
        self.assertEqual(snapshot_a.json()["accounts"][0]["account_id"], account_a["id"])
        self.assertEqual(snapshot_b_foreign.status_code, 404, snapshot_b_foreign.text)

        with patch(
            "src.services.portfolio_risk_service.PortfolioRiskService._fetch_belong_boards",
            return_value=[],
        ):
            risk_a = self.client.get(
                "/api/v1/portfolio/risk",
                headers=self._headers("a"),
                params={"as_of": today.isoformat(), "include_realtime": "false"},
            )
            risk_b = self.client.get(
                "/api/v1/portfolio/risk",
                headers=self._headers("b"),
                params={"as_of": today.isoformat(), "include_realtime": "false"},
            )
        self.assertEqual(risk_a.status_code, 200, risk_a.text)
        self.assertEqual(risk_b.status_code, 200, risk_b.text)
        self.assertGreater(risk_a.json()["concentration"]["total_market_value"], 0)
        self.assertEqual(risk_b.json()["concentration"]["total_market_value"], 0)

        csv_content = (
            "成交日期,证券代码,买卖标志,成交数量,成交价格,成交编号\n"
            f"{today.isoformat()},000001,买入,1,10,owner-import-1\n"
        ).encode("utf-8")
        foreign_dry_run = self.client.post(
            "/api/v1/portfolio/imports/csv/commit",
            headers=self._headers("b"),
            data={"account_id": str(account_a["id"]), "broker": "huatai", "dry_run": "true"},
            files={"file": ("trades.csv", csv_content, "text/csv")},
        )
        self.assertEqual(foreign_dry_run.status_code, 404, foreign_dry_run.text)

        importer = PortfolioImportService(portfolio_service=self.service, repo=self.repo)
        with self.assertRaises(PortfolioNotFoundError):
            importer.commit_trade_records(
                account_id=account_a["id"],
                broker="huatai",
                records=[],
                dry_run=True,
                owner_id="202",
            )

        self.assertEqual(
            {item["id"] for item in self.service.list_accounts()},
            {account_a["id"], account_b["id"], legacy["id"]},
        )
        self.assertTrue(self.service.delete_trade_event(trade_id))


if __name__ == "__main__":
    unittest.main()
