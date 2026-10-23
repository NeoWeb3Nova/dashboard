"""
Spot-Perp Cash & Carry Yield Engine Dashboard Component.
Institutional monitoring for Binance Spot (Long physical coin) + Hyperliquid Perpetual (Short contract).
Features:
- High-level KPI strip (Active pairs, Harvested Funding, Real ROE, Margin Buffer)
- Live Active Positions with 1-Hour Settlement Countdown & Liquidation Cushion Gauge
- Real-time High-Yield Funding Radar & Payback Barrier Matrix
- Historical Closed Trades Ledger & Financial PnL Attribution
"""
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def get_bot_spot_perp_cash_carry_data(bot_name: str, performance: dict) -> Tuple[bool, Optional[dict]]:
    """Retrieve spot_perp_cash_carry data from MQTT or local disk with strict isolation."""
    # Strict isolation: ignore funding_rate_arb and basis_stat_arb bots
    if "basis" in bot_name.lower() or "funding_rate_arb" in performance or "basis_stat_arb" in performance:
        if "spot_perp" not in bot_name.lower():
            return False, None

    arb_data = None
    for ctrl_id, ctrl_dict in performance.items():
        if ctrl_id in ["spot_perp_cash_carry", "spot_perp"]:
            c_info = ctrl_dict.get("custom_info", {})
            if "active_positions" in c_info or "closed_trades" in c_info or "market_candidates" in c_info:
                arb_data = c_info
                break

    candidate_paths = [
        os.path.abspath(f"../hummingbot-api/bots/instances/{bot_name}/data/spot_perp_cash_carry_trades.json"),
        os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/instances/{bot_name}/data/spot_perp_cash_carry_trades.json"),
        os.path.abspath(f"../hummingbot-api/bots/data/spot_perp_cash_carry_trades.json"),
        os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/data/spot_perp_cash_carry_trades.json"),
    ]

    if not arb_data:
        for json_path in candidate_paths:
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r") as jf:
                        arb_data = json.load(jf)
                    if arb_data and arb_data.get("strategy") == "spot_perp_cash_carry":
                        break
                except Exception:
                    pass

    is_spot_perp_bot = (
        "spot_perp" in bot_name.lower() or
        "spot-perp" in bot_name.lower() or
        "spot_perp_cash_carry" in performance or
        (arb_data is not None and arb_data.get("strategy") == "spot_perp_cash_carry")
    )

    return is_spot_perp_bot, arb_data


def render_spot_perp_cash_carry_kpi_cards(arb_data: dict):
    """Render top institutional KPI strip."""
    if not arb_data:
        return

    active_positions = arb_data.get("active_positions", [])
    closed_trades = arb_data.get("closed_trades", [])

    tot_active = len(active_positions)
    max_slots = arb_data.get("max_concurrent_tokens", 3)

    tot_net_closed = sum(float(t.get("total_net_pnl", 0.0)) for t in closed_trades)
    tot_funding_closed = sum(float(t.get("settled_funding_pnl", 0.0)) for t in closed_trades)
    funding_active = sum(float(p.get("settled_funding_pnl", 0.0)) for p in active_positions)
    tot_funding_all = tot_funding_closed + funding_active

    spot_capital_used = sum(float(p.get("spot_margin", p.get("position_size", 0.0))) for p in active_positions)
    perp_margin_used = sum(float(p.get("perp_margin", 0.0)) for p in active_positions)
    tot_capital_used = spot_capital_used + perp_margin_used

    avg_active_apr = (sum(float(p.get("current_apr", p.get("entry_apr", 0.0))) for p in active_positions) / tot_active) if tot_active > 0 else 0.0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric(
            label="🪙 在役现货-永续对冲对",
            value=f"{tot_active} / {max_slots} 标的",
            delta="1h 连续结算中" if tot_active > 0 else "空仓待命中",
        )
    with c2:
        st.metric(
            label="📈 已结入账资金费现金流",
            value=f"${tot_funding_all:+,.2f} USDC",
            delta=f"在役浮动 +${funding_active:,.2f}" if funding_active > 0 else None,
        )
    with c3:
        pnl_color = "normal" if tot_net_closed >= 0 else "inverse"
        st.metric(
            label="💰 历史总结算纯利",
            value=f"${tot_net_closed:+,.2f} USD",
            delta=f"{len(closed_trades)} 笔已结平仓",
            delta_color=pnl_color,
        )
    with c4:
        st.metric(
            label="🛡️ 现货占用 / 永续保证金",
            value=f"${tot_capital_used:,.0f} USD",
            delta=f"加权平均 APR {avg_active_apr:.1%}" if tot_active > 0 else "当前无资金占用",
        )


def _render_active_spot_perp_card_html(p: dict) -> str:
    """Render single active spot-perp position card with liquidation gauge and settlement countdown."""
    tok = p.get("token", "")
    amt = float(p.get("amount", 0.0))
    pos_sz = float(p.get("position_size", 1000.0))
    spot_ent = float(p.get("spot_entry_price", 0.0))
    perp_ent = float(p.get("perp_entry_price", 0.0))
    spot_curr = float(p.get("current_spot_price", spot_ent))
    perp_curr = float(p.get("current_perp_price", perp_ent))
    curr_apr = float(p.get("current_apr", p.get("entry_apr", 0.0)))
    funding_accrued = float(p.get("settled_funding_pnl", 0.0))
    settle_cnt = len(p.get("funding_payments", []))
    dur_h = float(p.get("duration_seconds", 0)) / 3600.0

    # Liquidation safety cushion
    cushion_pct = float(p.get("current_safety_cushion_pct", 0.30)) * 100.0
    cushion_color = "#10b981" if cushion_pct >= 20.0 else ("#f59e0b" if cushion_pct >= 15.0 else "#ef4444")

    # Floating PnL
    net_pnl = float(p.get("total_net_pnl", funding_accrued))
    pnl_color = "#10b981" if net_pnl >= 0 else "#ef4444"

    return f"""<div style="background: rgba(15, 23, 42, 0.7); border: 1px solid rgba(59, 130, 246, 0.25); border-radius: 12px; padding: 16px 18px; margin-bottom: 14px; box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.35);">
<div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
    <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 8px;">
        <span style="font-size: 1.3rem; font-weight: 800; color: #f8fafc;">🪙 {tok}</span>
        <span style="background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 5px; font-size: 0.75rem; font-weight: 700;">
            现货-永续 Cash & Carry
        </span>
        <span style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); padding: 2px 8px; border-radius: 5px; font-size: 0.75rem; font-weight: 700; font-family: monospace;">
            🔥 实时 APR: {curr_apr:.1%}
        </span>
        <span style="color: #94a3b8; font-size: 0.8rem;">⏱️ 持仓: <b style="color: #f1f5f9;">{dur_h:.1f}小时</b></span>
    </div>
    <div style="text-align: right;">
        <span style="font-size: 1.25rem; font-weight: 800; color: {pnl_color};">{net_pnl:+,.2f} USD</span>
        <span style="color: #94a3b8; font-size: 0.76rem; display: block;">实收到账资金费: <b style="color: #10b981;">+${funding_accrued:,.2f}</b> ({settle_cnt} 周期)</span>
    </div>
</div>

<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin-top: 14px;">
    <div style="background: rgba(30, 41, 59, 0.5); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px; padding: 10px 12px; font-size: 0.8rem; color: #cbd5e1;">
        <div style="color: #94a3b8; font-weight: 600; margin-bottom: 4px; border-bottom: 1px dashed rgba(255, 255, 255, 0.08); padding-bottom: 4px;">
            🔹 Binance 现货腿 (Long Spot Physical)
        </div>
        <div>持有代币: <b style="color: #f8fafc;">{amt:,.4f} {tok}</b></div>
        <div>入场价: ${spot_ent:.4f} ➔ 现价: ${spot_curr:.4f}</div>
        <div>名义货值: <b style="color: #38bdf8;">${pos_sz:,.2f} USDT</b> (实物现货，零强平风险)</div>
    </div>

    <div style="background: rgba(30, 41, 59, 0.5); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px; padding: 10px 12px; font-size: 0.8rem; color: #cbd5e1;">
        <div style="color: #94a3b8; font-weight: 600; margin-bottom: 4px; border-bottom: 1px dashed rgba(255, 255, 255, 0.08); padding-bottom: 4px;">
            🔸 Hyperliquid 永续对冲腿 (Short Perp 3x)
        </div>
        <div>空单合约: <b style="color: #f8fafc;">{amt:,.4f} {tok}</b> (Delta-Zero)</div>
        <div>开空均价: ${perp_ent:.4f} ➔ 现价: ${perp_curr:.4f}</div>
        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 2px;">
            <span>强平防御垫: <b style="color: {cushion_color};">{cushion_pct:.1f}%</b></span>
            <span style="font-size: 0.72rem; color: #94a3b8;">(距空单强平价)</span>
        </div>
    </div>
</div>
</div>"""


def render_spot_perp_cash_carry_tables(arb_data: dict, bot_name: str):
    """Render multi-tab interactive tables and radar."""
    if not arb_data:
        st.info("💡 暂无现货-永续 Cash & Carry 策略数据或实例未运行。")
        return

    active_positions = arb_data.get("active_positions", [])
    market_candidates = arb_data.get("market_candidates", [])
    closed_trades = arb_data.get("closed_trades", [])

    tab_active, tab_radar, tab_closed = st.tabs([
        f"📊 在役对冲持仓 ({len(active_positions)})",
        f"⚡ 高息轮动雷达 ({len(market_candidates)})",
        f"📋 历史结算战绩 ({len(closed_trades)})",
    ])

    with tab_active:
        if not active_positions:
            st.info("当前暂无在役持仓，策略正在后台以 1.0s 频率巡检高流动性山寨币盘口与资金费率。")
        else:
            for p in active_positions:
                card_html = _render_active_spot_perp_card_html(p)
                if hasattr(st, "html"):
                    st.html(card_html)
                else:
                    st.markdown(card_html, unsafe_allow_html=True)

    with tab_radar:
        st.markdown("#### ⚡ 币安现货 + Hyperliquid 永续高息山寨雷达矩阵")
        if not market_candidates:
            st.info("正在采集 Hyperliquid 实时资金费率与币安现货买卖盘口深度...")
        else:
            radar_rows = []
            for c in market_candidates:
                radar_rows.append({
                    "标的": c.get("token"),
                    "年化 APR": f"{c.get('annual_apr', 0.0):+.1%}",
                    "单期 1h 费率": f"{c.get('hourly_rate', 0.0):+.4%}",
                    "币安现货价": f"${c.get('spot_price', 0.0):.4f}",
                    "HL 永续价": f"${c.get('perp_price', 0.0):.4f}",
                    "初始基差偏离": f"{c.get('basis_pct', 0.0):+.2%}",
                    "摩擦回本周期": f"{c.get('payback_hours', 0.0):.1f} 小时",
                    "币安现货深度": f"${c.get('depth_spot', 0.0):,.0f}",
                    "HL 永续深度": f"${c.get('depth_perp', 0.0):,.0f}",
                    "状态": c.get("status", "Standby"),
                })
            df_radar = pd.DataFrame(radar_rows)
            st.dataframe(df_radar, use_container_width=True, hide_index=True)

    with tab_closed:
        st.markdown("#### 📋 现货-永续 Cash & Carry 结清底账")
        if not closed_trades:
            st.info("尚无历史结清交易。当在役币种费率衰退（APR < 8%）或触发动态换仓时将自动归档。")
        else:
            closed_rows = []
            for t in closed_trades:
                dur_h = float(t.get("duration_seconds", 0)) / 3600.0
                closed_rows.append({
                    "标的": t.get("token"),
                    "持仓历时": f"{dur_h:.1f} 小时",
                    "名义货值": f"${t.get('position_size', 1000.0):,.2f}",
                    "现货平仓价": f"${t.get('spot_close_price', 0.0):.4f}",
                    "永续平仓价": f"${t.get('perp_close_price', 0.0):.4f}",
                    "现货腿 PnL": f"${t.get('spot_pnl', 0.0):+,.2f}",
                    "永续腿 PnL": f"${t.get('perp_pnl', 0.0):+,.2f}",
                    "双边总摩擦": f"-${t.get('total_friction_cost', 0.0):,.2f}",
                    "实收资金费": f"${t.get('settled_funding_pnl', 0.0):+,.2f}",
                    "到账周期": f"{t.get('settlement_count', 0)} 次 (1h/次)",
                    "穿透纯利 (USD)": f"${t.get('total_net_pnl', 0.0):+,.2f}",
                    "实缴本金 ROE": f"{t.get('roe_pct', 0.0):+.2%}",
                    "平仓退出原因": t.get("close_reason", "take_profit"),
                })
            df_closed = pd.DataFrame(closed_rows)
            st.dataframe(df_closed, use_container_width=True, hide_index=True)
