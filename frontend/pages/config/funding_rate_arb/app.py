import os
import re
import yaml
import pandas as pd
import streamlit as st

from frontend.components.funding_scanner import scan_cross_exchange_opportunities
from frontend.st_utils import get_backend_api_client, initialize_st_page

# Initialize the Streamlit page
initialize_st_page(title="资金费率套利配置器 (Funding Rate Arb)", icon="💰", layout="wide")

# Get backend API client
backend_api_client = get_backend_api_client()

# ==================== 1. Educational Header & Banner ====================
st.markdown("""
### 💰 永续合约资金费率套利 (Funding Rate Arbitrage)
> **无需拼毫秒延迟、完全免疫币价涨跌的 Delta-Neutral（绝对中性）纯吃利息策略**
""")

with st.expander("📘 点击展开：为什么这是普通个人交易者最理想的套利策略？", expanded=False):
    c_info1, c_info2 = st.columns(2)
    with c_info1:
        st.markdown("""
        **1. 零币价涨跌风险（Delta-Neutral）**
        * 机器人在 **交易所 A 开多（Long）**，同时在 **交易所 B 开等额空单（Short）**。
        * 无论代币暴涨 10 倍还是暴跌 90%，双边盈亏完全互相抵消，净持仓始终为 0。
        
        **2. 收益来源：每 8 小时稳拿利息**
        * 加密货币永续合约每 1 小时或 8 小时由多头向空头（或反之）支付资金费。
        * 机器人监测两所费率差，多空对冲后坐收利差，历史年化通常在 **15% ~ 50%**。
        """)
    with c_info2:
        st.markdown("""
        **3. 为什么家用普通网络也能跑？**
        * 该策略不是高频争抢毫秒订单，持仓往往持续数天甚至数周。
        * 即使家里网络有延迟，交易所里的多空单依然在按时替你收取利息。
        
        **4. 自动止盈与风控闭环**
        * 内置收益达标自动平仓止盈（Take Profit）。
        * 内置费率倒挂保护：一旦利差反转变为负数，自动平仓止损（Stop Loss）。
        """)

st.write("---")

# ==================== 2. Interactive Live APR Calculator ====================
st.subheader("🧮 预期收益与年化利息计算器 (Live APR Calculator)")

calc_col1, calc_col2, calc_col3 = st.columns(3)
with calc_col1:
    calc_capital = st.number_input(
        "单币种名义开仓金额 (USDT)",
        min_value=10.0,
        max_value=100000.0,
        value=200.0,
        step=50.0,
        help="在两家交易所分别建立的多空名义头寸大小"
    )
with calc_col2:
    calc_rate_8h = st.number_input(
        "预估每 8 小时费率利差 (%)",
        min_value=0.001,
        max_value=2.0,
        value=0.05,
        step=0.01,
        format="%.3f",
        help="通常主流币费率为 0.01%~0.03%，热门山寨币（如 WIF/DOGE）在行情活跃时费率可达 0.05%~0.2%"
    )
with calc_col3:
    calc_leverage = st.slider(
        "使用杠杆倍数 (Leverage)",
        min_value=1,
        max_value=10,
        value=3,
        help="套利策略由于多空完全对冲，通常使用 2x~5x 杠杆以提高资金利用率。切勿使用极端高杠杆防插针！"
    )

# Math calculation
rate_8h_decimal = calc_rate_8h / 100.0
daily_rate = rate_8h_decimal * 3.0
annual_apr = daily_rate * 365.0 * 100.0

daily_income = calc_capital * daily_rate
monthly_income = daily_income * 30.0
margin_required = (calc_capital / calc_leverage) * 2  # 两边各开一份保证金

m1, m2, m3, m4 = st.columns(4)
m1.metric("预计单日纯利息", f"{daily_income:.2f} USDT", f"+{daily_rate*100:.2f}% / 天")
m2.metric("预计月度纯利息", f"{monthly_income:.2f} USDT", f"约 {daily_income*30/margin_required*100:.1f}% 保证金收益")
m3.metric("折算年化收益率 (APR)", f"{annual_apr:.2f} %", "纯吃息无币价风险")
m4.metric("所需实际占用保证金", f"{margin_required:.2f} USDT", f"双边合计 ({calc_leverage}x 杠杆)")

st.write("---")

# ==================== 3. Core Strategy Configuration Form ====================
st.subheader("⚙️ 资金费率套利参数配置")

# Step 0: Execution Mode (Dry-Run vs Live)
st.markdown("#### 0. 策略运行模式 (Execution Mode)")
mode_col1, mode_col2 = st.columns([0.45, 0.55])
with mode_col1:
    trading_mode = st.radio(
        "选择策略运行模式",
        options=["纸面预演 (Dry Run - 推荐/零风险)", "实盘交易 (Live Trading - 真实下单)"],
        index=0,
        help="纸面预演：使用交易所 100% 真实实时的盘口深度与资金费率在本地沙盒进行模拟套利与收益结算，绝不向交易所发送真实订单，无需配置 API Key。\n实盘交易：真实向交易所下单开仓，需提前配置好 API Key 或钱包私钥。"
    )
is_dry_run = trading_mode.startswith("纸面预演")

with mode_col2:
    if is_dry_run:
        st.success("""
        🟢 **纸面预演（Dry Run）模式已激活**
        * **实时真实行情**：通过公共 WebSocket/REST 实时监控 Binance 与 Hyperliquid 的深度与资金费率。
        * **沙盒虚拟结算**：命中套利阈值时在本地沙盒虚拟开仓，按各交易所真实结算周期自动结算资金费。
        * **免配密钥**：无需配置 API Key，绝无资金风险。
        """)
    else:
        st.warning("""
        ⚠️ **实盘交易（Live Trading）模式**
        * 机器人将真实调用交易所下单接口。
        * 启动前请确保已在 API / 系统凭证中配置对应交易所的 API 密钥与钱包私钥。
        """)

# Step 1: Connectors selection
st.markdown("#### 1. 永续合约交易所选择 (Perpetual Connectors)")
c_conn1, c_conn2 = st.columns(2)

perpetual_connectors = [
    "hyperliquid_perpetual",
    "binance_perpetual",
    "bybit_perpetual",
    "okx_perpetual",
    "gate_io_perpetual",
    "bitget_perpetual",
    "kucoin_perpetual",
    "binance_perpetual_testnet",
    "hyperliquid_perpetual_testnet",
    "bybit_perpetual_testnet"
]

with c_conn1:
    connector_1 = st.selectbox(
        "交易所 1 (Connector 1)",
        options=perpetual_connectors,
        index=0,
        help="通常推荐选择 Hyperliquid（去中心化低费率）或 Binance"
    )
with c_conn2:
    connector_2 = st.selectbox(
        "交易所 2 (Connector 2)",
        options=perpetual_connectors,
        index=1,
        help="通常推荐选择 Binance 或 Bybit 作为对冲对手所"
    )

if connector_1 == connector_2:
    st.error("⚠️ 两个交易所不能相同！请选择不同的永续合约交易所。")

# Step 2: Live Market Opportunities Scanner & Tokens Selection
st.markdown("#### 2. 全市场实时利差雷达与监控标的 (Market Radar & Tokens Selection)")
st.caption("🚀 **主动雷达巡检**：系统自动并发比对 Binance 与 Hyperliquid 全市场 200+ 个共有合约的最新资金费率，智能找出当前暴利机会，告别盲目死守死等！")

# Scanner cache and fetch
@st.cache_data(ttl=30, show_spinner=False)
def get_cached_opportunities(c1, c2):
    return scan_cross_exchange_opportunities(c1, c2)

with st.spinner("🔍 正在扫描 Binance 与 Hyperliquid 全市场 200+ 标的实时资金费率..."):
    live_opps = get_cached_opportunities(connector_1, connector_2)

top_opp_tokens = [o["coin"] for o in live_opps[:5]] if live_opps else ["2Z", "MON", "SAND", "MOODENG", "YGG"]

# Render Scanner expander / table
with st.expander(f"📡 点击展开：全市场实时利差机会排行榜 (已发现 {len(live_opps)} 个共有标的)", expanded=True):
    col_filter1, col_filter2, col_filter3 = st.columns([2, 1, 1])
    with col_filter1:
        if live_opps:
            st.markdown(f"🔥 当前全市场最高套利机会：**{live_opps[0]['coin']}** (年化 APR **{live_opps[0]['apr']:.2f}%**, 日化 **{live_opps[0]['daily_diff_pct']:+.4f}%/天**)")
        else:
            st.markdown("ℹ️ 暂未获取到全市场费率，请点击刷新。")
    with col_filter2:
        if st.button("⚡ 一键采纳 TOP 5 标的", use_container_width=True, help="一键勾选当前全市场利差最高的前 5 个标的"):
            st.session_state["selected_arb_tokens"] = top_opp_tokens
            st.rerun()
    with col_filter3:
        if st.button("🔄 刷新全市场费率", use_container_width=True, help="强制重新连接交易所 API 拉取最新盘口费率"):
            st.cache_data.clear()
            st.rerun()

    if live_opps:
        # Display top 15 in nice visual dataframe
        radar_df = pd.DataFrame([{
            "排名": f"#{i}",
            "标的代币": o["coin"],
            "折算日化利差": f"{o['daily_diff_pct']:+.4f}% / 天",
            "折算年化 (APR)": f"{o['apr']:+.2f}%",
            "推荐套利组合方向": o["best_path"],
            "现价 ($)": f"${o['price']:.4f}" if o['price'] < 10 else f"${o['price']:.2f}",
            "Hyperliquid 日化": f"{o['connector_1_rate_daily']:+.4f}%",
            "Binance 日化": f"{o['connector_2_rate_daily']:+.4f}%",
        } for i, o in enumerate(live_opps[:15], 1)])
        st.dataframe(radar_df, use_container_width=True, hide_index=True)

# Selection controls
if "selected_arb_tokens" not in st.session_state:
    st.session_state["selected_arb_tokens"] = top_opp_tokens if top_opp_tokens else ["ETH", "SOL", "WIF", "FET"]

all_available_options = [o["coin"] for o in live_opps] if live_opps else ["2Z", "MON", "SAND", "MOODENG", "YGG", "ETH", "BTC", "SOL", "WIF", "FET"]
for t in ["ETH", "BTC", "SOL", "WIF", "FET"]:
    if t not in all_available_options:
        all_available_options.append(t)

c_tok1, c_tok2 = st.columns([0.65, 0.35])
with c_tok1:
    selected_tokens = st.multiselect(
        "选择您认可并希望机器人执行套利的标的代币",
        options=all_available_options,
        default=[t for t in st.session_state["selected_arb_tokens"] if t in all_available_options],
        help="策略会在后台实时扫描这些所选币种。您可以结合上方雷达的年化利差与代币知名度/流动性自主挑选。"
    )
    # Sync session state
    st.session_state["selected_arb_tokens"] = selected_tokens
with c_tok2:
    custom_tokens_input = st.text_input(
        "追加自定义币种 (以逗号分隔)",
        value="",
        placeholder="如: APT,RENDER,ARB",
        help="可选。如果想监控其他币种，请直接在此输入代币简称"
    )

# Combine tokens
final_tokens = list(selected_tokens)
if custom_tokens_input:
    for t in custom_tokens_input.split(","):
        cleaned = t.strip().upper()
        if cleaned and cleaned not in final_tokens:
            final_tokens.append(cleaned)

if not final_tokens:
    st.warning("请至少选择或输入一个监控代币！")

# Step 3: Position, Leverage & Risk Management
st.markdown("#### 3. 仓位大小、杠杆与风控阈值")
c_p1, c_p2, c_p3 = st.columns(3)

with c_p1:
    leverage = st.number_input(
        "杠杆倍数 (Leverage)",
        min_value=1,
        max_value=20,
        value=5,
        step=1,
        help="设置合约的开仓杠杆。建议 2x~5x，安全稳健。"
    )
    position_size_quote = st.number_input(
        "单仓名义价值 (USDT/USD)",
        min_value=10.0,
        max_value=100000.0,
        value=100.0,
        step=50.0,
        help="每个币种套利时建立的单边头寸价值"
    )

with c_p2:
    min_funding_rate_profitability = st.number_input(
        "入场最小日化费率差 (%)",
        min_value=0.01,
        max_value=5.0,
        value=0.10,
        step=0.02,
        format="%.2f",
        help="24小时内两所费率差达到该百分比才开仓。例如 0.10% 对应年化约 36.5%"
    ) / 100.0

    profitability_to_take_profit = st.number_input(
        "达标自动止盈收益率 (%)",
        min_value=0.1,
        max_value=20.0,
        value=1.0,
        step=0.5,
        format="%.1f",
        help="当持仓总收益（已收资金费利息 + 盘口盈亏）达到本仓位的该比例时，自动双边平仓获利落袋"
    ) / 100.0

with c_p3:
    funding_rate_diff_stop_loss = st.number_input(
        "费率倒挂止损阈值 (%)",
        min_value=-5.0,
        max_value=0.0,
        value=-0.10,
        step=0.02,
        format="%.2f",
        help="如果市场行情突变导致两所利差反转并造成亏损，达到该负值时自动双边平仓止损"
    ) / 100.0

    trade_profitability_condition = st.checkbox(
        "开仓时检查盘口滑点与买卖价差",
        value=False,
        help="开启后，只有当开仓时两边的现价没有产生严重负滑点时才开仓，进一步保护利润"
    )

st.write("---")

# ==================== 4. Live YAML Config Generation & Preview ====================
st.subheader("📋 策略配置预览 (Live Configuration Preview)")

config_dict = {
    "script_file_name": "v2_funding_rate_arb.py",
    "dry_run": bool(is_dry_run),
    "connectors": [connector_1, connector_2],
    "tokens": final_tokens,
    "leverage": int(leverage),
    "position_size_quote": float(position_size_quote),
    "min_funding_rate_profitability": float(min_funding_rate_profitability),
    "profitability_to_take_profit": float(profitability_to_take_profit),
    "funding_rate_diff_stop_loss": float(funding_rate_diff_stop_loss),
    "trade_profitability_condition_to_enter": bool(trade_profitability_condition)
}

yaml_preview = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)

c_cfg1, c_cfg2 = st.columns([0.6, 0.4])
with c_cfg1:
    st.code(yaml_preview, language="yaml")

with c_cfg2:
    st.markdown("#### ⚙️ 配置文件命名与保存")
    mode_suffix = "_dry_run" if is_dry_run else ""
    default_cfg_name = f"funding_arb_{connector_1.split('_')[0]}_{connector_2.split('_')[0]}{mode_suffix}"
    user_config_name = st.text_input(
        "配置文件名 (Config Name)",
        value=default_cfg_name,
        help="仅限英文字母、数字、下划线和短横线"
    )

    # Sanitize config name
    clean_config_name = re.sub(r'[^A-Za-z0-9_-]', '_', user_config_name)
    if clean_config_name != user_config_name:
        st.info(f"已自动规范化文件名: `{clean_config_name}`")

    save_btn = st.button("💾 保存策略配置到系统", use_container_width=True, type="primary")

    if save_btn:
        if connector_1 == connector_2:
            st.error("无法保存：两所不能相同！")
        elif not final_tokens:
            st.error("无法保存：未选择监控代币！")
        else:
            try:
                # 1. Save via Backend API (Backend API manages conf/scripts inside Docker)
                backend_api_client.scripts.create_or_update_script_config(
                    config_name=clean_config_name,
                    config=config_dict
                )
                
                # 2. Best-effort local file sync if directory is writable
                local_scripts_conf = os.path.join(os.path.dirname(__file__), "../../../../../hummingbot-api/bots/conf/scripts")
                if os.path.exists(local_scripts_conf):
                    try:
                        with open(os.path.join(local_scripts_conf, f"{clean_config_name}.yml"), "w") as f:
                            yaml.dump(config_dict, f, default_flow_style=False)
                    except Exception:
                        pass  # Backend API already successfully persisted the file

                st.success(f"🎉 成功保存配置文件: `{clean_config_name}.yml`！")
                st.session_state["saved_funding_cfg"] = clean_config_name
            except Exception as e:
                st.error(f"保存失败: {e}")

st.write("---")

# ==================== 5. One-Click Bot Deployment ====================
st.subheader("🚀 一键部署并启动资金费率套利机器人")

# Fetch credentials profiles from API
try:
    available_credentials = backend_api_client.accounts.list_accounts()
except Exception:
    available_credentials = ["master_account"]

active_saved_cfg = st.session_state.get("saved_funding_cfg", clean_config_name)

if is_dry_run:
    st.info("💡 **纸面预演（Dry Run）部署提示**：启动机器人无需在凭证中配置 API Key，系统将自动连接交易所公开接口进行实时监控与沙盒模拟，无任何资金损失风险。")

dep_col1, dep_col2, dep_col3 = st.columns(3)
with dep_col1:
    default_bot_name = "funding-arb-dryrun" if is_dry_run else "funding-arb-bot"
    bot_name = st.text_input(
        "机器人实例名称 (Instance Name)",
        value=default_bot_name,
        help="部署后的容器与任务名称"
    )
with dep_col2:
    selected_cred = st.selectbox(
        "选择交易凭证配置 (Credentials Profile)",
        options=available_credentials if available_credentials else ["master_account"],
        help="包含交易所 API Key 的凭证环境"
    )
with dep_col3:
    image_name = st.text_input(
        "Hummingbot 容器镜像",
        value="hummingbot/hummingbot:latest",
        help="使用的 Docker 镜像版本"
    )

deploy_btn = st.button("🚀 立即部署资金费率套利机器人", use_container_width=True)

if deploy_btn:
    clean_bot_name = re.sub(r'[^A-Za-z0-9_-]', '_', bot_name)
    if not clean_bot_name:
        st.error("请输入有效的机器人实例名称！")
    else:
        with st.spinner(f"正在部署机器人 {clean_bot_name} 并注入配置 {active_saved_cfg}..."):
            try:
                # 确保配置已保存
                backend_api_client.scripts.create_or_update_script_config(
                    config_name=active_saved_cfg,
                    config=config_dict
                )
                
                # 调用 deploy_v2_script，确保 script_config 带有 .yml 后缀
                cfg_with_ext = active_saved_cfg if active_saved_cfg.endswith((".yml", ".yaml")) else f"{active_saved_cfg}.yml"
                deploy_res = backend_api_client.bot_orchestration.deploy_v2_script(
                    instance_name=clean_bot_name,
                    credentials_profile=selected_cred,
                    script="v2_funding_rate_arb",
                    script_config=cfg_with_ext,
                    image=image_name
                )
                
                if deploy_res.get("success"):
                    unique_name = deploy_res.get("unique_instance_name", clean_bot_name)
                    st.success(f"🎉 机器人部署成功！实例名称: `{unique_name}`")
                    st.balloons()
                    st.markdown("""
                    👉 **下一步：** 
                    * 可以在左侧导航栏的 **[Instances 实例管理](/instances)** 中查看机器人的实时运行状态、日志和利息收益！
                    """)
                else:
                    st.error(f"部署返回异常: {deploy_res}")
            except Exception as e:
                st.error(f"部署机器人失败: {e}")
