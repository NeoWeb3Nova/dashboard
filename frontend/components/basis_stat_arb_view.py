import json
import os
from datetime import datetime, timezone
import pandas as pd
import streamlit as st

from frontend.components.clock_monitor import get_exchange_clock_health
from frontend.components.timezone_utils import render_timezone_selector, format_timestamp


def get_bot_basis_stat_arb_data(bot_name, performance):
    """Retrieve basis stat arb custom_info from MQTT performance or local disk."""
    arb_data = None
    for ctrl_id, ctrl_dict in performance.items():
        if ctrl_id == "basis_stat_arb":
            c_info = ctrl_dict.get("custom_info", {})
            if "active_positions" in c_info or "closed_trades" in c_info or "market_comparison" in c_info:
                arb_data = c_info
                break

    candidate_json_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), f"../../../hummingbot-api/bots/instances/{bot_name}/data/basis_stat_arb_trades.json")),
        os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/instances/{bot_name}/data/basis_stat_arb_trades.json"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../hummingbot-api/bots/data/basis_stat_arb_trades.json")),
        os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/data/basis_stat_arb_trades.json"),
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
                                if "basis_stat_arb" in content or "v2_basis_stat_arb" in content:
                                    is_basis_stat_arb_bot = True
                                    break
                        except Exception:
                            pass
            if is_basis_stat_arb_bot:
                break

    if is_basis_stat_arb_bot and not arb_data:
        arb_data = {
            "strategy": "basis_stat_arb",
            "is_dry_run": True,
            "active_positions": [],
            "closed_trades": [],
            "market_comparison": []
        }

    return is_basis_stat_arb_bot, arb_data


def render_basis_stat_arb_kpi_cards(arb_data, total_volume_traded=0.0):
    """Render top institutional KPI summary for Basis Stat Arb Engine."""
    active_pos = arb_data.get("active_positions", [])
    closed_trades = arb_data.get("closed_trades", [])
    leverage = int(arb_data.get("leverage", 3))

    total_capital = sum(float(p.get("position_size", 0)) for p in active_pos)
    gross_capital = total_capital * 2.0
    real_margin_total = (gross_capital / leverage) if leverage > 0 else total_capital

    realized_trading_pnl = sum(float(t.get("trading_pnl", 0)) for t in closed_trades)
    realized_funding_pnl = sum(float(t.get("funding_pnl", 0)) for t in closed_trades)
    total_realized_pnl = sum(float(t.get("total_net_pnl", 0)) for t in closed_trades)

    unrealized_trading_pnl = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
    unrealized_funding_pnl = sum(float(p.get("funding_income", 0)) for p in active_pos)
    total_unrealized_pnl = unrealized_trading_pnl + unrealized_funding_pnl

    net_combined_pnl = total_realized_pnl + total_unrealized_pnl
    total_funding_harvested = realized_funding_pnl + unrealized_funding_pnl

    # Win rate & stats
    winning_trades = [t for t in closed_trades if float(t.get("total_net_pnl", 0)) > 0]
    win_rate = (len(winning_trades) / len(closed_trades) * 100.0) if closed_trades else 0.0
    avg_hold_minutes = (sum(int(t.get("duration_seconds", 0)) for t in closed_trades) / len(closed_trades) / 60.0) if closed_trades else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric(
            "💼 真实实缴保证金",
            f"${real_margin_total:,.2f}",
            f"{leverage}x 杠杆质押 (单边 ${total_capital:,.2f})",
            help="当前活跃基差套利在多空双边实缴的总保证金"
        )
    with c2:
        st.metric(
            "🏦 已平仓战报结清利润",
            f"${total_realized_pnl:+,.2f}",
            f"胜率 {win_rate:.1f}% ({len(winning_trades)}/{len(closed_trades)} 胜)",
            delta_color="normal",
            help="所有已均值回归平仓的结清真金白银净收益（扣除双边手续费）"
        )
    with c3:
        st.metric(
            "📈 活跃持仓浮动盈亏",
            f"${total_unrealized_pnl:+,.2f}",
            f"正在收敛 {len(active_pos)} 仓位",
            delta_color="normal",
            help="当前活跃基差仓位的瞬时浮动盈亏（随盘口基差压缩每秒动态更新）"
        )
    with c4:
        st.metric(
            "🎁 顺风收割利息 (Carry Bonus)",
            f"${total_funding_harvested:+,.2f}",
            f"已入账 + 浮动利息",
            delta_color="normal",
            help="在等待基差均值回归期间，持仓双边顺带收取的资金费现金流"
        )
    with c5:
        st.metric(
            "⏱️ 平均持仓平仓周期",
            f"{avg_hold_minutes:.1f} 分钟" if avg_hold_minutes < 120 else f"{avg_hold_minutes/60:.1f} 小时",
            f"共已结算 {len(closed_trades)} 笔",
            help="均值回归快速了结，绝不死扛，资金极速释放"
        )


def render_basis_stat_arb_tables(arb_data, bot_name):
    """Render full tables and visual perspective for Basis Stat Arb bot."""
    active_pos = arb_data.get("active_positions", [])
    closed_trades = arb_data.get("closed_trades", [])
    market_comp = arb_data.get("market_comparison", [])
    is_dry_run = arb_data.get("is_dry_run", True)

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
    st.markdown("""
    <div style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px; padding: 10px 16px; margin: 16px 0 12px 0;
                display: flex; align-items: center; justify-content: space-between;">
        <div style="display: flex; align-items: center; gap: 10px;">
            <span style="font-size: 1.15rem;">⚡</span>
            <span style="font-weight: 700; color: #f8fafc; font-size: 0.95rem; letter-spacing: 0.3px;">
                基差统计套利全景透视 (Basis Mean-Reversion + Carry Double-Dip)
            </span>
            <span style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3);
                         padding: 2px 8px; border-radius: 6px; font-size: 0.72rem; font-weight: 600;">
                均值回归脉冲引擎
            </span>
        </div>
        <span style="color: #64748b; font-size: 0.75rem;">
            💡 捕捉 0.7%~1.2% 脉冲，0.15% 压缩极速收割
        </span>
    </div>
    """, unsafe_allow_html=True)

    tab_active, tab_closed, tab_radar = st.tabs([
        f"⚡ 活跃基差持仓  [{len(active_pos)} 仓位]",
        f"📜 均值回归战绩  [{len(closed_trades)} 笔已结算]",
        f"📡 全市场基差雷达  [{len(market_comp)} 标的监控]",
    ])

    with tab_active:
        if not active_pos:
            st.info("ℹ️ 当前暂无活跃的基差偏离套利持仓。策略正以 1 秒高频持续巡检主流币跨所价差，一旦盘口基差偏离 ≥ 0.70%，将自动以两所等额多空对冲介入！")
        else:
            for p in active_pos:
                tok = p.get("token", "")
                dur_m = p.get("duration_seconds", 0) // 60
                dur_str = f"{dur_m // 60}h {dur_m % 60}m"
                entry_basis = float(p.get("entry_basis_pct", 0.0))
                curr_basis = float(p.get("current_basis_pct", 0.0))
                target_comp = float(p.get("target_compression_pct", 0.0015))
                pos_size = float(p.get("position_size", 0.0))
                unrealized = float(p.get("unrealized_trading_pnl", 0.0))
                unrealized_pct = float(p.get("unrealized_pnl_pct", 0.0))
                carry = float(p.get("funding_income", 0.0))
                side1 = p.get("side_1", "")
                side2 = p.get("side_2", "")
                c1 = p.get("connector_1", "")
                c2 = p.get("connector_2", "")

                # Compression progress calculation
                initial_abs_gap = abs(entry_basis)
                current_abs_gap = abs(curr_basis)
                progress_pct = max(0.0, min(100.0, (1.0 - (current_abs_gap / initial_abs_gap)) * 100.0)) if initial_abs_gap > 0 else 100.0

                pnl_color = "#10b981" if unrealized >= 0 else "#ef4444"

                st.markdown(f"""
                <div style="background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(59, 130, 246, 0.25);
                            border-radius: 10px; padding: 14px; margin-bottom: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <span style="font-size: 1.15rem; font-weight: 700; color: #f8fafc;">🪙 {tok}</span>
                            <span style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; margin-left: 8px;">
                                {side1} {c1} + {side2} {c2}
                            </span>
                            <span style="color: #94a3b8; font-size: 0.8rem; margin-left: 10px;">持仓耗时: {dur_str}</span>
                        </div>
                        <div style="text-align: right;">
                            <span style="font-size: 1.15rem; font-weight: 700; color: {pnl_color};">${unrealized:+,.2f}</span>
                            <span style="font-size: 0.85rem; color: {pnl_color}; margin-left: 6px;">({unrealized_pct:+.2%})</span>
                        </div>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-top: 10px; font-size: 0.82rem; color: #cbd5e1;">
                        <span>开仓基差: <b>{entry_basis:+.2%}</b></span>
                        <span>当前实时基差: <b>{curr_basis:+.2%}</b></span>
                        <span>目标止盈收敛线: <b>≤ ±{target_comp:.2%}</b></span>
                        <span>已收利息加成: <b style="color: #10b981;">+${carry:.2f}</b></span>
                        <span>名义货值: <b>${pos_size:,.0f}</b></span>
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
                </div>
                """, unsafe_allow_html=True)

    with tab_closed:
        if not closed_trades:
            st.info("ℹ️ 暂无历史平仓结算战报。一旦基差完成均值回归或触发止盈，平仓记录将实时在此归档。")
        else:
            closed_rows = []
            for t in reversed(closed_trades):
                dur_m = t.get("duration_seconds", 0) // 60
                dur_str = f"{dur_m // 60}h {dur_m % 60}m"
                closed_rows.append({
                    "平仓时间": format_timestamp(t.get("close_timestamp", 0), active_tz),
                    "标的": t.get("token", ""),
                    "交易方向": f"{t.get('side_1', '')} HL / {t.get('side_2', '')} BN",
                    "开仓基差": f"{float(t.get('entry_basis_pct', 0)):+.2%}",
                    "平仓基差": f"{float(t.get('close_basis_pct', 0)):+.2%}",
                    "价差净利润": f"${float(t.get('trading_pnl', 0)):+,.2f}",
                    "资金费加成": f"${float(t.get('funding_pnl', 0)):+,.2f}",
                    "结清净收益": f"${float(t.get('total_net_pnl', 0)):+,.2f}",
                    "收益率 (ROE)": f"{float(t.get('net_pnl_pct', 0)):+.2%}",
                    "持仓时长": dur_str,
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
                radar_rows.append({
                    "标的": m.get("token", ""),
                    "Hyperliquid 价格": f"${float(m.get('price_1', 0)):,.4f}",
                    "Binance 价格": f"${float(m.get('price_2', 0)):,.4f}",
                    "实时基差偏离率": f"{float(m.get('basis_spread_pct', 0)):+.3%}",
                    "HL 日资金费率": f"{float(m.get('c1_funding_daily', 0)):+.3%}/d",
                    "BN 日资金费率": f"{float(m.get('c2_funding_daily', 0)):+.3%}/d",
                    "顺风利差预计": f"{float(m.get('carry_if_short_c1', 0)):+.3%}/d",
                    "脉冲捕获状态": m.get("dislocation_status", ""),
                })
            df_radar = pd.DataFrame(radar_rows)
            st.dataframe(df_radar, use_container_width=True, hide_index=True)
