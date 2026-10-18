import json
import os
import time

import pandas as pd
import streamlit as st

from frontend.st_utils import get_backend_api_client, initialize_st_page

initialize_st_page(icon="🦅", show_readme=False)

# Initialize backend client
backend_api_client = get_backend_api_client()

# Initialize session state for auto-refresh
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = True

# Set refresh interval
REFRESH_INTERVAL = 10  # seconds


def render_funding_arbitrage_tables(arb_data, bot_name):
    """Render visual tables for Funding Rate Arbitrage positions, closed trades, and rate comparisons."""
    active_pos = arb_data.get("active_positions", [])
    closed_trades = arb_data.get("closed_trades", [])
    market_comp = arb_data.get("market_comparison", [])
    is_dry_run = arb_data.get("is_dry_run", True)

    mode_badge = "🟢 纸面预演 (Dry-Run)" if is_dry_run else "🔴 实盘交易 (Live)"
    st.markdown(f"#### 💰 资金费率套利持仓与成交详情 `{mode_badge}`")

    tab_pos, tab_closed, tab_rates = st.tabs([
        f"📈 活跃多空持仓 ({len(active_pos)})",
        f"📜 历史平仓成交 ({len(closed_trades)})",
        f"🌐 实时费率与利差监控 ({len(market_comp)})"
    ])

    with tab_pos:
        if active_pos:
            st.caption("ℹ️ 当前正在双边对冲并按期收取资金费的套利头寸（随交易所盘口每 10 秒自动刷新）")
            pos_rows = []
            for p in active_pos:
                duration_min = p.get("duration_seconds", 0) // 60
                dur_str = f"{duration_min // 60}h {duration_min % 60}m"
                side = p.get("side", "")
                long_ex = p.get("connector_1", "") if side == "BUY" else p.get("connector_2", "")
                short_ex = p.get("connector_2", "") if side == "BUY" else p.get("connector_1", "")
                long_p = p.get("entry_price_1", 0) if side == "BUY" else p.get("entry_price_2", 0)
                short_p = p.get("entry_price_2", 0) if side == "BUY" else p.get("entry_price_1", 0)
                curr_p1 = p.get("current_price_1", 0)
                curr_p2 = p.get("current_price_2", 0)
                curr_long_p = curr_p1 if side == "BUY" else curr_p2
                curr_short_p = curr_p2 if side == "BUY" else curr_p1

                now_ts = time.time()
                next_f1 = max(0, int(p.get("next_funding_1", 0) - now_ts)) // 60
                next_f2 = max(0, int(p.get("next_funding_2", 0) - now_ts)) // 60
                c1_abbr = long_ex.split('_')[0].capitalize()
                c2_abbr = short_ex.split('_')[0].capitalize()
                countdown_str = f"{c1_abbr}: {next_f1}m | {c2_abbr}: {next_f2}m"

                pos_rows.append({
                    "标的币种": p.get("token"),
                    "多头腿 (Long)": f"{long_ex} (买: ${long_p:.4f} | 现: ${curr_long_p:.4f})",
                    "空头腿 (Short)": f"{short_ex} (卖: ${short_p:.4f} | 现: ${curr_short_p:.4f})",
                    "名义头寸": f"${p.get('position_size', 0):.2f}",
                    "累计已收资金费": f"${p.get('funding_income', 0):+.4f} ({p.get('settlement_count', 0)}次)",
                    "盘口浮盈亏": f"${p.get('unrealized_trading_pnl', 0):+.4f}",
                    "综合净收益": f"${p.get('net_pnl', 0):+.4f} ({p.get('net_pnl_pct', 0):+.2%})",
                    "持仓时长": dur_str,
                    "下期结算倒计时": countdown_str,
                })
            st.dataframe(pd.DataFrame(pos_rows), use_container_width=True, hide_index=True)
        else:
            st.info("ℹ️ 当前暂无持仓中头寸。策略正在持续比对两所实时资金费率，一旦出现利差机会将在此处自动展示多空建仓与利息明细。")

    with tab_closed:
        if closed_trades:
            st.caption("ℹ️ 达到止盈目标（Take Profit）或触发费率倒挂止损（Stop Loss）而平仓的历史记录")
            closed_rows = []
            for t in closed_trades:
                close_dt = pd.to_datetime(t.get("close_timestamp", 0), unit="s").strftime("%Y-%m-%d %H:%M:%S") if t.get("close_timestamp") else "N/A"
                closed_rows.append({
                    "平仓时间": close_dt,
                    "标的币种": t.get("token"),
                    "套利交易所": f"{t.get('connector_1')} & {t.get('connector_2')}",
                    "平仓原因": t.get("close_reason", "Completed"),
                    "价差损益": f"${t.get('trading_pnl', 0):+.4f}",
                    "资金费净利息": f"${t.get('funding_pnl', 0):+.4f}",
                    "最终实现净利润 (Net PnL)": f"${t.get('total_net_pnl', 0):+.4f}",
                })
            st.dataframe(pd.DataFrame(closed_rows), use_container_width=True, hide_index=True)
        else:
            st.info("ℹ️ 暂无历史平仓记录。")

    with tab_rates:
        if market_comp:
            st.caption("ℹ️ 策略后台实时扫描的各币种在不同交易所的资金费率及最佳套利利差")
            rates_rows = []
            for m in market_comp:
                daily_diff = m.get('daily_rate_diff_pct', 0)
                apr_val = daily_diff * 365
                apr_str = f"{apr_val:+.2f}%" if abs(apr_val) < 10000 else f"{apr_val:+.2e}%"
                row = {
                    "监控币种": m.get("token"),
                    "推荐套利组合 (Path)": m.get("best_path", "N/A"),
                    "折算日化利差 (Daily Diff)": f"{daily_diff:+.4f}% / 天" if abs(daily_diff) < 1000 else f"{daily_diff:+.2e}% / 天",
                    "折算年化利差 (APR)": apr_str,
                }
                for k, v in m.items():
                    if k.endswith("_rate_daily"):
                        c_title = k.replace("_rate_daily", "")
                        v_str = f"{v:+.4f}%" if abs(v) < 1000 else f"{v:+.2e}%"
                        row[f"{c_title} 费率 (日化)"] = v_str
                rates_rows.append(row)
            st.dataframe(pd.DataFrame(rates_rows), use_container_width=True, hide_index=True)
        else:
            st.info("ℹ️ 正在连接交易所拉取实时费率对比数据...")


def get_bot_funding_arbitrage_data(bot_name, performance):
    """Retrieve funding arbitrage custom_info from MQTT performance or local disk."""
    arb_data = None
    for ctrl_id, ctrl_dict in performance.items():
        c_info = ctrl_dict.get("custom_info", {})
        if "active_positions" in c_info or "closed_trades" in c_info or "market_comparison" in c_info:
            arb_data = c_info
            break

    local_json = os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        f"../../../../../hummingbot-api/bots/instances/{bot_name}/data/arbitrage_trades.json"
    ))
    if not arb_data and os.path.exists(local_json):
        try:
            with open(local_json, "r") as jf:
                arb_data = json.load(jf)
        except Exception:
            pass

    # Check if bot is a funding rate arbitrage bot
    is_funding_arb_bot = (
        "funding" in bot_name.lower() or 
        "funding_rate_arb" in performance or 
        arb_data is not None
    )
    if not is_funding_arb_bot:
        scripts_conf_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__),
            f"../../../../../hummingbot-api/bots/instances/{bot_name}/conf/scripts"
        ))
        if os.path.isdir(scripts_conf_dir):
            for fname in os.listdir(scripts_conf_dir):
                if fname.endswith(".yml") or fname.endswith(".yaml"):
                    try:
                        with open(os.path.join(scripts_conf_dir, fname), "r") as f:
                            if "funding" in f.read():
                                is_funding_arb_bot = True
                                break
                    except Exception:
                        pass

    if is_funding_arb_bot and not arb_data:
        arb_data = {
            "is_dry_run": True,
            "active_positions": [],
            "closed_trades": [],
            "market_comparison": []
        }

    return is_funding_arb_bot, arb_data


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

                # Bot header
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    if is_running:
                        st.success(f"🤖 **{bot_name}** - Running")
                    else:
                        st.warning(f"🤖 **{bot_name}** - Stopped")

                with col3:
                    if is_running:
                        if st.button("⏹️ Stop", key=f"stop_{bot_name}", use_container_width=True):
                            stop_bot(bot_name)
                    else:
                        if st.button("📦 Archive", key=f"archive_{bot_name}", use_container_width=True):
                            archive_bot(bot_name)

                if is_running:
                    # Calculate totals
                    active_controllers = []
                    stopped_controllers = []
                    error_controllers = []
                    total_global_pnl_quote = 0
                    total_volume_traded = 0
                    total_unrealized_pnl_quote = 0

                    for controller, inner_dict in performance.items():
                        if controller == "funding_rate_arb":
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

                    total_global_pnl_pct = total_global_pnl_quote / total_volume_traded if total_volume_traded > 0 else 0

                    # Display metrics
                    col1, col2, col3, col4 = st.columns(4)

                    with col1:
                        st.metric("🏦 NET PNL", f"${total_global_pnl_quote:.2f}")
                    with col2:
                        st.metric("💹 Unrealized PNL", f"${total_unrealized_pnl_quote:.2f}")
                    with col3:
                        st.metric("📊 NET PNL (%)", f"{total_global_pnl_pct:.2%}")
                    with col4:
                        st.metric("💸 Volume Traded", f"${total_volume_traded:.2f}")

                    # Funding Rate Arbitrage Visual Section
                    is_funding_arb_bot, arb_data = get_bot_funding_arbitrage_data(bot_name, performance)
                    if is_funding_arb_bot:
                        render_funding_arbitrage_tables(arb_data, bot_name)

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
                    if is_funding_arb_bot and (arb_data.get("closed_trades") or arb_data.get("active_positions")):
                        render_funding_arbitrage_tables(arb_data, bot_name)

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
                            timestamp = log.get("timestamp", "")
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
                            timestamp = pd.to_datetime(int(log.get("timestamp", 0)), unit="s")
                            message = log.get("msg", "")
                            logger_name = log.get("logger_name", "")
                            st.text(f"{timestamp} - {logger_name}: {message}")
                    else:
                        if cleared_gen_ids:
                            st.success("✨ 屏幕显示已清空，当前暂无新日志产生。")
                        else:
                            st.info("No general logs available.")

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

                # Render each bot
                for bot_name in truly_active_bots.keys():
                    render_bot_card(bot_name)
            else:
                status_placeholder.info("No active bot instances found. Deploy a bot to see it here.")
        else:
            st.error("Failed to fetch active bots status.")

    except Exception as e:
        st.error(f"Failed to connect to backend: {e}")
        st.info("Please make sure the backend is running and accessible.")


# Call the fragment
show_bot_instances()
