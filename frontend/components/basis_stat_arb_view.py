import json
import os
import time
from datetime import datetime, timezone
import pandas as pd
import streamlit as st

try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

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


def _render_closed_trade_card_html(p: dict) -> str:
    """Render an institutional-grade visual execution card for a single closed trade."""
    tok = p["token"]
    side1 = p["side_1"]
    side2 = p["side_2"]
    c1 = p["connector_1"]
    c2 = p["connector_2"]
    net = p["real_net_trade"]
    gross = p["gross_tpnl"]
    fee = p["fee"]
    depth_slip = p["depth_slip_val"]
    latency_slip = p["latency_slip_val"]
    fric_tot = p["friction_tot"]
    fpnl = p["fpnl"]
    settle_cnt = p["settle_cnt"]
    roe = p["roe_pct"]
    retention = p["retention_pct"]
    dur_str = p["dur_str"]
    close_time_str = p["close_time_str"]
    entry_b = p["entry_basis"]
    close_b = p["close_basis"]
    cap_bps = p.get("captured_basis_bps", (entry_b - close_b) * 10000.0)
    cap_pct = p.get("captured_basis_pct", cap_bps / 10000.0)
    comp_pct = p["compression_pct"]
    pos_sz = p["pos_size_val"]
    dual_notional = p["dual_notional_val"]
    lev = p["lev_val"]
    margin = p["margin_val"]
    amt1 = p["amt_1"]
    amt2 = p["amt_2"]
    p1_ent = p["p1_ent"]
    p2_ent = p["p2_ent"]
    p1_cls = p["p1_close"]
    p2_cls = p["p2_close"]
    reason = p["close_reason"]

    if "TP" in reason or "Mean-Reversion" in reason or "take_profit" in reason or "compression" in reason.lower():
        reason_badge = '<span style="background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; font-weight: 600;">🟢 均值回归达标止盈</span>'
    elif "trailing" in reason.lower():
        reason_badge = '<span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; font-weight: 600;">🟡 移动止盈回撤保利</span>'
    elif "stop" in reason.lower():
        reason_badge = '<span style="background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; font-weight: 600;">🔴 基差离散风控止损</span>'
    elif "timeout" in reason.lower() or "hold" in reason.lower():
        reason_badge = '<span style="background: rgba(148, 163, 184, 0.2); color: #cbd5e1; border: 1px solid rgba(148, 163, 184, 0.4); padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; font-weight: 600;">⏳ 最大持仓超时平仓</span>'
    else:
        reason_badge = f'<span style="background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); padding: 2px 7px; border-radius: 4px; font-size: 0.72rem; font-weight: 600;">🏁 {reason[:20]}</span>'

    pnl_color = "#10b981" if net >= 0 else "#ef4444"
    pnl_bg = "rgba(16, 185, 129, 0.15)" if net >= 0 else "rgba(239, 68, 68, 0.15)"
    pnl_border = "rgba(16, 185, 129, 0.3)" if net >= 0 else "rgba(239, 68, 68, 0.3)"
    card_border = "rgba(16, 185, 129, 0.3)" if net >= 0 else "rgba(239, 68, 68, 0.3)"

    p1_cls_str = f"${p1_cls:,.4f}" if p1_cls > 0 else "市价结算"
    p2_cls_str = f"${p2_cls:,.4f}" if p2_cls > 0 else "市价结算"

    net_str = f"+${net:,.2f}" if net >= 0 else f"-${abs(net):,.2f}"
    gross_str = f"+${gross:,.2f}" if gross >= 0 else f"-${abs(gross):,.2f}"
    fpnl_str = f"+${fpnl:,.2f}" if fpnl >= 0 else f"-${abs(fpnl):,.2f}"

    return f"""<div style="background: rgba(15, 23, 42, 0.7); border: 1px solid {card_border}; border-radius: 12px; padding: 16px 18px; margin-bottom: 14px; box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.35);">
<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
    <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 6px;">
        <span style="font-size: 1.25rem; font-weight: 800; color: #f8fafc; letter-spacing: 0.02em;">🪙 {tok}</span>
        <span style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); padding: 2px 7px; border-radius: 5px; font-size: 0.75rem; font-weight: 600;">
            {side1} {c1} + {side2} {c2}
        </span>
        {reason_badge}
        <span style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.35); padding: 2px 7px; border-radius: 5px; font-size: 0.75rem; font-weight: 700; font-family: monospace;">
            🎯 捕获基差: {cap_bps:+.1f} bps
        </span>
        <span style="color: #94a3b8; font-size: 0.8rem; margin-left: 4px;">⏱️ 历时: <b style="color: #f1f5f9;">{dur_str}</b></span>
        <span style="color: #64748b; font-size: 0.75rem;">(平仓于 {close_time_str})</span>
    </div>
    <div style="text-align: right;">
        <div style="display: flex; align-items: baseline; justify-content: flex-end; gap: 8px;">
            <span style="font-size: 1.35rem; font-weight: 800; color: {pnl_color}; letter-spacing: -0.01em;">{net_str} USD</span>
            <span style="background: {pnl_bg}; color: {pnl_color}; border: 1px solid {pnl_border}; padding: 2px 8px; border-radius: 6px; font-size: 0.82rem; font-weight: 700;">
                {roe:+.2%} ROE
            </span>
        </div>
        <div style="font-size: 0.75rem; color: #94a3b8; margin-top: 2px;">
            理论毛利 <b style="color: #e2e8f0;">{gross_str}</b> · 摩擦损耗 <b style="color: #f87171;">-${fric_tot:,.2f}</b> · Alpha 留存 <b style="color: #38bdf8;">{retention:.1f}%</b>
        </div>
    </div>
</div>

<div style="background: rgba(15, 23, 42, 0.55); border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 8px; padding: 10px 14px; margin-top: 12px;">
    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.82rem; margin-bottom: 6px; flex-wrap: wrap; gap: 4px;">
        <div style="display: flex; align-items: center; gap: 6px;">
            <span style="color: #94a3b8;">🎯 入场基差偏离:</span>
            <span style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); padding: 1px 7px; border-radius: 4px; font-weight: 700; font-family: monospace;">
                {entry_b:+.2%} ({entry_b*10000:+.0f} bps)
            </span>
        </div>
        <div style="display: flex; align-items: center; gap: 6px;">
            <span style="background: rgba(56, 189, 248, 0.18); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.4); padding: 2px 8px; border-radius: 5px; font-weight: 700; font-size: 0.82rem; font-family: monospace;">
                🎯 实际捕获基差: {cap_bps:+.1f} bps ({cap_pct:+.2%})
            </span>
            <span style="color: #64748b; font-size: 0.74rem;">(均值归拢收敛度 <b>{comp_pct:.1f}%</b>)</span>
        </div>
        <div style="display: flex; align-items: center; gap: 6px;">
            <span style="color: #94a3b8;">🏁 出场收敛基差:</span>
            <span style="background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); padding: 1px 7px; border-radius: 4px; font-weight: 700; font-family: monospace;">
                {close_b:+.2%} ({close_b*10000:+.0f} bps)
            </span>
        </div>
    </div>
    <div style="background: #1e293b; height: 6px; border-radius: 3px; overflow: hidden;">
        <div style="background: linear-gradient(90deg, #f59e0b 0%, #3b82f6 50%, #10b981 100%); width: {comp_pct}%; height: 100%;"></div>
    </div>
</div>

<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin-top: 12px;">
    <div style="background: rgba(30, 41, 59, 0.45); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px; padding: 10px 12px; font-size: 0.8rem; color: #cbd5e1;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 6px; border-bottom: 1px dashed rgba(255, 255, 255, 0.08); padding-bottom: 4px;">
            <span style="color: #94a3b8; font-weight: 600;">⚖️ 双边敞口与对冲规格</span>
            <span style="color: #34d399; font-weight: 600;">🪙 等币数 Delta-Zero</span>
        </div>
        <div style="display: flex; justify-content: space-between; margin-bottom: 3px;">
            <span>单边货值: <b style="color: #38bdf8;">${pos_sz:,.2f}</b></span>
            <span>双边总敞口: <b style="color: #a78bfa;">${dual_notional:,.2f}</b></span>
            <span>实缴保证金: <b style="color: #fbbf24;">${margin:,.2f}</b> ({lev:.0f}x)</span>
        </div>
        <div style="font-size: 0.76rem; color: #94a3b8; margin-top: 6px;">
            <div>🔹 {side1} {c1}: {amt1:,.4f} {tok} @ 开仓 ${p1_ent:.4f} ➔ 平仓 {p1_cls_str}</div>
            <div style="margin-top: 2px;">🔸 {side2} {c2}: {amt2:,.4f} {tok} @ 开仓 ${p2_ent:.4f} ➔ 平仓 {p2_cls_str}</div>
        </div>
    </div>

    <div style="background: rgba(30, 41, 59, 0.45); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px; padding: 10px 12px; font-size: 0.8rem; color: #cbd5e1;">
        <div style="display: flex; justify-content: space-between; margin-bottom: 6px; border-bottom: 1px dashed rgba(255, 255, 255, 0.08); padding-bottom: 4px;">
            <span style="color: #94a3b8; font-weight: 600;">💳 财务穿透与摩擦拆解瀑布流</span>
            <span style="color: #38bdf8; font-weight: 600;">实得净利 {net_str}</span>
        </div>
        <div style="display: flex; justify-content: space-between; margin-bottom: 3px;">
            <span>理论毛价差: <b style="color: #f8fafc;">{gross_str}</b></span>
            <span>预估手续费: <b style="color: #f87171;">-${fee:,.2f}</b></span>
            <span>滑点与跳价: <b style="color: #f87171;">-${(depth_slip + latency_slip):,.2f}</b></span>
        </div>
        <div style="display: flex; justify-content: space-between; font-size: 0.76rem; color: #94a3b8; margin-top: 6px;">
            <span>深度滑点: -${depth_slip:,.2f} | 时延跳价: -${latency_slip:,.2f}</span>
            <span>已结资金费: <b style="color: #10b981;">{fpnl_str}</b> ({settle_cnt}周期)</span>
        </div>
    </div>
</div>
</div>"""


def render_closed_trades_analytics(parsed_trades: list):
    """Render interactive institutional charts for closed trades."""
    if not HAS_PLOTLY or not parsed_trades:
        st.info("ℹ️ 暂无足够图表数据或绘图库未加载。")
        return

    # Chronological sort for equity curve
    chron_trades = sorted(parsed_trades, key=lambda x: x["close_time_ts"])

    # 1. Cumulative Net PnL Curve
    cum_pnl = 0.0
    equity = []
    hover_texts = []
    for i, t in enumerate(chron_trades, 1):
        cum_pnl += t["real_net_trade"]
        equity.append(cum_pnl)
        hover_texts.append(
            f"#{i} {t['token']}<br>"
            f"净利: ${t['real_net_trade']:+,.2f}<br>"
            f"ROE: {t['roe_pct']:+.2%}<br>"
            f"捕获基差: {t['captured_basis_bps']:+.1f} bps<br>"
            f"收敛: {t['compression_pct']:.1f}%<br>"
            f"平仓基差: {t['close_basis']:+.2%}<br>"
            f"原因: {t['close_reason'][:25]}"
        )

    fig_equity = go.Figure()
    fig_equity.add_trace(go.Scatter(
        x=list(range(1, len(equity) + 1)),
        y=equity,
        mode="lines+markers",
        name="累计纯利 (USD)",
        line=dict(color="#10b981", width=3, shape="hv"),
        marker=dict(size=7, color="#34d399", line=dict(color="#064e3b", width=1.5)),
        fill="tozeroy",
        fillcolor="rgba(16, 185, 129, 0.12)",
        text=hover_texts,
        hoverinfo="text+y",
    ))
    fig_equity.update_layout(
        title="📈 穿透累计纯利爬升曲线 (Cumulative Equity Curve)",
        title_font=dict(size=14, color="#f8fafc"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15, 23, 42, 0.5)",
        height=280,
        margin=dict(l=40, r=20, t=40, b=30),
        xaxis=dict(title="交易笔数 (Trades)", gridcolor="rgba(255,255,255,0.06)", tickfont=dict(color="#94a3b8")),
        yaxis=dict(title="累计净利 (USD)", gridcolor="rgba(255,255,255,0.06)", tickprefix="$", tickfont=dict(color="#94a3b8")),
        showlegend=False,
    )

    # 2. PnL Contribution by Token
    token_pnl = {}
    for t in parsed_trades:
        token_pnl[t["token"]] = token_pnl.get(t["token"], 0.0) + t["real_net_trade"]

    sorted_tokens = sorted(token_pnl.items(), key=lambda x: x[1], reverse=True)
    tok_names = [x[0] for x in sorted_tokens]
    tok_pnls = [x[1] for x in sorted_tokens]
    bar_colors = ["#10b981" if v >= 0 else "#ef4444" for v in tok_pnls]

    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        x=tok_names,
        y=tok_pnls,
        marker=dict(color=bar_colors, line=dict(color="rgba(255,255,255,0.1)", width=1)),
        text=[f"${v:+,.2f}" for v in tok_pnls],
        textposition="outside",
        textfont=dict(color="#cbd5e1", size=11),
    ))
    fig_bar.update_layout(
        title="🪙 各标的累计贡献纯利排行 (Net PnL by Token)",
        title_font=dict(size=14, color="#f8fafc"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15, 23, 42, 0.5)",
        height=280,
        margin=dict(l=40, r=20, t=40, b=30),
        xaxis=dict(gridcolor="rgba(255,255,255,0.06)", tickfont=dict(color="#cbd5e1")),
        yaxis=dict(title="累计贡献 (USD)", gridcolor="rgba(255,255,255,0.06)", tickprefix="$", tickfont=dict(color="#94a3b8")),
        showlegend=False,
    )

    chart_c1, chart_c2 = st.columns(2)
    with chart_c1:
        st.plotly_chart(fig_equity, use_container_width=True)
    with chart_c2:
        st.plotly_chart(fig_bar, use_container_width=True)


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
            "market_comparison": [],
            "market_discovery_candidates": [],
            "rotation_history": [],
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
    discovery_candidates = arb_data.get("market_discovery_candidates", [])
    rotation_history = arb_data.get("rotation_history", [])
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
    tick_latency = float(arb_data.get("tick_latency_ms", 15.0))
    headroom_pct = max(0.0, 100.0 - (tick_latency / 1000.0 * 100.0))
    dock_banner_html = f"""<div style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 10px 16px; margin: 16px 0 12px 0; display: flex; align-items: center; justify-content: space-between;">
<div style="display: flex; align-items: center; gap: 10px;">
<span style="font-size: 1.15rem;">⚡</span>
<span style="font-weight: 700; color: #f8fafc; font-size: 0.95rem; letter-spacing: 0.3px;">
基差统计套利全景透视 (Basis Mean-Reversion + Carry Double-Dip)
</span>
<span style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); padding: 2px 8px; border-radius: 6px; font-size: 0.72rem; font-weight: 600;">
均值回归脉冲引擎
</span>
<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 6px; font-size: 0.72rem; font-weight: 600;">
⏱️ 1秒巡检耗时: {tick_latency:.1f}ms (算力余量 {headroom_pct:.1f}%)
</span>
</div>
<span style="color: #64748b; font-size: 0.75rem;">
💡 1秒本地内存撮合(0网络开销) · 捕捉 0.5%~1.2% 脉冲 · 0.20% 极速收割
</span>
</div>"""
    if hasattr(st, "html"):
        st.html(dock_banner_html)
    else:
        st.markdown(dock_banner_html, unsafe_allow_html=True)

    tab_active, tab_closed, tab_radar, tab_discovery, tab_rotation = st.tabs([
        f"⚡ 活跃基差持仓  [{len(active_pos)} 仓位]",
        f"📜 均值回归战绩  [{len(closed_trades)} 笔已结算]",
        f"🎯 在役撮合池  [{len(market_comp)} 标的监控]",
        f"🔭 全网寻优雷达  [{len(discovery_candidates)} 标的巡检]",
        f"🔄 动态换血记录  [{len(rotation_history)} 次轮换]",
    ])

    with tab_active:
        if not active_pos:
            st.info("ℹ️ 当前暂无活跃的基差偏离套利持仓。策略正以 1 秒高频持续巡检主流币跨所价差，一旦盘口基差偏离 ≥ 0.50%，将自动以两所等额多空对冲介入！")
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
            # 1. Parse and normalize all closed trade records
            parsed_trades = []
            for t in reversed(closed_trades):
                dur_sec = int(t.get("duration_seconds", 0))
                dur_str = format_precise_duration(dur_sec)
                fee, depth_slip_val, latency_slip_val, friction_tot = resolve_trade_friction(t)
                gross_tpnl = float(t.get("gross_trading_pnl", float(t.get("trading_pnl", float(t.get("total_net_pnl", 0.0)))) + friction_tot))
                fpnl = float(t.get("funding_pnl", 0.0))
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
                p1_close = float(t.get("close_price_1", 0.0))
                p2_close = float(t.get("close_price_2", 0.0))

                side_1 = t.get("side_1", "SELL")
                entry_basis = float(t.get("entry_basis_pct", 0.0))
                close_basis = float(t.get("close_basis_pct", 0.0))
                abs_entry = abs(entry_basis)
                abs_close = abs(close_basis)
                compression_pct = max(0.0, min(100.0, (1.0 - (abs_close / abs_entry)) * 100.0)) if abs_entry > 0 else 100.0

                # Calculate or retrieve actual captured basis in bps
                captured_basis_bps = t.get("captured_basis_bps")
                if captured_basis_bps is None:
                    if side_1 == "SELL":
                        c_pct = entry_basis - close_basis
                    else:
                        c_pct = entry_basis + close_basis
                    captured_basis_bps = c_pct * 10000.0
                    captured_basis_pct = c_pct
                else:
                    captured_basis_bps = float(captured_basis_bps)
                    captured_basis_pct = float(t.get("captured_basis_pct", captured_basis_bps / 10000.0))

                roe_pct = real_net_trade / margin_val if margin_val > 0 else 0.0
                retention_pct = (real_net_trade / gross_tpnl * 100.0) if gross_tpnl > 0 else (100.0 if real_net_trade >= 0 else 0.0)
                retention_pct = max(-100.0, min(100.0, retention_pct))

                settle_cnt = int(t.get("settlement_count") or len(set(fp.get("timestamp") for fp in t.get("funding_payments", []) if fp.get("timestamp"))))

                parsed_trades.append({
                    "raw": t,
                    "token": t.get("token", ""),
                    "connector_1": t.get("connector_1", "hyperliquid_perpetual"),
                    "connector_2": t.get("connector_2", "binance_perpetual"),
                    "side_1": side_1,
                    "side_2": t.get("side_2", "BUY"),
                    "amt_1": amt_1,
                    "amt_2": amt_2,
                    "p1_ent": p1_ent,
                    "p2_ent": p2_ent,
                    "p1_close": p1_close,
                    "p2_close": p2_close,
                    "entry_basis": entry_basis,
                    "close_basis": close_basis,
                    "captured_basis_pct": captured_basis_pct,
                    "captured_basis_bps": captured_basis_bps,
                    "compression_pct": compression_pct,
                    "pos_size_val": pos_size_val,
                    "dual_notional_val": dual_notional_val,
                    "lev_val": lev_val,
                    "margin_val": margin_val,
                    "gross_tpnl": gross_tpnl,
                    "fee": fee,
                    "depth_slip_val": depth_slip_val,
                    "latency_slip_val": latency_slip_val,
                    "friction_tot": friction_tot,
                    "fpnl": fpnl,
                    "settle_cnt": settle_cnt,
                    "real_net_trade": real_net_trade,
                    "paper_net": paper_net,
                    "roe_pct": roe_pct,
                    "retention_pct": retention_pct,
                    "dur_sec": dur_sec,
                    "dur_str": dur_str,
                    "close_time_ts": float(t.get("close_timestamp", 0.0)),
                    "close_time_str": format_timestamp(t.get("close_timestamp", 0), active_tz),
                    "close_reason": t.get("close_reason", "take_profit"),
                })

            # 2. Overall Summary Metrics
            tot_trades = len(parsed_trades)
            win_trades = sum(1 for p in parsed_trades if p["real_net_trade"] > 0)
            loss_trades = sum(1 for p in parsed_trades if p["real_net_trade"] < 0)
            win_rate = (win_trades / tot_trades * 100.0) if tot_trades > 0 else 0.0
            tot_net = sum(p["real_net_trade"] for p in parsed_trades)
            tot_gross = sum(p["gross_tpnl"] for p in parsed_trades)
            tot_fric = sum(p["friction_tot"] for p in parsed_trades)
            tot_fee = sum(p["fee"] for p in parsed_trades)
            tot_slip = tot_fric - tot_fee
            avg_trade = tot_net / tot_trades if tot_trades > 0 else 0.0
            avg_roe = (sum(p["roe_pct"] for p in parsed_trades) / tot_trades) if tot_trades > 0 else 0.0
            overall_ret = (tot_net / tot_gross * 100.0) if tot_gross > 0 else 0.0

            net_color = "#10b981" if tot_net >= 0 else "#ef4444"

            # Top Performance Summary Strip
            perf_strip_html = f"""<div style="background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(59, 130, 246, 0.25); border-radius: 10px; padding: 12px 16px; margin-bottom: 14px;">
<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
<div style="display: flex; align-items: center; gap: 18px; flex-wrap: wrap;">
<div>
<div style="font-size: 0.72rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">战绩总览与胜率</div>
<div style="font-size: 1.15rem; font-weight: 700; color: #f8fafc; margin-top: 1px;">
{tot_trades} 笔已结算 <span style="font-size: 0.85rem; color: #34d399; font-weight: 600; background: rgba(16, 185, 129, 0.15); padding: 1px 6px; border-radius: 4px; border: 1px solid rgba(16, 185, 129, 0.3);">{win_rate:.1f}% 胜率</span>
<small style="font-size: 0.75rem; color: #94a3b8; margin-left: 4px;">({win_trades}胜 / {loss_trades}负)</small>
</div>
</div>
<div style="border-left: 1px solid rgba(255, 255, 255, 0.1); padding-left: 18px;">
<div style="font-size: 0.72rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">穿透结清真实纯利</div>
<div style="font-size: 1.15rem; font-weight: 800; color: {net_color}; margin-top: 1px;">
${tot_net:+,.2f} USD <span style="font-size: 0.78rem; color: #94a3b8; font-weight: normal;">(单均 ${avg_trade:+,.2f})</span>
</div>
</div>
<div style="border-left: 1px solid rgba(255, 255, 255, 0.1); padding-left: 18px;">
<div style="font-size: 0.72rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">单笔平均实缴 ROE</div>
<div style="font-size: 1.15rem; font-weight: 700; color: #fbbf24; margin-top: 1px;">
{avg_roe:+.2%} <small style="font-size: 0.75rem; color: #94a3b8;">(按实缴保证金计)</small>
</div>
</div>
</div>
<div style="text-align: right; border-left: 1px solid rgba(255, 255, 255, 0.1); padding-left: 18px;">
<div style="font-size: 0.72rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">摩擦损耗与 Alpha 留存</div>
<div style="font-size: 0.95rem; font-weight: 700; color: #38bdf8; margin-top: 1px;">
Alpha 留存率 {overall_ret:.1f}%
</div>
<div style="font-size: 0.74rem; color: #94a3b8;">
毛利 ${tot_gross:+,.2f} · 佣金 -${tot_fee:,.2f} · 滑点 -${tot_slip:,.2f}
</div>
</div>
</div>
</div>"""
            if hasattr(st, "html"):
                st.html(perf_strip_html)
            else:
                st.markdown(perf_strip_html, unsafe_allow_html=True)

            # View Mode Controller & Filter Bar
            v_col, _ = st.columns([3.2, 0.8])
            with v_col:
                closed_view_mode = st.radio(
                    "战绩展示视角",
                    ["🎴 机构对账单卡片流 (Visual Cards)", "📈 收益走势与标的透视 (Analytics)", "📋 全维数据大表 (Data Table)"],
                    horizontal=True,
                    key=f"closed_view_mode_{bot_name}",
                    label_visibility="collapsed",
                )

            ctrl_c1, ctrl_c2, ctrl_c3, ctrl_c4 = st.columns([1.1, 1.1, 1.3, 1.5])
            with ctrl_c1:
                tok_choices = ["全部标的"] + sorted(list(set(p["token"] for p in parsed_trades)))
                filter_tok = st.selectbox(
                    "按标的筛选",
                    tok_choices,
                    key=f"closed_tok_{bot_name}",
                    help="仅查看指定币种的历史平仓成交记录",
                )
            with ctrl_c2:
                filter_res = st.selectbox(
                    "按盈亏筛选",
                    ["全部战报", f"🟢 仅看盈利 ({win_trades})", f"🔴 仅看亏损 ({loss_trades})"],
                    key=f"closed_res_{bot_name}",
                    help="按盈亏结果筛选交易记录",
                )
            with ctrl_c3:
                time_range_choices = [
                    "全部平仓时段",
                    "过去 1 小时内",
                    "过去 6 小时内",
                    "过去 12 小时内",
                    "过去 24 小时内",
                    "过去 3 天内",
                    "过去 7 天内",
                ]
                filter_time = st.selectbox(
                    "🕒 平仓时段过滤",
                    time_range_choices,
                    key=f"closed_time_{bot_name}",
                    help="按平仓发生的时间跨度过滤记录",
                )
            with ctrl_c4:
                sort_choices = [
                    "🕒 平仓时间 (最新在前)",
                    "🕒 平仓时间 (最早在前)",
                    "💰 真实纯利 (从高到低)",
                    "🛑 真实纯利 (从低到高)",
                    "⏱️ 持仓历时 (从长到短)",
                    "⚡ 实际捕获基差 (从大到小)",
                ]
                sort_opt = st.selectbox(
                    "🔃 战绩排序方式",
                    sort_choices,
                    key=f"closed_sort_{bot_name}",
                    help="切换战绩排序方式，默认按平仓时间倒序（最新平仓置顶）",
                )

            # Apply filters
            filtered_trades = parsed_trades
            if filter_tok != "全部标的":
                filtered_trades = [p for p in filtered_trades if p["token"] == filter_tok]
            if "仅看盈利" in filter_res:
                filtered_trades = [p for p in filtered_trades if p["real_net_trade"] > 0]
            elif "仅看亏损" in filter_res:
                filtered_trades = [p for p in filtered_trades if p["real_net_trade"] < 0]

            if filter_time != "全部平仓时段":
                import time as _py_time
                now_sec = _py_time.time()
                time_window_map = {
                    "过去 1 小时内": 3600,
                    "过去 6 小时内": 6 * 3600,
                    "过去 12 小时内": 12 * 3600,
                    "过去 24 小时内": 24 * 3600,
                    "过去 3 天内": 3 * 86400,
                    "过去 7 天内": 7 * 86400,
                }
                max_age_sec = time_window_map.get(filter_time, 86400 * 365)
                max_trade_ts = max((p["close_time_ts"] for p in parsed_trades), default=now_sec)
                anchor_now = max(now_sec, max_trade_ts)
                filtered_trades = [p for p in filtered_trades if (anchor_now - p["close_time_ts"]) <= max_age_sec]

            # Apply sorting
            if "平仓时间 (最新在前)" in sort_opt:
                filtered_trades = sorted(filtered_trades, key=lambda x: x["close_time_ts"], reverse=True)
            elif "平仓时间 (最早在前)" in sort_opt:
                filtered_trades = sorted(filtered_trades, key=lambda x: x["close_time_ts"], reverse=False)
            elif "真实纯利 (从高到低)" in sort_opt:
                filtered_trades = sorted(filtered_trades, key=lambda x: x["real_net_trade"], reverse=True)
            elif "真实纯利 (从低到高)" in sort_opt:
                filtered_trades = sorted(filtered_trades, key=lambda x: x["real_net_trade"], reverse=False)
            elif "持仓历时 (从长到短)" in sort_opt:
                filtered_trades = sorted(filtered_trades, key=lambda x: x["dur_sec"], reverse=True)
            elif "实际捕获基差 (从大到小)" in sort_opt or "捕获基差" in sort_opt:
                filtered_trades = sorted(filtered_trades, key=lambda x: x["captured_basis_bps"], reverse=True)
            else:
                filtered_trades = sorted(filtered_trades, key=lambda x: x["close_time_ts"], reverse=True)

            st.caption(f"ℹ️ 当前显示 **{len(filtered_trades)} / {tot_trades}** 笔已结算战绩 · 排序：**{sort_opt}** · 时段：**{filter_time}**")

            # Generate dataframe for Table View and expander
            closed_rows = []
            for p in filtered_trades:
                closed_rows.append({
                    "平仓时间": p["close_time_str"],
                    "标的": p["token"],
                    "交易方向": f"{p['side_1']} HL / {p['side_2']} BN",
                    "持仓时长": p["dur_str"],
                    "开仓币数 (HL/BN)": f"{p['amt_1']:,.2f} / {p['amt_2']:,.2f}",
                    "单边货值": f"${p['pos_size_val']:,.2f}",
                    "双边总敞口": f"${p['dual_notional_val']:,.2f}",
                    "实缴保证金": f"${p['margin_val']:,.2f} ({p['lev_val']:.0f}x)",
                    "开仓基差": f"{p['entry_basis']:+.2%}",
                    "平仓基差": f"{p['close_basis']:+.2%}",
                    "实际捕获基差 (BPS)": f"{p['captured_basis_bps']:+.1f} bps ({p['captured_basis_pct']:+.2%})",
                    "理论毛价差": f"${p['gross_tpnl']:+,.2f}",
                    "深度滑点损耗": f"-${p['depth_slip_val']:,.2f}",
                    "时延跳价损耗": f"-${p['latency_slip_val']:,.2f}",
                    "预估总手续费": f"-${p['fee']:,.2f}",
                    "摩擦总成本": f"-${p['friction_tot']:,.2f}",
                    "已结资金费": f"${p['fpnl']:+,.2f}",
                    "跨越结算窗口": f"{p['settle_cnt']} 周期",
                    "穿透真实净利": f"${p['real_net_trade']:+,.2f}",
                    "纸面净利 (仅扣佣金)": f"${p['paper_net']:+,.2f}",
                    "真实本金 ROE": f"{p['roe_pct']:+.2%}",
                    "Alpha 留存率": f"{p['retention_pct']:.1f}%",
                    "平仓原因": p["close_reason"],
                })
            df_closed = pd.DataFrame(closed_rows)

            # View Dispatcher
            if "Cards" in closed_view_mode:
                if not filtered_trades:
                    st.warning("⚠️ 筛选条件下无匹配的历史结算记录。")
                else:
                    if len(filtered_trades) > 15:
                        limit_col1, limit_col2 = st.columns([3, 1])
                        with limit_col2:
                            page_limit = st.selectbox("每页显示记录", [10, 20, 50, "全部"], index=1, key=f"page_lim_{bot_name}")
                        max_disp = len(filtered_trades) if page_limit == "全部" else int(page_limit)
                    else:
                        max_disp = len(filtered_trades)

                    for p in filtered_trades[:max_disp]:
                        card_html = _render_closed_trade_card_html(p)
                        if hasattr(st, "html"):
                            st.html(card_html)
                        else:
                            st.markdown(card_html, unsafe_allow_html=True)

                    if max_disp < len(filtered_trades):
                        st.caption(f"ℹ️ 当前显示前 {max_disp} 条记录，共 {len(filtered_trades)} 条。如需查看全部请调整每页显示数量或切换至大表视图。")

                    # Collapsible raw table below the cards
                    with st.expander(f"📋 展开全维数据原始大表 [{len(df_closed)} 条筛选记录]", expanded=False):
                        st.dataframe(df_closed, use_container_width=True, hide_index=True)

            elif "Analytics" in closed_view_mode:
                if not filtered_trades:
                    st.warning("⚠️ 筛选条件下无匹配的历史结算记录。")
                else:
                    render_closed_trades_analytics(filtered_trades)
                    with st.expander(f"📋 展开对应全维数据表 [{len(df_closed)} 条筛选记录]", expanded=False):
                        st.dataframe(df_closed, use_container_width=True, hide_index=True)

            else:  # Data Table View
                st.dataframe(df_closed, use_container_width=True, hide_index=True)
                st.caption(f"📊 已展示 {len(df_closed)} 条历史结算记录，包含 22 项全维金融穿透字段。")

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
                c1_depth = float(m.get('c1_top3_depth', 0))
                c2_depth = float(m.get('c2_top3_depth', 0))
                safe_cap = float(m.get('safe_capacity', 0))
                net_edge_val = m.get('net_executable_edge_pct')
                net_edge_str = f"{float(net_edge_val):+.3%}" if net_edge_val is not None else "N/A"
                
                radar_rows.append({
                    "标的": m.get("token", ""),
                    "Hyperliquid 价格": p1_str,
                    "Binance 价格": p2_str,
                    "HL Top3深度": f"${c1_depth:,.0f}" if c1_depth > 0 else "N/A",
                    "BN Top3深度": f"${c2_depth:,.0f}" if c2_depth > 0 else "N/A",
                    "安全定额容量": f"${safe_cap:,.0f}" if safe_cap > 0 else "N/A",
                    "可成交基差": f"{float(m.get('basis_spread_pct', 0)):+.3%}",
                    "VWAP 净空间": net_edge_str,
                    "HL 日资金费率": f"{float(m.get('c1_funding_daily', 0)):+.3%}/d",
                    "BN 日资金费率": f"{float(m.get('c2_funding_daily', 0)):+.3%}/d",
                    "顺风利差预计": f"{float(m.get('carry_if_short_c1', 0)):+.3%}/d",
                    "偏离撮合状态": m.get("dislocation_status", ""),
                })
            df_radar = pd.DataFrame(radar_rows)
            st.dataframe(df_radar, use_container_width=True, hide_index=True)

    with tab_discovery:
        st.markdown("##### 🔭 全网待命寻优雷达 (Tier 1 REST Background Discovery)")
        st.caption("后台轻量 REST 巡检 Binance 与 Hyperliquid 全量 185+ 永续合约标的。一旦发现盘口偏离 ≥ 0.30% 且 24h 成交量达标的优质标的，系统将智能淘汰在役池中基差最低的僵尸代币，动态热插拔吸纳新标的。")
        if not discovery_candidates:
            st.info("ℹ️ 全网寻优引擎正在后台执行轻量 REST 巡检（约 3 分钟一轮），请稍候刷新...")
        else:
            cand_rows = []
            for c in discovery_candidates:
                is_act = c.get("is_active", False)
                basis = float(c.get("basis_pct", 0.0))
                hl_vol = float(c.get("hl_volume_24h", 0.0))
                bn_vol = float(c.get("bn_volume_24h", 0.0))
                min_vol = float(c.get("min_volume_24h", min(hl_vol, bn_vol)))

                # Eligibility status
                if is_act:
                    status_badge = "🛡️ 在役护城河 (ACTIVE)"
                elif abs(basis) >= 0.0030 and bn_vol >= 2000000.0 and hl_vol >= 500000.0:
                    status_badge = "⚡ 达标待命 (QUALIFIED)"
                elif abs(basis) >= 0.0030:
                    status_badge = "⚠️ 基差偏离 (流动性不足)"
                else:
                    status_badge = "⏳ 观察中 (STANDBY)"

                cand_rows.append({
                    "标的": c.get("token", ""),
                    "状态": status_badge,
                    "理论基差": f"{basis:+.2%}",
                    "HL 价格": f"${float(c.get('hl_price', 0)):,.4f}",
                    "BN 价格": f"${float(c.get('bn_price', 0)):,.4f}",
                    "HL 24h成交量": f"${hl_vol/1e6:,.2f}M",
                    "BN 24h成交量": f"${bn_vol/1e6:,.2f}M",
                    "在役驻留时长": f"{c.get('dwell_seconds', 0)//60} 分钟" if is_act else "-",
                    "最新巡检时间": c.get("scanned_at", ""),
                })
            df_cand = pd.DataFrame(cand_rows)
            st.dataframe(df_cand, use_container_width=True, hide_index=True)

    with tab_rotation:
        st.markdown("##### 🔄 动态换血轮换记录 (Universe Rotation History Ledger)")
        st.caption("记录前台 48 席位与后台 185 待命池之间的动态热插拔轮换记录。严格遵守持仓免疫、30分钟驻留保护与 30 bps 净优势门槛。")
        if not rotation_history:
            st.info("ℹ️ 暂无动态轮换记录。当前 48 个在役标的运行良好；一旦出现高偏离热点脉冲标的，系统将在此自动登记轮换日志。")
        else:
            rot_rows = []
            for r in reversed(rotation_history):
                rot_rows.append({
                    "轮换时间": r.get("time_str", ""),
                    "淘汰标的": r.get("evicted_token", ""),
                    "淘汰时基差": f"{float(r.get('evicted_basis_pct', 0)):.2%}",
                    "引入新标的": r.get("admitted_token", ""),
                    "新标的基差": f"{float(r.get('admitted_basis_pct', 0)):.2%}",
                    "脉冲优势 (Δ)": f"{float(r.get('delta_edge_pct', 0)):+.2%}",
                    "新标的24h成交量": f"${float(r.get('admitted_volume_usd', 0))/1e6:,.2f}M",
                    "轮换原因": r.get("reason", ""),
                })
            df_rot = pd.DataFrame(rot_rows)
            st.dataframe(df_rot, use_container_width=True, hide_index=True)
