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


@st.cache_data(ttl=60, show_spinner=False)
def get_cached_market_opportunities():
    """Cache external cross-exchange funding rates & liquidity to prevent UI network lag and flickering."""
    try:
        from frontend.components.funding_scanner import scan_cross_exchange_opportunities
        return scan_cross_exchange_opportunities()
    except Exception:
        return []


def render_funding_arbitrage_tables(arb_data, bot_name):
    """Render visual tables and rich Plotly charts for Funding Rate Arbitrage positions, closed trades, and liquidity depth."""
    active_pos = arb_data.get("active_positions", [])
    closed_trades = arb_data.get("closed_trades", [])
    market_comp = arb_data.get("market_comparison", [])
    is_dry_run = arb_data.get("is_dry_run", True)

    mode_badge = "🟢 纸面预演 (Dry-Run)" if is_dry_run else "🔴 实盘交易 (Live)"
    st.markdown(f"#### 💰 资金费率套利持仓与量化透视 `{mode_badge}`")

    # --- Section A: Summary Key Performance Metrics ---
    total_capital = sum(float(p.get("position_size", 0)) for p in active_pos)
    total_funding_income = sum(float(p.get("funding_income", 0)) for p in active_pos) + sum(float(t.get("funding_pnl", 0)) for t in closed_trades)
    total_unrealized_pnl = sum(float(p.get("unrealized_trading_pnl", 0)) for p in active_pos)
    total_realized_pnl = sum(float(t.get("total_net_pnl", 0)) for t in closed_trades)
    net_combined_pnl = total_funding_income + total_unrealized_pnl + total_realized_pnl

    # Projected 24h funding cashflow
    rate_diff_map = {m.get("token"): float(m.get("daily_rate_diff_pct", 0.0)) for m in market_comp}
    projected_daily_usd = sum(float(p.get("position_size", 0)) * (rate_diff_map.get(p.get("token"), 0.0) / 100.0) for p in active_pos)
    projected_apr = (projected_daily_usd * 365.0 / total_capital * 100.0) if total_capital > 0 else 0.0

    global_leverage = int(arb_data.get("leverage", 3))
    real_margin_total = sum(
        float(p.get("position_size", 0)) * (
            1.0 / float(p.get("long_leverage", p.get("leverage", global_leverage))) +
            1.0 / float(p.get("short_leverage", p.get("leverage", global_leverage)))
        ) for p in active_pos
    )
    gross_capital = total_capital * 2.0
    roe_apr = (projected_daily_usd * 365.0 / real_margin_total * 100.0) if real_margin_total > 0 else 0.0

    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    with col_m1:
        st.metric("💼 真实实缴本金 (总保证金)", f"${real_margin_total:,.2f}", f"多空 {global_leverage}x 实缴 (单边 ${real_margin_total/2:,.2f})")
    with col_m2:
        st.metric("🎯 双边合约总货值 (Gross)", f"${gross_capital:,.2f}", f"单边对冲 ${total_capital:,.2f} (本金×{global_leverage}x)")
    with col_m3:
        st.metric("💰 累计已收资金费", f"${total_funding_income:+.4f}", "每小时 / 8小时结算到账")
    with col_m4:
        st.metric("⚡ 24h 预期现金流", f"+${projected_daily_usd:,.2f} / 天", f"本金年化 ROE {roe_apr:+.2f}%")
    with col_m5:
        st.metric("🛡️ 综合净盈亏 (Net PnL)", f"${net_combined_pnl:+.4f}", "价差损益 + 资金费")

    # --- Section B: 4 Multi-Dimensional Tabs ---
    tab_pos, tab_closed, tab_rates, tab_charts = st.tabs([
        f"📈 活跃多空持仓 ({len(active_pos)})",
        f"📜 历史平仓成交 ({len(closed_trades)})",
        f"🌐 实时费率与利差监控 ({len(market_comp)})",
        f"📊 盘口深度透视与多维图表看板"
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
                next_f1 = max(0, int(p.get("next_funding_1", 0) - now_ts)) // 60
                next_f2 = max(0, int(p.get("next_funding_2", 0) - now_ts)) // 60
                c1_abbr = long_ex.split('_')[0].capitalize()
                c2_abbr = short_ex.split('_')[0].capitalize()
                countdown_str = f"{c1_abbr}: {next_f1}m | {c2_abbr}: {next_f2}m"

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
                if min_dist >= 0.20:
                    health_badge = f"🟢 充沛 (+{min_dist:.1%})"
                elif min_dist >= 0.12:
                    health_badge = f"🟡 良好 (+{min_dist:.1%})"
                else:
                    health_badge = f"🔴 警戒 (+{min_dist:.1%})"

                pos_size = float(p.get('position_size', 0))
                single_long_margin = pos_size / long_lev if long_lev > 0 else pos_size / 3.0
                single_short_margin = pos_size / short_lev if short_lev > 0 else pos_size / 3.0
                real_margin = single_long_margin + single_short_margin
                token_sym = p.get('token', '')
                coin_amt_str = f"{pos_size / long_p:,.0f} {token_sym}" if long_p > 0 else "N/A"

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
                    "countdown_str": countdown_str,
                    "next_f1": next_f1,
                    "next_f2": next_f2,
                    "long_leg_pnl": long_leg_pnl,
                    "short_leg_pnl": short_leg_pnl,
                    "open_basis": open_basis,
                    "curr_basis": curr_basis,
                    "payback_hours": float(p.get("payback_hours", 0.0)),
                })

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

                # Compact Table Row: compact columns without excessive text bloat
                lev_tag = f"多{long_lev}x/空{short_lev}x" if long_lev != short_lev else f"多空{long_lev}x"
                pos_table_rows.append({
                    "标的 (杠杆)": f"{token_sym} ({lev_tag})",
                    "对冲路径": f"🟢{c1_abbr}(多) ⇄ 🔴{c2_abbr}(空)",
                    "实缴本金": f"${real_margin:,.2f}",
                    "单边货值": f"${pos_size:,.2f}",
                    "双边总货值": f"${pos_size * 2:,.2f}",
                    "持币数量": coin_amt_str,
                    "强平安全垫": health_badge,
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
                            st.markdown(
                                f"#### 🪙 `{item['token']}` &nbsp; "
                                f"<span style='font-size:13px; background:#1e293b; border:1px solid #334155; padding:3px 8px; border-radius:4px; color:#38bdf8; font-weight:600;'>"
                                f"⚡ 杠杆: 做多 {item['long_lev']}x | 做空 {item['short_lev']}x</span>",
                                unsafe_allow_html=True
                            )
                            st.caption(f"🧭 **对冲配对**: `🟢 {item['long_ex']} (多)` ⇄ `🔴 {item['short_ex']} (空)`")
                        with c_hd2:
                            st.markdown(f"⏱️ **持仓时长**: `{item['dur_str']}` &nbsp;&nbsp;|&nbsp;&nbsp; ⏳ **结算倒计时**: `{item['countdown_str']}`")
                            st.markdown(f"🛡️ **强平安全盾**: `{item['health_badge']}` *(≤8% 自动市价防守平仓)*")
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
                            st.markdown(f"""
                            - **实缴本金 (双边保证金)**: **`${item['real_margin']:,.2f}`**
                              - *(多头保证金 `${item['single_long_margin']:,.2f}` + 空头保证金 `${item['single_short_margin']:,.2f}`)*
                            - **双边合约总货值**: **`${item['pos_size'] * 2:,.2f}`** *(实缴本金 `${item['real_margin']:,.2f}` × {item['long_lev']}x 杠杆)*
                            - **单边对冲货值**: **`${item['pos_size']:,.2f}`** *(多空两腿各持仓 `${item['pos_size']:,.2f}`)*
                            - **持币对冲数量**: `{item['coin_amt_str']}` *(双向等量持币)*
                            - **开仓杠杆倍数**: **做多 `{item['long_lev']}x` | 做空 `{item['short_lev']}x`**
                            """)

                        with col_b:
                            st.markdown("##### 🛡️ 双腿盘口与强平防线")
                            st.markdown(f"""
                            - 🟢 **多头腿 (`{item['long_ex']}`)**:
                              - 买入: `${item['long_p']:.4f}` → 现价: `${item['curr_long_p']:.4f}` | 单腿损益: **`${item['long_leg_pnl']:+.2f}`**
                              - 预估强平: `${item['liq_long']:.4f}` *(安全垫: `+{item['dist_long']:.1%}`)*
                            - 🔴 **空头腿 (`{item['short_ex']}`)**:
                              - 卖出: `${item['short_p']:.4f}` → 现价: `${item['curr_short_p']:.4f}` | 单腿损益: **`${item['short_leg_pnl']:+.2f}`**
                              - 预估强平: `${item['liq_short']:.4f}` *(安全垫: `+{item['dist_short']:.1%}`)*
                            - ⚖️ **双腿价差与滑点对冲损益**:
                              - 开仓两所价差: `{item['open_basis']:+.3f}%` → 当前平仓价差: `{item['curr_basis']:+.3f}%`
                              - 两腿对冲价差净损益: **`${item['unrealized_pnl']:+.4f}`** *(多头 ${item['long_leg_pnl']:+.2f} + 空头 ${item['short_leg_pnl']:+.2f})*
                            """)

                        with col_c:
                            st.markdown("##### 📈 实时损益与费率利息")
                            pb_val = item.get("payback_hours", 0.0)
                            pb_init_str = f"{pb_val:.1f}h" if pb_val > 0 else "0.0h (无倒贴)"
                            st.markdown(f"""
                            - ⏳ **覆盖开仓损耗预估时间**: **{item.get('dynamic_payback_str', '计算中...')}**
                            - 🏷️ **开仓初始评测回本**: `{pb_init_str}` *(风控硬性门槛: ≤12h)*
                            - 📉 **盘口价差净损益 (当前平仓对冲摩擦)**: `${item['unrealized_pnl']:+.4f}`
                            - 💰 **累计已收净资金费**: **`${item['funding_inc']:+.4f}`** *(已结 `{item['settle_cnt']}` 次)*
                              - *(Hyperliquid 每1h结，币安每4h/8h结；周期交错期间暂显账期差)*
                            - 🛡️ **综合净利润**: **`${item['net_pnl']:+.4f}`**
                            - ⏰ **下期资金费结算**: 约 `{min(item['next_f1'], item['next_f2'])}m` 后到账
                            """)
            else:
                st.dataframe(pd.DataFrame(pos_table_rows), use_container_width=True, hide_index=True)

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
                  * **价差扩大**：如果平仓时两所价差朝着不利方向扩大，多空两腿对冲后损益会暂时为负，**完全实时体现在“盘口浮亏”里，一分钱都不会漏算**；  
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
        thresh_pct = arb_data.get("min_funding_rate_profitability_pct", 0.1)
        st.caption(f"ℹ️ 策略后台实时扫描的各币种利差对比（当前设定开仓门槛：日化利差 ≥ {thresh_pct:+.4f}% / 天）")
        if market_comp:
            rates_rows = []
            for m in market_comp:
                daily_diff = m.get('daily_rate_diff_pct', 0)
                apr_val = daily_diff * 365
                apr_str = f"{apr_val:+.2f}%" if abs(apr_val) < 10000 else f"{apr_val:+.2e}%"
                status_str = "✅ 已达标 (开仓)" if daily_diff >= thresh_pct else f"⏳ 观望中 (差 {(thresh_pct - daily_diff):.4f}%)"
                row = {
                    "监控币种": m.get("token"),
                    "推荐套利组合 (Path)": m.get("best_path", "N/A"),
                    "折算日化利差 (Daily Diff)": f"{daily_diff:+.4f}% / 天" if abs(daily_diff) < 1000 else f"{daily_diff:+.2e}% / 天",
                    "折算年化利差 (APR)": apr_str,
                    "门槛达标状态": status_str,
                }
                for k, v in m.items():
                    if k.endswith("_rate_daily"):
                        c_title = k.replace("_rate_daily", "")
                        v_str = f"{v:+.4f}%" if abs(v) < 1000 else f"{v:+.2e}%"
                        row[f"{c_title} 费率 (日化)"] = v_str
                rates_rows.append(row)
            st.dataframe(pd.DataFrame(rates_rows), use_container_width=True, hide_index=True)

            with st.expander("📡 查看全市场 200+ 标的实时高利差机会雷达 (Market Radar)", expanded=False):
                try:
                    all_opps = get_cached_market_opportunities()
                    if all_opps:
                        st.caption("ℹ️ 当前全市场正在爆发高利差的潜力标的排行榜（如果当前机器人监控币种利差较低，可参考此列表挑选并切换标的）：")
                        radar_rows = []
                        for i, o in enumerate(all_opps[:15], 1):
                            radar_rows.append({
                                "全网排名": f"#{i}",
                                "标的币种": o["coin"],
                                "折算年化 (APR)": f"{o['apr']:+.2f}%",
                                "折算日化利差": f"{o['daily_diff_pct']:+.4f}% / 天",
                                "流动性评级": o.get("liquidity_badge", "🟡 适中"),
                                "建议单笔上限": o.get("safe_cap", "$2,000 ~ $5,000"),
                                "HL 24h成交量": f"${o.get('hl_vol_24h', 0):,.0f}",
                                "BN 24h成交量": f"${o.get('bn_vol_24h', 0):,.0f}",
                                "推荐套利组合方向": o["best_path"],
                                "现价 ($)": f"${o['price']:.4f}" if o['price'] < 10 else f"${o['price']:.2f}",
                            })
                        st.dataframe(pd.DataFrame(radar_rows), use_container_width=True, hide_index=True)
                except Exception as e:
                    st.info(f"全市场雷达加载中: {e}")
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
