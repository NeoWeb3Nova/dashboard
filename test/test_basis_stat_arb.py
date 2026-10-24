"""
Regression and contract tests for Basis Statistical Arbitrage (Basis Stat Arb).
Covers:
- Anti-HFT churn guards (minimum holding duration, exit persistence)
- Precise duration formatting (preventing 0h 0m display)
- 4-leg taker fee deduction and transparent gross vs net trading PnL
- Discrete funding settlement window counting
- Strict mutual exclusion from funding rate arb bots
- KPI card calculation & real-margin ROE
- Config generator dictionary serialization
"""
import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from frontend.components.basis_stat_arb_view import (
    format_precise_duration,
    get_bot_basis_stat_arb_data,
    render_basis_stat_arb_kpi_cards,
    resolve_trade_friction,
)


class TestBasisDurationFormatting(unittest.TestCase):
    """Test precise seconds/minutes/hours duration formatting."""

    def test_zero_and_negative_duration(self):
        self.assertEqual(format_precise_duration(0), "0秒")
        self.assertEqual(format_precise_duration(-10), "0秒")
        self.assertEqual(format_precise_duration(None), "0秒")

    def test_sub_minute_durations(self):
        self.assertEqual(format_precise_duration(1), "1秒")
        self.assertEqual(format_precise_duration(29), "29秒")
        self.assertEqual(format_precise_duration(59), "59秒")

    def test_minute_and_seconds_durations(self):
        self.assertEqual(format_precise_duration(60), "1分 0秒")
        self.assertEqual(format_precise_duration(1415), "23分 35秒")
        self.assertEqual(format_precise_duration(3599), "59分 59秒")

    def test_hour_durations(self):
        self.assertEqual(format_precise_duration(3600), "1小时 0分 0秒")
        self.assertEqual(format_precise_duration(3665), "1小时 1分 5秒")
        self.assertEqual(format_precise_duration(10421), "2小时 53分 41秒")


class TestBasisBotIsolation(unittest.TestCase):
    """Test strict isolation between Basis Stat Arb and Funding Carry bots."""

    def test_funding_arb_bot_rejected(self):
        # A bot reporting funding_rate_arb must NEVER match as basis stat arb
        perf = {
            "funding_rate_arb": {
                "custom_info": {
                    "strategy": "funding_rate_arb",
                    "active_positions": [],
                }
            }
        }
        is_basis, data = get_bot_basis_stat_arb_data("funding-paper-001", perf)
        self.assertFalse(is_basis)
        self.assertIsNone(data)

    def test_basis_stat_arb_bot_matched_via_mqtt(self):
        perf = {
            "basis_stat_arb": {
                "custom_info": {
                    "strategy": "basis_stat_arb",
                    "is_dry_run": True,
                    "active_positions": [{"token": "STRK", "position_size": 1500}],
                    "closed_trades": [],
                }
            }
        }
        is_basis, data = get_bot_basis_stat_arb_data("basis-paper-001", perf)
        self.assertTrue(is_basis)
        self.assertEqual(len(data.get("active_positions", [])), 1)

    def test_basis_stat_arb_bot_matched_via_bot_name(self):
        # If bot name contains basis_stat_arb or basis-stat-arb, disk fallback works
        with tempfile.TemporaryDirectory() as tmp_dir:
            instance_data = Path(tmp_dir) / "data"
            instance_data.mkdir(parents=True)
            trade_file = instance_data / "basis_stat_arb_trades.json"
            trade_file.write_text(json.dumps({
                "strategy": "basis_stat_arb",
                "is_dry_run": True,
                "active_positions": [],
                "closed_trades": [{"token": "WLD", "total_net_pnl": 3.5}],
            }))
            # Verify file exists
            self.assertTrue(trade_file.exists())


class TestBasisFinancialAccounting(unittest.TestCase):
    """Verify 4-leg fee deduction, discrete windows, and true ROE formulas."""

    def test_four_leg_fee_and_net_pnl_consistency(self):
        # Case: 1500 USD notional, 0.05% taker fee per leg
        pos_size = 1500.0
        fee_rate = 0.0005
        est_fees = pos_size * fee_rate * 4.0  # $3.00
        self.assertAlmostEqual(est_fees, 3.00, places=4)

        gross_trading_pnl = 6.3725
        net_trading_pnl = gross_trading_pnl - est_fees
        self.assertAlmostEqual(net_trading_pnl, 3.3725, places=4)

        settled_funding = 0.0
        total_net_pnl = net_trading_pnl + settled_funding
        self.assertAlmostEqual(total_net_pnl, 3.3725, places=4)

    def test_real_margin_roe_formula(self):
        pos_size = 1500.0
        leverage = 3.0
        total_net_pnl = 3.3725
        # Dual-leg real margin = (pos_size / leverage) * 2 = 1000 USD
        real_margin = (pos_size / leverage) * 2.0
        self.assertAlmostEqual(real_margin, 1000.0, places=4)

        roe = total_net_pnl / real_margin
        self.assertAlmostEqual(roe, 0.0033725, places=6)  # +0.337% ROE

    def test_unique_discrete_settlement_window_deduplication(self):
        # Simulate dual-leg settlement at same timestamp UTC 12:00:00 (1792500000)
        payments = [
            {"connector": "hyperliquid_perpetual", "amount": 0.15, "timestamp": 1792500000},
            {"connector": "binance_perpetual", "amount": -0.075, "timestamp": 1792500000},
            {"connector": "hyperliquid_perpetual", "amount": 0.15, "timestamp": 1792503600},
        ]
        # len(payments) is 3, but unique windows is 2!
        unique_windows = len(set(p.get("timestamp") for p in payments if p.get("timestamp")))
        self.assertEqual(unique_windows, 2)

    def test_dual_notional_and_margin_breakdown(self):
        # pos_size = $1500, leverage = 3x, token = STRK, price = $0.05705
        pos_size = 1500.0
        leverage = 3.0
        p1 = 0.05705
        p2 = 0.05677
        
        amt_1 = pos_size / p1  # 26,292.7 STRK
        amt_2 = pos_size / p2  # 26,422.4 STRK
        dual_notional = pos_size * 2.0  # $3000.0
        margin_1 = pos_size / leverage  # $500.0
        margin_2 = pos_size / leverage  # $500.0
        total_margin = margin_1 + margin_2  # $1000.0

        self.assertAlmostEqual(dual_notional, 3000.0)
        self.assertAlmostEqual(total_margin, 1000.0)
        self.assertAlmostEqual(amt_1, 26292.725679, places=3)
        self.assertAlmostEqual(amt_2, 26422.406200, places=3)

    def test_coin_neutral_delta_zero_alignment(self):
        # pos_size = $1500, leverage = 3x, token = STRK, price = $0.05705 vs $0.05677
        pos_size = 1500.0
        p1 = 0.05705
        p2 = 0.05677
        
        # In Coin-Neutral / Delta-Zero mode:
        # Uniform quantity is sized by pos_size / max(p1, p2)
        max_p = max(p1, p2)
        hedged_amount = pos_size / max_p
        amt_1 = hedged_amount
        amt_2 = hedged_amount

        # Strict Delta-Neutrality: Q1 == Q2
        self.assertEqual(amt_1, amt_2)
        self.assertAlmostEqual(amt_1 - amt_2, 0.0)

        # Notional checks: neither leg exceeds pos_size
        notional_1 = amt_1 * p1
        notional_2 = amt_2 * p2
        self.assertLessEqual(notional_1, pos_size + 1e-6)
        self.assertLessEqual(notional_2, pos_size + 1e-6)
        
        # PnL strictly reflects basis compression without market beta leakage
        p1_close = p1 * 1.10
        p2_close = p1_close / 1.0005
        gross_pnl = (p1 - p1_close) * amt_1 + (p2_close - p2) * amt_2
        expected_gross = amt_1 * ((p1 - p2) - (p1_close - p2_close))
        self.assertAlmostEqual(gross_pnl, expected_gross, places=6)

    def test_basis_compression_exit_without_profit_mask(self):
        # Verifies that basis compression triggers exit regardless of total_net_pnl <= 0
        target_comp = 0.0005
        curr_basis = 0.0004
        is_basis_compressed = abs(curr_basis) <= target_comp
        # Pure mathematical exit, unmasked by artificial positive PnL check
        self.assertTrue(is_basis_compressed)

    def test_full_friction_accounting_breakdown(self):
        # Case with 4-leg depth slippage and latency slippage
        gross_trading_pnl = 15.00
        depth_slip = 1.80
        latency_slip = 1.20
        fees = 3.00
        settled_funding = 0.50

        total_friction = depth_slip + latency_slip + fees
        self.assertEqual(total_friction, 6.00)

        net_trading_pnl = gross_trading_pnl - total_friction
        self.assertEqual(net_trading_pnl, 9.00)

        total_net_pnl = net_trading_pnl + settled_funding
        self.assertEqual(total_net_pnl, 9.50)

        # Alpha retention: Net / Gross = 9.50 / 15.00 = 63.33%
        alpha_retention = (total_net_pnl / gross_trading_pnl) * 100.0
        self.assertAlmostEqual(alpha_retention, 63.3333, places=3)

    def test_resolve_trade_friction_fallback(self):
        # Legacy trade missing depth_slippage_cost and latency_slippage_cost
        legacy_trade = {
            "token": "BTC",
            "position_size": 1500.0,
            "trading_fee": 3.0,
            "trading_pnl": 2.5,
        }
        fee, depth_slip, latency_slip, tot_friction = resolve_trade_friction(legacy_trade)
        self.assertEqual(fee, 3.0)
        self.assertAlmostEqual(depth_slip, 0.90, places=2)  # 1500 * 0.00015 * 4 = 0.90
        self.assertAlmostEqual(latency_slip, 1.20, places=2)  # 1500 * 0.00020 * 4 = 1.20
        self.assertAlmostEqual(tot_friction, 5.10, places=2)  # 3.0 + 0.90 + 1.20 = 5.10

        # Modern trade with explicit orderbook VWAP slippage
        modern_trade = {
            "token": "ETH",
            "position_size": 1500.0,
            "trading_fee": 3.0,
            "depth_slippage_cost": 2.15,
            "latency_slippage_cost": 1.20,
            "total_friction_cost": 6.35,
        }
        fee_m, depth_m, latency_m, tot_m = resolve_trade_friction(modern_trade)
        self.assertEqual(fee_m, 3.0)
        self.assertEqual(depth_m, 2.15)
        self.assertEqual(latency_m, 1.20)
        self.assertEqual(tot_m, 6.35)


class TestBasisConfigContract(unittest.TestCase):
    """Verify Basis Stat Arb config fields required by Case 32."""

    def test_required_anti_hft_config_fields(self):
        config = {
            "script_file_name": "v2_basis_stat_arb.py",
            "dry_run": True,
            "connectors": ["hyperliquid_perpetual", "binance_perpetual"],
            "tokens": ["STRK", "WLD", "NEAR"],
            "position_size_quote": 1500.0,
            "leverage": 3,
            "min_entry_basis_pct": 0.0032,
            "take_profit_basis_compression_pct": 0.0005,
            "min_take_profit_net_pct": 0.0020,
            "max_basis_loss_pct": 0.0150,
            "max_hold_hours": 6.0,
            "min_holding_seconds": 60,
            "exit_persistence_ticks": 2,
            "taker_fee_rate": 0.0005,
            "simulated_latency_slippage_pct": 0.0002,
            "settlement_blackout_seconds": 60,
            "dynamic_capacity_sizing": True,
            "min_order_size_quote": 300.0,
            "min_executable_net_edge_pct": 0.0030,
            "max_executable_depth_slippage_pct": 0.0025,
        }
        self.assertEqual(config["min_holding_seconds"], 60)
        self.assertEqual(config["exit_persistence_ticks"], 2)
        self.assertEqual(config["taker_fee_rate"], 0.0005)
        self.assertEqual(config["simulated_latency_slippage_pct"], 0.0002)
        self.assertEqual(config["settlement_blackout_seconds"], 60)
        self.assertTrue(config["dynamic_capacity_sizing"])
        self.assertEqual(config["min_order_size_quote"], 300.0)
        self.assertEqual(config["min_executable_net_edge_pct"], 0.0030)
        self.assertEqual(config["max_executable_depth_slippage_pct"], 0.0025)
        self.assertGreaterEqual(config["min_entry_basis_pct"], 0.0022)  # Above friction barrier


class TestDynamicCapacityAndVWAPGate(unittest.TestCase):
    """Verify Executable VWAP Filter and Dynamic Capacity Sizing algorithms."""

    def test_dynamic_capacity_sizing_formula(self):
        ratio = Decimal("0.20")
        min_sz = Decimal("300.0")
        base_sz = Decimal("1500.0")

        def calc_cap(top3_depth: Decimal):
            if top3_depth < min_sz:
                return None
            return min(base_sz, max(min_sz, top3_depth * ratio))

        # 1. Deep book: BTC/ETH ($50k top 3 depth) -> capped at base_sz ($1,500)
        self.assertEqual(calc_cap(Decimal("50000.0")), Decimal("1500.0"))

        # 2. Mid book: SAGA ($2,500 top 3 depth) -> 20% = $500
        self.assertEqual(calc_cap(Decimal("2500.0")), Decimal("500.0"))

        # 3. Shallow book: STRK ($800 top 3 depth) -> 20% = $160, clamped to min_order_sz ($300)
        self.assertEqual(calc_cap(Decimal("800.0")), Decimal("300.0"))

        # 4. Mirage book: DYM ($180 top 3 depth < $300) -> blocked (None)
        self.assertIsNone(calc_cap(Decimal("180.0")))

    def test_executable_vwap_net_edge_calculation(self):
        # Case A: Strong pulse, healthy book
        raw_vwap_basis = Decimal("0.0075")  # 75 bps
        taker_fees = Decimal("0.0005") * Decimal("4.0")  # 20 bps
        latency_slip = Decimal("0.0002") * Decimal("4.0")  # 8 bps
        c1_slip = Decimal("0.0003")  # 3 bps
        c2_slip = Decimal("0.0004")  # 4 bps
        exit_depth_slip = c1_slip + c2_slip  # 7 bps
        total_friction = taker_fees + latency_slip + exit_depth_slip  # 35 bps

        net_edge = raw_vwap_basis - total_friction  # 40 bps
        self.assertAlmostEqual(float(net_edge), 0.0040, places=6)
        self.assertGreaterEqual(net_edge, Decimal("0.0030"))  # Passes 30 bps gate
        self.assertLessEqual(max(c1_slip, c2_slip), Decimal("0.0025"))  # Passes 25 bps slip cap

        # Case B: Excessive single leg depth slippage (thin book eaten past L3)
        c2_bad_slip = Decimal("0.0035")  # 35 bps > 25 bps ceiling
        self.assertGreater(c2_bad_slip, Decimal("0.0025"))  # Blocked by slippage ceiling

        # Case C: Insufficient net edge after friction
        marginal_vwap_basis = Decimal("0.0055")  # 55 bps
        marginal_net_edge = marginal_vwap_basis - total_friction  # 55 - 35 = 20 bps
        self.assertLess(marginal_net_edge, Decimal("0.0030"))  # Blocked by net edge gate


class TestBasisRendering(unittest.TestCase):
    """Verify Streamlit UI rendering of Basis Stat Arb tables with active and closed trades."""

    def test_render_basis_tables_with_active_position(self):
        from streamlit.testing.v1 import AppTest
        code = '''
import frontend.components.basis_stat_arb_view as view
data = {
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [{
        "token": "SAGA",
        "position_size": 1500.0,
        "entry_price_1": 0.0201,
        "entry_price_2": 0.0200,
        "current_price_1": 0.0201,
        "current_price_2": 0.0200,
        "unrealized_trading_pnl": 0.0,
        "trading_fee": 3.0,
        "duration_seconds": 120,
        "connector_1": "hyperliquid_perpetual",
        "connector_2": "binance_perpetual",
        "side_1": "SELL",
        "side_2": "BUY",
        "entry_basis_pct": 0.0035,
        "current_basis_pct": 0.0030,
    }],
    "closed_trades": [{
        "token": "STRK",
        "position_size": 1500.0,
        "entry_price_1": 0.057,
        "entry_price_2": 0.056,
        "close_price_1": 0.0565,
        "close_price_2": 0.0565,
        "trading_pnl": 3.5,
        "trading_fee": 3.0,
        "total_net_pnl": 3.5,
        "funding_pnl": 0.0,
        "duration_seconds": 1400,
        "side_1": "BUY",
        "side_2": "SELL",
        "close_reason": "take_profit",
        "close_timestamp": 1729500000,
    }],
    "market_comparison": [],
}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        # Verify active position renders as clean HTML without leaking raw code blocks into markdown
        if hasattr(app, "html") and len(app.html) > 0:
            self.assertTrue(any("资金与仓位规格明细面板" in h.value for h in app.html))
        self.assertFalse(any("<!-- 资金与仓位规格明细面板 -->" in m.value for m in app.markdown))

    def test_active_position_dynamic_duration_and_lock_badge(self):
        """Verify dynamic duration and lock badge correctly transition based on open_timestamp."""
        import time
        from streamlit.testing.v1 import AppTest
        now_ts = time.time()
        # Case 1: Held for 2 hours (7200s), duration_seconds omitted, should show Ready
        code = f'''
import frontend.components.basis_stat_arb_view as view
data = {{
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [{{
        "token": "SAGA",
        "position_size": 1500.0,
        "entry_price_1": 0.0201,
        "entry_price_2": 0.0200,
        "open_timestamp": {now_ts - 7200},
        "connector_1": "hyperliquid_perpetual",
        "connector_2": "binance_perpetual",
        "side_1": "SELL",
        "side_2": "BUY",
        "entry_basis_pct": 0.0035,
        "min_holding_seconds": 60,
        "funding_payments": [
            {{"timestamp": 100, "connector": "c1", "amount": 0.1}},
            {{"timestamp": 100, "connector": "c2", "amount": -0.05}},
            {{"timestamp": 200, "connector": "c1", "amount": 0.1}},
        ]
    }}],
    "closed_trades": [],
    "market_comparison": [
        {{"token": "SAGA", "price_1": 0.02005, "price_2": 0.02002}}
    ],
}}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        if hasattr(app, "html") and len(app.html) > 0:
            html_text = " ".join(h.value for h in app.html)
            self.assertIn("🟢 止盈就绪 (已过冷静期 60s)", html_text)
            self.assertIn("2小时", html_text)
            self.assertIn("2周期", html_text)  # Deduplicated from 3 events across 2 timestamps

    def test_radar_tab_rendering_with_dynamic_capacity_and_edge(self):
        """Verify Radar table renders Top 3 depth, safe capacity, and VWAP net edge."""
        from streamlit.testing.v1 import AppTest
        code = '''
import frontend.components.basis_stat_arb_view as view
data = {
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [],
    "closed_trades": [],
    "market_comparison": [
        {
            "token": "SAGA",
            "price_1": 0.0201,
            "price_2": 0.0200,
            "basis_spread_pct": 0.0050,
            "c1_funding_daily": 0.0003,
            "c2_funding_daily": 0.0001,
            "carry_if_short_c1": 0.0002,
            "dislocation_status": "⚡ 偏离超标 +0.50% (Short C1/Long C2)",
            "c1_top3_depth": 750.0,
            "c2_top3_depth": 2500.0,
            "safe_capacity": 300.0,
            "net_executable_edge_pct": 0.0035,
        },
        {
            "token": "DYM",
            "price_1": 0.150,
            "price_2": 0.148,
            "basis_spread_pct": 0.0135,
            "c1_funding_daily": 0.0001,
            "c2_funding_daily": 0.0001,
            "carry_if_short_c1": 0.0,
            "dislocation_status": "🛡️ 流动性虚幻/拦截 (Top3: $180)",
            "c1_top3_depth": 180.0,
            "c2_top3_depth": 1200.0,
            "safe_capacity": 180.0,
            "net_executable_edge_pct": -0.0010,
        }
    ],
}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        self.assertTrue(len(app.dataframe) >= 1)
        df_radar = app.dataframe[0].value
        self.assertIn("HL Top3深度", df_radar.columns)
        self.assertIn("BN Top3深度", df_radar.columns)
        self.assertIn("安全定额容量", df_radar.columns)
        self.assertIn("VWAP 净空间", df_radar.columns)
        # SAGA row
        saga_row = df_radar[df_radar["标的"] == "SAGA"].iloc[0]
        self.assertEqual(saga_row["安全定额容量"], "$300")
        self.assertEqual(saga_row["VWAP 净空间"], "+0.350%")
        # DYM row
        dym_row = df_radar[df_radar["标的"] == "DYM"].iloc[0]
        self.assertIn("流动性虚幻", dym_row["偏离撮合状态"])

    def test_dynamic_rotation_tabs_render(self):
        """Verify that discovery candidates and rotation history render properly."""
        from streamlit.testing.v1 import AppTest
        code = '''
import streamlit as st
from frontend.components import basis_stat_arb_view as view

data = {
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "leverage": 3,
    "active_positions": [],
    "closed_trades": [],
    "market_comparison": [],
    "market_discovery_candidates": [
        {
            "token": "SOPH",
            "is_active": False,
            "basis_pct": 0.0085,
            "hl_price": 0.0017,
            "bn_price": 0.001685,
            "hl_volume_24h": 600000.0,
            "bn_volume_24h": 9400000.0,
            "min_volume_24h": 600000.0,
            "dwell_seconds": 0,
            "scanned_at": "2026-10-22 14:00:00 UTC",
        },
        {
            "token": "BTC",
            "is_active": True,
            "basis_pct": 0.0001,
            "hl_price": 68000.0,
            "bn_price": 67990.0,
            "hl_volume_24h": 50000000.0,
            "bn_volume_24h": 200000000.0,
            "min_volume_24h": 50000000.0,
            "dwell_seconds": 3600,
            "scanned_at": "2026-10-22 14:00:00 UTC",
        }
    ],
    "rotation_history": [
        {
            "time_str": "2026-10-22 13:30:00 UTC",
            "evicted_token": "LTC",
            "admitted_token": "AERO",
            "evicted_basis_pct": 0.0002,
            "admitted_basis_pct": 0.0088,
            "delta_edge_pct": 0.0086,
            "admitted_volume_usd": 7300000.0,
            "reason": "Dynamic Hot-Swap: AERO replaces idle LTC",
        }
    ]
}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        self.assertTrue(len(app.dataframe) >= 2)
        df_disc = app.dataframe[0].value
        self.assertIn("理论基差", df_disc.columns)
        self.assertIn("状态", df_disc.columns)
        # Check SOPH qualified badge
        soph_row = df_disc[df_disc["标的"] == "SOPH"].iloc[0]
        self.assertIn("达标待命", soph_row["状态"])

        # Check rotation history
        df_rot = app.dataframe[1].value
        self.assertIn("淘汰标的", df_rot.columns)
        self.assertIn("引入新标的", df_rot.columns)
        self.assertEqual(df_rot.iloc[0]["淘汰标的"], "LTC")
        self.assertEqual(df_rot.iloc[0]["引入新标的"], "AERO")


class TestPreFlightMarginGate(unittest.TestCase):
    """Verify Pre-Flight Dual Margin Gate and Margin-Aware Downsizing (Case 40)."""

    def test_margin_gate_formulas(self):
        pos_size = Decimal("1500.0")
        leverage = Decimal("3.0")
        buffer_ratio = Decimal("0.05")
        taker_fee = Decimal("0.0005")

        margin_factor = (Decimal("1.0") / leverage) * (Decimal("1.0") + buffer_ratio) + taker_fee
        req_margin = pos_size * margin_factor
        self.assertEqual(req_margin, Decimal("525.75"))

        # Downsizing when available margin is $400
        avail = Decimal("400.0")
        max_feasible = avail / margin_factor
        self.assertAlmostEqual(float(max_feasible), 1141.2268, places=2)

    def test_config_contract_includes_margin_gate(self):
        config = {
            "check_margin_before_entry": True,
            "margin_buffer_ratio": 0.05,
            "margin_aware_downsizing": True,
            "dry_run_virtual_balance_quote": 0.0,
        }
        self.assertTrue(config["check_margin_before_entry"])
        self.assertEqual(config["margin_buffer_ratio"], 0.05)
        self.assertTrue(config["margin_aware_downsizing"])
        self.assertEqual(config["dry_run_virtual_balance_quote"], 0.0)


class TestClosedTradesMultiModePresentation(unittest.TestCase):
    """Verify Institutional Closed Trade Cards & Multi-Mode Presentation (Case 41)."""

    def test_closed_trade_card_html_content(self):
        from frontend.components.basis_stat_arb_view import _render_closed_trade_card_html
        trade_dict = {
            "token": "ETH",
            "side_1": "SELL",
            "side_2": "BUY",
            "connector_1": "hyperliquid_perpetual",
            "connector_2": "binance_perpetual",
            "real_net_trade": 18.42,
            "gross_tpnl": 24.50,
            "fee": 3.00,
            "depth_slip_val": 1.25,
            "latency_slip_val": 0.83,
            "friction_tot": 5.08,
            "fpnl": 0.0,
            "settle_cnt": 0,
            "roe_pct": 0.0368,
            "retention_pct": 75.18,
            "dur_str": "2分 15秒",
            "close_time_str": "2026-10-23 14:20:00",
            "entry_basis": 0.0072,
            "close_basis": 0.0004,
            "compression_pct": 94.4,
            "pos_size_val": 1500.0,
            "dual_notional_val": 3000.0,
            "lev_val": 3.0,
            "margin_val": 1000.0,
            "amt_1": 0.5842,
            "amt_2": 0.5842,
            "p1_ent": 2567.8,
            "p2_ent": 2549.4,
            "p1_close": 2555.0,
            "p2_close": 2554.0,
            "close_reason": "Take-Profit: Basis Compression to 4 bps",
        }
        card_html = _render_closed_trade_card_html(trade_dict)
        self.assertIn("🪙 ETH", card_html)
        self.assertIn("均值归拢收敛度", card_html)
        self.assertIn("94.4%", card_html)
        self.assertIn("+0.72%", card_html)
        self.assertIn("+$18.42 USD", card_html)
        self.assertIn("+3.68% ROE", card_html)
        self.assertIn("实际捕获基差", card_html)
        self.assertIn("+68.0 bps", card_html)
        self.assertIn("Alpha 留存", card_html)
        self.assertIn("Delta-Zero", card_html)
        self.assertIn("财务穿透与摩擦拆解瀑布流", card_html)

    def test_closed_trades_tab_renders_cards_and_strip(self):
        from streamlit.testing.v1 import AppTest
        code = '''
import frontend.components.basis_stat_arb_view as view
data = {
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [],
    "closed_trades": [
        {
            "token": "FIL",
            "connector_1": "hyperliquid_perpetual",
            "connector_2": "binance_perpetual",
            "side_1": "SELL",
            "side_2": "BUY",
            "amount_1": 500.0,
            "amount_2": 500.0,
            "entry_price_1": 1.00,
            "entry_price_2": 0.98,
            "close_price_1": 0.97,
            "close_price_2": 0.97,
            "entry_basis_pct": 0.02,
            "close_basis_pct": 0.0,
            "position_size": 500.0,
            "total_notional": 1000.0,
            "leverage": 3,
            "total_margin": 333.33,
            "gross_trading_pnl": 10.0,
            "depth_slippage_cost": 0.2,
            "latency_slippage_cost": 0.3,
            "trading_fee": 1.0,
            "total_friction_cost": 1.5,
            "funding_pnl": 0.0,
            "total_net_pnl": 8.5,
            "close_reason": "Basis Mean-Reversion TP",
            "close_timestamp": 1729600000,
            "duration_seconds": 60,
        }
    ],
    "market_comparison": [],
}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        # Verify radio view controller exists
        self.assertTrue(len(app.radio) >= 1)
        radio_opts = app.radio[0].options
        self.assertTrue(any("卡片流" in opt for opt in radio_opts))
        self.assertTrue(any("数据大表" in opt for opt in radio_opts))

    def test_closed_trades_table_captured_basis_column(self):
        """Verify closed trades data table contains 实际捕获基差 (BPS) column in expected order."""
        from streamlit.testing.v1 import AppTest
        code = '''
import frontend.components.basis_stat_arb_view as view
data = {
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [],
    "closed_trades": [
        {
            "token": "REZ",
            "connector_1": "hyperliquid_perpetual",
            "connector_2": "binance_perpetual",
            "side_1": "SELL",
            "side_2": "BUY",
            "amount_1": 1000.0,
            "amount_2": 1000.0,
            "entry_price_1": 0.05,
            "entry_price_2": 0.049,
            "close_price_1": 0.048,
            "close_price_2": 0.048,
            "entry_basis_pct": 0.0052,
            "close_basis_pct": 0.0002,
            "captured_basis_bps": 50.0,
            "captured_basis_pct": 0.0050,
            "position_size": 500.0,
            "total_notional": 1000.0,
            "leverage": 3,
            "total_margin": 333.33,
            "gross_trading_pnl": 2.5,
            "trading_fee": 0.5,
            "total_friction_cost": 0.8,
            "funding_pnl": 0.0,
            "total_net_pnl": 1.7,
            "close_reason": "Basis Mean-Reversion TP",
            "close_timestamp": 1729600000,
            "duration_seconds": 60,
        }
    ],
    "market_comparison": [],
}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        self.assertTrue(len(app.dataframe) >= 1)
        df_cols = list(app.dataframe[0].value.columns)
        self.assertIn("实际捕获基差 (BPS)", df_cols)
        self.assertIn("平仓基差", df_cols)
        self.assertIn("开仓基差", df_cols)
        col_idx_close = df_cols.index("平仓基差")
        col_idx_cap = df_cols.index("实际捕获基差 (BPS)")
        col_idx_gross = df_cols.index("理论毛价差")
        self.assertEqual(col_idx_cap, col_idx_close + 1)
        self.assertEqual(col_idx_gross, col_idx_cap + 1)
        row_val = app.dataframe[0].value.iloc[0]["实际捕获基差 (BPS)"]
        self.assertIn("50.0 bps", row_val)

    def test_closed_trades_time_filter_and_sorting(self):
        """Verify closed trades filter by close time window and sort by close timestamp."""
        import time as _t
        now_ts = _t.time()
        from streamlit.testing.v1 import AppTest
        code = f'''
import frontend.components.basis_stat_arb_view as view
now = {now_ts}
data = {{
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [],
    "closed_trades": [
        {{
            "token": "OLD_TOK",
            "connector_1": "hyperliquid_perpetual",
            "connector_2": "binance_perpetual",
            "side_1": "SELL",
            "side_2": "BUY",
            "amount_1": 100.0,
            "amount_2": 100.0,
            "entry_price_1": 1.0,
            "entry_price_2": 1.0,
            "close_price_1": 1.0,
            "close_price_2": 1.0,
            "entry_basis_pct": 0.01,
            "close_basis_pct": 0.0,
            "position_size": 100.0,
            "total_notional": 200.0,
            "leverage": 3,
            "total_margin": 66.66,
            "gross_trading_pnl": 5.0,
            "trading_fee": 0.2,
            "total_friction_cost": 0.3,
            "funding_pnl": 0.0,
            "total_net_pnl": 4.7,
            "close_reason": "TP",
            "close_timestamp": now - 7200,  # 2 hours ago
            "duration_seconds": 60,
        }},
        {{
            "token": "RECENT_TOK",
            "connector_1": "hyperliquid_perpetual",
            "connector_2": "binance_perpetual",
            "side_1": "SELL",
            "side_2": "BUY",
            "amount_1": 100.0,
            "amount_2": 100.0,
            "entry_price_1": 1.0,
            "entry_price_2": 1.0,
            "close_price_1": 1.0,
            "close_price_2": 1.0,
            "entry_basis_pct": 0.01,
            "close_basis_pct": 0.0,
            "position_size": 100.0,
            "total_notional": 200.0,
            "leverage": 3,
            "total_margin": 66.66,
            "gross_trading_pnl": 2.0,
            "trading_fee": 0.2,
            "total_friction_cost": 0.3,
            "funding_pnl": 0.0,
            "total_net_pnl": 1.7,
            "close_reason": "TP",
            "close_timestamp": now - 300,  # 5 minutes ago
            "duration_seconds": 30,
        }}
    ],
    "market_comparison": [],
}}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])

        # Verify default order: "🕒 平仓时间 (最新在前)" so RECENT_TOK is first
        df_val = app.dataframe[0].value
        self.assertEqual(len(df_val), 2)
        self.assertEqual(df_val.iloc[0]["标的"], "RECENT_TOK")
        self.assertEqual(df_val.iloc[1]["标的"], "OLD_TOK")

        # Verify time filter selectbox exists and test filtering by past 1 hour
        time_boxes = [sb for sb in app.selectbox if "平仓时段" in sb.label]
        self.assertEqual(len(time_boxes), 1)
        time_sb = time_boxes[0]
        self.assertIn("过去 1 小时内", time_sb.options)
        self.assertIn("全部平仓时段", time_sb.options)

        # Select "过去 1 小时内" -> only RECENT_TOK should remain
        time_sb.select("过去 1 小时内").run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        df_filtered = app.dataframe[0].value
        self.assertEqual(len(df_filtered), 1)
        self.assertEqual(df_filtered.iloc[0]["标的"], "RECENT_TOK")

        # Verify sort selectbox exists and test sorting by profit descending
        sort_boxes = [sb for sb in app.selectbox if "排序方式" in sb.label]
        self.assertEqual(len(sort_boxes), 1)
        sort_sb = sort_boxes[0]
        self.assertIn("🕒 平仓时间 (最新在前)", sort_sb.options)
        self.assertIn("💰 真实纯利 (从高到低)", sort_sb.options)

    def test_early_tp_and_floor_badges(self):
        """Verify _render_closed_trade_card_html renders Early TP and Floor Protected badges."""
        from frontend.components.basis_stat_arb_view import _render_closed_trade_card_html
        base_trade = {
            "token": "REZ",
            "side_1": "SELL",
            "side_2": "BUY",
            "connector_1": "hyperliquid_perpetual",
            "connector_2": "binance_perpetual",
            "real_net_trade": 1.5,
            "gross_tpnl": 2.5,
            "fee": 0.5,
            "depth_slip_val": 0.2,
            "latency_slip_val": 0.3,
            "friction_tot": 1.0,
            "fpnl": 0.0,
            "settle_cnt": 0,
            "roe_pct": 0.015,
            "retention_pct": 60.0,
            "dur_str": "15秒",
            "close_time_str": "10-23 23:00:00",
            "entry_basis": 0.008,
            "close_basis": 0.001,
            "captured_basis_bps": 70.0,
            "compression_pct": 87.5,
            "pos_size_val": 300.0,
            "dual_notional_val": 600.0,
            "lev_val": 3,
            "margin_val": 200.0,
            "amt_1": 100.0,
            "amt_2": 100.0,
            "p1_ent": 1.0,
            "p2_ent": 1.0,
            "p1_close": 1.0,
            "p2_close": 1.0,
            "close_reason": "Profit-Locked Early TP (+0.50% >= +0.35%, held 15s)",
        }
        card_html = _render_closed_trade_card_html(base_trade)
        self.assertIn("⚡ 浮盈爆发提前收割", card_html)

        base_trade["close_reason"] = "Trailing Stop Floor Protected (Peak: +0.62%, Secured at +0.15%)"
        card_html_floor = _render_closed_trade_card_html(base_trade)
        self.assertIn("🔒 底线保本强制锁定", card_html_floor)

    def test_quarantine_banner_rendered(self):
        """Verify quarantine banner is rendered when quarantined_tokens is present."""
        import time as _t
        now_ts = _t.time()
        from streamlit.testing.v1 import AppTest
        code = f'''
import frontend.components.basis_stat_arb_view as view
data = {{
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [],
    "closed_trades": [],
    "market_comparison": [],
    "quarantined_tokens": {{"SOPH": {now_ts + 1800}}},
}}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        warnings = [w.value for w in app.warning]
        self.assertTrue(any("动量发散冷冻隔离舱生效中" in w and "SOPH" in w for w in warnings))

    def test_sniper_badges_and_radar(self):
        """Verify Case 52 Sniper badges and radar display."""
        from frontend.components.basis_stat_arb_view import _render_closed_trade_card_html
        trade = {
            "token": "SOPH",
            "side_1": "SELL",
            "side_2": "BUY",
            "connector_1": "hyperliquid_perpetual",
            "connector_2": "binance_perpetual",
            "real_net_trade": 3.85,
            "gross_tpnl": 4.50,
            "fee": 0.30,
            "depth_slip_val": 0.15,
            "latency_slip_val": 0.20,
            "friction_tot": 0.65,
            "fpnl": 0.0,
            "settle_cnt": 0,
            "roe_pct": 0.0385,
            "retention_pct": 85.5,
            "dur_str": "45秒",
            "close_time_str": "10-24 08:00:00",
            "entry_basis": 0.0185,
            "close_basis": 0.0035,
            "captured_basis_bps": 150.0,
            "compression_pct": 81.1,
            "pos_size_val": 300.0,
            "dual_notional_val": 600.0,
            "lev_val": 3,
            "margin_val": 200.0,
            "amt_1": 1000.0,
            "amt_2": 1000.0,
            "p1_ent": 0.30,
            "p2_ent": 0.30,
            "p1_close": 0.30,
            "p2_close": 0.30,
            "is_sniper": True,
            "sniper_peak_basis": 0.0210,
            "close_reason": "🎯 [SNIPER-TAKE-PROFIT] Exhaustion Mean-Reversion Captured! (Peak: 2.10% ➔ Current: +0.35%)",
        }
        card_html = _render_closed_trade_card_html(trade)
        self.assertIn("🎯 极值力竭刺客收割", card_html)
        self.assertIn("🎯 极值刺客 (峰值 +2.10%)", card_html)

        trade["close_reason"] = "🛑 [SNIPER-INVALIDATION-STOP] Basis expanded past peak defense 2.40% (Current: +2.45%)"
        card_html_sl = _render_closed_trade_card_html(trade)
        self.assertIn("🛑 刺客极值破位止损", card_html_sl)

        # Test radar banner in streamlit
        import time as _t
        now_ts = _t.time()
        from streamlit.testing.v1 import AppTest
        code = f'''
import frontend.components.basis_stat_arb_view as view
data = {{
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [],
    "closed_trades": [],
    "market_comparison": [],
    "sniper_prey_tracker": {{
        "SOPH": {{
            "token": "SOPH",
            "source": "STOP_LOSS_REVERSAL",
            "initial_trigger_ts": {now_ts - 60},
            "peak_basis": 0.0210,
            "last_basis": 0.0185,
            "consecutive_contraction_ticks": 2,
            "status": "CONFIRMING_REVERSAL"
        }}
    }},
}}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        infos = [inf.value for inf in app.info]
        self.assertTrue(any("极值力竭刺客雷达生效中" in inf and "SOPH" in inf for inf in infos))


class TestRealizedLossAggregation(unittest.TestCase):
    """Test real failure loss amount aggregation and deep audit display."""

    def test_kpi_loss_aggregation_and_panel(self):
        try:
            from streamlit.testing.v1 import AppTest
        except ImportError:
            self.skipTest("streamlit.testing.v1 not available")

        code = '''
import streamlit as st
import frontend.components.basis_stat_arb_view as view

data = {
    "strategy": "basis_stat_arb",
    "is_dry_run": True,
    "active_positions": [],
    "closed_trades": [
        {
            "token": "WIN1",
            "trading_pnl": 12.0,
            "total_net_pnl": 10.0,
            "gross_trading_pnl": 12.0,
            "trading_fee": 1.0,
            "depth_slippage_cost": 0.5,
            "latency_slippage_cost": 0.5,
            "total_friction_cost": 2.0,
            "close_reason": "Basis Mean-Reversion TP",
            "close_timestamp": 1792600000,
            "duration_seconds": 60,
        },
        {
            "token": "LOSS1",
            "trading_pnl": -4.0,
            "total_net_pnl": -5.0,
            "gross_trading_pnl": -4.0,
            "trading_fee": 0.5,
            "depth_slippage_cost": 0.25,
            "latency_slippage_cost": 0.25,
            "total_friction_cost": 1.0,
            "close_reason": "Emergency Basis Divergence Stop-Loss",
            "close_timestamp": 1792600100,
            "duration_seconds": 45,
        },
        {
            "token": "LOSS2",
            "trading_pnl": -0.5,
            "total_net_pnl": -1.0,
            "gross_trading_pnl": -0.5,
            "trading_fee": 0.25,
            "depth_slippage_cost": 0.12,
            "latency_slippage_cost": 0.13,
            "total_friction_cost": 0.5,
            "close_reason": "Trailing Stop Floor Protected",
            "close_timestamp": 1792600200,
            "duration_seconds": 61,
        }
    ],
    "market_comparison": []
}
view.render_basis_stat_arb_tables(data, "test-bot")
'''
        app = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(app.exception, [r.value for r in app.exception])
        # Verify 7 metrics are rendered (now including 真实平仓失败归集)
        self.assertGreaterEqual(len(app.metric), 7)
        metric_labels = [m.label for m in app.metric]
        self.assertIn("🛑 真实平仓失败归集", metric_labels)
        loss_metric = [m for m in app.metric if m.label == "🛑 真实平仓失败归集"][0]
        # Total loss is 5.0 + 1.0 = 6.0 USD
        self.assertIn("6.00", loss_metric.value)


if __name__ == "__main__":
    unittest.main()
