import json
import os
import time
from datetime import datetime, timezone
import pandas as pd
import streamlit as st

from frontend.components.clock_monitor import get_exchange_clock_health
from frontend.components.timezone_utils import render_timezone_selector, format_timestamp


def format_precise_duration(seconds) -> str:
    """Format duration with seconds precision so short trades never show as 0h 0m."""
    if seconds is None or seconds < 0:
        return "0秒"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}秒"
    m = seconds // 60
    s = seconds % 60
    if m < 60:
        return f"{m}分 {s}秒"
    h = m // 60
    m = m % 60
    return f"{h}小时 {m}分 {s}秒"


def resolve_trade_friction(t):
    """
    Resolve trade friction metrics with strict orderbook and latency models fallback.
    Guarantees that slippage and friction metrics are never 0 or unpopulated even on legacy records.
    """
    pos_size = float(t.get("position_size", 1500.0))
    fee = float(t.get("trading_fee", 3.0))

    # Depth slippage: use stored value if present (> 0), otherwise 1.5 bps / leg (4 legs = 6.0 bps = $0.90)
    raw_depth = t.get("depth_slippage_cost")
    if raw_depth is not None and float(raw_depth) > 0:
        depth_slip = float(raw_depth)
    else:
        depth_slip = pos_size * 0.00015 * 4.0

    # Latency slippage: use stored value if present (> 0), otherwise 2.0 bps / leg (4 legs = 8.0 bps = $1.20)
    raw_latency = t.get("latency_slippage_cost")
    if raw_latency is not None and float(raw_latency) > 0:
        latency_slip = float(raw_latency)
    else:
        latency_slip = pos_size * 0.00020 * 4.0

    raw_tot = t.get("total_friction_cost")
    if raw_tot is not None and float(raw_tot) > 0:
        tot_friction = float(raw_tot)
    else:
        tot_friction = fee + depth_slip + latency_slip

    return fee, depth_slip, latency_slip, tot_friction


def get_bot_basis_stat_arb_data(bot_name, performance):
    """Retrieve basis stat arb custom_info from MQTT performance or local disk."""
    # Strict Isolation Guard: funding rate bots can NEVER be matched as basis bots
    if "funding" in bot_name.lower() or "funding_rate_arb" in performance:
        return False, None

    arb_data = None
    for ctrl_id, ctrl_dict in performance.items():
        if ctrl_id == "basis_stat_arb":
            c_info = ctrl_dict.get("custom_info", {})
            if "active_positions" in c_info or "closed_trades" in c_info or "market_comparison" in c_info:
                arb_data = c_info
                break

    # Strictly search only this instance's private directory
    candidate_json_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), f"../../../hummingbot-api/bots/instances/{bot_name}/data/basis_stat_arb_trades.json")),
        os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/instances/{bot_name}/data/basis_stat_arb_trades.json"),
    ]

    if not arb_data:
        for json_path in candidate_json_paths:
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r") as jf:
                        arb_data = json.load(jf)
                    if arb_data:
                        break
                except Exception:
                    pass

    is_basis_stat_arb_bot = (
        "basis" in bot_name.lower() or
        "basis_stat_arb" in performance or
        (arb_data is not None and arb_data.get("strategy") == "basis_stat_arb")
    )

    if not is_basis_stat_arb_bot:
        candidate_conf_dirs = [
            os.path.abspath(os.path.join(os.path.dirname(__file__), f"../../../hummingbot-api/bots/instances/{bot_name}/conf/scripts")),
            os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/instances/{bot_name}/conf/scripts"),
        ]
        for conf_dir in candidate_conf_dirs:
            if os.path.isdir(conf_dir):
                for fname in os.listdir(conf_dir):
                    if fname.endswith(".yml") or fname.endswith(".yaml"):
                        try:
                            with open(os.path.join(conf_dir, fname), "r") as f:
                                content = f.read()
                                if ("basis_stat_arb" in content or "v2_basis_stat_arb" in content) and "v2_funding_rate_arb" not in content:
                                    is_basis_stat_arb_bot = True
                                    break
                        except Exception:
                            pass
            if is_basis_stat_arb_bot:
                break

    if not is_basis_stat_arb_bot:
        return False, None

    if is_basis_stat_arb_bot and not arb_data:
        arb_data = {
            "strategy": "basis_stat_arb",
            "is_dry_run": True,
            "leverage": 3,
            "active_positions": [],
            "closed_trades": [],
            "market_comparison": []
        }
    elif arb_data and "leverage" not in arb_data:
        arb_data["leverage"] = 3

    return is_basis_stat_arb_bot, arb_data


def render_basis_stat_arb_kpi_cards(arb_data, total_volume_traded=0.0):
    """Render top institutional KPI summary for Basis Stat Arb Engine."""
    active_pos = arb_data.get("active_positions", [])
    closed_trades = arb_data.get("closed_trades", [])
    leverage = int(arb_data.get("leverage") or 3)
    if leverage <= 0:
        leverage = 3

    total_capital = sum(float(p.get("position_size", 0)) for p in active_pos)
    gross_capital = total_capital * 2.0
    real_margin_total = (gross_capital / leverage) if leverage > 0 else total_capital

    realized_trading_pnl = sum(float(t.get("trading_pnl", 0)) for t in closed_trades)
    realized_funding_pnl = sum(float(t.get("funding_pnl", 0)) for t in closed_trades)
    total_realized_pnl = sum(float(t.get("total_net_pnl", 0)) for t in closed_trades)
    
    trade_frictions = [resolve_trade_friction(t) for t in closed_trades]
    total_trading_fees = sum(f[0] for f in trade_frictions)
    total_depth_slip = sum(f[1] for f in trade_frictions)
    total_latency_slip = sum(f[2] for f in trade_frictions)
    total_friction = sum(f[3] for f in trade_frictions)
    total_gross_trading = sum(float(t.get("gross_trading_pnl", float(t.get("trading_pnl", 0)) + f[3])) for t, f in zip(closed_trades, trade_frictions))
    
    # Real Penetrated Net PnL (subtracts all frictions: fees + depth slippage + latency slippage)
    real_penetrated_pnl = total_gross_trading - total_friction + realized_funding_pnl
    # Paper Net PnL (only subtracts trading fees, ignores slippage)
    paper_net_pnl = total_gross_trading - total_trading_fees + realized_funding_pnl
    total_realized_pnl = real_penetrated_pnl

    avg_alpha_retention = (real_penetrated_pnl / total_gross_trading * 100.0) if total_gross_trading > 0 else 0.0

    market_comp = arb_data.get("market_comparison", [])
    market_comp_map = {m.get("token"): m for m in market_comp if m.get("token")}

    # Dynamically compute unrealized trading PnL with live market quotes
    active_unrealized_list = []
    for p in active_pos:
        tok = p.get("token", "")
        p1_entry = float(p.get("entry_price_1", 0.0))
        p2_entry = float(p.get("entry_price_2", 0.0))
        pos_size = float(p.get("position_size", 0.0))
        amt1 = float(p.get("amount_1", pos_size / p1_entry if p1_entry > 0 else 0.0))
        amt2 = float(p.get("amount_2", pos_size / p2_entry if p2_entry > 0 else 0.0))
        mc_info = market_comp_map.get(tok, {})
        p1_mc = mc_info.get("price_1")
        p2_mc = mc_info.get("price_2")
        p1_curr = float(p.get("current_price_1") or (p1_mc if p1_mc and p1_mc > 0 else p1_entry))
        p2_curr = float(p.get("current_price_2") or (p2_mc if p2_mc and p2_mc > 0 else p2_entry))
        side1 = p.get("side_1", "")
        side2 = p.get("side_2", "")
        l1 = (p1_curr - p1_entry) * amt1 if side1 == "BUY" else (p1_entry - p1_curr) * amt1
        l2 = (p2_curr - p2_entry) * amt2 if side2 == "BUY" else (p2_entry - p2_curr) * amt2
        fee_est = float(p.get("trading_fee", ((amt1 * p1_entry) + (amt2 * p2_entry) + (amt1 * p1_curr) + (amt2 * p2_curr)) * 0.0005))
        net_unrealized = (l1 + l2) - fee_est
        active_unrealized_list.append(net_unrealized)

    unrealized_trading_pnl = sum(active_unrealized_list)
    unrealized_settled_funding = sum(float(p.get("settled_funding_pnl", p.get("funding_income", 0))) for p in active_pos)
    unrealized_indicative_carry = sum(float(p.get("indicative_accrued_carry", 0)) for p in active_pos)
    total_unrealized_pnl = unrealized_trading_pnl + unrealized_settled_funding

    net_combined_pnl = total_realized_pnl + total_unrealized_pnl
    total_funding_harvested = realized_funding_pnl + unrealized_settled_funding

    # Real vs Paper Win rate & stats
    real_winning_trades = [t for t, f in zip(closed_trades, trade_frictions) if (float(t.get("gross_trading_pnl", float(t.get("trading_pnl", 0)) + f[3])) - f[3] + float(t.get("funding_pnl", 0))) > 0.0001]
    real_win_rate = (len(real_winning_trades) / len(closed_trades) * 100.0) if closed_trades else 0.0
    paper_winning_trades = [t for t in closed_trades if float(t.get("paper_net_pnl", t.get("total_net_pnl", 0))) > 0]
    paper_win_rate = (len(paper_winning_trades) / len(closed_trades) * 100.0) if closed_trades else 0.0

    avg_hold_seconds = (sum(int(t.get("duration_seconds", 0)) for t in closed_trades) / len(closed_trades)) if closed_trades else 0.0
    avg_hold_str = format_precise_duration(avg_hold_seconds)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        st.metric(
            "💼 真实实缴总保证金",
            f"${real_margin_total:,.2f}",
            f"单边 ${total_capital:,.0f} | 双边 ${gross_capital:,.0f} ({leverage}x)",
            help=f"当前活跃基差套利在两所实缴的总保证金。单边名义货值 ${total_capital:,.2f}，双边对冲总敞口 ${gross_capital:,.2f}，基于 {leverage}x 杠杆质押。"
        )
    with c2:
        st.metric(
            "🏦 真实穿透结清净利",
            f"${real_penetrated_pnl:+,.2f}",
            f"真实胜率 {real_win_rate:.1f}% ({len(real_winning_trades)}/{len(closed_trades)} 胜) | 纸面 ${paper_net_pnl:+,.1f}",
            delta_color="normal",
            help=f"全摩擦穿透落袋真金净利：理论毛利(${total_gross_trading:,.2f}) - 全摩擦总成本(${total_friction:,.2f}) + 已结资金费(${realized_funding_pnl:,.2f}) = ${real_penetrated_pnl:+,.2f}。\n包含L2深度冲击滑点(-${total_depth_slip:,.2f})、网络时延跳价(-${total_latency_slip:,.2f})与交易所手续费(-${total_trading_fees:,.2f})。\n扣除滑点后真实胜率为 {real_win_rate:.1f}%（未扣滑点的纯纸面胜率为 {paper_win_rate:.1f}%，纸面净利 ${paper_net_pnl:+,.2f}）。"
        )
    with c3:
        st.metric(
            "⚡ 真实摩擦损耗穿透",
            f"-${total_friction:,.2f}",
            f"滑点 -${total_depth_slip + total_latency_slip:,.1f} | 佣金 -${total_trading_fees:,.1f}",
            delta_color="inverse",
            help=f"四腿全摩擦穿透：包含L2订单簿深度冲击滑点(-${total_depth_slip:,.2f})、网络时延跳价滑点(-${total_latency_slip:,.2f})、交易所4腿Taker手续费(-${total_trading_fees:,.2f})。"
        )
    with c4:
        st.metric(
            "🛡️ Alpha 净利留存率",
            f"{avg_alpha_retention:.1f}%",
            f"毛利 ${total_gross_trading:+,.1f}",
            delta_color="normal",
            help="理论纸面价差毛利在完全承受深度滑点、网络时延跳价与双边手续费后的净利润留存比例（Net PnL / Gross PnL）。"
        )
    with c5:
        st.metric(
            "📈 活跃持仓浮动净盈亏",
            f"${total_unrealized_pnl:+,.2f}",
            f"{len(active_pos)} 对仓位 | 双边货值 ${gross_capital:,.0f}",
            delta_color="normal",
            help="当前活跃基差仓位的净浮动盈亏（已计入开平仓4腿手续费摩擦与动态盘口价差，随盘口基差压缩动态更新）"
        )
    with c6:
        st.metric(
            "🎁 已结算资金费 (Settled)",
            f"${total_funding_harvested:+,.2f}",
            f"周期 {avg_hold_str}",
            delta_color="normal",
            help="持仓实际跨越交易所结算时点（Binance 8h / Hyperliquid 1h）真实入账的资金费现金流。"
        )


def render_basis_stat_arb_tables(arb_data, bot_name):
    """Render full tables and visual perspective for Basis Stat Arb bot."""
    active_pos = arb_data.get("active_positions", [])
    closed_trades = arb_data.get("closed_trades", [])
    market_comp = arb_data.get("market_comparison", [])
    is_dry_run = arb_data.get("is_dry_run", True)
    leverage = float(arb_data.get("leverage", 3.0))
    if leverage <= 0:
        leverage = 3.0

    mode_badge = "🟢 纸面预演 (Dry-Run)" if is_dry_run else "🔴 实盘交易 (Live)"

    col_hdr_title, col_hdr_clock, col_hdr_tz = st.columns([2.8, 1.1, 1.1])
    with col_hdr_title:
        st.markdown(f"#### ⚡ 基差统计套利加资金费双击流 `{mode_badge}`")
    with col_hdr_clock:
        clock_health = get_exchange_clock_health()
        st.caption(f"{clock_health.get('badge', '⏱️ 时钟正常')}")
    with col_hdr_tz:
        active_tz, tz_label = render_timezone_selector(
            key=f"tz_sel_basis_{bot_name}",
            label="🌐 显示时区"
        )

    # Render KPI Cards
    render_basis_stat_arb_kpi_cards(arb_data)

    # Dock Navigation Tabs
    dock_banner_html = """<div style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 10px 16px; margin: 16px 0 12px 0; display: flex; align-items: center; justify-content: space-between;">
<div style="display: flex; align-items: center; gap: 10px;">
<span style="font-size: 1.15rem;">⚡</span>
<span style="font-weight: 700; color: #f8fafc; font-size: 0.95rem; letter-spacing: 0.3px;">
基差统计套利全景透视 (Basis Mean-Reversion + Carry Double-Dip)
</span>
<span style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); padding: 2px 8px; border-radius: 6px; font-size: 0.72rem; font-weight: 600;">
均值回归脉冲引擎
</span>
</div>
<span style="color: #64748b; font-size: 0.75rem;">
💡 捕捉 0.7%~1.2% 脉冲，0.15% 压缩极速收割
</span>
</div>"""
    if hasattr(st, "html"):
        st.html(dock_banner_html)
    else:
        st.markdown(dock_banner_html, unsafe_allow_html=True)

    tab_active, tab_closed, tab_radar = st.tabs([
        f"⚡ 活跃基差持仓  [{len(active_pos)} 仓位]",
        f"📜 均值回归战绩  [{len(closed_trades)} 笔已结算]",
        f"📡 全市场基差雷达  [{len(market_comp)} 标的监控]",
    ])

    with tab_active:
        if not active_pos:
            st.info("ℹ️ 当前暂无活跃的基差偏离套利持仓。策略正以 1 秒高频持续巡检主流币跨所价差，一旦盘口基差偏离 ≥ 0.70%，将自动以两所等额多空对冲介入！")
        else:
            market_comp_map = {m.get("token"): m for m in market_comp if m.get("token")}
            now_ts = time.time()
            for p in active_pos:
                min_hold = int(p.get("min_holding_seconds", arb_data.get("min_holding_seconds", 60)))
                tok = p.get("token", "")

                # Accurate dynamic duration calculation from open timestamp
                open_ts = float(p.get("open_timestamp", 0.0))
                if open_ts > 0 and now_ts >= open_ts:
                    dur_sec = max(0, int(now_ts - open_ts))
                else:
                    dur_sec = max(0, int(p.get("duration_seconds", 0)))
                dur_str = format_precise_duration(dur_sec)
                open_time_str = format_timestamp(open_ts, active_tz) if open_ts > 0 else "未知"

                entry_basis = float(p.get("entry_basis_pct", 0.0))
                target_comp = float(p.get("target_compression_pct", 0.0005))
                pos_size = float(p.get("position_size", 0.0))
                dual_notional = float(p.get("total_notional", pos_size * 2.0))
                pos_lev = float(p.get("leverage") or leverage)
                if pos_lev <= 0:
                    pos_lev = 1.0
                tot_margin = float(p.get("total_margin", (pos_size / pos_lev) * 2.0 if pos_lev > 0 else pos_size * 2.0))
                margin1 = float(p.get("margin_1", pos_size / pos_lev if pos_lev > 0 else pos_size))
                margin2 = float(p.get("margin_2", pos_size / pos_lev if pos_lev > 0 else pos_size))

                p1_entry = float(p.get("entry_price_1", 0.0))
                p2_entry = float(p.get("entry_price_2", 0.0))
                amt1 = float(p.get("amount_1", pos_size / p1_entry if p1_entry > 0 else 0.0))
                amt2 = float(p.get("amount_2", pos_size / p2_entry if p2_entry > 0 else 0.0))

                # Real-time prices & basis from market_comp or pos
                mc_info = market_comp_map.get(tok, {})
                p1_mc = mc_info.get("price_1")
                p2_mc = mc_info.get("price_2")
                p1_curr = float(p.get("current_price_1") or (p1_mc if p1_mc and p1_mc > 0 else p1_entry))
                p2_curr = float(p.get("current_price_2") or (p2_mc if p2_mc and p2_mc > 0 else p2_entry))

                if "current_basis_pct" in p and abs(float(p["current_basis_pct"])) > 0:
                    curr_basis = float(p["current_basis_pct"])
                elif mc_info.get("basis_spread_pct") is not None:
                    curr_basis = float(mc_info["basis_spread_pct"])
                else:
                    curr_basis = float(p.get("entry_basis_pct", 0.0))

                side1 = p.get("side_1", "")
                side2 = p.get("side_2", "")
                c1 = p.get("connector_1", "")
                c2 = p.get("connector_2", "")

                # Floating PnL breakdown
                if side1 == "BUY":
                    l1 = (p1_curr - p1_entry) * amt1
                else:
                    l1 = (p1_entry - p1_curr) * amt1

                if side2 == "BUY":
                    l2 = (p2_curr - p2_entry) * amt2
                else:
                    l2 = (p2_entry - p2_curr) * amt2

                gross_unrealized = l1 + l2
                fee_est = float(p.get("trading_fee", ((amt1 * p1_entry) + (amt2 * p2_entry) + (amt1 * p1_curr) + (amt2 * p2_curr)) * 0.0005))
                net_unrealized = gross_unrealized - fee_est
                carry = float(p.get("settled_funding_pnl", p.get("funding_income", 0.0)))
                total_pos_net = net_unrealized + carry
                roe_unrealized = total_pos_net / tot_margin if tot_margin > 0 else 0.0

                daily_carry = float(p.get("expected_daily_carry", 0.0))
                indicative_carry = float(pos_size * daily_carry * (dur_sec / 86400.0))

                f_payments = p.get("funding_payments", [])
                settle_cnt = int(p.get("settlement_count") or len(set(fp.get("timestamp") for fp in f_payments if fp.get("timestamp"))))

                # Anti-churn lock badge: clear countdown & status
                if dur_sec < min_hold:
                    rem_sec = min_hold - dur_sec
                    lock_badge = f'<span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; margin-left: 8px;">🛡️ 最短持仓冷静中 (已持仓 {dur_sec}s / 剩余 {rem_sec}s 解锁)</span>'
                else:
                    lock_badge = f'<span style="background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; margin-left: 8px;">🟢 止盈就绪 (已过冷静期 {min_hold}s)</span>'

                # Compression progress calculation
                initial_abs_gap = abs(entry_basis)
                current_abs_gap = abs(curr_basis)
                progress_pct = max(0.0, min(100.0, (1.0 - (current_abs_gap / initial_abs_gap)) * 100.0)) if initial_abs_gap > 0 else 100.0

                pnl_color = "#10b981" if total_pos_net >= 0 else "#ef4444"

                pos_card_html = f"""<div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(59, 130, 246, 0.25); border-radius: 10px; padding: 14px; margin-bottom: 12px;">
<div style="display: flex; justify-content: space-between; align-items: center;">
<div style="display: flex; align-items: center; flex-wrap: wrap; gap: 4px;">
<span style="font-size: 1.15rem; font-weight: 700; color: #f8fafc;">🪙 {tok}</span>
<span style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; margin-left: 4px;">
{side1} {c1} + {side2} {c2}
</span>
{lock_badge}
<span style="color: #94a3b8; font-size: 0.8rem; margin-left: 6px;">⏱️ 持仓: <b>{dur_str}</b></span>
<span style="color: #64748b; font-size: 0.75rem; margin-left: 4px;">(开仓于 {open_time_str})</span>
</div>
<div style="text-align: right;">
<span style="font-size: 1.15rem; font-weight: 700; color: {pnl_color};">${total_pos_net:+,.2f}</span>
<span style="font-size: 0.82rem; font-weight: 600; color: {pnl_color}; margin-left: 6px;">(ROE {roe_unrealized:+.2%})</span>
<div style="font-size: 0.75rem; color: #94a3b8;">毛价差 ${gross_unrealized:+,.2f} | 扣4腿手续费 -${fee_est:,.2f}</div>
</div>
</div>
<!-- 资金与仓位规格明细面板 -->
<div style="background: rgba(30, 41, 59, 0.45); border-radius: 8px; padding: 10px 14px; margin-top: 10px; border: 1px solid rgba(255, 255, 255, 0.05);">
<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 0.84rem; color: #f1f5f9;">
<span>💰 单边名义货值: <b style="color:#38bdf8;">${pos_size:,.2f}</b></span>
<span>🌐 双边总敞口: <b style="color:#a78bfa;">${dual_notional:,.2f}</b> <small style="color:#94a3b8;">(2 × 单边)</small></span>
<span>🛡️ 实缴总保证金: <b style="color:#fbbf24;">${tot_margin:,.2f}</b> <small style="color:#94a3b8;">({pos_lev:.0f}x 杠杆)</small></span>
<span>💳 预估4腿手续费: <b style="color:#f87171;">-${fee_est:,.2f}</b></span>
<span style="background: rgba(16, 185, 129, 0.2); color: #34d399; font-size: 0.72rem; padding: 1px 6px; border-radius: 4px; font-weight: 600;">🪙 严格等币数 (Delta-Zero)</span>
</div>
<div style="display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; font-size: 0.78rem; color: #cbd5e1; margin-top: 6px; border-top: 1px dashed rgba(255, 255, 255, 0.1); padding-top: 6px;">
<span>🔹 腿1 ({c1}): <b>{side1} {amt1:,.4f} {tok}</b> @ 开仓 ${p1_entry:.4f} (当前 ${p1_curr:.4f} | 当前货值 ${amt1 * p1_curr:,.2f} | 押金 ${margin1:,.2f})</span>
<span>🔸 腿2 ({c2}): <b>{side2} {amt2:,.4f} {tok}</b> @ 开仓 ${p2_entry:.4f} (当前 ${p2_curr:.4f} | 当前货值 ${amt2 * p2_curr:,.2f} | 押金 ${margin2:,.2f})</span>
</div>
</div>
<div style="display: flex; justify-content: space-between; margin-top: 10px; font-size: 0.82rem; color: #cbd5e1; flex-wrap: wrap; gap: 6px;">
<span>开仓基差: <b>{entry_basis:+.2%}</b></span>
<span>当前实时基差: <b>{curr_basis:+.2%}</b></span>
<span>目标止盈线: <b>≤ ±{target_comp:.2%}</b></span>
<span>已结资金费: <b style="color: #10b981;">+${carry:.2f}</b> <small style="color:#94a3b8;">({settle_cnt}周期)</small></span>
<span>待结预估: <small style="color:#94a3b8;">+${indicative_carry:.2f}</small></span>
</div>
<div style="margin-top: 8px;">
<div style="display: flex; justify-content: space-between; font-size: 0.72rem; color: #94a3b8; margin-bottom: 2px;">
<span>均值回归收敛进度</span>
<span>{progress_pct:.1f}%</span>
</div>
<div style="background: #1e293b; height: 6px; border-radius: 3px; overflow: hidden;">
<div style="background: linear-gradient(90deg, #3b82f6, #10b981); width: {progress_pct}%; height: 100%;"></div>
</div>
</div>
</div>"""
                if hasattr(st, "html"):
                    st.html(pos_card_html)
                else:
                    st.markdown(pos_card_html, unsafe_allow_html=True)

    with tab_closed:
        if not closed_trades:
            st.info("ℹ️ 暂无历史平仓结算战报。一旦基差完成均值回归或触发止盈，平仓记录将实时在此归档。")
        else:
            closed_rows = []
            for t in reversed(closed_trades):
                dur_sec = int(t.get("duration_seconds", 0))
                dur_str = format_precise_duration(dur_sec)
                fee, depth_slip_val, latency_slip_val, friction_tot = resolve_trade_friction(t)
                gross_tpnl = float(t.get("gross_trading_pnl", float(t.get("trading_pnl", 0)) + friction_tot))
                fpnl = float(t.get("funding_pnl", 0))
                real_net_trade = gross_tpnl - friction_tot + fpnl
                paper_net = gross_tpnl - fee + fpnl
                
                pos_size_val = float(t.get("position_size", 1500.0))
                dual_notional_val = float(t.get("total_notional", pos_size_val * 2.0))
                lev_val = float(t.get("leverage") or leverage)
                if lev_val <= 0:
                    lev_val = 1.0
                margin_val = float(t.get("total_margin", (pos_size_val / lev_val) * 2.0 if lev_val > 0 else pos_size_val * 2.0))
                p1_ent = float(t.get("entry_price_1", 0.0))
                p2_ent = float(t.get("entry_price_2", 0.0))
                amt_1 = float(t.get("amount_1", pos_size_val / p1_ent if p1_ent > 0 else 0.0))
                amt_2 = float(t.get("amount_2", pos_size_val / p2_ent if p2_ent > 0 else 0.0))

                roe_pct = real_net_trade / margin_val if margin_val > 0 else 0.0
                retention_pct = (real_net_trade / gross_tpnl * 100.0) if gross_tpnl > 0 else 0.0

                closed_rows.append({
                    "平仓时间": format_timestamp(t.get("close_timestamp", 0), active_tz),
                    "标的": t.get("token", ""),
                    "交易方向": f"{t.get('side_1', '')} HL / {t.get('side_2', '')} BN",
                    "持仓时长": dur_str,
                    "开仓币数 (HL/BN)": f"{amt_1:,.2f} / {amt_2:,.2f}",
                    "单边货值": f"${pos_size_val:,.2f}",
                    "双边总敞口": f"${dual_notional_val:,.2f}",
                    "实缴保证金": f"${margin_val:,.2f} ({lev_val:.0f}x)",
                    "开仓基差": f"{float(t.get('entry_basis_pct', 0)):+.2%}",
                    "平仓基差": f"{float(t.get('close_basis_pct', 0)):+.2%}",
                    "理论毛价差": f"${gross_tpnl:+,.2f}",
                    "深度滑点损耗": f"-${depth_slip_val:,.2f}",
                    "时延跳价损耗": f"-${latency_slip_val:,.2f}",
                    "预估总手续费": f"-${fee:,.2f}",
                    "摩擦总成本": f"-${friction_tot:,.2f}",
                    "已结资金费": f"${fpnl:+,.2f}",
                    "跨越结算窗口": f"{t.get('settlement_count', 0)} 周期",
                    "穿透真实净利": f"${real_net_trade:+,.2f}",
                    "纸面净利 (仅扣佣金)": f"${paper_net:+,.2f}",
                    "真实本金 ROE": f"{roe_pct:+.2%}",
                    "Alpha 留存率": f"{retention_pct:.1f}%",
                    "平仓原因": t.get("close_reason", ""),
                })
            df_closed = pd.DataFrame(closed_rows)
            st.dataframe(df_closed, use_container_width=True, hide_index=True)

    with tab_radar:
        if not market_comp:
            st.info("ℹ️ 正在从 Binance 与 Hyperliquid 深度盘口拉取实时基差数据...")
        else:
            radar_rows = []
            for m in market_comp:
                p1_val = float(m.get('price_1', 0))
                p2_val = float(m.get('price_2', 0))
                p1_str = f"${p1_val:,.4f}" if p1_val > 0 else "N/A"
                p2_str = f"${p2_val:,.4f}" if p2_val > 0 else "N/A"
                radar_rows.append({
                    "标的": m.get("token", ""),
                    "Hyperliquid 价格": p1_str,
                    "Binance 价格": p2_str,
                    "可成交基差偏离率": f"{float(m.get('basis_spread_pct', 0)):+.3%}",
                    "HL 日资金费率": f"{float(m.get('c1_funding_daily', 0)):+.3%}/d",
                    "BN 日资金费率": f"{float(m.get('c2_funding_daily', 0)):+.3%}/d",
                    "顺风利差预计": f"{float(m.get('carry_if_short_c1', 0)):+.3%}/d",
                    "偏离撮合状态": m.get("dislocation_status", ""),
                })
            df_radar = pd.DataFrame(radar_rows)
            st.dataframe(df_radar, use_container_width=True, hide_index=True)
