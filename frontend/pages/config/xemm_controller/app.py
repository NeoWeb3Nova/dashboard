import re
import plotly.graph_objects as go
import streamlit as st
import yaml

from frontend.st_utils import get_backend_api_client, initialize_st_page

# Initialize the Streamlit page
initialize_st_page(title="XEMM 跨交易所做市控制器配置", icon="⚡️")

# Page introduction & guide
st.markdown("""
### ⚡️ XEMM (Cross-Exchange Market Making) 控制器配置
跨交易所做市是一种**市场中性套利策略**：在 Maker 交易所挂被动限价单（等待被吃），成交后瞬间在流动性更深的 Taker 交易所市价吃单平仓，锁定两端价差利润。
""")

with st.expander("📖 点击展开：XEMM 核心概念与风控逻辑说明", expanded=False):
    st.markdown("""
    - **Maker 交易所（挂单端）**：挂限价单（Limit Order）等待外部对手盘成交，通常选择手续费便宜或利差较大的市场。
    - **Taker 交易所（对冲端）**：Maker 订单一旦成交，机器人**毫秒级市价吃单（Market Order）**平仓对冲，消除单边持仓波动风险。
    - **Target Profitability（目标毛利率 %）**：挂单相较于 Taker 现价拉开的价差百分比。例如 0.3% 表示赚取千分之三价差。
    - **Min Profitability（撤单保护线 %）**：当市场行情剧烈反向运动，导致当前挂单预期利润跌穿此线时，机器人**立即撤单止损**。
    - **Max Profitability（追单重挂线 %）**：当行情剧烈顺向运动导致价差过大时，机器人撤销远离盘口的单子，向前靠近重挂以提高成交概率。
    """)

st.write("---")

# 1. Trading Environment Selection
st.subheader("1. 运行模式选择")
mode_col, _ = st.columns([2, 1])
with mode_col:
    trading_mode = st.radio(
        "选择运行环境",
        ["🧪 模拟盘 (Paper Trade) - 零资金无风险演练", "🚀 实盘 (Live Trading) - 需配置交易所 API Key"],
        index=0,
        horizontal=True,
        help="模拟盘使用内置虚拟撮合引擎，无需提供交易所真实秘钥；实盘需要提前在 Backend API 配置 API Key。"
    )

is_paper_trade = "模拟盘" in trading_mode

# Available connectors presets
POPULAR_PAPER_CONNECTORS = [
    "kucoin_paper_trade",
    "binance_paper_trade",
    "gate_io_paper_trade",
    "okx_paper_trade",
    "bybit_paper_trade",
    "mexc_paper_trade",
    "自定义输入..."
]

POPULAR_LIVE_CONNECTORS = [
    "kucoin",
    "binance",
    "okx",
    "bybit",
    "gate_io",
    "mexc",
    "kraken",
    "coinbase_advanced_trade",
    "自定义输入..."
]

connector_options = POPULAR_PAPER_CONNECTORS if is_paper_trade else POPULAR_LIVE_CONNECTORS
default_maker_idx = 0
default_taker_idx = 1 if len(connector_options) > 1 else 0

# Popular trading pairs
POPULAR_PAIRS = [
    "ETH-USDT",
    "BTC-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "DOGE-USDT",
    "XRP-USDT",
    "PEPE-USDT",
    "自定义输入..."
]

st.write("---")
# 2. Exchange & Trading Pair Selection
st.subheader("2. 交易所与交易对")
c1, c2 = st.columns(2)

with c1:
    st.markdown("#### 🏢 Maker 端（挂单方）")
    maker_choice = st.selectbox(
        "Maker 交易所",
        connector_options,
        index=default_maker_idx,
        help="在这里发布被动限价挂单，等待被外部市场吃单。"
    )
    if maker_choice == "自定义输入...":
        maker_connector = st.text_input("输入 Maker Connector 名称", value="kucoin_paper_trade" if is_paper_trade else "kucoin")
    else:
        maker_connector = maker_choice

    maker_pair_choice = st.selectbox(
        "Maker 交易对",
        POPULAR_PAIRS,
        index=0,
        help="格式为 BASE-QUOTE，例如 ETH-USDT。"
    )
    if maker_pair_choice == "自定义输入...":
        maker_trading_pair = st.text_input("输入 Maker 交易对", value="ETH-USDT").upper().strip()
    else:
        maker_trading_pair = maker_pair_choice

with c2:
    st.markdown("#### 🛡️ Taker 端（对冲方）")
    taker_choice = st.selectbox(
        "Taker 交易所",
        connector_options,
        index=default_taker_idx,
        help="Maker 端挂单一旦成交，在此处立即市价对冲平仓。建议选择流动性最好的大交易所（如 Binance）。"
    )
    if taker_choice == "自定义输入...":
        taker_connector = st.text_input("输入 Taker Connector 名称", value="binance_paper_trade" if is_paper_trade else "binance")
    else:
        taker_connector = taker_choice

    sync_pairs = st.checkbox("Taker 与 Maker 保持相同交易对", value=True)
    if sync_pairs:
        taker_trading_pair = maker_trading_pair
        st.text_input("Taker 交易对 (已联动)", value=maker_trading_pair, disabled=True)
    else:
        taker_pair_choice = st.selectbox(
            "Taker 交易对",
            POPULAR_PAIRS,
            index=0,
            help="跨币对对冲时使用，常规套利请保持相同。"
        )
        if taker_pair_choice == "自定义输入...":
            taker_trading_pair = st.text_input("输入 Taker 交易对", value="ETH-USDT").upper().strip()
        else:
            taker_trading_pair = taker_pair_choice

st.write("---")

# 3. Profitability & Risk Control Thresholds
st.subheader("3. 收益目标与风控保护阈值")
p1, p2 = st.columns(2)

with p1:
    min_profit_pct = st.number_input(
        "🛡️ 撤单保护线 Min Profitability (%)",
        value=0.15,
        min_value=0.01,
        max_value=10.0,
        step=0.01,
        format="%.2f",
        help="止损阈值：当行情波动导致当前预估利润低于该百分比时，立即撤销 Maker 挂单，防止被砸穿亏损。"
    )

with p2:
    max_profit_pct = st.number_input(
        "🎯 追单上限 Max Profitability (%)",
        value=0.80,
        min_value=0.05,
        max_value=20.0,
        step=0.01,
        format="%.2f",
        help="追单阈值：当行情远离挂单导致预估利润超过此上限时，撤单并靠近现价重新挂单，增加成交机会。"
    )

min_profitability = min_profit_pct / 100.0
max_profitability = max_profit_pct / 100.0

# 4. Multi-Level Grids
st.write("---")
st.subheader("4. 多阶梯挂单网格设置")

grid1, grid2 = st.columns(2)

with grid1:
    st.markdown("#### 🟢 买单阶梯设置 (Buy Maker)")
    buy_maker_levels = st.number_input("买单档位数", min_value=1, max_value=5, value=1, step=1)
    buy_targets_amounts = []
    for i in range(buy_maker_levels):
        b_c1, b_c2 = st.columns([1, 1])
        with b_c1:
            default_target = round(0.30 + i * 0.20, 2)
            t_prof = st.number_input(
                f"买阶梯 {i+1} 目标毛利 (%)",
                value=float(default_target),
                min_value=0.01,
                max_value=20.0,
                step=0.05,
                format="%.2f",
                key=f"buy_prof_{i}"
            )
        with b_c2:
            amt = st.number_input(
                f"买阶梯 {i+1} 挂单金额 (USDT)",
                value=10.0 + i * 5.0,
                min_value=1.0,
                max_value=100000.0,
                step=5.0,
                key=f"buy_amt_{i}"
            )
        buy_targets_amounts.append([t_prof / 100.0, amt])

with grid2:
    st.markdown("#### 🔴 卖单阶梯设置 (Sell Maker)")
    sell_maker_levels = st.number_input("卖单档位数", min_value=1, max_value=5, value=1, step=1)
    sell_targets_amounts = []
    for i in range(sell_maker_levels):
        s_c1, s_c2 = st.columns([1, 1])
        with s_c1:
            default_target = round(0.30 + i * 0.20, 2)
            t_prof = st.number_input(
                f"卖阶梯 {i+1} 目标毛利 (%)",
                value=float(default_target),
                min_value=0.01,
                max_value=20.0,
                step=0.05,
                format="%.2f",
                key=f"sell_prof_{i}"
            )
        with s_c2:
            amt = st.number_input(
                f"卖阶梯 {i+1} 挂单金额 (USDT)",
                value=10.0 + i * 5.0,
                min_value=1.0,
                max_value=100000.0,
                step=5.0,
                key=f"sell_amt_{i}"
            )
        sell_targets_amounts.append([t_prof / 100.0, amt])

# Validation alert
has_validation_error = False
for i, (t_p, _) in enumerate(buy_targets_amounts):
    if t_p <= min_profitability:
        st.error(f"❌ 买阶梯 {i+1} 目标收益 ({t_p*100:.2f}%) 必须大于撤单保护线 ({min_profit_pct:.2f}%)，否则挂单会瞬间被系统撤销！")
        has_validation_error = True
    if t_p >= max_profitability:
        st.error(f"❌ 买阶梯 {i+1} 目标收益 ({t_p*100:.2f}%) 必须小于追单上限 ({max_profit_pct:.2f}%)，否则挂单无法正常生成！")
        has_validation_error = True

for i, (t_p, _) in enumerate(sell_targets_amounts):
    if t_p <= min_profitability:
        st.error(f"❌ 卖阶梯 {i+1} 目标收益 ({t_p*100:.2f}%) 必须大于撤单保护线 ({min_profit_pct:.2f}%)！")
        has_validation_error = True
    if t_p >= max_profitability:
        st.error(f"❌ 卖阶梯 {i+1} 目标收益 ({t_p*100:.2f}%) 必须小于追单上限 ({max_profit_pct:.2f}%)！")
        has_validation_error = True


# 5. Interactive Chart & Simulation
st.write("---")
st.subheader("5. 挂单深度与收益测算模拟")

sim_col, chart_col = st.columns([1, 1])

with sim_col:
    st.markdown("#### 💡 实时挂单测算卡片")
    sim_price = st.number_input(
        f"输入参考现价 (USDT, 以 {maker_trading_pair} 为例)",
        value=2450.0,
        step=10.0,
        format="%.2f",
        help="模拟在当前市场基准价格下，机器人会挂在什么价位、锁定的理论利润是多少。"
    )

    first_buy_profit = buy_targets_amounts[0][0]
    first_buy_amt = buy_targets_amounts[0][1]
    est_buy_price = sim_price * (1.0 - first_buy_profit)
    est_buy_cancel_price = sim_price * (1.0 - min_profitability)

    first_sell_profit = sell_targets_amounts[0][0]
    first_sell_amt = sell_targets_amounts[0][1]
    est_sell_price = sim_price * (1.0 + first_sell_profit)
    est_sell_cancel_price = sim_price * (1.0 + min_profitability)

    st.info(f"""
    **买单套利模拟 (Level 1)**:
    - 🟢 在 `{maker_connector}` 挂买单价：**${est_buy_price:.2f}** (金额: {first_buy_amt:.1f} USDT)
    - ⚡️ 成交后在 `{taker_connector}` 市价卖出：**${sim_price:.2f}**
    - 💰 锁定毛利空间：**+{first_buy_profit*100:.2f}%** (约 ${(sim_price - est_buy_price)*(first_buy_amt/sim_price):.3f} USDT)
    - 🛡️ 撤单保护触发点：当 Taker 跌破 **${est_buy_cancel_price:.2f}** 时自动撤销买单
    """)

    st.warning(f"""
    **卖单套利模拟 (Level 1)**:
    - 🔴 在 `{maker_connector}` 挂卖单价：**${est_sell_price:.2f}** (金额: {first_sell_amt:.1f} USDT)
    - ⚡️ 成交后在 `{taker_connector}` 市价买入：**${sim_price:.2f}**
    - 💰 锁定毛利空间：**+{first_sell_profit*100:.2f}%** (约 ${(est_sell_price - sim_price)*(first_sell_amt/sim_price):.3f} USDT)
    - 🛡️ 撤单保护触发点：当 Taker 涨破 **${est_sell_cancel_price:.2f}** 时自动撤销卖单
    """)

with chart_col:
    st.markdown("#### 📊 阶梯收益率与保护线分布")
    fig = go.Figure()

    # Buy targets
    b_x = [t[0] * 100 for t in buy_targets_amounts]
    b_y = [t[1] for t in buy_targets_amounts]
    b_labels = [f"买 L{i+1}: {x:.2f}%" for i, x in enumerate(b_x)]
    fig.add_trace(go.Bar(
        x=b_labels,
        y=b_y,
        name="买单阶梯 (USDT)",
        marker=dict(color="#00CC96")
    ))

    # Sell targets
    s_x = [t[0] * 100 for t in sell_targets_amounts]
    s_y = [t[1] for t in sell_targets_amounts]
    s_labels = [f"卖 L{i+1}: {x:.2f}%" for i, x in enumerate(s_x)]
    fig.add_trace(go.Bar(
        x=s_labels,
        y=s_y,
        name="卖单阶梯 (USDT)",
        marker=dict(color="#EF553B")
    ))

    fig.update_layout(
        title="阶梯挂单金额与预期毛利分布",
        yaxis_title="挂单金额 (USDT)",
        xaxis_title="阶梯档位",
        height=380,
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig, use_container_width=True)


# 6. Configuration Naming & One-Click Upload
st.write("---")
st.subheader("6. 配置命名与一键上传")

# Clean coin name from trading pair (e.g. ETH-USDT -> eth)
base_coin = maker_trading_pair.split("-")[0].lower() if "-" in maker_trading_pair else "asset"
default_base_name = f"xemm-{maker_connector}-{taker_connector}-{base_coin}"

n1, n2 = st.columns([3, 1])
with n1:
    config_base = st.text_input("配置基础名称 (Config Base)", value=default_base_name, help="只能包含英文字母、数字、下划线和连字符。")
with n2:
    config_tag = st.text_input("版本标签 (Tag)", value="v1", help="版本后缀，如 v1, v2 等。切勿包含小数点句号！")

# Sanitize config id: strictly alphanumeric, underscore, hyphen
raw_id = f"{config_base}_{config_tag}".lower()
sanitized_id = re.sub(r"[^a-z0-9_-]", "_", raw_id)

is_id_valid = bool(re.match(r"^[a-z0-9_-]+$", sanitized_id))

if is_id_valid:
    st.success(f"✅ 生成的标准配置唯一 ID: `{sanitized_id}`")
else:
    st.error(f"❌ 配置 ID 不合法 (`{sanitized_id}`)，只允许字母、数字、下划线和连字符。")

# Assemble the final dictionary for the controller
controller_config_payload = {
    "id": sanitized_id,
    "controller_name": "xemm_multiple_levels",
    "controller_type": "generic",
    "maker_connector": maker_connector,
    "maker_trading_pair": maker_trading_pair,
    "taker_connector": taker_connector,
    "taker_trading_pair": taker_trading_pair,
    "min_profitability": min_profitability,
    "max_profitability": max_profitability,
    "buy_levels_targets_amount": buy_targets_amounts,
    "sell_levels_targets_amount": sell_targets_amounts
}

with st.expander("📄 展开查看生成的标准 YAML 配置文件"):
    st.code(yaml.dump(controller_config_payload, default_flow_style=False, sort_keys=False), language="yaml")

# Upload button
can_upload = is_id_valid and not has_validation_error

if st.button("🚀 上传配置到 Hummingbot-API", disabled=not can_upload, type="primary", use_container_width=True):
    backend_api_client = get_backend_api_client()
    try:
        backend_api_client.controllers.create_or_update_controller_config(
            config_name=sanitized_id,
            config=controller_config_payload
        )
        st.success(f"🎉 配置 `{sanitized_id}` 已成功上传到后端服务！")
        st.info("""
        **下一步操作指南**：
        1. 前往左侧导航栏：**Orchestration ➡️ Instances**（实例管理）。
        2. 找到您的实例或点击 **Launch Bot**，选择脚本 `v2_with_controllers.py`。
        3. 在控制器列表中勾选刚才创建的 `""" + sanitized_id + """`。
        4. 点击 **Deploy**，机器人即会以此全新配置运行做市套利！
        """)
    except Exception as e:
        st.error(f"上传失败，错误信息: {e}")
