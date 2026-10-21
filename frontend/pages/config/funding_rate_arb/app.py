import os
import re
import uuid
import yaml
import pandas as pd
import streamlit as st

from frontend.components.funding_scanner import scan_cross_exchange_opportunities
from frontend.components.funding_config_ui import render_discovery_config
from frontend.components.funding_contract import store_new_config
from frontend.components.timezone_utils import DEFAULT_TIMEZONE, TIMEZONE_CHOICES
from frontend.st_utils import get_backend_api_client, initialize_st_page

# Initialize the Streamlit page
initialize_st_page(title="资金费率套利配置器 (Funding Rate Arb)", icon="💰", layout="wide")

# Get backend API client
backend_api_client = get_backend_api_client()

st.markdown("### 跨交易所永续资金费差：模拟配置")
st.caption("匹配两腿数量可减少方向敞口，仍会承担基差变化、资金费反转、手续费、单腿保证金与数据中断风险。预测费差不是已实现收益。")

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
        help="用于演算的周期化费差假设；不代表交易所当前费率或已确认收入。"
    )
with calc_col3:
    calc_leverage = st.slider(
        "使用杠杆倍数 (Leverage)",
        min_value=1,
        max_value=3,
        value=1,
        help="这里只计算保证金，不改变资金费率或固定名义头寸的毛收入；单腿仍有强平风险。"
    )

# Math calculation
rate_8h_decimal = calc_rate_8h / 100.0
daily_rate = rate_8h_decimal * 3.0
annual_apr = daily_rate * 365.0 * 100.0

daily_income = calc_capital * daily_rate
monthly_income = daily_income * 30.0
margin_required = (calc_capital / calc_leverage) * 2  # 两边各开一份保证金

m1, m2, m3, m4 = st.columns(4)
m1.metric("假设日毛资金费", f"{daily_income:.2f} USDT", f"+{daily_rate*100:.2f}% / 天")
m2.metric("假设30天毛资金费", f"{monthly_income:.2f} USDT", f"约 {daily_income*30/margin_required*100:.1f}% 保证金收益")
m3.metric("折算年化收益率 (APR)", f"{annual_apr:.2f} %", "按当前假设费差简单年化，未扣成本")
m4.metric("所需实际占用保证金", f"{margin_required:.2f} USDT", f"双边合计 ({calc_leverage}x 杠杆)")

st.caption("此计算器假设双方费差均已折算至同一 8 小时周期，且费差保持不变。实际按各合约周期、持仓方向和历史费率结算；不是净收益预测。")
st.write("---")

# ==================== 3. Core Strategy Configuration Form ====================
st.subheader("⚙️ 资金费率套利参数配置")

# Step 0: Execution Mode (Dry-Run vs Live)
st.markdown("#### 0. 策略运行模式 (Execution Mode)")
mode_col1, mode_col2 = st.columns([0.45, 0.55])
with mode_col1:
    trading_mode = st.radio(
        "选择策略运行模式",
        options=["纸面预演 (Dry Run - 审计版)"],
        index=0,
        help="本次审计版仅接受模拟交易。盘口成交与资金费金额仍是模型估算；真实双腿成交、故障补偿与账户对账验收通过后才可开放实盘。"
    )
is_dry_run = trading_mode.startswith("纸面预演")

with mode_col2:
    if is_dry_run:
        st.success("""
        🟢 **纸面预演（Dry Run）模式已激活**
        * **实时真实行情**：通过公共 WebSocket/REST 实时监控 Binance 与 Hyperliquid 的深度与资金费率。
        * **沙盒虚拟结算**：命中套利阈值时在本地沙盒虚拟开仓，按各币种结算周期查询已公布的历史费率；参考价格缺失时明确标记金额估算。
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
        help="本轮资金费结算审计覆盖 Binance USDT 与 Hyperliquid USD"
    )

if connector_1 == connector_2:
    st.error("⚠️ 两个交易所不能相同！请选择不同的永续合约交易所。")

# Step 2: Live Market Opportunities Scanner & Tokens Selection
st.markdown("#### 2. 全市场实时利差雷达与监控标的 (Market Radar & Tokens Selection)")
st.caption("此处为独立公开费率初筛，仅辅助选择启动币；其过滤条件和刷新时刻与运行引擎不同，不代表已通过盘口、完整成本与预算验证。")

# Scanner cache and fetch
@st.cache_data(ttl=30, show_spinner=False)
def get_cached_opportunities(c1, c2):
    return scan_cross_exchange_opportunities(c1, c2)

with st.spinner("🔍 正在扫描 Binance 与 Hyperliquid 实时资金费率..."):
    live_opps = get_cached_opportunities(connector_1, connector_2) if connector_1 != connector_2 else []

top_opp_tokens = [o["coin"] for o in live_opps[:5]] if live_opps else ["BTC", "ETH", "SOL"]

# Render Scanner expander / table
with st.expander(f"📡 点击展开：全市场实时利差机会排行榜 (已发现 {len(live_opps)} 个共有标的)", expanded=True):
    col_filter1, col_filter2, col_filter3 = st.columns([2, 1, 1])
    with col_filter1:
        if live_opps:
            st.markdown(f"当前公开初筛的最高预测毛费差：**{live_opps[0]['coin']}** (年化 APR **{live_opps[0]['apr']:.2f}%**, 日化 **{live_opps[0]['daily_diff_pct']:+.4f}%/天**)")
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
        # Display top 15 in nice visual dataframe with liquidity metrics
        radar_df = pd.DataFrame([{
            "排名": f"#{i}",
            "标的代币": o.get("display_name", o["coin"]),
            "折算年化 (APR)": f"{o['apr']:+.2f}%",
            "折算日化利差": f"{o['daily_diff_pct']:+.4f}% / 天",
            "流动性评级": o.get("liquidity_badge", "🟡 适中"),
            "盘口容量结论": o.get("safe_cap", "未测算"),
            "HL 24h成交量": f"${o.get('hl_vol_24h', 0):,.0f}",
            "BN 24h成交量": f"${o.get('bn_vol_24h', 0):,.0f}",
            "推荐套利组合方向": o["best_path"],
            "现价 ($)": f"${o['price']:.4f}" if o['price'] < 10 else f"${o['price']:.2f}",
        } for i, o in enumerate(live_opps[:15], 1)])
        st.dataframe(radar_df, use_container_width=True, hide_index=True)

        st.caption("雷达做了名称/乘数和价格偏差初筛，不能证明资产身份或安全交易容量。运行引擎还会执行更严格的成交量、基差、真实盘口与完整费用约束。")


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

paper_account_balance_quote = st.number_input("模拟账户总资金 (USD 等值；两所各分配一半)", min_value=100.0, value=10000.0, step=100.0)
paper_margin_reserve_pct = st.slider("各交易所保留资金比例", min_value=0.0, max_value=0.8, value=0.25, step=0.05)
st.caption("此资金仅用于模拟预算。USDT/USD 按 1:1 估值，未模拟脱锚、排队成交和网络延迟。")

# Step 3: Position, Leverage & Risk Management
st.markdown("#### 3. 仓位大小、杠杆与风控阈值")
c_p1, c_p2, c_p3 = st.columns(3)

with c_p1:
    leverage = st.number_input(
        "目标杠杆倍数 (Target Leverage)",
        min_value=1,
        max_value=3,
        value=1,
        step=1,
        help="设置模拟永续仓位杠杆（1~3x）。即使 1x，空头单腿也存在强平风险，两所保证金不能自动互相补充。"
    )
    min_leverage_capital_threshold = st.number_input(
        "小额免杠杆安全线 ($)",
        min_value=0.0,
        max_value=5000.0,
        value=500.0,
        step=50.0,
        help="小额仓位采用 1x；仍需独立检查两所保证金和空头风险。"
    )
    position_size_quote = st.number_input(
        "单仓名义价值 (USDT/USD)",
        min_value=100.0,
        max_value=100000.0,
        value=500.0,
        step=50.0,
        help="每个币种套利时建立的单边头寸价值"
    )


with c_p2:
    min_funding_rate_profitability = st.number_input(
        "入场最小日化费率差 (%)",
        min_value=0.01,
        max_value=5.0,
        value=0.12,
        step=0.02,
        format="%.2f",
        help="24小时内两所费率差达到该百分比才开仓。例如 0.10% 对应年化约 36.5%"
    ) / 100.0

    profitability_to_take_profit = st.number_input(
        "净损益止盈门槛（占单边参考名义，%）",
        min_value=0.1,
        max_value=20.0,
        value=2.0,
        step=0.5,
        format="%.1f",
        help="按后台净损益/单边参考名义金额判断；含两腿入场与预计退出费用。这不是双腿保证金 ROE。"
    ) / 100.0

with c_p3:
    funding_rate_diff_stop_loss = st.number_input(
        "费率倒挂止损阈值（日比例，%）",
        min_value=-5.0,
        max_value=-0.10,
        value=-0.10,
        step=0.02,
        format="%.2f",
        help="如果市场行情突变导致两所利差反转并造成亏损，达到该负值时自动双边平仓止损"
    ) / 100.0

    trade_profitability_condition = st.checkbox(
        "额外要求即刻往返净损益非负",
        value=False,
        help="无论此开关状态，盘口、完整费用、基差、回本与容量检查始终执行。开启后还要求立即往返净损益非负，可能导致长期无交易。"
    )

    dynamic_position_sizing = st.checkbox(
        "🛡️ 盘口流动性自适应动态控仓 (推荐)",
        value=True,
        help="开启后，策略将实时根据币种在两所的盘口挂单深度动态计算安全仓位（最高不超过目标名义价值），防止小币种吃穿盘口造成滑点损失。"
    )

    selected_timezone = st.selectbox(
        "🌐 页面显示时区（不写入策略 YAML）",
        options=list(TIMEZONE_CHOICES.keys()),
        index=0,  # "Asia/Shanghai" by default!
        format_func=lambda k: TIMEZONE_CHOICES.get(k, k),
        help="只影响前端时间展示。交易所结算周期和引擎时间戳不因此改变。"
    )

st.session_state["selected_timezone"] = selected_timezone

with st.expander("🛡️ 高级风控与防抖防护盾 (Institutional Risk Shields)", expanded=False):
    c_r1, c_r2, c_r3 = st.columns(3)
    with c_r1:
        max_payback_hours = st.number_input(
            "回本周期硬拦截上限 (小时)",
            min_value=1.0,
            max_value=12.0,
            value=12.0,
            step=1.0,
            help="两腿完整往返手续费、盘口与不利基差压力成本按当前预测资金费计算的回本小时数，超过该阈值坚决不开仓（默认 ≤ 12h）。"
        )
        max_entry_spread_friction_pct = st.number_input(
            "完整开平仓摩擦比例上限（%）",
            min_value=0.05,
            max_value=3.0,
            value=0.50,
            step=0.05,
            format="%.2f",
            help="无论资金费率多诱人，完整往返盘口、费用与基差压力成本超过此百分比坚决不开仓，防止踩入流动性陷阱（默认 ≤ 0.5%）。"
        ) / 100.0
    with c_r2:
        single_loss_quarantine_threshold = st.number_input(
            "单笔亏损熔断阈值 (USDT)",
            min_value=5.0,
            max_value=500.0,
            value=15.0,
            step=5.0,
            help="单笔交易若亏损超过此金额，该代币立即被关进禁闭室（隔离冷却），杜绝连续失血。"
        )
        token_quarantine_hours = st.number_input(
            "毒币隔离冷却时长 (小时)",
            min_value=1.0,
            max_value=168.0,
            value=24.0,
            step=6.0,
            help="触发熔断的代币在多少小时内完全禁止再次开仓（默认 24h）。"
        )
    with c_r3:
        rate_inversion_confirmation_ticks = st.number_input(
            "费率倒挂防假止损确认周期 (Ticks)",
            min_value=1,
            max_value=10,
            value=3,
            step=1,
            help="利差倒挂必须连续维持 N 个心跳周期（默认 3 秒）才触发止损，过滤 1 秒毛刺噪音与误平仓。"
        )
        reentry_cooldown_seconds = st.number_input(
            "平仓重入防抖冷却 (秒)",
            min_value=60,
            max_value=7200,
            value=1800,
            step=300,
            help="平仓后至少等待多少秒才允许再次开仓相同代币（默认 1800 秒 / 30分钟），杜绝频繁来回磨损。"
        )

st.write("---")

# ==================== 4. Live YAML Config Generation & Preview ====================
st.subheader("📋 新实例配置预览（不会改动运行中实例）")

config_dict = {
    "script_file_name": "v2_funding_rate_arb.py",
    "dry_run": True,
    "paper_account_balance_quote": float(paper_account_balance_quote),
    "paper_margin_reserve_pct": float(paper_margin_reserve_pct),
    "connectors": [connector_1, connector_2],
    "tokens": final_tokens,
    "leverage": int(leverage),
    "min_leverage_capital_threshold": float(min_leverage_capital_threshold),
    "position_size_quote": float(position_size_quote),
    "dynamic_position_sizing": bool(dynamic_position_sizing),
    "max_orderbook_depth_ratio": 0.15,
    "min_funding_rate_profitability": float(min_funding_rate_profitability),
    "profitability_to_take_profit": float(profitability_to_take_profit),
    "funding_rate_diff_stop_loss": float(funding_rate_diff_stop_loss),
    "trade_profitability_condition_to_enter": bool(trade_profitability_condition),
    "max_payback_hours": float(max_payback_hours),
    "max_entry_spread_friction_pct": float(max_entry_spread_friction_pct),
    "token_quarantine_hours": float(token_quarantine_hours),
    "single_loss_quarantine_threshold": float(single_loss_quarantine_threshold),
    "rate_inversion_confirmation_ticks": int(rate_inversion_confirmation_ticks),
    "reentry_cooldown_seconds": int(reentry_cooldown_seconds),
}

config_dict, validation_errors = render_discovery_config(config_dict)
for validation_error in validation_errors:
    st.error(validation_error)
yaml_preview = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)

c_cfg1, c_cfg2 = st.columns([0.6, 0.4])
with c_cfg1:
    st.code(yaml_preview, language="yaml")

with c_cfg2:
    st.markdown("#### ⚙️ 配置文件命名与保存")
    mode_suffix = "_dry_run" if is_dry_run else ""
    if 'funding_config_suffix' not in st.session_state:
        st.session_state['funding_config_suffix'] = uuid.uuid4().hex[:8]
    default_cfg_name = f"funding_arb_{connector_1.split('_')[0]}_{connector_2.split('_')[0]}{mode_suffix}_{st.session_state['funding_config_suffix']}"
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
        if validation_errors or not clean_config_name:
            st.error('配置无效，请先修正上面的参数及文件名。')
        elif connector_1 == connector_2:
            st.error("无法保存：两所不能相同！")
        elif not final_tokens:
            st.error("无法保存：未选择监控代币！")
        else:
            try:
                # 1. Save via Backend API (Backend API manages conf/scripts inside Docker)
                store_new_config(backend_api_client.scripts, clean_config_name, config_dict)
                
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

active_saved_cfg = clean_config_name  # Deploy current preview under its current name, not a stale saved name.

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
        options=sorted(available_credentials, key=lambda name: name != "funding-paper-public") if available_credentials else ["master_account"],
        help="优先使用 funding-paper-public；策略使用公开行情，不需要交易所密钥。"
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
    if validation_errors or not active_saved_cfg:
        st.error('配置无效，未保存或部署。请修正参数及文件名。')
    elif not clean_bot_name:
        st.error("请输入有效的机器人实例名称！")
    else:
        with st.spinner(f"正在部署机器人 {clean_bot_name} 并注入配置 {active_saved_cfg}..."):
            try:
                # 确保配置已保存
                store_new_config(backend_api_client.scripts, active_saved_cfg, config_dict)
                
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
