import json
import os
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from frontend.st_utils import get_backend_api_client, initialize_st_page

initialize_st_page(icon="🦅", show_readme=False)

# Anti-flicker CSS to prevent aggressive dimming/flashing during auto-refresh
st.markdown("""
<style>
/* Prevent dimming/flashing during fragment auto-refresh */
[data-stale="true"],
[data-stale="true"] * {
    opacity: 1.0 !important;
    filter: none !important;
    transition: none !important;
}
/* Ensure Plotly charts and tables remain stable */
.js-plotly-plot {
    transition: none !important;
}
</style>
""", unsafe_allow_html=True)

# Initialize backend client
backend_api_client = get_backend_api_client()

# Initialize session state for auto-refresh
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = True

# Set refresh interval
REFRESH_INTERVAL = 10  # seconds


# Decoupled component import for Funding Rate Arbitrage (ensuring zero-conflict upstream updates)
from frontend.components.funding_arbitrage_view import (
    get_bot_funding_arbitrage_data,
    render_funding_arbitrage_kpi_cards,
    render_funding_arbitrage_tables,
)
from frontend.components.basis_stat_arb_view import (
    get_bot_basis_stat_arb_data,
    render_basis_stat_arb_tables,
)
from frontend.components.timezone_utils import format_timestamp


def stop_bot(bot_name):
    """Stop a running bot."""
    try:
        backend_api_client.bot_orchestration.stop_and_archive_bot(bot_name)
        st.success(f"Bot {bot_name} stopped and archived successfully")
        time.sleep(2)  # Give time for the backend to process
    except Exception as e:
        st.error(f"Failed to stop bot {bot_name}: {e}")


def archive_bot(bot_name):
    """Archive a stopped bot."""
    try:
        backend_api_client.docker.stop_container(bot_name)
        backend_api_client.docker.remove_container(bot_name)
        st.success(f"Bot {bot_name} archived successfully")
        time.sleep(1)
    except Exception as e:
        st.error(f"Failed to archive bot {bot_name}: {e}")


def stop_controllers(bot_name, controllers):
    """Stop selected controllers."""
    success_count = 0
    for controller in controllers:
        try:
            backend_api_client.controllers.update_bot_controller_config(
                bot_name,
                controller,
                {"manual_kill_switch": True}
            )
            success_count += 1
        except Exception as e:
            st.error(f"Failed to stop controller {controller}: {e}")

    if success_count > 0:
        st.success(f"Successfully stopped {success_count} controller(s)")
        # Temporarily disable auto-refresh to prevent immediate state reset
        st.session_state.auto_refresh_enabled = False

    return success_count > 0


def start_controllers(bot_name, controllers):
    """Start selected controllers."""
    success_count = 0
    for controller in controllers:
        try:
            backend_api_client.controllers.update_bot_controller_config(
                bot_name,
                controller,
                {"manual_kill_switch": False}
            )
            success_count += 1
        except Exception as e:
            st.error(f"Failed to start controller {controller}: {e}")

    if success_count > 0:
        st.success(f"Successfully started {success_count} controller(s)")
        # Temporarily disable auto-refresh to prevent immediate state reset
        st.session_state.auto_refresh_enabled = False

    return success_count > 0


def render_bot_card(bot_name):
    """Render a bot performance card using native Streamlit components."""
    try:
        # Get bot status first
        bot_status = backend_api_client.bot_orchestration.get_bot_status(bot_name)

        # Only try to get controller configs if bot exists and is running
        controller_configs = []
        if bot_status.get("status") == "success":
            bot_data = bot_status.get("data", {})
            is_running = bot_data.get("status") == "running"
            if is_running:
                try:
                    controller_configs = backend_api_client.controllers.get_bot_controller_configs(bot_name)
                    controller_configs = controller_configs if controller_configs else []
                except Exception as e:
                    # If controller configs fail, continue without them
                    st.warning(f"Could not fetch controller configs for {bot_name}: {e}")
                    controller_configs = []

        with st.container(border=True):

            if bot_status.get("status") == "error":
                # Error state
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.error(f"🤖 **{bot_name}** - Not Available")
                st.error(f"An error occurred while fetching bot status of {bot_name}. Please check the bot client.")
            else:
                bot_data = bot_status.get("data", {})
                is_running = bot_data.get("status") == "running"
                performance = bot_data.get("performance", {})
                error_logs = bot_data.get("error_logs", [])
                general_logs = bot_data.get("general_logs", [])

                # Early fetch funding rate arbitrage & basis stat arb data
                is_funding_arb_bot, arb_data = get_bot_funding_arbitrage_data(bot_name, performance)
                is_basis_arb_bot, basis_data = get_bot_basis_stat_arb_data(bot_name, performance)

                if is_basis_arb_bot:
                    strategy_badge = "⚡ 基差统计双击流"
                    badge_style = "background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.4); color: #38bdf8;"
                elif is_funding_arb_bot:
                    strategy_badge = "💰 纯资金费率套利"
                    badge_style = "background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: #10b981;"
                else:
                    strategy_badge = "🤖 通用策略"
                    badge_style = "background: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.4); color: #c084fc;"

                # Expand / Collapse session state tracking
                expand_key = f"bot_expand_state_{bot_name}"
                if expand_key not in st.session_state:
                    st.session_state[expand_key] = True
                is_expanded = st.session_state[expand_key]

                # Bot header
                col_head, col_btns = st.columns([3, 2])
                with col_head:
                    status_dot = "🟢" if is_running else "🟡"
                    status_label = "Running" if is_running else "Stopped"
                    status_color = "#10b981" if is_running else "#f59e0b"
                    st.markdown(
                        f"<div style='display:flex;align-items:center;gap:8px;padding-top:4px;'>"
                        f"<span style='{badge_style};padding:3px 8px;border-radius:6px;font-size:0.8rem;font-weight:700;'>{strategy_badge}</span>"
                        f"<span style='font-size:1.15rem;font-weight:700;color:#f8fafc;'>{bot_name}</span>"
                        f"<span style='font-size:0.85rem;font-weight:600;color:{status_color};'>({status_dot} {status_label})</span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )

                with col_btns:
                    col_b1, col_b2 = st.columns(2)
                    with col_b1:
                        toggle_btn_text = "🔼 收起折叠" if is_expanded else "🔽 点击展开"
                        toggle_btn_help = "收起当前策略卡片为紧凑概览条，节省纵向空间" if is_expanded else "展开查看完整持仓、套利监控雷达与订单深度"
                        if st.button(toggle_btn_text, key=f"toggle_expand_{bot_name}", use_container_width=True, help=toggle_btn_help):
                            st.session_state[expand_key] = not is_expanded
                            st.rerun()

                    with col_b2:
                        if is_running:
                            if st.button("⏹️ Stop", key=f"stop_{bot_name}", use_container_width=True, help="停止此容器实例"):
                                stop_bot(bot_name)
                        else:
                            if st.button("📦 Archive", key=f"archive_{bot_name}", use_container_width=True, help="归档此容器实例"):
                                archive_bot(bot_name)

                # COLLAPSED EXECUTIVE SUMMARY STRIP (80px compact view)
                if not is_expanded:
                    st.markdown("<hr style='margin: 8px 0 12px 0; border: none; border-top: 1px solid rgba(255,255,255,0.08);'>", unsafe_allow_html=True)
                    if is_funding_arb_bot and arb_data:
                        active_pos = arb_data.get("active_positions", [])
                        closed_trades = arb_data.get("closed_trades", [])
                        c_realized = sum(float(t.get("total_net_pnl", 0)) for t in closed_trades)
                        c_floating_unreal = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
                        c_floating_funding = sum(float(p.get("funding_income", 0)) for p in active_pos)
                        c_total_funding = c_floating_funding + sum(float(t.get("funding_pnl", 0)) for t in closed_trades)
                        c_net = c_realized + c_floating_unreal + c_floating_funding

                        c1, c2, c3, c4 = st.columns(4)
                        with c1:
                            st.metric("持仓状态", f"{len(active_pos)} 笔活跃", f"已结 {len(closed_trades)} 笔", help="当前持仓对冲与历史平仓笔数")
                        with c2:
                            st.metric("累计资金费利息", f"+${c_total_funding:,.2f}", help="真金白银已到账不可逆利息总和")
                        with c3:
                            st.metric("已平仓锁定利润", f"${c_realized:+,.2f}", help="完全闭环平仓的历史交易最终净利润")
                        with c4:
                            st.metric("综合预估总净值", f"${c_net:+,.2f}", f"持仓浮动 ${c_floating_unreal + c_floating_funding:+,.2f}", help="已实现利润 + 当前活跃仓位假想平仓净值")

                    elif is_basis_arb_bot and basis_data:
                        active_pos = basis_data.get("active_positions", [])
                        closed_trades = basis_data.get("closed_trades", [])
                        radar_pairs = basis_data.get("market_comparison", [])
                        c_realized = sum(float(t.get("total_net_pnl", 0)) for t in closed_trades)
                        c_floating_unreal = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
                        c_floating_funding = sum(float(p.get("funding_income", 0)) for p in active_pos)
                        c_net = c_realized + c_floating_unreal + c_floating_funding

                        c1, c2, c3, c4 = st.columns(4)
                        with c1:
                            st.metric("市场雷达监控", f"{len(radar_pairs)} 对标的", "双所微观基差与深度实时跟踪", help="实时监控的 Binance vs Hyperliquid 现货/永续基差币对")
                        with c2:
                            st.metric("活跃基差套利仓", f"{len(active_pos)} 笔活跃", f"已结 {len(closed_trades)} 笔", help="当前基差均值回归套利持仓")
                        with c3:
                            st.metric("已平仓锁定利润", f"${c_realized:+,.2f}", help="均值回归完成后已锁定的净收益")
                        with c4:
                            st.metric("综合预估总净值", f"${c_net:+,.2f}", f"持仓浮动 ${c_floating_unreal + c_floating_funding:+,.2f}")

                    else:
                        c1, c2, c3, c4 = st.columns(4)
                        with c1:
                            st.metric("运行状态", "🟢 正常" if is_running else "🟡 停止")
                        with c2:
                            st.metric("控制器数量", f"{len(controller_configs)} 个")
                        with c3:
                            st.metric("错误日志", f"{len(error_logs)} 条")
                        with c4:
                            st.metric("常规日志", f"{len(general_logs)} 条")

                    st.caption("💡 此策略卡片已收起折叠（节省纵向滚动空间）。若需查看持仓明细、监控雷达、收益图表与系统日志，请点击右上角「🔽 点击展开」。")
                    return

                if is_running:
                    # Calculate totals
                    active_controllers = []
                    stopped_controllers = []
                    error_controllers = []
                    total_global_pnl_quote = 0.0
                    total_volume_traded = 0.0
                    total_unrealized_pnl_quote = 0.0

                    for controller, inner_dict in performance.items():
                        if controller in ("funding_rate_arb", "basis_stat_arb"):
                            # Accumulate arbitrage performance into top-level totals
                            arb_perf = inner_dict.get("performance", {})
                            total_global_pnl_quote += float(arb_perf.get("global_pnl_quote", 0.0))
                            total_unrealized_pnl_quote += float(arb_perf.get("unrealized_pnl_quote", 0.0))
                            total_volume_traded += float(arb_perf.get("volume_traded", 0.0))
                            continue

                        controller_status = inner_dict.get("status")
                        if controller_status == "error":
                            error_controllers.append({
                                "Controller": controller,
                                "Error": inner_dict.get("error", "Unknown error")
                            })
                            continue

                        controller_performance = inner_dict.get("performance", {})
                        controller_config = next(
                            (config for config in controller_configs if config.get("id") == controller), {}
                        )

                        controller_name = controller_config.get("controller_name", controller)

                        connector_name = controller_config.get("connector_name", "N/A")
                        trading_pair = controller_config.get("trading_pair", "N/A")
                        kill_switch_status = controller_config.get("manual_kill_switch", False)

                        realized_pnl_quote = controller_performance.get("realized_pnl_quote", 0)
                        unrealized_pnl_quote = controller_performance.get("unrealized_pnl_quote", 0)
                        global_pnl_quote = controller_performance.get("global_pnl_quote", 0)
                        volume_traded = controller_performance.get("volume_traded", 0)

                        close_types = controller_performance.get("close_type_counts", {})
                        tp = close_types.get("CloseType.TAKE_PROFIT", 0)
                        sl = close_types.get("CloseType.STOP_LOSS", 0)
                        time_limit = close_types.get("CloseType.TIME_LIMIT", 0)
                        ts = close_types.get("CloseType.TRAILING_STOP", 0)
                        refreshed = close_types.get("CloseType.EARLY_STOP", 0)
                        failed = close_types.get("CloseType.FAILED", 0)
                        close_types_str = f"TP: {tp} | SL: {sl} | TS: {ts} | TL: {time_limit} | ES: {refreshed} | F: {failed}"

                        controller_info = {
                            "Select": False,
                            "ID": controller_config.get("id"),
                            "Controller": controller_name,
                            "Connector": connector_name,
                            "Trading Pair": trading_pair,
                            "Realized PNL ($)": round(realized_pnl_quote, 2),
                            "Unrealized PNL ($)": round(unrealized_pnl_quote, 2),
                            "NET PNL ($)": round(global_pnl_quote, 2),
                            "Volume ($)": round(volume_traded, 2),
                            "Close Types": close_types_str,
                            "_controller_id": controller
                        }

                        if kill_switch_status:
                            stopped_controllers.append(controller_info)
                        else:
                            active_controllers.append(controller_info)

                        total_global_pnl_quote += global_pnl_quote
                        total_volume_traded += volume_traded
                        total_unrealized_pnl_quote += unrealized_pnl_quote

                    # Fallback / augment with arb_data if funding arb bot
                    if is_funding_arb_bot and arb_data:
                        active_pos = arb_data.get("active_positions", [])
                        closed_trades = arb_data.get("closed_trades", [])

                        # If MQTT didn't provide performance totals, compute directly from arb_data
                        if total_volume_traded == 0 and (active_pos or closed_trades):
                            active_unrealized = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
                            active_funding = sum(float(p.get("funding_income", 0)) for p in active_pos)
                            closed_pnl = sum(float(t.get("total_net_pnl", 0)) for t in closed_trades)

                            total_unrealized_pnl_quote = active_unrealized + active_funding
                            total_global_pnl_quote = closed_pnl + active_unrealized + active_funding

                            active_vol = sum(float(p.get("position_size", 10000.0)) * 2.0 for p in active_pos)
                            closed_vol = sum(float(t.get("position_size", 10000.0)) * 4.0 for t in closed_trades)
                            total_volume_traded = active_vol + closed_vol
                        else:
                            # Ensure round-trip volume of closed trades is also counted if MQTT only counted active
                            closed_vol = sum(float(t.get("position_size", 10000.0)) * 4.0 for t in closed_trades)
                            min_expected_vol = sum(float(p.get("position_size", 10000.0)) * 2.0 for p in active_pos) + closed_vol
                            if total_volume_traded < min_expected_vol:
                                total_volume_traded = min_expected_vol

                    # Calculate detailed breakdown for funding arbitrage or basis stat arb bots
                    if is_funding_arb_bot and arb_data:
                        render_funding_arbitrage_kpi_cards(arb_data, total_volume_traded, total_global_pnl_quote)
                        render_funding_arbitrage_tables(arb_data, bot_name)
                    elif is_basis_arb_bot and basis_data:
                        render_basis_stat_arb_tables(basis_data, bot_name)
                    else:
                        total_global_pnl_pct = total_global_pnl_quote / total_volume_traded if total_volume_traded > 0 else 0

                        # Display metrics
                        col1, col2, col3, col4 = st.columns(4)

                        with col1:
                            st.metric("🏦 NET PNL", f"${total_global_pnl_quote:,.2f}")
                        with col2:
                            st.metric("💹 Unrealized PNL", f"${total_unrealized_pnl_quote:,.2f}")
                        with col3:
                            st.metric("📊 NET PNL (%)", f"{total_global_pnl_pct:+.2%}")
                        with col4:
                            st.metric("💸 Volume Traded", f"${total_volume_traded:,.2f}")

                    # Active Controllers
                    if active_controllers:
                        st.success("🚀 **Active Controllers:** Controllers currently running and trading")
                        active_df = pd.DataFrame(active_controllers)

                        edited_active_df = st.data_editor(
                            active_df,
                            column_config={
                                "Select": st.column_config.CheckboxColumn(
                                    "Select",
                                    help="Select controllers to stop",
                                    default=False,
                                ),
                                "_controller_id": None,  # Hide this column
                            },
                            disabled=[col for col in active_df.columns if col != "Select"],
                            hide_index=True,
                            use_container_width=True,
                            key=f"active_table_{bot_name}"
                        )

                        selected_active = [
                            row["_controller_id"]
                            for _, row in edited_active_df.iterrows()
                            if row["Select"]
                        ]

                        if selected_active:
                            if st.button(f"⏹️ Stop Selected ({len(selected_active)})",
                                         key=f"stop_active_{bot_name}",
                                         type="secondary"):
                                with st.spinner(f"Stopping {len(selected_active)} controller(s)..."):
                                    stop_controllers(bot_name, selected_active)
                                    time.sleep(1)

                    # Stopped Controllers
                    if stopped_controllers:
                        st.warning("💤 **Stopped Controllers:** Controllers that are paused or stopped")
                        stopped_df = pd.DataFrame(stopped_controllers)

                        edited_stopped_df = st.data_editor(
                            stopped_df,
                            column_config={
                                "Select": st.column_config.CheckboxColumn(
                                    "Select",
                                    help="Select controllers to start",
                                    default=False,
                                ),
                                "_controller_id": None,  # Hide this column
                            },
                            disabled=[col for col in stopped_df.columns if col != "Select"],
                            hide_index=True,
                            use_container_width=True,
                            key=f"stopped_table_{bot_name}"
                        )

                        selected_stopped = [
                            row["_controller_id"]
                            for _, row in edited_stopped_df.iterrows()
                            if row["Select"]
                        ]

                        if selected_stopped:
                            if st.button(f"▶️ Start Selected ({len(selected_stopped)})",
                                         key=f"start_stopped_{bot_name}",
                                         type="primary"):
                                with st.spinner(f"Starting {len(selected_stopped)} controller(s)..."):
                                    start_controllers(bot_name, selected_stopped)
                                    time.sleep(1)

                    # Error Controllers
                    if error_controllers:
                        st.error("💀 **Controllers with Errors:** Controllers that encountered errors")
                        error_df = pd.DataFrame(error_controllers)
                        st.dataframe(error_df, use_container_width=True, hide_index=True)

                else:
                    # For stopped bots, still show historical arbitrage data if present
                    is_funding_arb_bot, arb_data = get_bot_funding_arbitrage_data(bot_name, performance)
                    is_basis_arb_bot, basis_data = get_bot_basis_stat_arb_data(bot_name, performance)
                    if is_funding_arb_bot and (arb_data.get("closed_trades") or arb_data.get("active_positions")):
                        render_funding_arbitrage_tables(arb_data, bot_name)
                    elif is_basis_arb_bot and (basis_data.get("closed_trades") or basis_data.get("active_positions")):
                        render_basis_stat_arb_tables(basis_data, bot_name)

                # Logs sections (available for both running and stopped bots)
                with st.expander("📋 Error Logs"):
                    clear_error_key = f"cleared_error_ids_{bot_name}"
                    cleared_error_ids = st.session_state.get(clear_error_key, set())

                    col_ctrl1, col_ctrl2 = st.columns([3, 1])
                    with col_ctrl2:
                        if cleared_error_ids:
                            if st.button("🔄 显示全部", key=f"restore_errors_{bot_name}", use_container_width=True):
                                st.session_state[clear_error_key] = set()
                                st.rerun()
                        else:
                            if error_logs and st.button("🧹 清空显示", key=f"clear_errors_{bot_name}", use_container_width=True, help="清空屏幕上旧的错误显示，仅在出现新错误时提示（不影响底层日志记录）"):
                                st.session_state[clear_error_key] = {
                                    f"{l.get('timestamp')}_{l.get('msg')}" for l in error_logs
                                }
                                st.rerun()

                    # Filter visible error logs
                    if cleared_error_ids:
                        visible_error_logs = [
                            l for l in error_logs
                            if f"{l.get('timestamp')}_{l.get('msg')}" not in cleared_error_ids
                        ]
                    else:
                        visible_error_logs = error_logs

                    if cleared_error_ids and len(visible_error_logs) < len(error_logs):
                        with col_ctrl1:
                            st.caption(f"ℹ️ 已清空屏幕旧显示（隐藏 {len(error_logs) - len(visible_error_logs)} 条旧日志，仅监测新错误）")

                    if visible_error_logs:
                        for log in visible_error_logs[:50]:
                            timestamp = format_timestamp(log.get("timestamp", ""))
                            message = log.get("msg", "")
                            logger_name = log.get("logger_name", "")
                            st.text(f"{timestamp} - {logger_name}: {message}")
                    else:
                        if cleared_error_ids:
                            st.success("✨ 屏幕显示已清空，当前暂无新错误产生。")
                        else:
                            st.info("No error logs available.")

                with st.expander("📝 General Logs"):
                    clear_gen_key = f"cleared_gen_ids_{bot_name}"
                    cleared_gen_ids = st.session_state.get(clear_gen_key, set())

                    col_g1, col_g2 = st.columns([3, 1])
                    with col_g2:
                        if cleared_gen_ids:
                            if st.button("🔄 显示全部", key=f"restore_gen_{bot_name}", use_container_width=True):
                                st.session_state[clear_gen_key] = set()
                                st.rerun()
                        else:
                            if general_logs and st.button("🧹 清空显示", key=f"clear_gen_{bot_name}", use_container_width=True, help="清空屏幕上旧日志显示，仅在产生新日志时输出（不影响底层日志记录）"):
                                st.session_state[clear_gen_key] = {
                                    f"{l.get('timestamp')}_{l.get('msg')}" for l in general_logs
                                }
                                st.rerun()

                    # Filter visible general logs
                    if cleared_gen_ids:
                        visible_gen_logs = [
                            l for l in general_logs
                            if f"{l.get('timestamp')}_{l.get('msg')}" not in cleared_gen_ids
                        ]
                    else:
                        visible_gen_logs = general_logs

                    if cleared_gen_ids and len(visible_gen_logs) < len(general_logs):
                        with col_g1:
                            st.caption(f"ℹ️ 已清空屏幕旧显示（隐藏 {len(general_logs) - len(visible_gen_logs)} 条旧日志）")

                    if visible_gen_logs:
                        for log in visible_gen_logs[:50]:
                            timestamp = format_timestamp(log.get("timestamp", 0))
                            message = log.get("msg", "")
                            logger_name = log.get("logger_name", "")
                            st.text(f"{timestamp} - {logger_name}: {message}")
                    else:
                        if cleared_gen_ids:
                            st.success("✨ 屏幕显示已清空，当前暂无新日志产生。")
                        else:
                            st.info("No general logs available.")

                # Bottom collapse shortcut to avoid scrolling back up
                st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
                col_bf1, col_bf2 = st.columns([4, 1])
                with col_bf2:
                    if st.button("🔼 收起此策略卡片", key=f"foot_collapse_{bot_name}", use_container_width=True, help="收起当前策略卡片"):
                        st.session_state[expand_key] = False
                        st.rerun()

    except Exception as e:
        with st.container(border=True):
            st.error(f"🤖 **{bot_name}** - Error")
            st.error(f"An error occurred while fetching bot status: {str(e)}")


# Page Header
st.title("🦅 Hummingbot Instances")

# Auto-refresh controls
col1, col2, col3 = st.columns([3, 1, 1])

# Create placeholder for status message
status_placeholder = col1.empty()

with col2:
    if st.button("▶️ Start Auto-refresh" if not st.session_state.auto_refresh_enabled else "⏸️ Stop Auto-refresh",
                 use_container_width=True):
        st.session_state.auto_refresh_enabled = not st.session_state.auto_refresh_enabled

with col3:
    if st.button("🔄 Refresh Now", use_container_width=True):
        # Re-enable auto-refresh if it was temporarily disabled
        if not st.session_state.auto_refresh_enabled:
            st.session_state.auto_refresh_enabled = True
        pass


@st.fragment(run_every=REFRESH_INTERVAL if st.session_state.auto_refresh_enabled else None)
def show_bot_instances():
    """Fragment to display bot instances with auto-refresh."""
    try:
        active_bots_response = backend_api_client.bot_orchestration.get_active_bots_status()

        if active_bots_response.get("status") == "success":
            active_bots = active_bots_response.get("data", {})

            # Filter out any bots that might be in transitional state
            truly_active_bots = {}
            for bot_name, bot_info in active_bots.items():
                try:
                    bot_status = backend_api_client.bot_orchestration.get_bot_status(bot_name)
                    if bot_status.get("status") == "success":
                        bot_data = bot_status.get("data", {})
                        if bot_data.get("status") in ["running", "stopped"]:
                            truly_active_bots[bot_name] = bot_info
                except Exception:
                    continue

            if truly_active_bots:
                # Show refresh status
                if st.session_state.auto_refresh_enabled:
                    status_placeholder.info(f"🔄 Auto-refreshing every {REFRESH_INTERVAL} seconds")
                else:
                    status_placeholder.warning("⏸️ Auto-refresh paused. Click 'Refresh Now' to resume.")

                # Classify running bots by strategy type
                basis_bots = [b for b in truly_active_bots.keys() if "basis" in b.lower()]
                funding_bots = [b for b in truly_active_bots.keys() if "funding" in b.lower()]
                other_bots = [b for b in truly_active_bots.keys() if b not in basis_bots and b not in funding_bots]

                filter_options = [
                    "🗂️ 平铺全部策略",
                    "⚡ 基差统计双击流",
                    "💰 纯资金费率套利",
                ]
                if other_bots:
                    filter_options.append("🤖 其他策略实例")

                def format_filter_label(opt):
                    if "基差" in opt:
                        return f"{opt} ({len(basis_bots)})"
                    elif "资金" in opt:
                        return f"{opt} ({len(funding_bots)})"
                    elif "其他" in opt:
                        return f"{opt} ({len(other_bots)})"
                    else:
                        return f"{opt} ({len(truly_active_bots)})"

                # Navigation & Action Dock
                st.markdown("<div style='margin-top: 10px; margin-bottom: 4px;'></div>", unsafe_allow_html=True)
                col_filter, col_actions = st.columns([3, 2])
                with col_filter:
                    current_filter = st.radio(
                        "策略视图筛选",
                        options=filter_options,
                        format_func=format_filter_label,
                        horizontal=True,
                        label_visibility="collapsed",
                        key="instances_strategy_filter",
                    )

                with col_actions:
                    col_a1, col_a2 = st.columns(2)
                    with col_a1:
                        if st.button("🔽 全部展开", key="expand_all_instances", use_container_width=True, help="一键展开所有策略卡片的完整明细"):
                            for b in truly_active_bots.keys():
                                st.session_state[f"bot_expand_state_{b}"] = True
                            st.rerun()
                    with col_a2:
                        if st.button("🔼 全部收起", key="collapse_all_instances", use_container_width=True, help="一键折叠所有策略卡片为紧凑概览条，消除垂直滚动"):
                            for b in truly_active_bots.keys():
                                st.session_state[f"bot_expand_state_{b}"] = False
                            st.rerun()

                # Determine which bots to display based on selected filter
                if "基差" in current_filter:
                    visible_bots = basis_bots
                    empty_msg = "当前暂无运行中的【基差统计双击流】实例。可前往「Config Generators -> Basis Stat Arb」快速配置启动。"
                elif "资金" in current_filter:
                    visible_bots = funding_bots
                    empty_msg = "当前暂无运行中的【纯资金费率套利】实例。"
                elif "其他" in current_filter:
                    visible_bots = other_bots
                    empty_msg = "当前暂无其他运行中的策略实例。"
                else:
                    visible_bots = list(truly_active_bots.keys())
                    empty_msg = "暂无活跃实例。"

                if visible_bots:
                    # Render each visible bot
                    for bot_name in visible_bots:
                        render_bot_card(bot_name)
                else:
                    st.info(empty_msg)
            else:
                status_placeholder.info("No active bot instances found. Deploy a bot to see it here.")
        else:
            st.error("Failed to fetch active bots status.")

    except Exception as e:
        st.error(f"Failed to connect to backend: {e}")
        st.info("Please make sure the backend is running and accessible.")


# Call the fragment
show_bot_instances()
