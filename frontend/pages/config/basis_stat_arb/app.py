import os
import re
import yaml
import pandas as pd
import streamlit as st

from frontend.components.basis_scanner import scan_cross_exchange_basis_opportunities
from frontend.components.timezone_utils import DEFAULT_TIMEZONE, TIMEZONE_CHOICES
from frontend.st_utils import get_backend_api_client, initialize_st_page

# Initialize the Streamlit page
initialize_st_page(title="基差统计套利配置器 (Basis Stat Arb)", icon="⚡", layout="wide")

# Get backend API client
backend_api_client = get_backend_api_client()

# ==================== 1. Educational Header & Banner ====================
st.markdown("""
### ⚡ 基差统计套利加资金费双击流 (Basis Stat Arb + Carry Double-Dip)
> **捕捉主流币跨所盘口瞬时脱锚价差（0.7% ~ 1.2%），极速均值回归收割 + 顺风坐收资金费**
""")

with st.expander("📘 点击展开：为什么这是解决资金利用率与高频创收的核心武器？", expanded=False):
    c_info1, c_info2 = st.columns(2)
    with c_info1:
        st.markdown("""
        **1. 拒绝死等利息：从周级别缩短至小时级别**
        * 纯资金费策略每天仅赚 0.08%~0.15% 净利息，资金周转极其缓慢。
        * 基差统计套利捕捉交易所流动性补单滞后产生的 **0.8% ~ 1.2% 盘口价差**，均值回归一旦完成立即止盈，**15 分钟 ~ 2 小时单笔收割 +$15 ~ +$30 真实利润**！
        
        **2. 双击加成（Double Dip Alpha）**
        * “做空高估交易所 + 做多低估交易所”不仅赚取基差压缩的巨额差价，持仓期间还顺带收取多方支付的高额利息。
        """)
    with c_info2:
        st.markdown("""
        **3. 为什么仅限主流币？（Tier 1 & Tier 2 铁律）**
        * 坚决剔除不可预测的垃圾山寨币！
        * 策略专注于 BTC、ETH、SOL、AVAX、SUI、DOGE、NEAR、APT 等顶级做市币。国际顶级量化做市商（Wintermute、Flow Traders）在这些币上必定在几分钟内抹平价差，均值回归确定性高达 95%+！
        
        **4. 严格四重防线保本**
        * 内置移动保本止损锁（Trailing Stop）。
        * 内置超时强制退避（Max Hold Time），杜绝长时间资金沉淀。
        * 独立数据文件 `basis_stat_arb_trades.json`，与纯资金费套利完全物理隔离。
        """)

st.write("---")

# ==================== 2. Interactive Live Dislocation Calculator ====================
st.subheader("🧮 均值回归创收与周转模拟计算器 (Dislocation Profit Calculator)")

calc_col1, calc_col2, calc_col3 = st.columns(3)
with calc_col1:
    calc_capital = st.number_input(
        "单币种单边名义货值 (USDT)",
        min_value=100.0,
        max_value=100000.0,
        value=1500.0,
        step=100.0,
        help="在两家交易所分别建立的等额多空货值"
    )
with calc_col2:
    calc_basis_reversion = st.number_input(
        "预估基差均值回归收敛幅度 (%)",
        min_value=0.20,
        max_value=3.00,
        value=0.75,
        step=0.05,
        format="%.2f",
        help="例如开仓基差为 0.90%，在收敛至 0.15% 时平仓，价差收敛获利为 0.75%"
    )
with calc_col3:
    calc_leverage = st.slider(
        "质押杠杆倍数 (Leverage)",
        min_value=1,
        max_value=5,
        value=3,
        help="高流动性主流币建议使用 2x~3x 安全杠杆，大幅提高资金利用率"
    )

fee_drag = 0.0010  # 0.10% 双边综合开平摩擦
net_gain_pct = (calc_basis_reversion / 100.0) - fee_drag
profit_per_trade = calc_capital * max(0.0, net_gain_pct)
margin_required = (calc_capital / calc_leverage) * 2.0
roe_per_trade = (profit_per_trade / margin_required * 100.0) if margin_required > 0 else 0.0

m1, m2, m3, m4 = st.columns(4)
m1.metric("单笔交易净收益", f"+${profit_per_trade:.2f} USDT", f"净基差收敛 +{net_gain_pct*100:.2f}%")
m2.metric("单笔真实保证金回报率 (ROE)", f"+{roe_per_trade:.2f} %", f"基于 {calc_leverage}x 杠杆质押")
m3.metric("若每天捕获 2~3 笔脉冲", f"+${profit_per_trade*2.5:.2f} / 天", "资金快速释放再入场")
m4.metric("双边实缴总保证金占用", f"${margin_required:,.2f} USDT", f"两所各押 ${margin_required/2:,.2f}")

st.write("---")

# ==================== 3. Live Cross-Exchange Basis Radar ====================
st.subheader("📡 实时跨所基差脉冲雷达 (Live Basis Dislocation Radar)")
st.caption("从 Binance 与 Hyperliquid 深度实时拉取主流高流动性标的盘口，监测偏离脉冲机会。")

top_tokens_default = [
    "BTC", "ETH", "SOL", "BNB", "AVAX", "SUI", "NEAR", "APT", "ADA", "ARB",
    "OP", "TIA", "SEI", "INJ", "STRK", "SAGA", "DYM", "ZK", "MANTA", "AAVE",
    "CRV", "UNI", "ENA", "PENDLE", "LDO", "ONDO", "DYDX", "JTO", "PYTH", "SNX",
    "FET", "RENDER", "IO", "WLD", "KAS", "TAO", "FIL", "EIGEN", "DOGE", "WIF",
    "BOME", "ORDI", "POPCAT", "MEME", "XRP", "LINK", "LTC", "BCH"
]
core_tokens_11 = ["BTC", "ETH", "SOL", "AVAX", "SUI", "DOGE", "NEAR", "APT", "ARB", "OP", "LINK"]

col_radar_btn1, col_radar_btn2, _ = st.columns([1.5, 1.5, 3])
with col_radar_btn1:
    refresh_radar = st.button("🔄 刷新实时基差盘口", use_container_width=True)
with col_radar_btn2:
    adopt_top_btn = st.button("⚡ 采纳基差偏离 TOP 标的", use_container_width=True)

with st.spinner("正在拉取 Binance 与 Hyperliquid 实时行情与费率..."):
    df_radar = scan_cross_exchange_basis_opportunities(target_tokens=top_tokens_default, min_basis_pct=0.0070)

if not df_radar.empty:
    display_df = df_radar[[
        "token", "hl_price", "binance_price", "basis_spread_pct",
        "action", "carry_daily_pct", "status"
    ]].copy()
    display_df["hl_price"] = display_df["hl_price"].apply(lambda x: f"${x:,.4f}")
    display_df["binance_price"] = display_df["binance_price"].apply(lambda x: f"${x:,.4f}")
    display_df["basis_spread_pct"] = display_df["basis_spread_pct"].apply(lambda x: f"{x:+.3%}")
    display_df["carry_daily_pct"] = display_df["carry_daily_pct"].apply(lambda x: f"{x:+.3%}/d")
    display_df.columns = ["标的", "Hyperliquid 价格", "Binance 价格", "实时基差偏离率", "建议对冲方向", "顺风利息预计", "状态"]

    st.dataframe(display_df, use_container_width=True, hide_index=True)
else:
    st.info("暂无数据或网络连接中...")

st.write("---")

# ==================== 4. Core Strategy Configuration Form ====================
st.subheader("⚙️ 基差统计套利参数配置")

form_col1, form_col2 = st.columns(2)

with form_col1:
    st.markdown("##### 1. 基础环境与标的")
    exec_mode = st.radio(
        "执行模式",
        ["🟢 纸面预演 (Dry-Run 仿真撮合，零风险)", "🔴 实盘交易 (Live 实盘真实下单)"],
        index=0,
        help="建议首选 Dry-Run 模式充分跑通基差均值回归节奏与真实滑点"
    )
    is_dry_run = "Dry-Run" in exec_mode

    connectors_input = st.text_input(
        "交易连接器 (Connectors)",
        value="hyperliquid_perpetual,binance_perpetual",
        help="用逗号分隔，首选 Hyperliquid 与 Binance 永续合约"
    )

    col_tok_btn1, col_tok_btn2 = st.columns(2)
    if "selected_basis_tokens" not in st.session_state:
        st.session_state["selected_basis_tokens"] = ", ".join(top_tokens_default)
    with col_tok_btn1:
        if st.button("⚡ 载入 48 精选高弹性标的", use_container_width=True):
            st.session_state["selected_basis_tokens"] = ", ".join(top_tokens_default)
            st.rerun()
    with col_tok_btn2:
        if st.button("🎯 仅载入 11 大核心主流币", use_container_width=True):
            st.session_state["selected_basis_tokens"] = ", ".join(core_tokens_11)
            st.rerun()

    tokens_input = st.text_area(
        "监控高流动性标的池 (Tokens)",
        value=st.session_state["selected_basis_tokens"],
        help="用逗号分隔。强烈建议仅配置主流做市币（严禁配置无做市商的劣质山寨币）"
    )

    if "pos_size_val" not in st.session_state:
        st.session_state["pos_size_val"] = 1500.0

    c_s1, c_s2 = st.columns(2)
    with c_s1:
        if st.button("🧪 影子微额压测 ($15/腿)", use_container_width=True):
            st.session_state["pos_size_val"] = 15.0
            st.rerun()
    with c_s2:
        if st.button("💼 稳健生产配置 ($1,500/腿)", use_container_width=True):
            st.session_state["pos_size_val"] = 1500.0
            st.rerun()

    pos_size_input = st.number_input(
        "单边开仓名义金额 (Quote Asset / USD)",
        min_value=10.0,
        max_value=50000.0,
        value=float(st.session_state["pos_size_val"]),
        step=50.0 if st.session_state["pos_size_val"] >= 100.0 else 5.0,
        help="每个币种在两家交易所分别建立的名义头寸大小。实盘前建议使用 $10~$15 进行影子微额压测"
    )
    st.session_state["pos_size_val"] = float(pos_size_input)

    leverage_input = st.number_input(
        "杠杆倍数 (Leverage)",
        min_value=1,
        max_value=5,
        value=3,
        step=1,
        help="主流币基差套利推荐 2x~3x 杠杆"
    )

    single_quote = float(pos_size_input)
    dual_quote = single_quote * 2.0
    lev_int = int(leverage_input)
    dual_margin = (dual_quote / lev_int) if lev_int > 0 else dual_quote
    four_leg_fee = single_quote * 0.0005 * 4.0

    details_html = f"""<div style="background: rgba(30, 41, 59, 0.45); border-radius: 8px; padding: 10px 14px; margin: 8px 0; border: 1px solid rgba(59, 130, 246, 0.25);">
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
<span style="font-size: 0.78rem; font-weight: 700; color: #94a3b8;">📊 单标的资金占用与对冲规格明细</span>
<span style="background: rgba(16, 185, 129, 0.2); color: #34d399; font-size: 0.72rem; padding: 1px 6px; border-radius: 4px; font-weight: 600;">🪙 严格币数等额 (Delta-Zero)</span>
</div>
<div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: #cbd5e1; flex-wrap: wrap; gap: 6px;">
<span>💰 单边名义货值: <b style="color: #38bdf8;">${single_quote:,.2f}</b></span>
<span>🌐 双边总敞口: <b style="color: #a78bfa;">${dual_quote:,.2f}</b></span>
<span>🛡️ 实缴总保证金: <b style="color: #fbbf24;">${dual_margin:,.2f}</b></span>
<span>💳 4腿预估摩擦: <b style="color: #f87171;">-${four_leg_fee:,.2f}</b></span>
</div>
</div>"""
    if hasattr(st, "html"):
        st.html(details_html)
    else:
        st.markdown(details_html, unsafe_allow_html=True)

with form_col2:
    st.markdown("##### 2. 基差触发与止盈风控参数")

    min_entry_basis_pct = st.number_input(
        "基差偏离入场门槛 (%)",
        min_value=0.20,
        max_value=3.00,
        value=0.70,
        step=0.01,
        format="%.2f",
        help="当两所可成交价差偏离达到此数值时触发展开入场。机构级安全区间为 0.60% ~ 0.80% (60~80 bps)，推荐 0.70% (70 bps) 留足滑点与时延安全垫，专抓高胜率异常脱锚。"
    ) / 100.0

    tp_compression_pct = st.number_input(
        "均值回归止盈压缩阈值 (%)",
        min_value=0.01,
        max_value=0.50,
        value=0.05,
        step=0.01,
        format="%.2f",
        help="当基差重新压缩至此数值以内时（如 0.05% 即 5 bps），判定均值回归完成，立即平仓止盈"
    ) / 100.0

    min_tp_net_pct = st.number_input(
        "综合净收益达标止盈线 (%)",
        min_value=0.10,
        max_value=2.00,
        value=0.20,
        step=0.02,
        format="%.2f",
        help="持仓综合真实净利润率（已扣除 4 腿真实 Taker 手续费并加上已结算资金费）达到此数值直接止盈"
    ) / 100.0

    max_hold_hours = st.number_input(
        "最大持仓时间上限 (小时)",
        min_value=0.5,
        max_value=24.0,
        value=6.0,
        step=0.5,
        help="超过此时限若基差仍未收敛，只要小盈或微亏即主动离场释放资金"
    )

    max_basis_loss_pct = st.number_input(
        "基差恶性走阔硬止损线 (%)",
        min_value=0.50,
        max_value=5.00,
        value=1.50,
        step=0.10,
        format="%.2f",
        help="若两所基差反向撕裂达到此比例且持续确认，双边强制平仓止损防御（不受最低持仓冷静期限制）"
    ) / 100.0

st.write("")
st.markdown("##### 3. 🛡️ 防高频磨损与微观执行风控 (Case 32 机构级防线)")
col_guard1, col_guard2, col_guard3 = st.columns(3)
with col_guard1:
    min_holding_seconds = st.number_input(
        "最低持仓冷静期 (秒)",
        min_value=0,
        max_value=3600,
        value=60,
        step=10,
        help="止盈出场前强制维持的最短持仓时间（秒），杜绝 1~2 秒盘口微波噪音虚假刷单换手。注意：硬止损防爆不受限制！"
    )
    exit_persistence_ticks = st.number_input(
        "止盈信号维持确认 Tick 数",
        min_value=1,
        max_value=10,
        value=2,
        step=1,
        help="止盈条件必须连续满足的 Tick 计数，过滤瞬时盘口毛刺脉冲"
    )

with col_guard2:
    taker_fee_rate_input = st.number_input(
        "预估单腿 Taker 手续费率 (%)",
        min_value=0.01,
        max_value=0.20,
        value=0.05,
        step=0.01,
        format="%.3f",
        help="两所单腿 Taker 手续费估算值（如 0.05% 即 0.0005，双边开平共 4 腿合计约 0.20%）"
    )
    taker_fee_rate = taker_fee_rate_input / 100.0

    settlement_blackout_seconds = st.number_input(
        "资金费结算避让保护窗 (秒)",
        min_value=0,
        max_value=600,
        value=60,
        step=10,
        help="距离交易所离散资金费结算窗口前后的保护秒数，若预期息差倒挂则跳过开仓"
    )

with col_guard3:
    trailing_activation_input = st.number_input(
        "移动止盈激活阈值 (%)",
        min_value=0.10,
        max_value=2.00,
        value=0.30,
        step=0.05,
        format="%.2f",
        help="当持仓综合净利润率达到此比例时激活移动止盈追踪锁"
    )
    trailing_stop_activation_pct = trailing_activation_input / 100.0

    trailing_distance_input = st.number_input(
        "移动止盈回撤回吐线 (%)",
        min_value=0.05,
        max_value=1.00,
        value=0.10,
        step=0.01,
        format="%.2f",
        help="自最高峰值利润回撤达到此比例时，触发移动止盈双边平仓"
    )
    trailing_stop_distance_pct = trailing_distance_input / 100.0

# Build final config dictionary
tokens_list = [t.strip().upper() for t in tokens_input.split(",") if t.strip()]
connectors_list = [c.strip().lower() for c in connectors_input.split(",") if c.strip()]

config_dict = {
    "script_file_name": "v2_basis_stat_arb.py",
    "dry_run": is_dry_run,
    "connectors": connectors_list,
    "tokens": tokens_list,
    "position_size_quote": float(pos_size_input),
    "leverage": int(leverage_input),
    "min_entry_basis_pct": float(min_entry_basis_pct),
    "take_profit_basis_compression_pct": float(tp_compression_pct),
    "min_take_profit_net_pct": float(min_tp_net_pct),
    "max_basis_loss_pct": float(max_basis_loss_pct),
    "max_hold_hours": float(max_hold_hours),
    "entry_persistence_ticks": 2,
    "stop_loss_confirmation_ticks": 5,
    "reentry_cooldown_seconds": 600,
    "trailing_stop_activation_pct": float(trailing_stop_activation_pct),
    "trailing_stop_distance_pct": float(trailing_stop_distance_pct),
    "require_favorable_funding": False,
    "max_adverse_daily_funding_pct": 0.0010,
    "max_orderbook_depth_ratio": 0.20,
    "min_holding_seconds": int(min_holding_seconds),
    "exit_persistence_ticks": int(exit_persistence_ticks),
    "taker_fee_rate": float(taker_fee_rate),
    "settlement_blackout_seconds": int(settlement_blackout_seconds),
}

st.write("---")

# ==================== 5. Preview & Save / Deploy ====================
st.subheader("📝 配置文件预览与保存")

yaml_preview = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)
st.code(yaml_preview, language="yaml")

col_save1, col_save2 = st.columns([2, 1])
with col_save1:
    config_file_name = st.text_input(
        "配置文件名",
        value="basis_stat_arb_hyperliquid_binance_dry_run.yml",
        help="将保存至 bots/conf/scripts/ 目录下，并自动按需同步至匹配实例目录"
    )
with col_save2:
    st.write("")
    st.write("")
    save_btn = st.button("💾 保存基差套利配置", use_container_width=True, type="primary")

if save_btn:
    if not config_file_name.endswith(".yml") and not config_file_name.endswith(".yaml"):
        config_file_name += ".yml"

    save_paths = [
        os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/conf/scripts/{config_file_name}"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), f"../../../../hummingbot-api/bots/conf/scripts/{config_file_name}")),
    ]

    # Rule 3: Check and sync to matching active instance directories
    import glob
    instance_pattern = os.path.abspath(f"/home/neo/projects/hummingbot/hummingbot-api/bots/instances/*/conf/scripts/{config_file_name}")
    matched_instances = glob.glob(instance_pattern)
    for inst_conf in matched_instances:
        if inst_conf not in save_paths:
            save_paths.append(inst_conf)

    saved_count = 0
    saved_locations = []
    for p in save_paths:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
            saved_count += 1
            saved_locations.append(p)
        except Exception as e:
            pass

    if saved_count > 0:
        st.success(f"✅ 配置已成功保存并同步至 {saved_count} 处路径：\n" + "\n".join([f"- `{loc}`" for loc in saved_locations]))
    else:
        st.error(f"❌ 无法保存配置文件，请检查目录权限。")
