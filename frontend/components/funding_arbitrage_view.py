"""
Funding Rate Arbitrage Isolated View Component
=============================================
This component decouples all funding rate arbitrage specific presentation,
tables, depth analysis charts, and KPI cards from the generic Hummingbot
instances orchestration page (app.py).

By isolating custom arbitrage logic here, the upstream app.py remains minimally
invasive, keeping custom reporting separate from upstream orchestration.
"""
import json
import os
import time

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from frontend.components.clock_monitor import get_exchange_clock_health
from frontend.components.funding_universe_status import no_rotation_message, render_discovery_status
from frontend.components.market_links import get_official_market_url, get_token_research_urls
from frontend.components.timezone_utils import (
    format_timestamp,
    get_current_timezone,
    get_timezone_label,
    render_timezone_selector,
)
from frontend.components.trade_attribution import analyze_trade_attribution, calculate_closed_summary
from frontend.components.pnl_analytics import render_institutional_pnl_analytics



def render_funding_arbitrage_kpi_cards(arb_data, total_volume_traded, total_global_pnl_quote):
    """Render the 4 decoupled accounting KPI cards for Funding Rate Arbitrage bots."""
    active_pos = arb_data.get("active_positions", [])
    audit = arb_data.get("audit", {})
    if arb_data.get("is_dry_run"):
        st.caption("模拟账本：盘口成交、手续费及强平距离为模型估算，不代表真实成交或交易所账户余额。")
    if audit.get("blocked_reason"):
        st.error(f"策略暂停新增仓位：{audit['blocked_reason']}")
    if audit.get("valid_valuation") is False:
        st.warning("部分持仓行情不可用，下列浮盈亏包含旧估值，不能作为当前可平仓收益。")
    if any(p.get("funding_pending") for p in active_pos):
        st.info("部分资金费历史仍在补记；当前净收益可能继续修订。")
    if arb_data.get("paper_account"):
        account = arb_data["paper_account"]
        st.caption(f"模拟总资金 ${float(account['initial_balance']):,.2f} · 模型净值 ${float(account['equity']):,.2f} · 各所可用预算 {account.get('available', {})}")
    closed_trades = arb_data.get("closed_trades", [])
    global_lev = int(arb_data.get("leverage", 3))

    total_realized_pnl = sum(float(t.get("total_net_pnl", 0)) for t in closed_trades)
    total_funding_income = sum(float(p.get("funding_income", 0)) for p in active_pos) + sum(float(t.get("funding_pnl", 0)) for t in closed_trades)
    total_active_unrealized = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
    total_active_funding = sum(float(p.get("funding_income", 0)) for p in active_pos)
    total_active_net = total_active_unrealized + total_active_funding

    real_margin_total = sum(
        float(p.get("initial_margin_quote", float(p.get("position_size", 0)) * (
            1.0 / float(p.get("long_leverage", global_lev)) +
            1.0 / float(p.get("short_leverage", global_lev))
        ))) for p in active_pos
    )

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "🏦 综合净利润 (NET PNL)",
            f"${total_global_pnl_quote:,.2f}",
            f"已结 ${total_realized_pnl:+,.2f} | 浮动 ${total_active_net:+,.2f}",
            help="全周期累计综合总盈亏 = 历史平仓已锁定净利 + 活跃持仓瞬时浮动净盈亏（含资金费）"
        )
    with col2:
        st.metric(
            "💰 纯资金费利息 (Funding)",
            f"+${total_funding_income:,.2f}",
            f"已结 +${sum(float(t.get('funding_pnl', 0)) for t in closed_trades):,.2f} | 持仓 +${total_active_funding:,.2f}",
            help="策略核心 Alpha：真金白银已到账且不可逆的利息现金流总额（只增不减）"
        )
    with col3:
        st.metric(
            "📜 已平仓实现盈亏 (Realized)",
            f"${total_realized_pnl:+,.2f}",
            f"已结 {len(closed_trades)} 笔 (胜负已锁定，不随盘口跳动)",
            help="已经完全闭环平仓的历史交易最终实现净利润，不随当前盘口微观基差跳动"
        )
    with col4:
        st.metric(
            "💹 活跃持仓盘口浮动 (Floating)",
            f"${total_active_net:+,.2f}",
            f"持仓 {len(active_pos)} 笔 (受两所瞬时基差跳动影响)",
            help="当前活跃持仓若按此时此刻市价平仓的假想净值，受两所盘口瞬时基差漂移与吃单滑点影响"
        )


@st.cache_data(ttl=60, show_spinner=False)
def get_cached_market_opportunities():
    """Cache external cross-exchange funding rates & liquidity to prevent UI network lag and flickering."""
    try:
        from frontend.components.funding_scanner import scan_cross_exchange_opportunities
        return scan_cross_exchange_opportunities()
    except Exception:
        return []


def send_strategy_command(bot_name: str, command: dict) -> bool:
    """Send real-time strategy commands (add_token, remove_token, trigger_rotation) to bot data directory."""
    try:
        candidate_dirs = [
            f"/home/neo/projects/hummingbot/hummingbot-api/bots/instances/{bot_name}/data",
            os.path.abspath(os.path.join(os.path.dirname(__file__), f"../../../hummingbot-api/bots/instances/{bot_name}/data")),
            "/home/hummingbot/data"
        ]
        for d in candidate_dirs:
            if os.path.isdir(d):
                cmd_path = os.path.join(d, "strategy_commands.json")
                with open(cmd_path, "w") as f:
                    json.dump(command, f, indent=2)
                return True
        return False
    except Exception:
        return False


def render_funding_arbitrage_tables(arb_data, bot_name):
    """Render visual tables and rich Plotly charts for Funding Rate Arbitrage positions, closed trades, and liquidity depth."""
    active_pos = arb_data.get("active_positions", [])
    closed_trades = arb_data.get("closed_trades", [])
    market_comp = arb_data.get("market_comparison", [])
    is_dry_run = arb_data.get("is_dry_run", True)

    mode_badge = "🟢 纸面预演 (Dry-Run)" if is_dry_run else "🔴 实盘交易 (Live)"

    col_hdr_title, col_hdr_clock, col_hdr_tz = st.columns([2.8, 1.1, 1.1])
    with col_hdr_title:
        st.markdown(
            f"#### 💰 资金费率套利持仓与量化透视 `{mode_badge}` &nbsp; "
            f'<span style="font-size: 0.75rem; color: #94a3b8; font-weight: normal;">| 姐妹策略：<a href="/basis_stat_arb" target="_self" style="color: #60a5fa; text-decoration: none;">⚡ 基差统计双击流</a> (独立运行)</span>',
            unsafe_allow_html=True
        )
    with col_hdr_clock:
        clock_health = get_exchange_clock_health()
        st.caption(f"{clock_health.get('badge', '⏱️ 时钟正常')}")
    with col_hdr_tz:
        active_tz, tz_label = render_timezone_selector(
            key=f"tz_sel_{bot_name}",
            label="🌐 显示时区"
        )

    if clock_health.get("status") == "danger":
        st.warning(
            f"⚠️ **【量化高精度时钟风险拦截警报】**：本地系统时钟与 Binance 合约服务器偏差达到 **`{clock_health.get('offset_ms', 0):+.0f}ms`**（逼近 1000ms 拒单红线）！\n\n"
            f"- **风险成因**：WSL2/系统休眠唤醒后虚拟化时钟未对齐，高频下单极易遭遇交易所 `code: -1021 (Timestamp outside recvWindow)` 拒单，导致**一腿开平仓成功、一腿被拒单（裸奔风险）**！\n"
            f"- **一键修复指南**：在宿主机终端执行 `sudo systemctl restart systemd-timesyncd` 立即对齐时钟（切勿执行 `hwclock -s` 覆写硬件粗时间），或运行 `python3 scripts/check-clock-sync.py` 诊断。"
        )


    # --- Section A: Summary Key Performance Metrics ---
    total_capital = sum(float(p.get("position_size", 0)) for p in active_pos)
    total_funding_income = sum(float(p.get("funding_income", 0)) for p in active_pos) + sum(float(t.get("funding_pnl", 0)) for t in closed_trades)
    total_unrealized_pnl = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
    total_realized_pnl = sum(float(t.get("total_net_pnl", 0)) for t in closed_trades)
    net_combined_pnl = sum(float(p.get("net_pnl", 0)) for p in active_pos) + total_realized_pnl

    # Projected 24h funding cashflow
    rate_diff_map = {m.get("token"): float(m.get("daily_rate_diff_pct", 0.0)) for m in market_comp}
    projected_daily_usd = sum(float(p.get("projected_daily_funding_quote") or 0) for p in active_pos)
    projected_apr = (projected_daily_usd * 365.0 / total_capital * 100.0) if total_capital > 0 else 0.0

    global_leverage = int(arb_data.get("leverage", 3))
    real_margin_total = sum(
        float(p.get("initial_margin_quote", float(p.get("position_size", 0)) * (
            1.0 / float(p.get("long_leverage", p.get("leverage", global_leverage))) +
            1.0 / float(p.get("short_leverage", p.get("leverage", global_leverage)))
        ))) for p in active_pos
    )
    gross_capital = total_capital * 2.0
    effective_leverage = (gross_capital / real_margin_total) if real_margin_total > 0 else float(global_leverage)
    single_margin = real_margin_total / 2.0
    roe_apr = (projected_daily_usd * 365.0 / real_margin_total * 100.0) if real_margin_total > 0 else 0.0

    # --- Dynamic Adaptive Leverage Detection ---
    active_levs = []
    for p in active_pos:
        l_lev = int(p.get("long_leverage") or p.get("leverage") or global_leverage)
        s_lev = int(p.get("short_leverage") or p.get("leverage") or global_leverage)
        active_levs.extend([l_lev, s_lev])
    unique_levs = sorted(list(set(active_levs))) if active_levs else []

    if not active_pos:
        m1_delta = f"当前空仓待命中 (策略预设 {global_leverage}x 上限)"
        m2_delta = f"单边对冲 $0.00 (无合约风险敞口)"
        m1_help = f"当前无活跃套利持仓，未占用交易所保证金。策略预设最大目标杠杆为 {global_leverage}x。"
        m2_help = "当前无活跃套利持仓，双边合约总货值为 $0.00。"
    elif len(unique_levs) == 1 and unique_levs[0] == 1:
        # All positions 1x: Zero leverage / Spot equivalent safe hedging
        m1_delta = f"🛡️ 1x 全额免杠杆 (多空各押 ${single_margin:,.2f})"
        m2_delta = f"🛡️ 1:1 现货级对冲无放大 (单边 ${total_capital:,.2f})"
        m1_help = f"自适应安全模式：当前全部活跃持仓均采用 1x 全额免杠杆对冲（仍有单腿强平风险），多空两所各出资 ${single_margin:,.2f} 全额质押。"
        m2_help = f"全部持仓采用 1x 免杠杆安全对冲，合约名义总货值严格等于实缴总本金（${real_margin_total:,.2f}），没有任何杠杆乘数放大。"
    elif len(unique_levs) == 1 and unique_levs[0] > 1:
        # All positions share the exact same leverage (e.g. 2x, 3x)
        lev = unique_levs[0]
        m1_delta = f"两所各押 ${single_margin:,.2f} ({lev}x 杠杆实缴)"
        m2_delta = f"总本金×{lev}x 放大 (单边对冲 ${total_capital:,.2f})"
        m1_help = f"当前所有活跃持仓均采用统一的 {lev}x 杠杆质押，多空两所各自出资一半（${single_margin:,.2f}）。"
        m2_help = f"双边合计控制的合约总名义资产 = 多头货值 (${total_capital:,.2f}) + 空头货值 (${total_capital:,.2f}) = 实缴总本金 (${real_margin_total:,.2f}) × {lev}x 杠杆；单边对冲货值占总本金的 {total_capital/real_margin_total if real_margin_total > 0 else 1.5:.1f}x。"
    else:
        # Mixed leverages (e.g. some 1x, some 2x, some 3x)
        lev_tags = "/".join(f"{l}x" for l in unique_levs)
        has_1x = 1 in unique_levs
        if has_1x:
            m1_delta = f"动态加权 {effective_leverage:.1f}x (含 1x免杠杆仓)"
            m2_delta = f"总本金×{effective_leverage:.1f}x 综合放大 (单边 ${total_capital:,.2f})"
            m1_help = f"自适应动态杠杆生效中：当前持仓组合包含 {lev_tags} 多种杠杆配置。小额资金标的自动降为 1x 免杠杆以降低保证金占用风险，大额资金采用目标杠杆增效，全组合综合加权有效杠杆为 {effective_leverage:.1f}x（多空两所各押 ${single_margin:,.2f}）。"
            m2_help = f"双边合约总名义资产 = 实缴总本金 (${real_margin_total:,.2f}) × 综合加权 {effective_leverage:.1f}x 杠杆放大（包含 1x 免杠杆安全仓与加速仓）；单边对冲敞口为 ${total_capital:,.2f}。"
        else:
            m1_delta = f"动态加权 {effective_leverage:.1f}x (混合 {lev_tags})"
            m2_delta = f"总本金×{effective_leverage:.1f}x 综合放大 (单边 ${total_capital:,.2f})"
            m1_help = f"当前持仓组合包含 {lev_tags} 多种杠杆，全组合综合加权有效杠杆为 {effective_leverage:.1f}x（多空两所各押 ${single_margin:,.2f}）。"
            m2_help = f"双边合约总名义资产 = 实缴总本金 (${real_margin_total:,.2f}) × 综合加权 {effective_leverage:.1f}x 杠杆放大；单边对冲敞口为 ${total_capital:,.2f}。"

    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    with col_m1:
        st.metric(
            "💼 持仓保证金 (模型估算)",
            f"${real_margin_total:,.2f}",
            m1_delta,
            help=m1_help
        )
    with col_m2:
        st.metric(
            "🎯 双边合约总货值 (Gross)",
            f"${gross_capital:,.2f}",
            m2_delta,
            help=m2_help
        )
    with col_m3:
        st.metric("💰 累计已收资金费", f"${total_funding_income:+.4f}", "每小时 / 8小时结算到账")
    with col_m4:
        st.metric("⚡ 24h 预期现金流", f"+${projected_daily_usd:,.2f} / 天", f"本金年化 ROE {roe_apr:+.2f}%")
    with col_m5:
        st.metric("🛡️ 综合净盈亏 (Net PnL)", f"${net_combined_pnl:+.4f}", "价差损益 + 资金费")

    # --- Section A.1: Institutional Risk Shields - Toxic Blacklist, Quarantine, Probation & Post-TP Lockout ---
    permanent_blacklist = arb_data.get("permanent_blacklist", [])
    quarantined_tokens = arb_data.get("quarantined_tokens", {})
    post_tp_lockout = arb_data.get("post_tp_lockout", {})
    probationary_tokens = arb_data.get("probationary_tokens", {})
    graduation_status = arb_data.get("graduation_status", {})

    if permanent_blacklist or quarantined_tokens or post_tp_lockout or probationary_tokens:
        total_intercepted = len(set(permanent_blacklist) | set(quarantined_tokens.keys()) | set(post_tp_lockout.keys()) | set(probationary_tokens.keys()))
        with st.expander(f"🛡️ **机构级量化风控拦截、熔断与标的资质晋升**：`{total_intercepted}` 个标的处于风控限制或考察状态", expanded=True):
            st.caption(
                "机构级风控与自进化资产池：拦截历史累计严重亏损毒币（累计净亏 ≥ $30 终生拉黑）、"
                "短时严重失血/连续止损标的（24h 物理隔离）、刚止盈标的（4h 防高位追高锁定），"
                "并配合「标的资质晋升算法」每 7 天定期对黑名单进行流动性体检（要求 24h成交量 ≥ $30M 且两所偏离 ≤ 0.5%），符合条件以 $200 探针仓位特赦试水！"
            )

            # 1. 永久拉黑毒币熔断区
            if permanent_blacklist:
                hd_col1, hd_col2 = st.columns([3, 1.2])
                with hd_col1:
                    st.markdown(f"##### 🚫 永久拉黑毒币熔断名单 (`{len(permanent_blacklist)}` 个代币已终生熔断)")
                    st.caption("以下标的历史累计净亏损突破 $30 红线，算法已实行终生一票否决；若标的后续成长成熟，可通过资质体检获得特赦考察资格：")
                with hd_col2:
                    if st.button("🎓 立即执行资质体检", key=f"grad_audit_{bot_name}", use_container_width=True, help="立即向后台轮换引擎发送指令，全面体检黑名单中毒币的24h成交量（要求≥$30M）与标记价格偏离度（要求≤0.5%）。符合条件的币将获得特赦，以 $200 探针仓位进入考察期！"):
                        if send_strategy_command(bot_name, {"action": "trigger_graduation_audit"}):
                            st.success("已发送资质体检指令，正在后台扫描黑名单标的流动性与价格偏离度！")
                            st.rerun()

                b_cols = st.columns(min(len(permanent_blacklist), 4))
                for i, b_tok in enumerate(permanent_blacklist):
                    with b_cols[i % len(b_cols)]:
                        st.markdown(
                            f"<div style='background:#450a0a; border:1px solid #dc2626; border-radius:6px; padding:8px; margin-bottom:6px; text-align:center;'>"
                            f"<span style='font-size:16px; font-weight:bold; color:#fca5a5;'>🚫 {b_tok}</span><br>"
                            f"<span style='font-size:11px; color:#f87171;'>累计亏损超标终生熔断</span>"
                            f"</div>",
                            unsafe_allow_html=True
                        )
                        if st.button(f"🕊️ 手动赦免 {b_tok}", key=f"unbl_{b_tok}_{bot_name}", use_container_width=True):
                            if send_strategy_command(bot_name, {"action": "unblacklist_token", "token": b_tok}):
                                st.success(f"已发送解封指令，{b_tok} 已移出永久黑名单！")
                                st.rerun()

            # 1b. 特赦考察期名单
            if probationary_tokens:
                st.markdown(f"##### 🟡 特赦考察期试水名单 (`{len(probationary_tokens)}` 个标的流动性成熟，试水中)")
                st.caption(
                    "以下标的已通过流动性体检（24h成交量 ≥ $30M 且两所价差偏离 ≤ 0.5%），当前处于特赦考察期。"
                    "算法采用安全探针仓位（$200 / 1x 免杠杆）试水；若首战实现盈利或触发止盈，正式晋升回归常规资产池；若再次净亏损，则退回永久黑名单。"
                )
                pr_rows = []
                for pr_tok, pr_info in probationary_tokens.items():
                    bn_v = pr_info.get("bn_vol", 0.0) / 1e6
                    hl_v = pr_info.get("hl_vol", 0.0) / 1e6
                    tot_v = bn_v + hl_v
                    div_pct = pr_info.get("divergence", 0.0)
                    probe_done = pr_info.get("probe_executed", False)
                    status_str = "🔄 正在持仓试水" if probe_done else "⏳ 等待盘口信号入场"
                    pr_rows.append({
                        "代币": pr_tok,
                        "考察状态": status_str,
                        "探针仓位": f"${pr_info.get('probe_size', 200.0):,.0f} (1x免杠杆)",
                        "24h 合计成交量": f"${tot_v:.1f}M (币安 ${bn_v:.1f}M / HL ${hl_v:.1f}M)",
                        "两所价格偏离度": f"{div_pct:.3%}",
                        "特赦原因": pr_info.get("reason", "流动性体检达标"),
                    })
                if pr_rows:
                    st.dataframe(pd.DataFrame(pr_rows), use_container_width=True, hide_index=True)

            # 2. 动态隔离区
            if quarantined_tokens:
                st.markdown(f"##### 🛑 动态隔离观察区 (`{len(quarantined_tokens)}` 个标的冷却中)")
                q_rows = []
                for q_tok, q_info in quarantined_tokens.items():
                    if q_tok in permanent_blacklist:
                        continue  # already shown in permanent blacklist
                    rem_h = q_info.get("remaining_hours", 0.0)
                    rem_s = q_info.get("remaining_seconds", 0)
                    rem_str = f"{int(rem_h)}小时 {int((rem_s % 3600) // 60)}分钟" if rem_h >= 1.0 else f"{int(rem_s // 60)}分钟"
                    q_rows.append({
                        "代币": q_tok,
                        "熔断原因": q_info.get("reason", "重度亏损触发熔断"),
                        "剩余冷却": rem_str,
                        "触发亏损额": f"${q_info.get('loss_amount', 0.0):,.2f}",
                        "连续止损": q_info.get("consecutive_losses", 1),
                    })
                if q_rows:
                    st.dataframe(pd.DataFrame(q_rows), use_container_width=True, hide_index=True)

            # 3. 止盈防高位追高锁定
            if post_tp_lockout:
                st.markdown(f"##### 🔒 止盈防高位回踩锁定 (`{len(post_tp_lockout)}` 个标的高位防回踩中)")
                tp_rows = []
                for tp_tok, tp_info in post_tp_lockout.items():
                    rem_m = tp_info.get("remaining_minutes", 0.0)
                    tp_rows.append({
                        "代币": tp_tok,
                        "风控机制": "止盈后 4.0 小时防追高冷冻期 (Anti-Churning)",
                        "剩余冷冻时间": f"{int(rem_m // 60)}h {int(rem_m % 60)}m" if rem_m >= 60 else f"{int(rem_m)}分钟",
                        "解锁后动作": "恢复正常利差扫描评估",
                    })
                st.dataframe(pd.DataFrame(tp_rows), use_container_width=True, hide_index=True)

    # --- Section B: 5 Multi-Dimensional High-Visibility Tabs ---
    st.markdown("""
    <style>
    /* === Institutional High-Contrast Segmented Tabs Navigation === */
    div[data-testid="stTabs"] {
        margin-top: 10px;
    }

    div[data-testid="stTabs"] [data-baseweb="tab-list"] {
        background: linear-gradient(180deg, rgba(30, 41, 59, 0.95) 0%, rgba(15, 23, 42, 0.98) 100%) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        padding: 8px 12px !important;
        border-radius: 16px !important;
        border: 1.5px solid rgba(56, 189, 248, 0.4) !important;
        box-shadow: 0 10px 30px -5px rgba(0, 0, 0, 0.7), inset 0 1px 0 rgba(255, 255, 255, 0.2) !important;
        gap: 10px !important;
        display: flex !important;
        flex-wrap: wrap !important;
    }

    div[data-testid="stTabs"] button[data-baseweb="tab"] {
        height: 50px !important;
        padding: 0 22px !important;
        border-radius: 12px !important;
        background: rgba(30, 41, 59, 0.8) !important;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.08) !important;
        transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1) !important;
        cursor: pointer !important;
    }

    div[data-testid="stTabs"] button[data-baseweb="tab"] div[data-testid="stMarkdownContainer"] p {
        font-size: 15px !important;
        font-weight: 600 !important;
        color: #cbd5e1 !important;
        letter-spacing: 0.02em !important;
        margin: 0 !important;
        transition: color 0.2s ease !important;
    }

    div[data-testid="stTabs"] button[data-baseweb="tab"]:hover {
        background: rgba(51, 65, 85, 0.95) !important;
        border-color: rgba(56, 189, 248, 0.6) !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4) !important;
    }

    div[data-testid="stTabs"] button[data-baseweb="tab"]:hover div[data-testid="stMarkdownContainer"] p {
        color: #ffffff !important;
    }

    div[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {
        background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%) !important;
        border: 2px solid #38bdf8 !important;
        box-shadow: 0 0 22px rgba(56, 189, 248, 0.5), inset 0 1px 1px rgba(255, 255, 255, 0.35) !important;
        transform: scale(1.02) !important;
    }

    div[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] div[data-testid="stMarkdownContainer"] p {
        color: #ffffff !important;
        font-weight: 700 !important;
        text-shadow: 0 1px 2px rgba(0, 0, 0, 0.4) !important;
    }

    /* Hide default thin red underline */
    div[data-testid="stTabs"] [data-baseweb="tab-highlight"],
    div[data-testid="stTabs"] [data-baseweb="tab-border"] {
        display: none !important;
        height: 0 !important;
    }
    </style>
    <div style="background: linear-gradient(90deg, rgba(2, 132, 199, 0.2) 0%, rgba(15, 23, 42, 0.0) 100%); border-left: 4px solid #38bdf8; padding: 12px 18px; border-radius: 0 12px 12px 0; margin-top: 24px; margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between;">
        <div>
            <span style="font-size: 18px; font-weight: 700; color: #f8fafc; letter-spacing: 0.02em;">🧭 策略功能与多维战绩导航中心</span>
            <span style="font-size: 13px; color: #94a3b8; margin-left: 14px;">（点击下方专属发光大按钮，即可秒级切换 5 大核心模块）</span>
        </div>
        <span style="font-size: 12px; font-weight: 600; color: #38bdf8; background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.4); padding: 4px 12px; border-radius: 20px;">
            💡 高亮选中有发光立体边框
        </span>
    </div>
    """, unsafe_allow_html=True)

    tab_pos, tab_pnl, tab_closed, tab_rates, tab_charts = st.tabs([
        f"📈 活跃多空持仓  [{len(active_pos)} 仓位]",
        "📊 机构级盈亏全景  [每日/每周/月历]",
        f"📜 历史平仓成交  [{len(closed_trades)} 笔明细]",
        f"🌐 实时费率利差  [{len(market_comp)} 币雷达]",
        "🔬 盘口深度与流动性  [容量分析]"
    ])

    with tab_pos:
        if active_pos:
            top_c1, top_c2 = st.columns([3, 2])
            with top_c1:
                st.caption("ℹ️ 当前正在双边对冲并按期收取资金费的套利头寸（随交易所盘口每 10 秒自动刷新）")
            with top_c2:
                pos_view = st.radio(
                    "持仓展示模式",
                    ["🗂️ 结构化卡片透视 (免横向滚动，推荐)", "📋 精简总览表格 (紧凑免滚)"],
                    horizontal=True,
                    label_visibility="collapsed",
                    key=f"pos_view_mode_{bot_name}"
                )

            pos_card_items = []
            pos_table_rows = []

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
                open_ts = p.get("open_timestamp")
                if not open_ts:
                    open_ts = now_ts - p.get("duration_seconds", 0)
                open_time_str = format_timestamp(open_ts, active_tz)

                next_f1_ts = p.get("next_funding_1", 0)
                next_f2_ts = p.get("next_funding_2", 0)
                next_f1 = max(0, int(next_f1_ts - now_ts)) // 60 if next_f1_ts > 0 else 0
                next_f2 = max(0, int(next_f2_ts - now_ts)) // 60 if next_f2_ts > 0 else 0
                f1_clock = format_timestamp(next_f1_ts, active_tz, "%H:%M") if next_f1_ts > 0 else "N/A"
                f2_clock = format_timestamp(next_f2_ts, active_tz, "%H:%M") if next_f2_ts > 0 else "N/A"
                c1_abbr = long_ex.split('_')[0].capitalize()
                c2_abbr = short_ex.split('_')[0].capitalize()
                countdown_str = f"{c1_abbr}: {next_f1}m ({f1_clock}) | {c2_abbr}: {next_f2}m ({f2_clock})"

                # Leverage handling
                lev = int(p.get("leverage") or arb_data.get("leverage") or 3)
                long_lev = int(p.get("long_leverage") or lev)
                short_lev = int(p.get("short_leverage") or lev)

                # Distance & Liquidation
                liq_p1 = p.get("liq_price_1", 0)
                liq_p2 = p.get("liq_price_2", 0)
                liq_long = liq_p1 if side == "BUY" else liq_p2
                liq_short = liq_p2 if side == "BUY" else liq_p1
                dist_long = p.get("liq_distance_1_pct", 0.30) if side == "BUY" else p.get("liq_distance_2_pct", 0.30)
                dist_short = p.get("liq_distance_2_pct", 0.30) if side == "BUY" else p.get("liq_distance_1_pct", 0.30)

                min_dist = p.get("min_liq_distance_pct", min(dist_long, dist_short))
                req_buf = float(p.get("liquidation_buffer_req_pct", 0.08))
                tier_num = int(p.get("tier", 2))
                if min_dist >= req_buf * 2:
                    health_badge = f"🟢 充沛 (+{min_dist:.1%} / 门槛 {req_buf:.0%})"
                elif min_dist >= req_buf * 1.2:
                    health_badge = f"🟡 适中 (+{min_dist:.1%} / 门槛 {req_buf:.0%})"
                else:
                    health_badge = f"🔴 临界防御 (+{min_dist:.1%} / 门槛 {req_buf:.0%})"

                # Partial Take Profit Badge (Option B)
                partial_done = bool(p.get("partial_tp_executed", False))
                locked_pnl = float(p.get("locked_tp_pnl", 0.0))
                pos_size = float(p.get('position_size', 0))
                net_pnl = float(p.get('net_pnl', 0))
                take_profit = float(arb_data.get('profitability_to_take_profit', 0.02))
                partial_ratio = float(arb_data.get('partial_tp_ratio', 0.5))
                partial_threshold = float(arb_data.get('partial_tp_threshold_pct', 0.01))
                if not arb_data.get('scaling_tp_enabled', True):
                    tp_badge = f"净收益目标 {take_profit:.2%}（含模型费用）"
                elif partial_done:
                    tp_badge = f"⚡ 已减仓 {partial_ratio:.0%}，该部分净损益 ${locked_pnl:+.2f} | 剩余目标 {take_profit:.2%}"
                else:
                    target_stage1 = pos_size * partial_threshold
                    gap_stage1 = max(0.0, target_stage1 - net_pnl)
                    tp_badge = f"⏳ 阶段1: {partial_threshold:.2%} (差 ${gap_stage1:.2f}，计划减仓 {partial_ratio:.0%})"
                single_long_margin = pos_size / long_lev if long_lev > 0 else pos_size / 3.0
                single_short_margin = pos_size / short_lev if short_lev > 0 else pos_size / 3.0
                long_i = 1 if p.get('side') == 'BUY' else 2
                short_i = 3 - long_i
                single_long_margin = float(p.get(f'initial_margin_{long_i}_quote', single_long_margin))
                single_short_margin = float(p.get(f'initial_margin_{short_i}_quote', single_short_margin))
                real_margin = single_long_margin + single_short_margin
                token_sym = p.get('token', '')
                coin_amt_str = f"{pos_size / long_p:,.0f} {token_sym}" if long_p > 0 else "N/A"
                long_market_url = get_official_market_url(long_ex, token_sym)
                short_market_url = get_official_market_url(short_ex, token_sym)
                research_urls = get_token_research_urls(token_sym)

                unrealized_pnl = float(p.get('unrealized_trading_pnl', 0))
                funding_inc = float(p.get('funding_income', 0))
                settle_cnt = int(p.get('settlement_count', 0))
                net_pnl = float(p.get('net_pnl', 0))
                net_pct = float(p.get('net_pnl_pct', 0))

                long_coin = pos_size / long_p if long_p > 0 else 0
                short_coin = pos_size / short_p if short_p > 0 else 0
                long_leg_pnl = (curr_long_p - long_p) * long_coin
                short_leg_pnl = (short_p - curr_short_p) * short_coin
                open_basis = (short_p - long_p) / long_p * 100.0 if long_p > 0 else 0.0
                curr_basis = (curr_long_p - curr_short_p) / curr_short_p * 100.0 if curr_short_p > 0 else 0.0

                pos_card_items.append({
                    "token": token_sym,
                    "side": side,
                    "long_ex": long_ex,
                    "short_ex": short_ex,
                    "c1_abbr": c1_abbr,
                    "c2_abbr": c2_abbr,
                    "long_market_url": long_market_url,
                    "short_market_url": short_market_url,
                    "tradingview_url": research_urls["tradingview"],
                    "coinglass_url": research_urls["coinglass"],
                    "coingecko_url": research_urls["coingecko"],
                    "coinmarketcap_url": research_urls["coinmarketcap"],
                    "long_lev": long_lev,
                    "short_lev": short_lev,
                    "long_p": long_p,
                    "short_p": short_p,
                    "curr_long_p": curr_long_p,
                    "curr_short_p": curr_short_p,
                    "liq_long": liq_long,
                    "liq_short": liq_short,
                    "dist_long": dist_long,
                    "dist_short": dist_short,
                    "health_badge": health_badge,
                    "pos_size": pos_size,
                    "single_long_margin": single_long_margin,
                    "single_short_margin": single_short_margin,
                    "real_margin": real_margin,
                    "coin_amt_str": coin_amt_str,
                    "unrealized_pnl": unrealized_pnl,
                    "funding_inc": funding_inc,
                    "settle_cnt": settle_cnt,
                    "net_pnl": net_pnl,
                    "net_pct": net_pct,
                    "dur_str": dur_str,
                    "open_time_str": open_time_str,
                    "countdown_str": countdown_str,
                    "next_f1": next_f1,
                    "next_f2": next_f2,
                    "long_leg_pnl": long_leg_pnl,
                    "short_leg_pnl": short_leg_pnl,
                    "open_basis": open_basis,
                    "curr_basis": curr_basis,
                    "payback_hours": float(p.get("payback_hours", 0.0)),
                    "tier": int(p.get("tier", 2)),
                    "is_probationary_probe": bool(p.get("is_probationary_probe", False)),
                })

                tier_num = int(p.get("tier", 2))
                if tier_num == 1:
                    tier_badge_html = "<span style='font-size:11px; background:#1e1b4b; border:1px solid #4338ca; padding:2px 6px; border-radius:4px; color:#a5b4fc; font-weight:600;'>💎 T1 蓝筹</span>"
                elif tier_num == 2:
                    tier_badge_html = "<span style='font-size:11px; background:#14532d; border:1px solid #16a34a; padding:2px 6px; border-radius:4px; color:#86efac; font-weight:600;'>⭐ T2 主流</span>"
                else:
                    tier_badge_html = "<span style='font-size:11px; background:#451a03; border:1px solid #d97706; padding:2px 6px; border-radius:4px; color:#fde68a; font-weight:600;'>⚠️ T3 微盘</span>"

                # Dynamic Breakeven calculation: how long to cover entry friction & net deficit
                token_rate_daily = rate_diff_map.get(token_sym, 0.0) / 100.0
                hourly_funding_usd = pos_size * (token_rate_daily / 24.0)
                remaining_deficit = -net_pnl  # net deficit to recover
                if remaining_deficit <= 0:
                    dynamic_payback_str = f"✅ 已覆盖买入损耗 (当前纯盈利 ${net_pnl:+.4f})"
                    table_payback_str = "✅ 已回本"
                elif hourly_funding_usd > 0:
                    est_hours_left = remaining_deficit / hourly_funding_usd
                    dynamic_payback_str = f"⏳ 预计约 {est_hours_left:.1f} 小时覆盖损耗 (按每小时预收息 +${hourly_funding_usd:.4f} 计算)"
                    table_payback_str = f"还需 ~{est_hours_left:.1f}h"
                else:
                    dynamic_payback_str = "⚠️ 当前息差为负，暂无法通过收息覆盖损耗"
                    table_payback_str = "息差为负"

                pos_card_items[-1]["dynamic_payback_str"] = dynamic_payback_str
                pos_card_items[-1]["hourly_funding_usd"] = hourly_funding_usd
                pos_card_items[-1]["tier_badge_html"] = tier_badge_html
                pos_card_items[-1]["tp_badge"] = tp_badge

                breakeven_active = bool(p.get("breakeven_active", False))
                if breakeven_active:
                    be_badge = "🟢 移动保本已激活 (锁线: +0.05%)"
                else:
                    gap_to_be = max(0.0, 0.006 - net_pct)
                    be_badge = f"⏳ 移动保本 (达+0.60%锁定，差{gap_to_be:.2%})"

                pos_card_items[-1]["be_badge"] = be_badge
                pos_card_items[-1]["breakeven_active"] = breakeven_active

                # Compact Table Row: compact columns without excessive text bloat
                is_probe = bool(p.get("is_probationary_probe", False))
                if is_probe:
                    lev_tag = "🔬 考察探针1x"
                elif long_lev == 1 and short_lev == 1:
                    lev_tag = "🛡️ 1x免杠杆"
                elif long_lev == short_lev:
                    lev_tag = f"⚡ {long_lev}x"
                else:
                    lev_tag = f"⚡ 多{long_lev}x/空{short_lev}x"
                pos_table_rows.append({
                    "标的 (分级/杠杆)": f"{token_sym} (T{tier_num} / {lev_tag})",
                    "对冲路径": f"🟢{c1_abbr}(多) ⇄ 🔴{c2_abbr}(空)",
                    "多头官方行情": long_market_url,
                    "空头官方行情": short_market_url,
                    "代币官网与资料": research_urls["coinglass"],
                    f"开仓时间 ({tz_label})": open_time_str,
                    "实缴本金": f"${real_margin:,.2f}",
                    "单边货值": f"${pos_size:,.2f}",
                    "双边总货值": f"${pos_size * 2:,.2f}",
                    "持币数量": coin_amt_str,
                    "强平安全垫": health_badge,
                    "移动保本": be_badge,
                    "阶梯止盈": tp_badge,
                    "预期回本": table_payback_str,
                    "盘口浮亏": f"${unrealized_pnl:+.4f}",
                    "累计资金费": f"${funding_inc:+.4f} ({settle_cnt}次)",
                    "综合净收益": f"${net_pnl:+.4f} ({net_pct:+.2%})",
                    "持仓时长": dur_str,
                    "下期结算": f"{min(next_f1, next_f2)}m 后",
                })

            if "卡片" in pos_view:
                for item in pos_card_items:
                    with st.container(border=True):
                        # Top bar of the card
                        c_hd1, c_hd2, c_hd3 = st.columns([1.8, 2.0, 1.2])
                        with c_hd1:
                            if item.get('is_probationary_probe'):
                                lev_badge_html = (
                                    f"<span style='font-size:12px; background:#78350f; border:1px solid #f59e0b; padding:3px 8px; border-radius:4px; color:#fde68a; font-weight:600;'>"
                                    f"🔬 特赦考察试水单 (探针 $200 / 1x免杠杆)</span>"
                                )
                            elif item['long_lev'] == 1 and item['short_lev'] == 1:
                                lev_badge_html = (
                                    f"<span style='font-size:12px; background:#064e3b; border:1px solid #059669; padding:3px 8px; border-radius:4px; color:#34d399; font-weight:600;'>"
                                    f"🛡️ 1x 全额免杠杆安全对冲 (仍有单腿强平风险)</span>"
                                )
                            else:
                                lev_badge_html = (
                                    f"<span style='font-size:12px; background:#1e293b; border:1px solid #334155; padding:3px 8px; border-radius:4px; color:#38bdf8; font-weight:600;'>"
                                    f"⚡ 杠杆: 多{item['long_lev']}x | 空{item['short_lev']}x (大额资金增效)</span>"
                                )
                            st.markdown(
                                f"#### 🪙 `{item['token']}` &nbsp; {item.get('tier_badge_html', '')} &nbsp; {lev_badge_html}",
                                unsafe_allow_html=True
                            )

                            st.caption(f"🧭 **对冲配对**: `🟢 {item['long_ex']} (多)` ⇄ `🔴 {item['short_ex']} (空)`")

                            # Official Exchange & Research Web Links
                            st.markdown(
                                f"<div style='margin-top:6px; margin-bottom:4px; display:flex; flex-wrap:wrap; gap:6px; font-size:12px;'>"
                                f"<a href='{item['long_market_url']}' target='_blank' style='text-decoration:none; background:#064e3b; color:#34d399; padding:3px 8px; border-radius:4px; border:1px solid #059669; font-weight:600;'>"
                                f"🟢 {item['c1_abbr']} 官方K线/盘口 ↗</a>"
                                f"<a href='{item['short_market_url']}' target='_blank' style='text-decoration:none; background:#7f1d1d; color:#f87171; padding:3px 8px; border-radius:4px; border:1px solid #dc2626; font-weight:600;'>"
                                f"🔴 {item['c2_abbr']} 官方K线/盘口 ↗</a>"
                                f"<a href='{item['tradingview_url']}' target='_blank' style='text-decoration:none; background:#1e293b; color:#93c5fd; padding:3px 8px; border-radius:4px; border:1px solid #3b82f6; font-weight:500;'>"
                                f"📊 TradingView K线 ↗</a>"
                                f"<a href='{item['coinglass_url']}' target='_blank' style='text-decoration:none; background:#1e293b; color:#fbbf24; padding:3px 8px; border-radius:4px; border:1px solid #f59e0b; font-weight:500;'>"
                                f"🌐 代币官网/背景 ↗</a>"
                                f"</div>",
                                unsafe_allow_html=True
                            )
                        with c_hd2:
                            st.markdown(f"⏱️ **开仓时刻**: `{item['open_time_str']}` *({tz_label})* &nbsp;&nbsp;|&nbsp;&nbsp; ⏳ **时长**: `{item['dur_str']}`")
                            st.markdown(f"🕒 **结算点**: `{item['countdown_str']}` &nbsp;&nbsp;|&nbsp;&nbsp; 🛡️ **强平盾**: `{item['health_badge']}`")
                            st.markdown(f"🎯 **止盈状态**: `{item.get('tp_badge', '阶梯止盈监控中')}` &nbsp;&nbsp;|&nbsp;&nbsp; `{item.get('be_badge', '保本监控')}`")
                        with c_hd3:
                            pnl_color = "#00e676" if item['net_pnl'] >= 0 else "#ff5252"
                            st.markdown(
                                f"<div style='text-align:right; padding-top:2px;'>"
                                f"<span style='font-size:12px; color:#94a3b8;'>综合净收益 (Net PnL)</span><br>"
                                f"<span style='font-size:22px; font-weight:bold; color:{pnl_color};'>${item['net_pnl']:+.4f}</span><br>"
                                f"<span style='font-size:13px; color:{pnl_color}; font-weight:500;'>{item['net_pct']:+.2%}</span>"
                                f"</div>",
                                unsafe_allow_html=True
                            )

                        st.divider()

                        # 3-column Bento details
                        col_a, col_b, col_c = st.columns([1.1, 1.4, 1.1])
                        with col_a:
                            st.markdown("##### 💼 资金规模与开仓杠杆")
                            if item['long_lev'] == 1 and item['short_lev'] == 1:
                                card_gross_sub = f"*(1:1 现货级免杠杆，实缴本金 `${item['real_margin']:,.2f}` 全额对冲)*"
                                card_lev_desc = f"🛡️ **1x 全额免杠杆** *(小额资金安全模式，仍有单腿强平风险)*"
                            elif item['long_lev'] == item['short_lev']:
                                card_gross_sub = f"*(实缴本金 `${item['real_margin']:,.2f}` × {item['long_lev']}x 杠杆放大)*"
                                card_lev_desc = f"⚡ **多空统一 `{item['long_lev']}x` 杠杆** *(大额资金增效模式)*"
                            else:
                                card_gross_sub = f"*(实缴本金 `${item['real_margin']:,.2f}` 差异杠杆放大)*"
                                card_lev_desc = f"⚡ **做多 `{item['long_lev']}x` | 做空 `{item['short_lev']}x`**"

                            st.markdown(f"""
                            - **实缴本金 (双边保证金)**: **`${item['real_margin']:,.2f}`**
                              - *(多头保证金 `${item['single_long_margin']:,.2f}` + 空头保证金 `${item['single_short_margin']:,.2f}`)*
                            - **双边合约总货值**: **`${item['pos_size'] * 2:,.2f}`** {card_gross_sub}
                            - **单边对冲货值**: **`${item['pos_size']:,.2f}`** *(多空两腿各持仓 `${item['pos_size']:,.2f}`)*
                            - **持币对冲数量**: `{item['coin_amt_str']}` *(双向等量持币)*
                            - **开仓杠杆状态**: {card_lev_desc}
                            """)

                        with col_b:
                            st.markdown("##### 🛡️ 双腿盘口与强平防线")
                            st.markdown(f"""
                            - 🟢 **多头腿 (`{item['long_ex']}`)** <a href='{item['long_market_url']}' target='_blank' style='color:#34d399; font-size:12px; font-weight:600; text-decoration:none;'>[打开官方K线/盘口 ↗]</a>:
                              - 买入: `${item['long_p']:.4f}` → 现价: `${item['curr_long_p']:.4f}` | 单腿损益: **`${item['long_leg_pnl']:+.2f}`**
                              - 预估强平: `${item['liq_long']:.4f}` *(安全垫: `+{item['dist_long']:.1%}`)*
                            - 🔴 **空头腿 (`{item['short_ex']}`)** <a href='{item['short_market_url']}' target='_blank' style='color:#f87171; font-size:12px; font-weight:600; text-decoration:none;'>[打开官方K线/盘口 ↗]</a>:
                              - 卖出: `${item['short_p']:.4f}` → 现价: `${item['curr_short_p']:.4f}` | 单腿损益: **`${item['short_leg_pnl']:+.2f}`**
                              - 预估强平: `${item['liq_short']:.4f}` *(安全垫: `+{item['dist_short']:.1%}`)*
                            - ⚖️ **双腿价差与滑点对冲损益**:
                              - 开仓两所价差: `{item['open_basis']:+.3f}%` → 当前平仓价差: `{item['curr_basis']:+.3f}%`
                              - 两腿对冲价差净损益: **`${item['unrealized_pnl']:+.4f}`** *(多头 ${item['long_leg_pnl']:+.2f} + 空头 ${item['short_leg_pnl']:+.2f})*
                            """, unsafe_allow_html=True)

                        with col_c:
                            st.markdown("##### 📈 实时损益与费率利息")
                            pb_val = item.get("payback_hours", 0.0)
                            pb_init_str = f"{pb_val:.1f}h" if pb_val > 0 else "0.0h (无倒贴)"
                            st.markdown(f"""
                            - 🎯 **阶梯止盈进度**: **{item.get('tp_badge', '阶梯止盈监控中')}**
                            - 🛡️ **移动保本防线**: **{item.get('be_badge', '保本监控')}**
                            - ⏳ **覆盖开仓损耗预估时间**: **{item.get('dynamic_payback_str', '计算中...')}**
                            - 🏷️ **开仓初始评测回本**: `{pb_init_str}` *(风控硬性门槛: ≤12h)*
                            - 📉 **盘口价差净损益 (当前平仓对冲摩擦)**: `${item['unrealized_pnl']:+.4f}`
                            - 💰 **累计已收净资金费**: **`${item['funding_inc']:+.4f}`** *(已结 `{item['settle_cnt']}` 次)*
                              - *(Hyperliquid 每1h结，币安每4h/8h结；周期交错期间暂显账期差)*
                            - 🛡️ **综合净利润**: **`${item['net_pnl']:+.4f}`**
                            - ⏰ **下期资金费结算**: 约 `{min(item['next_f1'], item['next_f2'])}m` 后到账
                            """)
            else:
                pos_col_cfg = {
                    "多头官方行情": st.column_config.LinkColumn("多头官方行情", display_text="🟢 官方行情 ↗"),
                    "空头官方行情": st.column_config.LinkColumn("空头官方行情", display_text="🔴 官方行情 ↗"),
                    "代币官网与资料": st.column_config.LinkColumn("代币官网与资料", display_text="🌐 官网资料 ↗"),
                }
                st.dataframe(pd.DataFrame(pos_table_rows), use_container_width=True, hide_index=True, column_config=pos_col_cfg)

            with st.expander("🔬 财务核算速递：什么是盘口浮盈亏？为什么刚开仓往往微负？", expanded=False):
                st.markdown("""
                * **1. 什么是盘口浮盈亏（Unrealized Trading PnL）？**  
                  指的是多头与空头两腿在**此时此刻如果立即市价双向平仓**，产生的现货价差损益（已预扣除平仓手续费）：
                  $$\\text{盘口浮盈亏} = (\\text{多头腿现价损益} + \\text{空头腿现价损益}) - \\text{预估平仓出场手续费}$$
                * **2. 为什么刚开仓往往是负的（几美金到几十美金）？**  
                  * **买卖盘口点差摩擦（Bid-Ask Spread）**：以市价平仓时，多头按买一价（Bid）平，空头按卖一价（Ask）平，天然存在微小的点差摩擦（约 0.05%~0.2%）。
                  * **两所微小基差偏离**：两家交易所对同一个币在几分钟内的波动不是 100% 绝对同步的（例如币安跌了 0.5%，Hyperliquid 只跌了 0.3%），导致微小的暂时价差偏离。
                * **3. 为什么最终一定能赚钱？（套利的数学本质）**  
                  $$\\text{综合净收益 (Net PnL)} = \\text{盘口浮盈亏} + \\textbf{累计已收资金费利息}$$
                  刚开仓时会有轻微的现货点差摩擦（如 -0.1% ~ -0.4%），但**定时到账的高额资金费利息（+Funding Income）会源源不断滚入累加，迅速覆盖并超越开仓摩擦**，最终将综合净收益推向正利润，直至触发 1.0% 的目标止盈！
                * **4. 财务核算必读：为什么实缴本金 $12,466 对应双边合约总货值 $37,400？**  
                  在双边跨所资金费率对冲套利中，存在 **“单边对冲货值”** 与 **“双边合约总货值”** 的区别：
                  * **单边对冲货值 ($18,700)**：指的是单腿的名义价值（比如在币安做多 $18,700 合约）。
                  * **双边合约总货值 ($37,400)**：因为这是绝对对冲套利，必须同时在 Hyperliquid 做空等额的 $18,700 合约！两腿合计控制的合约总资产价值为：
                    $$\\text{双边合约总货值} = \\text{多头货值 (\\$18,700)} + \\text{空头货值 (\\$18,700)} = \\mathbf{\\$37,400}$$
                  * **实缴保证金 (总本金 $12,466.67)**：在 3 倍杠杆下，每条腿需要占用 $18,700 \\div 3 = \\$6,233.33$ 保证金，两腿合计投入：
                    $$\\text{实缴总保证金} = \\$6,233.33 + \\$6,233.33 = \\mathbf{\\$12,466.67}$$
                  * **严密验算公式**：
                    $$\\mathbf{\\$12,466.67 (实缴总本金)} \\times \\mathbf{3 (杠杆倍数)} = \\mathbf{\\$37,400.00 (双边合约总货值)}$$
                    两者完全严丝合缝！此前界面将单边的 $18,700 简称为“总货值”，导致产生了 1.5 倍的错觉，现已全面显性化双边总货值与单边对冲货值！
                * **5. 为什么说“盘口浮盈亏”就是两所开平仓的价差成本？**  
                  许多人会有疑问：“开仓时有价差，平仓时又有价差，如果价差扩大，这个成本到底在哪里体现？”  
                  **答案是：系统每 10 秒刷新的「盘口浮盈亏」，计算的就是这个双边价差变动（基差漂移）！**  
                  $$\\text{盘口浮盈亏} = \\underbrace{(P_{\\text{卖,现}} - P_{\\text{买,开}}) \\times Q}_{\\text{多头腿损益}} + \\underbrace{(P_{\\text{卖,开}} - P_{\\text{买,现}}) \\times Q}_{\\text{空头腿损益}} = \\mathbf{\\Big[ \\Delta_{\\text{开仓两所价差}} - \\Delta_{\\text{平仓两所价差}} \\Big] \\times Q}$$  
                  * **价差扩大**：如果平仓时两所价差朝着不利方向扩大，多空两腿对冲后损益会暂时为负，**完全实时体现在“盘口浮亏”里，仍需检查估值状态与尚未补记的资金费**；
                  * **价差收窄**：随着两所套利资金抹平基差，价差回归到 0，多空对冲损益就会回归到 0；  
                  * **全订单簿深度滑点预扣**：系统并非按理想中间价测算，而是调用了深度撮合算法（VWAP），**把此时此刻市价吃单的真实冲击滑点全部直接算进了成本**！
                * **6. 为什么刚结算一次资金费，累计已收资金费反而是负的？违背开仓条件了吗？**  
                  **完全没有违背！这是由两家交易所的「结算周期异步（Asymmetric Settlement Cycles）」造成的暂时账期错觉：**  
                  * **Hyperliquid 结算周期**：**每 1 小时** 结算一次（按 1/24 日化分期细水长流）；  
                  * **币安 Binance 结算周期**：**每 4 小时或 8 小时** 结算一次（按 1/6 或 1/3 日化大额整笔划拨）。  
                  
                  **当两家交易所按不同节拍交错结算时，会发生账期错位：**  
                  * **场景 A（如 SAND）**：币安是超级负费率（做多每 8 小时领大红包 +$5.19），Hyper 做空每 1 小时扣小利息（-$0.32）。在第 1 个小时，Hyper 先扣走了 -$0.32，而币安的 8 小时大红包还在倒计时排队中！账面暂时显示 -$0.32，等 8 小时币安发薪日一到，+$5.19 大额划入，立刻转为 +$2.63 大额净赚！  
                  * **场景 B（如 MON / MOODENG）**：币安每 4 小时一次性扣款（-$0.25），而 Hyper 每 1 小时发放一次补贴（+$0.13）。在币安扣款的整点，币安一口气扣除了 4 小时的总成本，而 Hyper 此刻才结算了 1 个小时！接下来的 3 个小时里，币安不再扣钱，而 Hyper 会继续打款 3 次（+$0.13 × 3 = +$0.40），完整周期走完立刻转为净收益！  
                  * **场景 C（如 2Z）**：两边都在收钱（币安做空收钱 +$0.15，Hyper 做多收钱 +$0.25），无任何扣款方，累计资金费直接为正（+$0.41）！  
                  
                  **总结**：策略核算的是**完整周期（8小时）内的综合净收益必定为正**。只要度过收付错位的暂时时间差，资金费总额必然是实打实的净流入！
                * **7. 核心问答：多长时间能够回本？预期值怎么计算？是把盘口的价差作为损益吗？**  
                  **是的！正是把【多空双腿实时平仓的价差净损益（买入时产生并随行情变动的点差手续费总摩擦）】作为待对冲的损益！**  
                  * **计算公式**：
                    $$\\text{预计剩余回本时间 (小时)} = \\frac{\\text{当前待覆盖净缺口 (盘口浮亏 - 累计已收资金费)}}{\\text{每小时预收息差现金流}}$$
                    其中：
                    $$\\text{每小时预收息差现金流} = \\text{仓位货值} \\times \\left(\\frac{\\text{日息差}}{24}\\right)$$
                  * **直观实例**：
                    假设仓位为 $10,000，开仓买卖点差手续费产生了 -$12.00 的盘口损耗，当前已收到资金费 $0.00，两所日息差为 0.24%（折合每小时收息 +$1.00）：
                    $$\\text{预计回本时间} = \\frac{\\$12.00}{\\$1.00 / \\text{小时}} = \\mathbf{12.0 \\text{ 小时}}$$
                  * **动态倒计时演进**：
                    随着持仓时间增加，每隔 1h/8h 交易所就会发放一笔真金白银的资金费（如 +$1.00 到账），待覆盖缺口从 $12 缩小到 $11...$10...$0。  
                    一旦资金费累计额超过点差亏损，**综合净利润即转为正数，回本状态自动转为「✅ 已完全回本并进入净盈利」**！
                * **8. 专题深度剖析：为什么未平仓浮动盈亏与 ROE 会频繁跳动？是受手续费、滑点还是基差影响？真实收益怎么核算？**  
                  * **核心驱动因子追踪与实盘归因**：
                    1. **手续费（Trading Fees，影响 ~5%）**：Taker 双边固定为 0.08%~0.10%，只在开仓/平仓瞬间扣减一次，额度固定且微小；
                    2. **吃单滑点（Slippage，影响 ~10%）**：由于策略已内置 Top 25 档动态定额（Dynamic Sizing），开仓额限制在流动性安全线内，滑点损耗平稳；
                    3. **两所微观盘口基差偏离（Basis Divergence，影响 >85%！）**：
                       **这是导致浮动盈亏与瞬时 ROE 每秒剧烈跳动的核心根源！**  
                       *现场真实案例*：以币种 **2Z** 为例，开仓时两所基差为 `-0.03%`（价差几乎为 0）。但在持仓过程中，币安端因大买单单边冲高涨了 **+3.7%**，而 Hyperliquid 盘口由于做市商补单滞后价格未跟涨，导致做空腿瞬时产生 `-$202` 的基差浮亏！  
                       *后续演进*：一旦两所套利搬砖资金拉平基差，或者价格回归均值，这 `-$202` 的浮亏就会迅速回弹抹平。**只要不触发止损、不主动平仓，这种浮亏就只是未实现的“纸面账目”，而不是真实亏损！**
                  * **为什么拿“当前瞬时持仓保证金”算总收益率有严重缺陷？**  
                    * 机器人在持续开仓和平仓：持仓 5 个币时保证金为 $14,466，若 4 个币止盈平仓后只剩 1 个币（保证金 $1,133），**分母急剧缩水 10 倍**，收益率百分比就会被动暴涨或暴跌；若全部空仓，分母甚至变成 0！
                  * **量化界公认的「三大真实对账真理指标」**：
                    1. **已平仓结清利润（Realized Net PnL）**：实盘战绩真金白银（当前已平仓 21 笔，累计完全锁定纯利润 **`+$181.47`**！）；
                    2. **纯资金费利息现金流（Funding Harvested）**：只看收到多少利息（当前累计收息 **`+$10.23`**），只增不减、永不回退；
                    3. **规划总本金池净值收益率（Portfolio Committed NAV ROI）**：以固定总充值池（如 $30,000）为恒定分母，提供稳定连续的净值曲线。
                """)
        else:
            thresh_pct = arb_data.get("min_funding_rate_profitability_pct", 0.1)
            best_token = "N/A"
            best_diff = 0.0
            for m in market_comp:
                d = m.get("daily_rate_diff_pct", 0)
                if d > best_diff:
                    best_diff = d
                    best_token = m.get("token")
            st.info(
                f"ℹ️ **当前暂无持仓中头寸（策略正在实时巡检风控观望中）**\n\n"
                f"- **🎯 当前设定开仓门槛**：日化利差需 **≥ {thresh_pct:+.4f}% / 天**（折合年化 APR **{thresh_pct * 365:+.2f}%**）\n"
                f"- **📊 当前市场最大利差**：`{best_token}` 为 **{best_diff:+.4f}% / 天**（距触发开仓还差 **`{(thresh_pct - best_diff):.4f}%`**）\n"
                f"- **🛡️ 为什么不立即开仓？** 跨所双边对冲开仓需扣除 Taker 手续费（双边合计约 0.08%~0.12%），若在微小利差下盲目进场会被手续费侵蚀，因此策略严格遵守安全门槛。\n"
                f"- **💡 立即预演测试建议**：若希望在 Dry-Run 模式下立刻体验多空持仓与资金费定时结算，可将配置中的 `min_funding_rate_profitability` 调低至 `0.0001`（0.01%/天）或 `0`。"
            )

    with tab_pnl:
        render_institutional_pnl_analytics(arb_data, bot_name, active_tz=active_tz, tz_label=tz_label)

    with tab_closed:
        if closed_trades:
            analyzed_list = [analyze_trade_attribution(t, tz_label=tz_label, active_tz=active_tz) for t in closed_trades]
            summary_info = calculate_closed_summary(analyzed_list)

            st.caption(f"ℹ️ 达到止盈目标（Take Profit）或触发费率倒挂止损（Stop Loss）而平仓的历史成交复盘追踪（当前基准时区：{tz_label}）")

            # --- 1. Summary KPI Metrics Bar ---
            c_sm1, c_sm2, c_sm3, c_sm4 = st.columns(4)
            with c_sm1:
                st.metric(
                    "📜 历史平仓总笔数",
                    f"{summary_info['total_count']} 笔",
                    f"胜率 {summary_info['win_rate_pct']:.1f}% ({summary_info['win_count']}胜 {summary_info['loss_count']}负)"
                )
            with c_sm2:
                st.metric(
                    "🎯 累计已结净利润 (Net PnL)",
                    f"${summary_info['total_net_pnl']:+,.4f}",
                    f"均单 ROE {summary_info['avg_roe_pct']:+.2f}%"
                )
            with c_sm3:
                st.metric(
                    "💰 资金费贡献 vs 价差摩擦",
                    f"+${summary_info['total_funding_pnl']:,.2f}",
                    f"价差摩擦 ${summary_info['total_trading_pnl']:+,.2f}"
                )
            with c_sm4:
                st.metric(
                    "⏱️ 平均持仓周期",
                    summary_info["avg_dur_str"],
                    "全生命周期对冲历时"
                )

            st.markdown("---")
            st.markdown("##### 🔍 交易全过程细节与复盘归因分析 (Post-Mortem Review)")

            # 构建币种统计与下拉列表
            token_counts = {}
            for item in analyzed_list:
                tok = item.get("token", "UNKNOWN")
                token_counts[tok] = token_counts.get(tok, 0) + 1
            sorted_tokens = sorted(token_counts.keys(), key=lambda t: (-token_counts[t], t))
            token_options = [f"全部币种 ({len(analyzed_list)}笔)"] + [f"{tok} ({token_counts[tok]}笔)" for tok in sorted_tokens]

            # 4 栏全功能量化复盘筛选控制台
            f_col_tok, f_col_time, f_col_res, f_col_sort = st.columns([1.3, 1.3, 1.1, 1.3])
            with f_col_tok:
                selected_tok_opt = st.selectbox(
                    "🪙 标的币种筛选",
                    token_options,
                    key=f"filter_token_{bot_name}",
                    help="仅查看指定币种的历史平仓成交记录"
                )
            with f_col_time:
                time_range_opt = st.selectbox(
                    "🕒 平仓时段跨度",
                    [
                        "全部历史时段",
                        "过去 6 小时内",
                        "过去 12 小时内",
                        "过去 24 小时内",
                        "过去 3 天内",
                        "过去 7 天内",
                    ],
                    key=f"filter_timerange_{bot_name}",
                    help="按平仓发生的时间跨度过滤"
                )
            with f_col_res:
                filter_res_opt = st.selectbox(
                    "🎯 盈亏定性筛选",
                    [
                        f"全部盈亏 ({len(analyzed_list)})",
                        f"🟢 仅看盈利单 ({summary_info['win_count']})",
                        f"🔴 仅看止损/亏损单 ({summary_info['loss_count']})"
                    ],
                    key=f"filter_res_{bot_name}",
                    help="按盈亏结果筛选交易记录"
                )
            with f_col_sort:
                sort_opt = st.selectbox(
                    "🔃 记录排序方式",
                    [
                        "🕒 平仓时间 (最新在前)",
                        "🕒 平仓时间 (最早在前)",
                        "💰 最终净利 (从高到低)",
                        "🛑 最终净利 (从低到高)",
                        "⏱️ 持仓历时 (从长到短)"
                    ],
                    key=f"sort_closed_{bot_name}",
                    help="切换历史平仓成交排序方式，默认按平仓时间倒序"
                )

            # --- 1. 执行多维筛选过滤 ---
            filtered_list = analyzed_list[:]

            # 标的币种过滤
            if "全部币种" not in selected_tok_opt:
                target_tok = selected_tok_opt.split(" ")[0].strip()
                filtered_list = [x for x in filtered_list if x.get("token") == target_tok]

            # 平仓时段跨度过滤
            if "全部历史时段" not in time_range_opt:
                import time as _py_time
                now_sec = _py_time.time()
                time_window_map = {
                    "过去 6 小时内": 6 * 3600,
                    "过去 12 小时内": 12 * 3600,
                    "过去 24 小时内": 24 * 3600,
                    "过去 3 天内": 3 * 86400,
                    "过去 7 天内": 7 * 86400,
                }
                max_age_sec = time_window_map.get(time_range_opt, 86400 * 365)
                # 以当前时间或最新成交时间中较大者作为时间锚点（防止本地时钟偏移影响过滤）
                max_trade_ts = max((x.get("close_ts", 0) for x in analyzed_list), default=now_sec)
                anchor_now = max(now_sec, max_trade_ts)
                filtered_list = [x for x in filtered_list if (anchor_now - x.get("close_ts", 0)) <= max_age_sec]

            # 盈亏定性过滤
            if "仅看盈利单" in filter_res_opt:
                filtered_list = [x for x in filtered_list if x.get("is_win", False)]
            elif "仅看止损" in filter_res_opt:
                filtered_list = [x for x in filtered_list if not x.get("is_win", False)]

            # --- 2. 执行排序 ---
            if "平仓时间 (最新在前)" in sort_opt:
                display_list = sorted(filtered_list, key=lambda x: x.get("close_ts", 0), reverse=True)
            elif "平仓时间 (最早在前)" in sort_opt:
                display_list = sorted(filtered_list, key=lambda x: x.get("close_ts", 0), reverse=False)
            elif "最终净利 (从高到低)" in sort_opt:
                display_list = sorted(filtered_list, key=lambda x: x.get("total_net_pnl", 0.0), reverse=True)
            elif "最终净利 (从低到高)" in sort_opt:
                display_list = sorted(filtered_list, key=lambda x: x.get("total_net_pnl", 0.0), reverse=False)
            elif "持仓历时 (从长到短)" in sort_opt:
                display_list = sorted(filtered_list, key=lambda x: x.get("dur_sec", 0), reverse=True)
            else:
                display_list = sorted(filtered_list, key=lambda x: x.get("close_ts", 0), reverse=True)

            # --- 3. 动态展示筛选后子集统计透视 ---
            if len(display_list) < len(analyzed_list):
                sub_pnl = sum(x.get("total_net_pnl", 0.0) for x in display_list)
                sub_funding = sum(x.get("funding_pnl", 0.0) for x in display_list)
                sub_trading = sum(x.get("trading_pnl", 0.0) for x in display_list)
                sub_wins = sum(1 for x in display_list if x.get("is_win", False))
                sub_wr = (sub_wins / len(display_list) * 100.0) if display_list else 0.0
                st.info(
                    f"🎯 **专项筛选透视已生效**：匹配 **`{len(display_list)}`** 笔平仓成交（全部 {len(analyzed_list)} 笔） ｜ "
                    f"子集累计净利：**`{sub_pnl:+.4f} USD`** (资金费利息 `{sub_funding:+.4f}` / 价差摩擦 `{sub_trading:+.4f}`) ｜ "
                    f"子集胜率：**`{sub_wr:.1f}%`** (`{sub_wins}`胜 `{len(display_list)-sub_wins}`负)"
                )
            else:
                st.caption(f"当前共显示全部 **{len(display_list)}** 笔历史平仓记录（默认按平仓时间倒序置顶）")

            if not display_list:
                st.warning("⚠️ 当前筛选组合下暂无匹配的平仓成交记录，请重置或放宽筛选条件。")

            for idx, item in enumerate(display_list):
                icon = "🟢" if item["is_win"] else "🔴"
                pnl_sign = "+" if item["total_net_pnl"] >= 0 else ""

                exp_title = (
                    f"#{idx+1:02d} 🪙【 {item['token']} 】  {icon} 净利 {pnl_sign}${item['total_net_pnl']:.4f} (ROE {item['roe_pct']:+.2f}%) "
                    f"｜ 平仓: {item['close_dt_str']} ｜ 历时 {item['dur_str']} ｜ {item['attr_tag']}"
                )

                with st.expander(exp_title, expanded=(idx < 2)):
                    # 🌟 头部超醒目代币与归因卡片 Banner
                    banner_border = "#22c55e" if item["is_win"] else "#ef4444"
                    banner_bg = "rgba(34, 197, 94, 0.12)" if item["is_win"] else "rgba(239, 68, 68, 0.12)"
                    st.markdown(
                        f"""
                        <div style="background: {banner_bg}; 
                                    border-left: 6px solid {banner_border}; 
                                    border-radius: 6px; 
                                    padding: 10px 16px; 
                                    margin-bottom: 14px; 
                                    display: flex; 
                                    align-items: center; 
                                    justify-content: space-between;">
                            <div>
                                <span style="font-size: 11px; color: #94A3B8; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;">套利复盘核心标的 (Target Asset)</span><br/>
                                <span style="font-size: 24px; font-weight: 900; color: #FFFFFF; letter-spacing: 1px;">🪙 {item['token']}</span>
                                <span style="background: rgba(148, 163, 184, 0.25); color: #E2E8F0; font-size: 13px; font-weight: 600; padding: 3px 10px; border-radius: 4px; margin-left: 10px;">{item['connector_1']} ⇋ {item['connector_2']}</span>
                            </div>
                            <div style="text-align: right;">
                                <span style="font-size: 11px; color: #94A3B8;">盈亏因果定性归因</span><br/>
                                <span style="font-size: 15px; font-weight: 700; color: {'#4ADE80' if item['is_win'] else '#F87171'};">{item['attr_tag']}</span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                    # Bento 4 Metrics
                    bm1, bm2, bm3, bm4 = st.columns(4)
                    with bm1:
                        st.metric("最终实结净利 (Net PnL)", f"${item['total_net_pnl']:+.4f}", f"真实实缴 ROE: {item['roe_pct']:+.2f}%")
                    with bm2:
                        st.metric("累计资金费净利息", f"${item['funding_pnl']:+.4f}", "纯利息现金流")
                    with bm3:
                        st.metric("盘口价差与摩擦损益", f"${item['trading_pnl']:+.4f}", "平仓基差与交易手续费")
                    with bm4:
                        st.metric("对冲持仓历时", item["dur_str"], f"{item['annualized_apr']:+.1f}% 年化参考")

                    # 深度因果剖析 Box
                    if item["is_win"]:
                        st.success(f"**💡 为什么会盈利？（成因与归因剖析）**\n\n{item['detail_explanation']}")
                    else:
                        st.warning(f"**🛑 为什么会亏损？（成因与归因剖析）**\n\n{item['detail_explanation']}")

                    # 交易细节与量化对账事实
                    c_det1, c_det2 = st.columns(2)
                    with c_det1:
                        st.markdown(f"**📋 交易生命周期时间线**:")
                        st.markdown(f"- **🪙 标的代码**：**`{item['token']}`**")
                        st.markdown(f"- **开仓时间 ({tz_label})**：`{item['open_dt_str']}`")
                        st.markdown(f"- **平仓时间 ({tz_label})**：`{item['close_dt_str']}`")
                        st.markdown(f"- **持仓历时**：`{item['dur_str']}`（精确秒数：`{item['dur_sec']}` 秒）")
                        st.markdown(f"- **平仓触发原因**：`{item['close_reason']}`")
                    with c_det2:
                        st.markdown(f"**💼 财务口径与资本对账**:")
                        st.markdown(f"- **单边名义货值**：`${item['pos_size']:,.2f}`（双边总货值 `${item['pos_size']*2:,.2f}`）")
                        st.markdown(f"- **杠杆倍数**：`{item['leverage']}x` | **双边实缴本金**：`${item['real_margin']:,.2f}`")
                        st.markdown(f"- **套利交易所组合**：`{item['connector_1']} ⇋ {item['connector_2']}`")
                        st.markdown(f"- **归因成因概述**：`{item['summary_reason']}`")

                    # 官方复盘直达按钮
                    st.markdown("**🔗 官方行情与代币背景深度复盘直达**:")
                    hl_col1, hl_col2, hl_col3, hl_col4 = st.columns(4)
                    with hl_col1:
                        st.link_button(f"🟢 {item['connector_1']} 官方K线/盘口 ↗", item["c1_url"], use_container_width=True)
                    with hl_col2:
                        st.link_button(f"🔴 {item['connector_2']} 官方K线/盘口 ↗", item["c2_url"], use_container_width=True)
                    with hl_col3:
                        st.link_button("📊 TradingView 大屏K线 ↗", item["tradingview_url"], use_container_width=True)
                    with hl_col4:
                        st.link_button("🌐 代币官网/CoinGlass ↗", item["coinglass_url"], use_container_width=True)

            # --- 3. Full Tabular Data View ---
            with st.expander("📋 查看完整历史平仓数据总表 (可排序/筛选/导出)", expanded=False):
                closed_rows = []
                for item in display_list:
                    closed_rows.append({
                        "标的代币": f"🪙 {item['token']}",
                        f"平仓时间 ({tz_label})": item["close_dt_str"],
                        "持仓时长": item["dur_str"],
                        "最终实现净利润 (Net PnL)": f"${item['total_net_pnl']:+.4f}",
                        "真实实缴ROE": f"{item['roe_pct']:+.2f}%",
                        "盈亏归因标签": item["attr_tag"],
                        "平仓原因": item["close_reason"],
                        "资金费净利息": f"${item['funding_pnl']:+.4f}",
                        "价差损益": f"${item['trading_pnl']:+.4f}",
                        f"开仓时间 ({tz_label})": item["open_dt_str"],
                        "腿1官方行情": item["c1_url"],
                        "腿2官方行情": item["c2_url"],
                        "代币资料/官网": item["coinglass_url"],
                    })
                closed_col_cfg = {
                    "腿1官方行情": st.column_config.LinkColumn("腿1官方行情", display_text="📈 腿1K线 ↗"),
                    "腿2官方行情": st.column_config.LinkColumn("腿2官方行情", display_text="📈 腿2K线 ↗"),
                    "代币资料/官网": st.column_config.LinkColumn("代币资料/官网", display_text="🌐 官网资料 ↗"),
                }
                st.dataframe(pd.DataFrame(closed_rows), use_container_width=True, hide_index=True, column_config=closed_col_cfg)
        else:
            st.info("ℹ️ 暂无历史平仓记录。")


    with tab_rates:
        rot_info = arb_data.get("rotation_status", {})
        # --- Section 1: Auto-Rotation Status & Control Console ---
        rot_c1, rot_c2, rot_c3 = st.columns([2.5, 2.0, 1.5])
        with rot_c1:
            render_discovery_status(arb_data, active_tz)
        with rot_c2:
            st.markdown("**轮换准入与替换条件**")
            st.caption("新候选须通过完整成本、盘口深度及预算检查；"
                       "池满时只替换符合条件的闲置标的，已有持仓继续保留。")
        with rot_c3:
            if st.button("⚡ 立即触发全网最优轮换", key=f"btn_rot_now_{bot_name}", use_container_width=True):
                success = send_strategy_command(bot_name, {"action": "trigger_rotation"})
                if success:
                    st.toast("✅ 已发送扫描请求；扫描和准入检查完成后，符合条件才会更新观察池。")
                    st.rerun()
                else:
                    st.error("写入指令失败，请检查实例运行状态")

        st.markdown("---")
        thresh_pct = arb_data.get("min_funding_rate_profitability_pct", 0.1)
        st.caption(f"ℹ️ 策略后台实时维护的各币种利差对比（当前设定开仓门槛：日化利差 ≥ {thresh_pct:+.4f}% / 天 | 当前显示时区：{tz_label}）")
        if market_comp:
            active_tokens_set = {p.get("token") for p in active_pos}
            quarantined_dict = arb_data.get("quarantined_tokens", {})
            perm_bl_set = set(arb_data.get("permanent_blacklist", []))
            post_tp_set = set(arb_data.get("post_tp_lockout", {}).keys())
            rates_rows = []
            for m in market_comp:
                tok = m.get("token", "")
                daily_diff = m.get('daily_rate_diff_pct', 0)
                apr_val = daily_diff * 365
                apr_str = f"{apr_val:+.2f}%" if abs(apr_val) < 10000 else f"{apr_val:+.2e}%"

                # Status
                if tok in active_tokens_set:
                    status_badge = "🟢 活跃持仓中"
                elif tok in perm_bl_set:
                    status_badge = "🚫 永久拉黑毒币"
                elif tok in post_tp_set:
                    status_badge = "🔒 止盈防追高锁定"
                elif tok in quarantined_dict:
                    status_badge = "🛑 动态隔离中"
                elif daily_diff >= thresh_pct:
                    status_badge = "✅ 利差达标(候选)"
                else:
                    status_badge = f"⏳ 观望中 (差 {(thresh_pct - daily_diff):.4f}%)"

                bn_url = get_official_market_url("binance_perpetual", tok)
                hl_url = get_official_market_url("hyperliquid_perpetual", tok)
                res_url = get_token_research_urls(tok)["coinglass"]
                row = {
                    "监控币种": tok,
                    "持仓状态": status_badge,
                    "币安官方K线": bn_url,
                    "HL官方盘口": hl_url,
                    "代币官网与资料": res_url,
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
            rates_col_cfg = {
                "币安官方K线": st.column_config.LinkColumn("币安官方K线", display_text="🟡 币安K线 ↗"),
                "HL官方盘口": st.column_config.LinkColumn("HL官方盘口", display_text="🟢 HL盘口 ↗"),
                "代币官网与资料": st.column_config.LinkColumn("代币官网与资料", display_text="🌐 官网资料 ↗"),
            }
            st.dataframe(pd.DataFrame(rates_rows), use_container_width=True, hide_index=True, column_config=rates_col_cfg)

            # --- Section 2: Quick Watchlist Management Controller ---
            watched_toks = [m.get("token") for m in market_comp]
            st.markdown("##### 🎛️ 监控池快捷管理与热配置控制台")
            col_rm_sel, col_rm_btn, col_add_txt, col_add_btn = st.columns([2, 1.2, 2, 1.2])
            with col_rm_sel:
                rm_target = st.selectbox("选择移出标的", watched_toks, key=f"rm_sel_{bot_name}", label_visibility="collapsed")
            with col_rm_btn:
                if st.button("➖ 移出监控", key=f"btn_rm_{bot_name}", use_container_width=True):
                    if rm_target:
                        send_strategy_command(bot_name, {"action": "remove_token", "token": rm_target})
                        st.toast(f"✅ 已向策略发送移出 {rm_target} 指令！若有持仓将安全平仓后退出。")
                        st.rerun()
            with col_add_txt:
                new_target = st.text_input("输入币种代码 (如 SUI, APT)", key=f"add_txt_{bot_name}", label_visibility="collapsed").strip().upper()
            with col_add_btn:
                if st.button("➕ 加入监控", key=f"btn_add_{bot_name}", use_container_width=True):
                    if new_target:
                        send_strategy_command(bot_name, {"action": "add_token", "token": new_target})
                        st.toast(f"✅ 已向策略发送加入 {new_target} 指令！连接器正建立 WebSocket 监听。")
                        st.rerun()

            # --- Section 3: 200+ Market Radar with Quick-Add ---
            with st.expander("📡 查看全市场 200+ 标的实时高利差机会雷达 (Market Radar)", expanded=False):
                try:
                    all_opps = get_cached_market_opportunities()
                    if all_opps:
                        st.caption("ℹ️ 当前全市场正在爆发高利差的潜力标的排行榜（点击即可一键加入实盘监控池）：")
                        radar_rows = []
                        for i, o in enumerate(all_opps[:15], 1):
                            tok = o["coin"]
                            bn_url = get_official_market_url("binance_perpetual", tok)
                            hl_url = get_official_market_url("hyperliquid_perpetual", tok)
                            res_url = get_token_research_urls(tok)["coinglass"]
                            radar_rows.append({
                                "全网排名": f"#{i}",
                                "标的币种": tok,
                                "币安K线": bn_url,
                                "HL盘口": hl_url,
                                "官网资料": res_url,
                                "折算年化 (APR)": f"{o['apr']:+.2f}%",
                                "折算日化利差": f"{o['daily_diff_pct']:+.4f}% / 天",
                                "流动性评级": o.get("liquidity_badge", "🟡 适中"),
                                "建议单笔上限": o.get("safe_cap", "$2,000 ~ $5,000"),
                                "HL 24h成交量": f"${o.get('hl_vol_24h', 0):,.0f}",
                                "BN 24h成交量": f"${o.get('bn_vol_24h', 0):,.0f}",
                                "推荐套利组合方向": o["best_path"],
                                "现价 ($)": f"${o['price']:.4f}" if o['price'] < 10 else f"${o['price']:.2f}",
                            })
                        radar_col_cfg = {
                            "币安K线": st.column_config.LinkColumn("币安K线", display_text="🟡 币安 ↗"),
                            "HL盘口": st.column_config.LinkColumn("HL盘口", display_text="🟢 HL ↗"),
                            "官网资料": st.column_config.LinkColumn("官网资料", display_text="🌐 资料 ↗"),
                        }
                        st.dataframe(pd.DataFrame(radar_rows), use_container_width=True, hide_index=True, column_config=radar_col_cfg)

                        # One-Click Add from Radar
                        top_radar_toks = [o["coin"] for o in all_opps[:15] if o["coin"] not in watched_toks]
                        if top_radar_toks:
                            r_add_c1, r_add_c2 = st.columns([3, 1.5])
                            with r_add_c1:
                                quick_add_tok = st.selectbox("从雷达前 15 名中挑选潜力标的一键加入监控", top_radar_toks, key=f"quick_radar_add_{bot_name}")
                            with r_add_c2:
                                st.write("")
                                st.write("")
                                if st.button(f"➕ 将 {quick_add_tok} 纳入监控", key=f"btn_quick_radar_add_{bot_name}", use_container_width=True):
                                    send_strategy_command(bot_name, {"action": "add_token", "token": quick_add_tok})
                                    st.toast(f"✅ 已向策略发送加入 {quick_add_tok} 指令！")
                                    st.rerun()
                except Exception as e:
                    st.info(f"全市场雷达加载中: {e}")

            # --- Section 4: Auto-Rotation Audit Log ---
            rot_events = rot_info.get("rotation_events", [])
            with st.expander(f"🔄 自动轮换历史审计流水 ({len(rot_events)} 笔记录)", expanded=False):
                if rot_events:
                    ev_rows = []
                    for ev in rot_events:
                        ev_ts = ev.get("timestamp", 0)
                        ev_time_str = format_timestamp(ev_ts, active_tz) if ev_ts > 0 else "N/A"
                        ev_rows.append({
                            "轮换时间": ev_time_str,
                            "淘汰闲置币": ev.get("retired_token", "NONE"),
                            "原年化 (APR)": f"{ev.get('retired_apr', 0.0):+.1f}%",
                            "晋级新候选": ev.get("added_token", ""),
                            "新年化 (APR)": f"{ev.get('added_apr', 0.0):+.1f}%",
                            "利差增量 (ΔAPR)": f"+{ev.get('apr_delta', 0.0):.1f}%",
                            "轮换触发原因": ev.get("reason", "Yield Optimization"),
                        })
                    st.dataframe(pd.DataFrame(ev_rows), use_container_width=True, hide_index=True)
                else:
                    st.info(no_rotation_message(rot_info))
        else:
            st.info("ℹ️ 正在连接交易所拉取实时费率对比数据...")

    with tab_charts:
        st.markdown("##### 🔬 盘口深度测算原理与量化分析看板")

        with st.expander("📖 知识速递：盘口深度是怎么算出来的？为什么大单会遇到深度枯竭？", expanded=True):
            st.markdown("""
            **1. 盘口深度（Order Book Depth）的底层计算原理**：
            * 订单簿由买单列表（Bids）和卖单列表（Asks）构成，每一档包含 `(价格 Pi, 挂单量 Qi)`。
            * **累计深度金额 (USD)** = $\\sum_{i=1}^{n} (P_i \\times Q_i)$。
            * 当机器人市价开仓（如名义价值 **$10,000**）时，系统从盘口第 1 档（最优价）开始吃单，逐层向下消耗流动性，直到累计吃满 $10,000。
            * **为什么之前 $10,000 会报错？**  
              在主流币（BTC/ETH）上，前 10 档深度可达数百万美元；但在某些热门山寨币上，Hyperliquid 的总挂单深度在某一瞬间可能只有 **$3,000 ~ $6,000**。如果下单金额设定为 $10,000，就会直接把整个挂单簿“吃空”，导致系统取不到可成交价格（返回 `NaN`）。
            * **量化实战建议**：单笔开仓金额应控制在对应币种 24h 成交量的 **0.1% ~ 0.5%** 以内，既能稳收资金费，又能把滑点摩擦降至极低。
            """)

        # Fetch liquidity details for active / watched coins
        try:
            opps = get_cached_market_opportunities()
            opp_dict = {o["coin"]: o for o in opps}
        except Exception:
            opp_dict = {}

        watched_tokens = [p.get("token") for p in active_pos] if active_pos else [m.get("token") for m in market_comp]
        if watched_tokens:
            chart_tokens = []
            hl_rates = []
            bn_rates = []
            spreads = []
            aprs = []
            hl_vols = []
            bn_vols = []
            cashflows = []

            for t in watched_tokens:
                m_info = next((m for m in market_comp if m.get("token") == t), {})
                o_info = opp_dict.get(t, {})
                diff = float(m_info.get("daily_rate_diff_pct", o_info.get("daily_diff_pct", 0.0)))
                hl_r = float(m_info.get("hyperliquid_perpetual_rate_daily", o_info.get("connector_1_rate_daily", 0.0)))
                bn_r = float(m_info.get("binance_perpetual_rate_daily", o_info.get("connector_2_rate_daily", 0.0)))
                hl_v = float(o_info.get("hl_vol_24h", 100000))
                bn_v = float(o_info.get("bn_vol_24h", 1000000))

                pos_match = next((p for p in active_pos if p.get("token") == t), None)
                p_size = float(pos_match.get("position_size", 10000)) if pos_match else 10000.0
                daily_cash = p_size * (diff / 100.0)

                chart_tokens.append(t)
                hl_rates.append(hl_r)
                bn_rates.append(bn_r)
                spreads.append(diff)
                aprs.append(diff * 365.0)
                hl_vols.append(hl_v)
                bn_vols.append(bn_v)
                cashflows.append(daily_cash)

            col_c1, col_c2 = st.columns(2)
            with col_c1:
                # Chart 1: Funding Rates & APR Spread
                fig1 = go.Figure()
                fig1.add_trace(go.Bar(
                    x=chart_tokens, y=hl_rates,
                    name='Hyperliquid 费率(日化)',
                    marker_color='#3b82f6'
                ))
                fig1.add_trace(go.Bar(
                    x=chart_tokens, y=bn_rates,
                    name='Binance 费率(日化)',
                    marker_color='#f59e0b'
                ))
                fig1.add_trace(go.Scatter(
                    x=chart_tokens, y=spreads,
                    name='日化净利差 (Daily Diff)',
                    mode='lines+markers+text',
                    text=[f"{a:.1f}% APR" for a in aprs],
                    textposition='top center',
                    line=dict(color='#10b981', width=3),
                    marker=dict(size=8)
                ))
                fig1.update_layout(
                    title="📈 标的费率与净利差 (APR) 对比",
                    yaxis_title="日化费率 % / 天",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    height=380,
                    margin=dict(l=20, r=20, t=50, b=20),
                    transition_duration=0,
                )
                st.plotly_chart(fig1, use_container_width=True, key=f"rates_chart_{bot_name}", config={"displayModeBar": False, "responsive": True})

            with col_c2:
                # Chart 2: 24h Volume Liquidity Comparison (Log Scale)
                fig2 = go.Figure()
                fig2.add_trace(go.Bar(
                    x=chart_tokens, y=hl_vols,
                    name='Hyperliquid 24h成交量 ($)',
                    marker_color='#6366f1'
                ))
                fig2.add_trace(go.Bar(
                    x=chart_tokens, y=bn_vols,
                    name='Binance 24h成交量 ($)',
                    marker_color='#06b6d4'
                ))
                fig2.update_layout(
                    title="🌊 两所 24h 交易流动性量级对比 (对数刻度)",
                    yaxis_type="log",
                    yaxis_title="24h 成交额 (USD, Log Scale)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    height=380,
                    margin=dict(l=20, r=20, t=50, b=20),
                    transition_duration=0,
                )
                st.plotly_chart(fig2, use_container_width=True, key=f"vol_chart_{bot_name}", config={"displayModeBar": False, "responsive": True})

            # Chart 3: Projected Daily Cashflow per token
            st.markdown("##### 💵 各标的 24 小时预期资金费现金流收益 ($/天)")
            fig3 = go.Figure()
            fig3.add_trace(go.Bar(
                x=chart_tokens, y=cashflows,
                text=[f"+${c:.2f}/天" for c in cashflows],
                textposition='auto',
                marker_color='#10b981',
                name='单币种 24h 预期入账'
            ))
            fig3.update_layout(
                yaxis_title="预期日入账 ($ USD)",
                height=280,
                margin=dict(l=20, r=20, t=30, b=20),
                transition_duration=0,
            )
            st.plotly_chart(fig3, use_container_width=True, key=f"cashflow_chart_{bot_name}", config={"displayModeBar": False, "responsive": True})

            # Liquidity Summary Table
            st.markdown("##### 🛡️ 标的盘口深度评级与安全持仓容量表")
            liq_table = []
            for t in chart_tokens:
                o_item = opp_dict.get(t, {})
                liq_table.append({
                    "标的币种": t,
                    "流动性评级": o_item.get("liquidity_badge", "🟡 适中"),
                    "建议安全单笔上限": o_item.get("safe_cap", "$2,000 ~ $5,000"),
                    "Hyperliquid 24h成交额": f"${o_item.get('hl_vol_24h', 0):,.0f}",
                    "Binance 24h成交额": f"${o_item.get('bn_vol_24h', 0):,.0f}",
                    "折算年化 (APR)": f"{o_item.get('apr', 0):+.2f}%",
                    "推荐对冲方向": o_item.get("best_path", "N/A"),
                })
            st.dataframe(pd.DataFrame(liq_table), use_container_width=True, hide_index=True)
        else:
            st.info("ℹ️ 暂无足够标的数据生成可视化图表。")



def get_bot_funding_arbitrage_data(bot_name, performance):
    """Retrieve funding arbitrage custom_info from MQTT performance or local disk."""
    # Strict Isolation Guard: basis stat arb bots can NEVER be matched as funding bots
    if "basis" in bot_name.lower() or "basis_stat_arb" in performance:
        return False, None

    arb_data = None
    for ctrl_id, ctrl_dict in performance.items():
        if ctrl_id == "funding_rate_arb":
            c_info = ctrl_dict.get("custom_info", {})
            if "active_positions" in c_info or "closed_trades" in c_info or "market_comparison" in c_info:
                arb_data = c_info
                break

    # Search for local_json across multiple possible root configurations
    candidate_json_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), f"../../../hummingbot-api/bots/instances/{bot_name}/data/arbitrage_trades.json")),
        os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/instances/{bot_name}/data/arbitrage_trades.json"),
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

    # Check if bot is a funding rate arbitrage bot
    is_funding_arb_bot = (
        "funding" in bot_name.lower() or 
        "funding_rate_arb" in performance or 
        (arb_data is not None and arb_data.get("strategy") != "basis_stat_arb")
    )
    if not is_funding_arb_bot:
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
                                if "funding" in f.read():
                                    is_funding_arb_bot = True
                                    break
                        except Exception:
                            pass
            if is_funding_arb_bot:
                break

    if not is_funding_arb_bot:
        return False, None

    if is_funding_arb_bot and not arb_data:
        arb_data = {
            "is_dry_run": True,
            "active_positions": [],
            "closed_trades": [],
            "market_comparison": []
        }

    return is_funding_arb_bot, arb_data

