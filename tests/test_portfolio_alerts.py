# -*- coding: utf-8 -*-
"""Unit tests for P6 portfolio alert helpers."""

from __future__ import annotations

import json
import unittest
from datetime import date

from src.portfolio_ownership import (
    LEGACY_GLOBAL_PORTFOLIO_SCOPE,
    UNSET_PORTFOLIO_SCOPE,
    PortfolioScope,
)
from src.services.portfolio_alerts import (
    PortfolioRiskAlert,
    evaluate_portfolio_risk_alert,
    expand_symbol_targets,
    normalize_portfolio_alert_parameters,
)


class FakeRiskService:
    def __init__(self, report):
        self.report = report
        self.calls = []

    def get_risk_report(self, **kwargs):
        self.calls.append(kwargs)
        return self.report


class FakePortfolioService:
    def __init__(self, *, snapshot=None, accounts=None):
        self.snapshot = snapshot or {}
        self.accounts = accounts or []
        self.snapshot_calls = []
        self.list_calls = []

    def get_portfolio_snapshot(self, **kwargs):
        self.snapshot_calls.append(kwargs)
        return self.snapshot

    def list_accounts(self, include_inactive=False, portfolio_scope=UNSET_PORTFOLIO_SCOPE):
        self.list_calls.append({"include_inactive": include_inactive, "portfolio_scope": portfolio_scope})
        return self.accounts


def _risk_rule(
    alert_type: str,
    *,
    target="1",
    parameters=None,
    portfolio_scope: PortfolioScope = LEGACY_GLOBAL_PORTFOLIO_SCOPE,
) -> PortfolioRiskAlert:
    return PortfolioRiskAlert(
        target_scope="portfolio_account",
        target=target,
        alert_type=alert_type,
        parameters=parameters or {},
        portfolio_scope=portfolio_scope,
        metadata={"persisted_rule_id": 7, "effective_target": f"account:{target}"},
    )


def _risk_report():
    return {
        "as_of": "2026-05-20",
        "account_id": 1,
        "currency": "USD",
        "thresholds": {
            "concentration_alert_pct": 35.0,
            "drawdown_alert_pct": 10.0,
            "stop_loss_alert_pct": 10.0,
            "stop_loss_near_ratio": 0.8,
        },
        "stop_loss": {
            "near_alert": True,
            "triggered_count": 1,
            "near_count": 2,
            "items": [
                {"account_id": 1, "symbol": "AAPL", "loss_pct": 12.0, "is_triggered": True},
                {"account_id": 1, "symbol": "MSFT", "loss_pct": 8.5, "is_triggered": False},
            ],
        },
        "concentration": {
            "top_weight_pct": 42.5,
            "alert": True,
            "total_market_value": 10000.0,
            "top_positions": [{"symbol": "AAPL", "weight_pct": 42.5}],
        },
        "drawdown": {
            "series_points": 5,
            "current_drawdown_pct": 5.0,
            "max_drawdown_pct": 20.0,
            "alert": True,
            "fx_stale": True,
        },
    }


class PortfolioAlertsTestCase(unittest.TestCase):
    def test_normalizes_stop_loss_mode(self) -> None:
        self.assertEqual(normalize_portfolio_alert_parameters("portfolio_stop_loss", {}), {"mode": "near"})
        self.assertEqual(
            normalize_portfolio_alert_parameters("portfolio_stop_loss", {"mode": "breach"}),
            {"mode": "breach"},
        )
        with self.assertRaisesRegex(ValueError, "near or breach"):
            normalize_portfolio_alert_parameters("portfolio_stop_loss", {"mode": "bad"})

    def test_stop_loss_near_and_breach_are_account_level_triggers(self) -> None:
        report = _risk_report()
        risk_service = FakeRiskService(report)

        near = evaluate_portfolio_risk_alert(
            _risk_rule("portfolio_stop_loss", parameters={"mode": "near"}),
            risk_service=risk_service,
        )
        breach = evaluate_portfolio_risk_alert(
            _risk_rule("portfolio_stop_loss", parameters={"mode": "breach"}),
            risk_service=FakeRiskService(report),
        )

        self.assertTrue(near["triggered"])
        self.assertEqual(near["observed_value"], 12.0)
        self.assertIn("2 affected symbols", near["message"])
        self.assertTrue(breach["triggered"])
        self.assertIn("1 affected symbols", breach["message"])
        diagnostics = json.loads(near["diagnostics"])
        self.assertEqual(diagnostics["account_id"], 1)
        self.assertEqual(diagnostics["currency"], "USD")
        self.assertEqual(diagnostics["as_of"], "2026-05-20")
        self.assertEqual(diagnostics["top_affected_symbols"], ["AAPL", "MSFT"])

    def test_concentration_uses_top_weight_pct_and_owner_scope(self) -> None:
        scope = PortfolioScope.user("42")
        risk_service = FakeRiskService(_risk_report())
        result = evaluate_portfolio_risk_alert(
            _risk_rule("portfolio_concentration", portfolio_scope=scope),
            risk_service=risk_service,
        )

        self.assertTrue(result["triggered"])
        self.assertEqual(result["observed_value"], 42.5)
        self.assertEqual(result["threshold"], 35.0)
        self.assertEqual(
            risk_service.calls,
            [{"account_id": 1, "cost_method": "fifo", "portfolio_scope": scope}],
        )

    def test_drawdown_uses_risk_report_alert_and_max_drawdown(self) -> None:
        result = evaluate_portfolio_risk_alert(
            _risk_rule("portfolio_drawdown"),
            risk_service=FakeRiskService(_risk_report()),
        )

        self.assertTrue(result["triggered"])
        self.assertEqual(result["observed_value"], 20.0)
        diagnostics = json.loads(result["diagnostics"])
        self.assertEqual(diagnostics["current_drawdown_pct"], 5.0)
        self.assertEqual(diagnostics["max_drawdown_pct"], 20.0)
        self.assertTrue(diagnostics["fx_stale"])

    def test_price_stale_triggers_on_stale_or_missing_position_price(self) -> None:
        snapshot = {
            "as_of": "2026-05-20",
            "currency": "CNY",
            "fx_stale": False,
            "accounts": [
                {
                    "account_id": 3,
                    "positions": [
                        {"symbol": "600519", "price_stale": True, "price_available": True},
                        {"symbol": "000001", "price_stale": True, "price_available": False},
                    ],
                }
            ],
        }
        scope = PortfolioScope.user("42")
        portfolio_service = FakePortfolioService(snapshot=snapshot)
        result = evaluate_portfolio_risk_alert(
            _risk_rule("portfolio_price_stale", target="3", portfolio_scope=scope),
            portfolio_service=portfolio_service,
        )

        self.assertTrue(result["triggered"])
        self.assertEqual(result["observed_value"], 2.0)
        self.assertEqual(
            portfolio_service.snapshot_calls,
            [{"account_id": 3, "cost_method": "fifo", "portfolio_scope": scope}],
        )
        diagnostics = json.loads(result["diagnostics"])
        self.assertEqual(diagnostics["account_id"], 3)
        self.assertTrue(diagnostics["price_stale"])
        self.assertFalse(diagnostics["data_available"])
        self.assertEqual(diagnostics["top_affected_symbols"], ["600519", "000001"])

    def test_portfolio_holdings_expansion_deduplicates_symbols_and_caps(self) -> None:
        snapshot = {
            "accounts": [
                {"positions": [{"symbol": "aapl", "quantity": 2}, {"symbol": "AAPL", "quantity": 1}]},
                {"positions": [{"symbol": "hk00700", "quantity": 3}, {"symbol": "ZERO", "quantity": 0}]},
            ]
        }

        scope = PortfolioScope.user("42")
        portfolio_service = FakePortfolioService(snapshot=snapshot)
        targets, overflow = expand_symbol_targets(
            target_scope="portfolio_holdings",
            target="all",
            config=None,
            portfolio_service=portfolio_service,
            portfolio_scope=scope,
        )

        self.assertEqual([item.symbol for item in targets], ["AAPL", "HK00700"])
        self.assertEqual(overflow, 0)
        self.assertEqual(
            portfolio_service.snapshot_calls,
            [{"account_id": None, "cost_method": "fifo", "portfolio_scope": scope}],
        )

    def test_portfolio_holdings_expansion_preserves_exchange_identity_and_dedupes_equivalent_formats(self) -> None:
        snapshot = {
            "accounts": [
                {
                    "positions": [
                        {"symbol": "SH000001", "quantity": 2},
                        {"symbol": "000001.SH", "quantity": 2},
                        {"symbol": "SZ000001", "quantity": 1},
                        {"symbol": "000001.SZ", "quantity": 1},
                        {"symbol": "000001", "quantity": 3},
                        {"symbol": "600519.SH", "quantity": 4},
                        {"symbol": "SH600519", "quantity": 4},
                    ]
                },
            ]
        }

        portfolio_service = FakePortfolioService(snapshot=snapshot)
        targets, overflow = expand_symbol_targets(
            target_scope="portfolio_holdings",
            target="all",
            config=None,
            portfolio_service=portfolio_service,
            portfolio_scope=LEGACY_GLOBAL_PORTFOLIO_SCOPE,
        )

        self.assertEqual([item.symbol for item in targets], ["SH000001", "SZ000001", "000001", "SH600519"])
        self.assertEqual(overflow, 0)
        self.assertEqual(
            portfolio_service.snapshot_calls,
            [
                {
                    "account_id": None,
                    "cost_method": "fifo",
                    "portfolio_scope": LEGACY_GLOBAL_PORTFOLIO_SCOPE,
                }
            ],
        )

    def test_watchlist_expansion_refreshes_stock_list(self) -> None:
        class Config:
            stock_list = ["600519", "600519", "aapl"]

            def __init__(self):
                self.refreshed = False

            def refresh_stock_list(self):
                self.refreshed = True
                self.stock_list = ["000001", "000001", "hk00700"]

        config = Config()
        targets, overflow = expand_symbol_targets(
            target_scope="watchlist",
            target="default",
            config=config,
        )

        self.assertTrue(config.refreshed)
        self.assertEqual([item.symbol for item in targets], ["000001", "HK00700"])
        self.assertEqual(overflow, 0)


if __name__ == "__main__":
    unittest.main()
