"""
Institutional PnL Analytics & Quantitative Performance Engine
=============================================================
Provides institutional-grade, multi-timeframe PnL analysis for Funding Rate Arbitrage:
1. Executive Macro KPI Suite (Total Net PnL, Funding Cashflow, Trading PnL, Profit Factor, Win Rate, MDD)
2. Multi-Timeframe Grouping: Daily (每日), Weekly (每周), Monthly (每月) with interactive Plotly bar charts
3. Cumulative Net Equity Curve & Steady Funding Accumulation Area Chart (净值曲线与高水位线)
4. Binance/VIP Style Interactive PnL Calendar Heatmap (战绩日历热力看板 with 周小计)
5. Asset PnL Attribution Ranking (各标的盈亏归因全景排行榜)
6. Regime Segmentation Toggle (全部历史 vs 剔除永久黑名单毒币净化透视)
"""

import calendar
import datetime
from typing import Dict, Any, List, Optional
import pandas as pd
import plotly.graph_objects as go
from zoneinfo import ZoneInfo
import streamlit as st


def _parse_trades_dataframe(closed_trades: List[Dict[str, Any]], active_tz: str = "Asia/Shanghai") -> pd.DataFrame:
    """
    Parse closed trades list into a structured Pandas DataFrame with localized datetime columns.
    """
    if not closed_trades:
        return pd.DataFrame()

    tz = ZoneInfo(active_tz)
    records = []
    for t in closed_trades:
        tok = t.get("token", "UNKNOWN")
        close_ts = int(t.get("close_timestamp", 0) or t.get("exit_timestamp", 0))
        open_ts = int(t.get("open_timestamp", 0))
        dur_sec = max(0, close_ts - open_ts) if (close_ts > 0 and open_ts > 0) else 0

        dt = datetime.datetime.fromtimestamp(close_ts, tz=tz) if close_ts > 0 else datetime.datetime.now(tz=tz)
        t_pnl = float(t.get("trading_pnl", 0.0))
        f_pnl = float(t.get("funding_pnl", 0.0))
        net_pnl = float(t.get("total_net_pnl", t_pnl + f_pnl))

        pos_size = float(t.get("position_size", t.get("position_size_quote", 0)))
        leverage = int(t.get("leverage", 3))
        real_margin = float(t.get("initial_margin_quote", (2.0 * pos_size) / leverage if leverage > 0 else 0))
        roe_pct = (net_pnl / real_margin) * 100.0 if real_margin > 0 else 0.0

        records.append({
            "token": tok,
            "close_timestamp": close_ts,
            "open_timestamp": open_ts,
            "close_datetime": dt,
            "date": dt.strftime("%Y-%m-%d"),
            "year": dt.year,
            "month": dt.strftime("%Y-%m"),
            "week": f"{dt.year}-W{dt.isocalendar()[1]:02d}",
            "day_of_month": dt.day,
            "weekday": dt.weekday(),  # 0=Mon, 6=Sun
            "net_pnl": net_pnl,
            "trading_pnl": t_pnl,
            "funding_pnl": f_pnl,
            "pos_size": pos_size,
            "real_margin": real_margin,
            "roe_pct": roe_pct,
            "duration_hours": dur_sec / 3600.0,
            "is_win": net_pnl > 0.0,
            "close_reason": str(t.get("close_reason", "Completed")),
        })

    df = pd.DataFrame(records)
    df.sort_values(by="close_timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def _calculate_pnl_metrics(df: pd.DataFrame, active_pos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compute comprehensive institutional trading & risk metrics.
    """
    if df.empty:
        active_funding = sum(float(p.get("funding_income", 0)) for p in active_pos)
        active_unrealized = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
        active_net = sum(float(p.get("net_pnl", 0)) for p in active_pos)
        return {
            "total_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "realized_net_pnl": 0.0,
            "realized_trading_pnl": 0.0,
            "realized_funding_pnl": 0.0,
            "active_unrealized": active_unrealized,
            "active_funding": active_funding,
            "active_net": active_net,
            "total_global_net_pnl": active_net,
            "total_global_funding": active_funding,
            "profit_factor": 0.0,
            "win_loss_ratio": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "max_drawdown_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_dur_h": 0.0,
            "avg_win_dur_h": 0.0,
            "avg_loss_dur_h": 0.0,
        }

    total_count = len(df)
    wins = df[df["is_win"]]
    losses = df[~df["is_win"]]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_count * 100.0) if total_count > 0 else 0.0

    realized_net = df["net_pnl"].sum()
    realized_trade = df["trading_pnl"].sum()
    realized_fund = df["funding_pnl"].sum()

    gross_profit = wins["net_pnl"].sum() if not wins.empty else 0.0
    gross_loss = abs(losses["net_pnl"].sum()) if not losses.empty else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    avg_win = wins["net_pnl"].mean() if not wins.empty else 0.0
    avg_loss = abs(losses["net_pnl"].mean()) if not losses.empty else 0.0
    win_loss_ratio = (avg_win / avg_loss) if avg_loss > 0 else (99.0 if avg_win > 0 else 0.0)

    # Calculate Max Drawdown from Cumulative Net PnL curve
    df["cum_net"] = df["net_pnl"].cumsum()
    df["peak"] = df["cum_net"].cummax().clip(lower=0)
    df["drawdown"] = df["cum_net"] - df["peak"]
    max_dd_usd = abs(df["drawdown"].min()) if not df.empty else 0.0

    # Active positions contribution
    active_funding = sum(float(p.get("funding_income", 0)) for p in active_pos)
    active_unrealized = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
    active_net = sum(float(p.get("net_pnl", 0)) for p in active_pos)

    total_global_net = realized_net + active_net
    total_global_funding = realized_fund + active_funding

    avg_dur_h = df["duration_hours"].mean() if total_count > 0 else 0.0
    avg_win_dur_h = wins["duration_hours"].mean() if not wins.empty else 0.0
    avg_loss_dur_h = losses["duration_hours"].mean() if not losses.empty else 0.0

    return {
        "total_count": total_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": win_rate,
        "realized_net_pnl": realized_net,
        "realized_trading_pnl": realized_trade,
        "realized_funding_pnl": realized_fund,
        "active_unrealized": active_unrealized,
        "active_funding": active_funding,
        "active_net": active_net,
        "total_global_net_pnl": total_global_net,
        "total_global_funding": total_global_funding,
        "profit_factor": profit_factor,
        "win_loss_ratio": win_loss_ratio,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_drawdown_usd": max_dd_usd,
        "avg_dur_h": avg_dur_h,
        "avg_win_dur_h": avg_win_dur_h,
        "avg_loss_dur_h": avg_loss_dur_h,
    }


def _render_executive_kpi_cards(metrics: Dict[str, Any], active_pos: List[Dict[str, Any]]):
    """
    Render Linear/Apple style high-end KPI card tiles using double-bezel concentric layout.
    """
    c1, c2, c3, c4 = st.columns(4)

    net_val = metrics["total_global_net_pnl"]
    net_color = "#10b981" if net_val >= 0 else "#ef4444"
    net_sign = "+" if net_val >= 0 else ""

    with c1:
        with st.container(border=True):
            st.markdown(
                f"<div style='font-size:12px; color:#94a3b8; font-weight:600; text-transform:uppercase; letter-spacing:0.05em;'>"
                f"🏦 综合总净盈亏 (Net PnL)</div>"
                f"<div style='font-size:26px; font-weight:700; color:{net_color}; margin:6px 0;'>"
                f"{net_sign}${net_val:,.2f}</div>"
                f"<div style='font-size:12px; color:#cbd5e1;'>"
                f"已平仓: <b style='color:{'#10b981' if metrics['realized_net_pnl'] >= 0 else '#ef4444'}'>"
                f"${metrics['realized_net_pnl']:+,.2f}</b> | 浮动: <b>${metrics['active_net']:+,.2f}</b></div>",
                unsafe_allow_html=True
            )

    with c2:
        with st.container(border=True):
            st.markdown(
                f"<div style='font-size:12px; color:#94a3b8; font-weight:600; text-transform:uppercase; letter-spacing:0.05em;'>"
                f"💰 资金费纯收租入账 (Carry Yield)</div>"
                f"<div style='font-size:26px; font-weight:700; color:#10b981; margin:6px 0;'>"
                f"+${metrics['total_global_funding']:,.2f}</div>"
                f"<div style='font-size:12px; color:#cbd5e1;'>"
                f"已结收息: <b>+${metrics['realized_funding_pnl']:,.2f}</b> | 持仓累积: <b>+${metrics['active_funding']:,.2f}</b></div>",
                unsafe_allow_html=True
            )

    with c3:
        with st.container(border=True):
            trade_val = metrics["realized_trading_pnl"]
            t_color = "#10b981" if trade_val >= 0 else "#f59e0b"
            st.markdown(
                f"<div style='font-size:12px; color:#94a3b8; font-weight:600; text-transform:uppercase; letter-spacing:0.05em;'>"
                f"⚖️ 盘口价差与摩擦 (Basis PnL)</div>"
                f"<div style='font-size:26px; font-weight:700; color:{t_color}; margin:6px 0;'>"
                f"${trade_val:+,.2f}</div>"
                f"<div style='font-size:12px; color:#cbd5e1;'>"
                f"持仓盘口浮动: <b>${metrics['active_unrealized']:+,.2f}</b> (对冲基差偏离)</div>",
                unsafe_allow_html=True
            )

    with c4:
        with st.container(border=True):
            pf = metrics["profit_factor"]
            pf_color = "#10b981" if pf >= 1.5 else ("#f59e0b" if pf >= 1.0 else "#ef4444")
            st.markdown(
                f"<div style='font-size:12px; color:#94a3b8; font-weight:600; text-transform:uppercase; letter-spacing:0.05em;'>"
                f"📊 利润因子与盈亏比 (Profit Factor)</div>"
                f"<div style='font-size:26px; font-weight:700; color:{pf_color}; margin:6px 0;'>"
                f"{pf:.2f}x</div>"
                f"<div style='font-size:12px; color:#cbd5e1;'>"
                f"均单胜/负比: <b>{metrics['win_loss_ratio']:.1f}x</b> (${metrics['avg_win']:.1f} / ${metrics['avg_loss']:.1f})</div>",
                unsafe_allow_html=True
            )

    # Row 2 Secondary Metrics
    r2_c1, r2_c2, r2_c3, r2_c4 = st.columns(4)
    with r2_c1:
        st.metric("🎯 交易胜率 (Win Rate)", f"{metrics['win_rate']:.1f}%", f"{metrics['win_count']}胜 {metrics['loss_count']}负 (共{metrics['total_count']}笔)")
    with r2_c2:
        st.metric("📉 历史最大回撤 (MDD)", f"${metrics['max_drawdown_usd']:,.2f}", "从净值峰值最大回落")
    with r2_c3:
        st.metric("⏱️ 平均持仓久期 (Duration)", f"{metrics['avg_dur_h']:.1f} 小时", f"盈利单均持 {metrics['avg_win_dur_h']:.1f}h | 止损单均持 {metrics['avg_loss_dur_h']:.1f}h")
    with r2_c4:
        # 24h cashflow projection based on active positions
        projected_24h = sum(float(p.get("position_size", 0)) * (float(p.get("expected_profitability", 0))) for p in active_pos)
        st.metric("💵 当前持仓 24h 预期入账", f"+${projected_24h:.2f} /天", "基于活跃持仓实时年化利差")


def _render_timeframe_bar_chart(df: pd.DataFrame, timeframe_mode: str, bot_name: str):
    """
    Render Daily / Weekly / Monthly aggregated bar chart with Net PnL, Funding Fee, and Trade Count.
    """
    if df.empty:
        st.info("ℹ️ 暂无历史平仓数据，等待策略产生首批平仓结算。")
        return

    group_col = "date" if timeframe_mode == "每日 (Daily)" else ("week" if timeframe_mode == "每周 (Weekly)" else "month")
    agg = df.groupby(group_col).agg(
        net_pnl=("net_pnl", "sum"),
        trade_pnl=("trading_pnl", "sum"),
        funding_pnl=("funding_pnl", "sum"),
        trade_count=("net_pnl", "count"),
        win_count=("is_win", lambda x: int(x.sum())),
    ).reset_index()

    agg["loss_count"] = agg["trade_count"] - agg["win_count"]
    agg["win_rate"] = (agg["win_count"] / agg["trade_count"]) * 100.0

    colors = ["#10b981" if v >= 0 else "#ef4444" for v in agg["net_pnl"]]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=agg[group_col],
        y=agg["net_pnl"],
        name="综合净盈亏 (Net PnL)",
        marker_color=colors,
        text=[f"{v:+,.2f}" for v in agg["net_pnl"]],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>综合净盈亏: <b>$%{y:+,.2f}</b><br>平仓笔数: %{customdata[0]}笔 (胜率 %{customdata[1]:.1f}%)<br>资金费入账: +$%{customdata[2]:,.2f}<br>价差损益: $%{customdata[3]:+,.2f}<extra></extra>",
        customdata=list(zip(agg["trade_count"], agg["win_rate"], agg["funding_pnl"], agg["trade_pnl"]))
    ))

    # Add funding PnL trend line
    fig.add_trace(go.Scatter(
        x=agg[group_col],
        y=agg["funding_pnl"],
        name="资金费纯利息 (Funding)",
        mode="lines+markers",
        line=dict(color="#34d399", width=2.5, dash="dot"),
        marker=dict(size=7, color="#34d399")
    ))

    fig.update_layout(
        title=f"📈 {timeframe_mode} 盈亏柱状图与资金费现金流分布",
        yaxis_title="盈亏金额 (USD)",
        xaxis_title="结算周期",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=380,
        margin=dict(l=20, r=20, t=50, b=20),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.08)", zeroline=True, zerolinecolor="rgba(255,255,255,0.2)"),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"tf_bar_{bot_name}_{timeframe_mode}")

    # Summary table below chart
    with st.expander(f"📋 查看 {timeframe_mode} 详细财务对账表格", expanded=False):
        table_df = agg.rename(columns={
            group_col: "周期标签",
            "net_pnl": "综合净盈亏 ($)",
            "funding_pnl": "资金费入账 ($)",
            "trade_pnl": "价差损益 ($)",
            "trade_count": "平仓笔数",
            "win_count": "盈利笔数",
            "loss_count": "止损笔数",
            "win_rate": "胜率 (%)",
        })
        table_df["综合净盈亏 ($)"] = table_df["综合净盈亏 ($)"].map(lambda x: f"${x:+,.2f}")
        table_df["资金费入账 ($)"] = table_df["资金费入账 ($)"].map(lambda x: f"+${x:,.2f}")
        table_df["价差损益 ($)"] = table_df["价差损益 ($)"].map(lambda x: f"${x:+,.2f}")
        table_df["胜率 (%)"] = table_df["胜率 (%)"].map(lambda x: f"{x:.1f}%")
        st.dataframe(table_df, use_container_width=True, hide_index=True)


def _render_equity_curve_chart(df: pd.DataFrame, bot_name: str):
    """
    Render Cumulative Net Equity Curve & Steady Funding Accumulation Area Chart.
    """
    if df.empty:
        return

    plot_df = df.copy()
    plot_df["cum_net"] = plot_df["net_pnl"].cumsum()
    plot_df["cum_funding"] = plot_df["funding_pnl"].cumsum()
    plot_df["cum_trade"] = plot_df["trading_pnl"].cumsum()
    plot_df["high_watermark"] = plot_df["cum_net"].cummax().clip(lower=0)

    fig = go.Figure()

    # High Water Mark
    fig.add_trace(go.Scatter(
        x=plot_df["close_datetime"],
        y=plot_df["high_watermark"],
        name="净值历史最高水位线 (High Water Mark)",
        mode="lines",
        line=dict(color="rgba(148, 163, 184, 0.4)", width=1.5, dash="dash"),
    ))

    # Cumulative Net PnL Area
    fig.add_trace(go.Scatter(
        x=plot_df["close_datetime"],
        y=plot_df["cum_net"],
        name="综合累计净盈亏曲线 (Cumulative Net)",
        mode="lines+markers",
        line=dict(color="#3b82f6", width=3),
        fill="tozeroy",
        fillcolor="rgba(59, 130, 246, 0.08)",
        marker=dict(size=5),
        hovertemplate="时间: %{x}<br>累计净盈亏: <b>$%{y:+,.2f}</b><extra></extra>"
    ))

    # Cumulative Funding (Uninterrupted positive cashflow)
    fig.add_trace(go.Scatter(
        x=plot_df["close_datetime"],
        y=plot_df["cum_funding"],
        name="累计资金费稳健收租 (Cumulative Funding Cashflow)",
        mode="lines",
        line=dict(color="#10b981", width=2.5),
        hovertemplate="时间: %{x}<br>累计利息收入: <b>+%{y:+,.2f}</b><extra></extra>"
    ))

    fig.update_layout(
        title="🌊 累计资产净值曲线 (Equity Curve) vs 资金费稳健增长线",
        yaxis_title="累计盈亏 (USD)",
        xaxis_title="平仓时间戳",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=380,
        margin=dict(l=20, r=20, t=50, b=20),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.08)", zeroline=True, zerolinecolor="rgba(255,255,255,0.2)"),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"equity_chart_{bot_name}")


def _render_pnl_calendar_heatmap(df: pd.DataFrame, active_tz: str, tz_label: str):
    """
    Render a VIP/Binance-style interactive monthly PnL calendar with daily badges and weekly totals.
    """
    st.markdown("##### 🗓️ 机构级战绩日历热力看板 (PnL Calendar Heatmap)")
    st.caption(f"ℹ️ 按照交易所 VIP 标准日历格式，透视每日实收盈亏与每周小计（当前基准时区：{tz_label}）")

    now = datetime.datetime.now(tz=ZoneInfo(active_tz))
    curr_year = now.year
    curr_month = now.month

    # Group dataframe by day
    daily_map = {}
    if not df.empty:
        for _, row in df.iterrows():
            d_str = row["date"]  # YYYY-MM-DD
            if d_str not in daily_map:
                daily_map[d_str] = {"net": 0.0, "fund": 0.0, "trade": 0.0, "count": 0, "wins": 0}
            daily_map[d_str]["net"] += row["net_pnl"]
            daily_map[d_str]["fund"] += row["funding_pnl"]
            daily_map[d_str]["trade"] += row["trading_pnl"]
            daily_map[d_str]["count"] += 1
            if row["is_win"]:
                daily_map[d_str]["wins"] += 1

    cal = calendar.monthcalendar(curr_year, curr_month)

    # Build Calendar Table HTML
    weekday_headers = ["周一 (Mon)", "周二 (Tue)", "周三 (Wed)", "周四 (Thu)", "周五 (Fri)", "周六 (Sat)", "周日 (Sun)", "周小计 (Total)"]
    header_html = "".join([f"<th style='padding:8px; text-align:center; color:#94a3b8; font-size:12px; border-bottom:1px solid #334155;'>{h}</th>" for h in weekday_headers])

    rows_html = ""
    month_total_net = 0.0
    month_total_trades = 0

    for week in cal:
        row_cells = ""
        week_total_net = 0.0
        week_trades = 0

        for day in week:
            if day == 0:
                row_cells += "<td style='padding:8px; background:rgba(255,255,255,0.01); border:1px solid rgba(255,255,255,0.03);'></td>"
                continue

            date_key = f"{curr_year}-{curr_month:02d}-{day:02d}"
            info = daily_map.get(date_key, None)

            if info:
                net = info["net"]
                count = info["count"]
                week_total_net += net
                week_trades += count
                month_total_net += net
                month_total_trades += count

                if net > 50.0:
                    bg = "rgba(16, 185, 129, 0.28)"
                    border = "#10b981"
                    txt_color = "#34d399"
                elif net > 0.0:
                    bg = "rgba(16, 185, 129, 0.16)"
                    border = "#059669"
                    txt_color = "#6ee7b7"
                elif net > -15.0:
                    bg = "rgba(239, 68, 68, 0.15)"
                    border = "#dc2626"
                    txt_color = "#fca5a5"
                else:
                    bg = "rgba(239, 68, 68, 0.28)"
                    border = "#b91c1c"
                    txt_color = "#f87171"

                pnl_str = f"{net:+,.2f}"
                cell_content = f"""
                <div style='background:{bg}; border:1px solid {border}; border-radius:6px; padding:6px; min-height:68px; text-align:center;'>
                    <div style='font-size:11px; color:#94a3b8; text-align:left; font-weight:600;'>{day}日</div>
                    <div style='font-size:14px; font-weight:bold; color:{txt_color}; margin:2px 0;'>${pnl_str}</div>
                    <div style='font-size:10px; color:#cbd5e1;'>{count}笔 ({info['wins']}胜)</div>
                </div>
                """
            else:
                cell_content = f"""
                <div style='background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:6px; padding:6px; min-height:68px; text-align:center;'>
                    <div style='font-size:11px; color:#64748b; text-align:left;'>{day}日</div>
                    <div style='font-size:12px; color:#475569; margin-top:8px;'>$0.00</div>
                    <div style='font-size:10px; color:#475569;'>0笔</div>
                </div>
                """

            row_cells += f"<td style='padding:4px; vertical-align:top;'>{cell_content}</td>"

        # Weekly Total Column
        w_color = "#34d399" if week_total_net >= 0 else ("#f87171" if week_total_net < 0 else "#94a3b8")
        w_bg = "rgba(16, 185, 129, 0.1)" if week_total_net > 0 else ("rgba(239, 68, 68, 0.1)" if week_total_net < 0 else "rgba(255,255,255,0.02)")
        week_cell = f"""
        <div style='background:{w_bg}; border:1px dashed #475569; border-radius:6px; padding:6px; min-height:68px; text-align:center; display:flex; flex-direction:column; justify-content:center;'>
            <div style='font-size:10px; color:#94a3b8; font-weight:600;'>周汇总</div>
            <div style='font-size:14px; font-weight:bold; color:{w_color};'>${week_total_net:+,.2f}</div>
            <div style='font-size:10px; color:#cbd5e1;'>共 {week_trades} 笔</div>
        </div>
        """
        row_cells += f"<td style='padding:4px; vertical-align:top;'>{week_cell}</td>"
        rows_html += f"<tr>{row_cells}</tr>"

    m_color = "#34d399" if month_total_net >= 0 else "#f87171"
    calendar_html = f"""
    <div style='overflow-x:auto; margin:10px 0;'>
        <div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;'>
            <span style='font-size:16px; font-weight:700; color:#f8fafc;'>📅 {curr_year} 年 {curr_month} 月 战绩看板</span>
            <span style='font-size:14px; font-weight:600;'>当月累计盈亏: <b style='color:{m_color};'>${month_total_net:+,.2f}</b> ({month_total_trades} 笔平仓)</span>
        </div>
        <table style='width:100%; border-collapse:collapse;'>
            <thead><tr>{header_html}</tr></thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """
    st.markdown(calendar_html, unsafe_allow_html=True)


def _render_asset_attribution_ranking(df: pd.DataFrame, arb_data: Dict[str, Any], bot_name: str):
    """
    Render asset attribution bar chart and breakdown table ranking every token from best to worst.
    """
    if df.empty:
        return

    st.markdown("##### 🪙 标的资产盈亏归因全景排行榜 (Asset PnL Attribution)")
    st.caption("ℹ️ 穿透分析每一个代币的综合净收益、纯利息贡献与价差摩擦，明确谁在真正创造价值")

    perm_bl = set(arb_data.get("permanent_blacklist", []))
    quarantined = set(arb_data.get("quarantined_tokens", {}).keys())
    active_tokens = {p.get("token") for p in arb_data.get("active_positions", [])}

    tok_agg = df.groupby("token").agg(
        net_pnl=("net_pnl", "sum"),
        funding_pnl=("funding_pnl", "sum"),
        trade_pnl=("trading_pnl", "sum"),
        trades=("net_pnl", "count"),
        wins=("is_win", lambda x: int(x.sum())),
    ).reset_index()

    tok_agg["win_rate"] = (tok_agg["wins"] / tok_agg["trades"]) * 100.0
    tok_agg.sort_values(by="net_pnl", ascending=True, inplace=True)

    col1, col2 = st.columns([1.6, 1.4])

    with col1:
        # Horizontal Bar Chart
        bar_colors = ["#10b981" if v >= 0 else "#ef4444" for v in tok_agg["net_pnl"]]
        fig = go.Figure(go.Bar(
            x=tok_agg["net_pnl"],
            y=tok_agg["token"],
            orientation="h",
            marker_color=bar_colors,
            text=[f"${v:+,.2f}" for v in tok_agg["net_pnl"]],
            textposition="outside",
            hovertemplate="标的: <b>%{y}</b><br>净盈亏: <b>$%{x:+,.2f}</b><extra></extra>"
        ))
        fig.update_layout(
            title="📊 各标的累计贡献净利润 ($ USD)",
            xaxis_title="净盈亏 ($ USD)",
            height=max(280, len(tok_agg) * 28),
            margin=dict(l=20, r=20, t=40, b=20),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.08)", zeroline=True, zerolinecolor="rgba(255,255,255,0.2)"),
        )
        st.plotly_chart(fig, use_container_width=True, key=f"token_bar_{bot_name}")

    with col2:
        # Table with badges
        table_rows = []
        for _, r in tok_agg.sort_values(by="net_pnl", ascending=False).iterrows():
            t = r["token"]
            if t in perm_bl:
                status = "🚫 永久拉黑毒币"
            elif t in active_tokens:
                status = "🟢 活跃持仓中"
            elif t in quarantined:
                status = "🛑 动态隔离中"
            else:
                status = "⚪ 已结清平仓"

            table_rows.append({
                "标的币种": t,
                "当前风控状态": status,
                "综合净盈亏": f"${r['net_pnl']:+,.2f}",
                "资金费收租": f"+${r['funding_pnl']:,.2f}",
                "价差摩擦": f"${r['trade_pnl']:+,.2f}",
                "平仓笔数": f"{r['trades']} 笔",
                "胜率": f"{r['win_rate']:.0f}%",
            })
        st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)


def render_institutional_pnl_analytics(arb_data: Dict[str, Any], bot_name: str, active_tz: str = "Asia/Shanghai", tz_label: str = "北京时间"):
    """
    Main entry point for Institutional PnL Analytics.
    """
    closed_trades = arb_data.get("closed_trades", [])
    active_pos = arb_data.get("active_positions", [])

    if not closed_trades and not active_pos:
        st.info("ℹ️ 暂无持仓或平仓交易记录。策略运行产生结算后，将自动呈现全方位每日/每周/每月盈亏分析。")
        return

    # --- Section A: Regime Segmentation Filter ---
    perm_bl_tokens = set(arb_data.get("permanent_blacklist", []))

    ctrl_c1, ctrl_c2 = st.columns([2.5, 1.5])
    with ctrl_c1:
        regime_mode = st.radio(
            "历史账本与事后筛选",
            [
                f"🌐 全量历史真实账本 (含早期试错旧币, 共 {len(closed_trades)} 笔)",
                "事后剔除当前黑名单（假设子集，不代表策略实绩）",
            ],
            horizontal=True,
            key=f"regime_mode_{bot_name}",
            help="默认展示全部历史损益。事后剔除亏损资产存在选择偏差，不能证明当时可以避免亏损或未来盈利。"
        )
    with ctrl_c2:
        timeframe_sel = st.selectbox(
            "⏱️ 周期聚合模式",
            ["每日 (Daily)", "每周 (Weekly)", "每月 (Monthly)"],
            key=f"tf_sel_{bot_name}"
        )

    # Filter dataframe based on selected regime
    raw_df = _parse_trades_dataframe(closed_trades, active_tz=active_tz)

    if "剔除" in regime_mode:
        df = raw_df[~raw_df["token"].isin(perm_bl_tokens)].copy() if not raw_df.empty else raw_df
    else:
        df = raw_df

    if "剔除" in regime_mode:
        st.warning("当前为事后筛选的假设子集。累计真实/模拟总损益仍以全量账本为准；筛选不回写历史。")

    # Calculate metrics
    metrics = _calculate_pnl_metrics(df, active_pos)

    # --- Section B: Executive KPI Tiles ---
    _render_executive_kpi_cards(metrics, active_pos)

    st.markdown("---")

    # --- Section C: PnL Calendar Heatmap ---
    _render_pnl_calendar_heatmap(df, active_tz, tz_label)

    st.markdown("---")

    # --- Section D: Multi-Timeframe Bar Chart & Cumulative Equity ---
    tab_bar, tab_equity = st.tabs([
        f"📊 {timeframe_sel} 盈亏直方图 (Bar Distribution)",
        "🌊 累计净值走势曲线 (Equity Curve & Drawdown)"
    ])

    with tab_bar:
        _render_timeframe_bar_chart(df, timeframe_sel, bot_name)

    with tab_equity:
        _render_equity_curve_chart(df, bot_name)

    st.markdown("---")

    # --- Section E: Asset Attribution Ranking ---
    _render_asset_attribution_ranking(df, arb_data, bot_name)
