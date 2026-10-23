"""
Regression and Contract Tests for Spot-Perp Cash & Carry Yield Engine Frontend Component.
Covers:
- Strict Isolation from Basis Stat Arb and Legacy Funding Bots
- Active Spot-Perp Position Card HTML Structure & Liquidation Cushion
- AppTest Tab Execution & Dataframe Schema Rendering
"""
import json
import tempfile
import unittest
from pathlib import Path

from frontend.components.spot_perp_cash_carry_view import (
    _render_active_spot_perp_card_html,
    get_bot_spot_perp_cash_carry_data,
    render_spot_perp_cash_carry_kpi_cards,
    render_spot_perp_cash_carry_tables,
)


class TestSpotPerpBotIsolation(unittest.TestCase):
    """Test strict isolation between Spot-Perp Cash & Carry and other strategies."""

    def test_basis_stat_arb_bot_rejected(self):
        perf = {
            "basis_stat_arb": {
                "custom_info": {
                    "strategy": "basis_stat_arb",
                    "active_positions": [],
                }
            }
        }
        is_sp, data = get_bot_spot_perp_cash_carry_data("basis-paper-001", perf)
        self.assertFalse(is_sp)
        self.assertIsNone(data)

    def test_legacy_funding_rate_arb_rejected(self):
        perf = {
            "funding_rate_arb": {
                "custom_info": {
                    "strategy": "funding_rate_arb",
                    "active_positions": [],
                }
            }
        }
        is_sp, data = get_bot_spot_perp_cash_carry_data("funding-paper-001", perf)
        self.assertFalse(is_sp)
        self.assertIsNone(data)

    def test_spot_perp_bot_matched_via_mqtt(self):
        perf = {
            "spot_perp_cash_carry": {
                "custom_info": {
                    "strategy": "spot_perp_cash_carry",
                    "is_dry_run": True,
                    "active_positions": [{"token": "ENA", "position_size": 1000}],
                    "closed_trades": [],
                }
            }
        }
        is_sp, data = get_bot_spot_perp_cash_carry_data("spot-perp-paper-001", perf)
        self.assertTrue(is_sp)
        self.assertEqual(len(data.get("active_positions", [])), 1)


class TestSpotPerpCardRendering(unittest.TestCase):
    """Verify HTML card formatting and safety cushion display."""

    def test_active_card_html_content(self):
        pos_dict = {
            "token": "SAGA",
            "amount": 2500.0,
            "position_size": 1000.0,
            "spot_entry_price": 0.40,
            "perp_entry_price": 0.402,
            "current_spot_price": 0.405,
            "current_perp_price": 0.406,
            "current_apr": 0.485,
            "settled_funding_pnl": 4.25,
            "funding_payments": [{"amount": 1.0}, {"amount": 1.5}, {"amount": 1.75}],
            "duration_seconds": 7200,
            "current_safety_cushion_pct": 0.285,
            "total_net_pnl": 5.10,
        }
        html_out = _render_active_spot_perp_card_html(pos_dict)
        self.assertIn("🪙 SAGA", html_out)
        self.assertIn("现货-永续 Cash & Carry", html_out)
        self.assertIn("48.5%", html_out)
        self.assertIn("Binance 现货腿", html_out)
        self.assertIn("Hyperliquid 永续对冲腿", html_out)
        self.assertIn("28.5%", html_out)
        self.assertIn("+$4.25", html_out)


class TestSpotPerpTablesRendering(unittest.TestCase):
    """Verify Streamlit tabs and dataframes render cleanly with AppTest."""

    def test_render_spot_perp_tables_apptest(self):
        from streamlit.testing.v1 import AppTest
        code = '''
import frontend.components.spot_perp_cash_carry_view as view
data = {
    "strategy": "spot_perp_cash_carry",
    "is_dry_run": True,
    "max_concurrent_tokens": 3,
    "active_positions": [
        {
            "token": "ENA",
            "amount": 2000.0,
            "position_size": 1000.0,
            "spot_entry_price": 0.50,
            "perp_entry_price": 0.501,
            "current_spot_price": 0.502,
            "current_perp_price": 0.503,
            "current_apr": 0.35,
            "settled_funding_pnl": 2.50,
            "funding_payments": [{"amount": 1.25}, {"amount": 1.25}],
            "duration_seconds": 3600,
            "current_safety_cushion_pct": 0.30,
            "total_net_pnl": 3.10,
        }
    ],
    "market_candidates": [
        {
            "token": "ENA",
            "annual_apr": 0.35,
            "hourly_rate": 0.00004,
            "spot_price": 0.50,
            "perp_price": 0.501,
            "basis_pct": 0.002,
            "payback_hours": 6.5,
            "depth_spot": 25000.0,
            "depth_perp": 18000.0,
            "status": "In Position"
        },
        {
            "token": "W",
            "annual_apr": 0.42,
            "hourly_rate": 0.00005,
            "spot_price": 0.25,
            "perp_price": 0.251,
            "basis_pct": 0.004,
            "payback_hours": 5.2,
            "depth_spot": 32000.0,
            "depth_perp": 22000.0,
            "status": "Eligible"
        }
    ],
    "closed_trades": [
        {
            "token": "ALT",
            "position_size": 1000.0,
            "spot_close_price": 0.12,
            "perp_close_price": 0.12,
            "spot_pnl": 1.20,
            "perp_pnl": -0.80,
            "total_friction_cost": 0.90,
            "settled_funding_pnl": 8.50,
            "settlement_count": 6,
            "total_net_pnl": 8.00,
            "roe_pct": 0.06,
            "duration_seconds": 21600,
            "close_reason": "Funding Yield Decay Exit",
        }
    ]
}
view.render_spot_perp_cash_carry_kpi_cards(data)
view.render_spot_perp_cash_carry_tables(data, "test-spot-perp-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        self.assertTrue(len(app.tabs) >= 3)
        self.assertTrue(len(app.dataframe) >= 2)


if __name__ == "__main__":
    unittest.main()
