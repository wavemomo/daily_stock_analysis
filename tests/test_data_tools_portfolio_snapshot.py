# -*- coding: utf-8 -*-
"""Contract tests for get_portfolio_snapshot agent tool."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.agent.tool_surface import ToolSurface
from src.agent.tools.data_tools import (
    _handle_get_portfolio_snapshot,
    get_portfolio_snapshot_tool,
)
from src.agent.tools.execution import (
    ToolAccessContext,
    bind_tool_execution_context,
    reset_tool_execution_context,
)
from src.agent.tools.registry import ToolRegistry
from src.portfolio_ownership import LEGACY_GLOBAL_PORTFOLIO_SCOPE, PortfolioScope


class _FakePortfolioService:
    calls = []

    def get_portfolio_snapshot(self, **kwargs):
        type(self).calls.append(kwargs)
        return {
            "as_of": "2026-03-15",
            "cost_method": "fifo",
            "currency": "CNY",
            "account_count": 1,
            "total_cash": 10000.0,
            "total_market_value": 50000.0,
            "total_equity": 60000.0,
            "realized_pnl": 1200.0,
            "unrealized_pnl": 800.0,
            "fx_stale": False,
            "accounts": [
                {
                    "account_id": 1,
                    "account_name": "Main",
                    "market": "cn",
                    "base_currency": "CNY",
                    "total_equity": 60000.0,
                    "total_market_value": 50000.0,
                    "total_cash": 10000.0,
                    "realized_pnl": 1200.0,
                    "unrealized_pnl": 800.0,
                    "fx_stale": False,
                    "positions": [
                        {
                            "symbol": "600519",
                            "market": "cn",
                            "currency": "CNY",
                            "quantity": 10.0,
                            "avg_cost": 1500.0,
                            "last_price": 1600.0,
                            "market_value_base": 16000.0,
                        },
                        {
                            "symbol": "000001",
                            "market": "cn",
                            "currency": "CNY",
                            "quantity": 100.0,
                            "avg_cost": 10.0,
                            "last_price": 11.0,
                            "market_value_base": 1100.0,
                        },
                    ],
                }
            ],
        }


class _FakeRiskService:
    calls = []

    def __init__(self, **_kwargs):
        pass

    def get_risk_report(self, **kwargs):
        type(self).calls.append(kwargs)
        return {
            "as_of": "2026-03-15",
            "currency": "CNY",
            "cost_method": "fifo",
            "thresholds": {"concentration_alert_pct": 35.0},
            "concentration": {
                "alert": True,
                "top_weight_pct": 40.12,
                "top_positions": [
                    {"symbol": "600519", "weight_pct": 40.12},
                    {"symbol": "000001", "weight_pct": 12.3},
                ],
            },
            "drawdown": {
                "alert": False,
                "max_drawdown_pct": 8.7,
                "current_drawdown_pct": 3.2,
                "fx_stale": False,
            },
            "stop_loss": {
                "near_alert": True,
                "triggered_count": 1,
                "near_count": 2,
                "items": [{"symbol": "600519", "loss_pct": 9.8}],
            },
        }


class TestGetPortfolioSnapshotTool(unittest.TestCase):
    def setUp(self) -> None:
        _FakePortfolioService.calls = []
        _FakeRiskService.calls = []

    def _execute_with_scope(self, portfolio_scope, **kwargs):
        token = bind_tool_execution_context(
            ToolAccessContext(portfolio_scope=portfolio_scope)
        )
        try:
            return _handle_get_portfolio_snapshot(**kwargs)
        finally:
            reset_tool_execution_context(token)

    def test_direct_handler_without_context_fails_closed(self) -> None:
        self.assertEqual(
            _handle_get_portfolio_snapshot(account_id=1),
            {
                "status": "not_authorized",
                "error": "portfolio access scope is required",
            },
        )

    @patch("src.services.portfolio_service.PortfolioService", _FakePortfolioService)
    @patch("src.services.portfolio_risk_service.PortfolioRiskService", _FakeRiskService)
    def test_trusted_context_returns_compact_snapshot_and_risk(self) -> None:
        result = self._execute_with_scope(LEGACY_GLOBAL_PORTFOLIO_SCOPE, account_id=1)
        self.assertEqual(result["status"], "ok")
        self.assertIn("snapshot", result)
        self.assertIn("risk", result)
        self.assertEqual(result["risk"]["status"], "ok")

        account = result["snapshot"]["accounts"][0]
        self.assertIn("top_positions", account)
        self.assertNotIn("positions", account)
        self.assertEqual(account["position_count"], 2)
        self.assertEqual(account["top_positions"][0]["symbol"], "600519")
        self.assertEqual(
            _FakePortfolioService.calls[0]["portfolio_scope"],
            LEGACY_GLOBAL_PORTFOLIO_SCOPE,
        )
        self.assertEqual(
            _FakeRiskService.calls[0]["portfolio_scope"],
            LEGACY_GLOBAL_PORTFOLIO_SCOPE,
        )

    @patch("src.services.portfolio_service.PortfolioService", _FakePortfolioService)
    @patch("src.services.portfolio_risk_service.PortfolioRiskService", _FakeRiskService)
    def test_tool_surface_uses_trusted_scope_and_rejects_spoofed_argument(self) -> None:
        registry = ToolRegistry()
        registry.register(get_portfolio_snapshot_tool)
        surface = ToolSurface(registry)

        result = surface.execute_tool(
            "get_portfolio_snapshot",
            {"account_id": 1},
            ToolAccessContext(
                resource_owner_id="42",
                portfolio_scope=PortfolioScope.user("42"),
            ),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            _FakePortfolioService.calls[0]["portfolio_scope"],
            PortfolioScope.user("42"),
        )
        self.assertEqual(
            _FakeRiskService.calls[0]["portfolio_scope"],
            PortfolioScope.user("42"),
        )

        spoofed = surface.execute_tool(
            "get_portfolio_snapshot",
            {"account_id": 1, "owner_id": "attacker"},
            ToolAccessContext(
                resource_owner_id="42",
                portfolio_scope=PortfolioScope.user("42"),
            ),
        )
        self.assertFalse(spoofed["ok"])
        self.assertEqual(spoofed["error"]["code"], "invalid_arguments")

    @patch("src.services.portfolio_service.PortfolioService", _FakePortfolioService)
    @patch("src.services.portfolio_risk_service.PortfolioRiskService", _FakeRiskService)
    def test_include_positions_and_disable_risk(self) -> None:
        result = self._execute_with_scope(
            PortfolioScope.user("77"),
            account_id=1,
            include_positions=True,
            include_risk=False,
            as_of="2026-03-15",
        )
        self.assertEqual(result["status"], "ok")
        account = result["snapshot"]["accounts"][0]
        self.assertIn("positions", account)
        self.assertNotIn("risk", result)

        invalid = self._execute_with_scope(
            PortfolioScope.user("77"),
            as_of="2026/03/15",
        )
        self.assertIn("error", invalid)
        self.assertIn("YYYY-MM-DD", invalid["error"])


if __name__ == "__main__":
    unittest.main()
