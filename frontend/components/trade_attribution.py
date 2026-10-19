"""
Trade Attribution & Post-Mortem Engine for Funding Arbitrage
历史平仓成交记录深度归因与复盘引擎

核心功能：
1. 为什么会盈利？（资金费红利驱动 vs 价差收敛红利 vs 综合止盈）
2. 为什么会亏损？（费率倒挂防抽水止损 vs 强平安全垫防御 vs 摩擦未回本）
3. 持续了多长时间？（天/时/分 精确周期追踪与结算频次）
4. 真实财务口径对账（实缴本金真实 ROE、双腿对冲检验、手续费摩擦与官方行情复盘直达）
"""

from typing import Dict, Any, List
from frontend.components.timezone_utils import format_timestamp
from frontend.components.market_links import get_official_market_url, get_token_research_urls

def analyze_trade_attribution(trade: Dict[str, Any], tz_label: str = "北京时间", active_tz: str = "Asia/Shanghai") -> Dict[str, Any]:
    """
    对单笔历史平仓单进行量化和定性归因分析
    """
    token = trade.get("token", "UNKNOWN")
    c1 = trade.get("connector_1", "binance_perpetual")
    c2 = trade.get("connector_2", "hyperliquid_perpetual")
    close_reason = trade.get("close_reason", "Completed")
    
    open_ts = int(trade.get("open_timestamp", 0))
    close_ts = int(trade.get("close_timestamp", 0))
    dur_sec = max(0, close_ts - open_ts) if (close_ts > 0 and open_ts > 0) else 0
    
    # 格式化持仓时长
    days = dur_sec // 86400
    hours = (dur_sec % 86400) // 3600
    minutes = (dur_sec % 3600) // 60
    if days > 0:
        dur_str = f"{days}天 {hours}小时 {minutes}分"
    elif hours > 0:
        dur_str = f"{hours}小时 {minutes}分"
    else:
        dur_str = f"{minutes}分钟" if minutes > 0 else f"{dur_sec}秒"

    trading_pnl = float(trade.get("trading_pnl", 0.0))
    funding_pnl = float(trade.get("funding_pnl", 0.0))
    total_net_pnl = float(trade.get("total_net_pnl", trading_pnl + funding_pnl))
    
    pos_size = float(trade.get("position_size", 10000.0))
    leverage = int(trade.get("leverage", 3))
    # 双边实缴保证金总额 = 2 * 单边名义货值 / 杠杆
    real_margin = (2.0 * pos_size) / leverage if leverage > 0 else pos_size
    roe_pct = (total_net_pnl / real_margin) * 100.0 if real_margin > 0 else 0.0
    
    dur_days = max(dur_sec / 86400.0, 1.0 / 24.0)
    annualized_apr = (roe_pct / dur_days) * 365.0

    is_win = total_net_pnl >= 0

    # 深度因果剖析与归因标签
    if is_win:
        if funding_pnl > 0 and (funding_pnl >= abs(trading_pnl) or trading_pnl <= 0):
            attr_tag = "💰 资金费丰厚覆盖摩擦获利"
            summary_reason = f"【{token}】利息现金流不仅完全消化了开平仓滑点与手续费，还锁定了稳健正收益。"
            detail_explanation = (
                f"【资金费红利驱动】：在 **{token}** 持仓的 {dur_str} 期间，策略稳定收割资金费现金流，"
                f"累计净入账 +${funding_pnl:,.4f}。该利息现金流完全覆盖并超越了平仓价差损耗及手续费摩擦（-${abs(trading_pnl):,.4f}），"
                f"最终实现净利润 +${total_net_pnl:,.4f}（实缴本金 ROE: {roe_pct:+.2f}%）。"
            )
        elif trading_pnl > 0:
            attr_tag = "📈 基差/价差收窄超额红利"
            summary_reason = f"【{token}】双边盘口价差收敛带来超额盘口利润，提前触发策略止盈。"
            extra_funding_str = f"，期间亦收取利息 +${funding_pnl:,.4f}" if funding_pnl > 0 else ""
            detail_explanation = (
                f"【价差收敛红利驱动】：在 **{token}** 持仓期间，{c1} 与 {c2} 之间的盘口价差向有利方向收窄（基差均值回归），"
                f"额外贡献了 +${trading_pnl:,.4f} 的盘口基差红利{extra_funding_str}，"
                f"成功触发并超越设定的止盈线（Take Profit），机器人主动在最佳深度锁定平仓（净赚 +${total_net_pnl:,.4f}，ROE: {roe_pct:+.2f}%）。"
            )
        else:
            attr_tag = "🎯 综合达标止盈获利"
            summary_reason = f"【{token}】总收益率达到预设目标，策略自动双边对冲平仓落袋。"
            detail_explanation = (
                f"【止盈线达标退出】：标的 **{token}** 累计净收益达到策略设定的止盈阈值，"
                f"机器人自动以最优市价挂单平仓，锁定净利润 +${total_net_pnl:,.4f}。"
            )
    else:
        if "Rate Inversion" in close_reason or "费率持续倒挂止损" in close_reason:
            attr_tag = "🛑 费率持续倒挂滤波止损"
            summary_reason = f"【{token}】利差持续倒挂经多周期待定滤波确认，策略平仓杜绝反向抽水。"
            detail_explanation = (
                f"【费率倒挂确认拦截】：策略实时监控到 **{token}** 在两交易所间的日化利差倒挂跌破止损阈值，"
                f"经多周期迟滞滤波确认排除瞬间盘口噪音后坚决止损，平仓净结算 -${abs(total_net_pnl):,.4f}，"
                f"成功切断利差持续倒挂抽水并进入重入冷却期。"
            )
        elif "Stop Loss" in close_reason:
            attr_tag = "🛑 费率倒挂主动止损"
            summary_reason = f"【{token}】利差恶化跌破安全红线，策略坚决平仓杜绝反向抽水。"
            detail_explanation = (
                f"【费率反抽风控拦截】：策略实时监控到 **{token}** 在两交易所间的日化利差恶化并跌破止损阈值（日化利差低于安全线），"
                f"系统坚决触发主动对冲止损。虽然双边平仓承担了点差与手续费摩擦 -${abs(trading_pnl):,.4f}（持仓期间回收资金费 +${funding_pnl:,.4f}，"
                f"净结算 -${abs(total_net_pnl):,.4f}），但【坚决杜绝了利差倒挂导致本金被持续反向抽水】，完美履行风控铁律。"
            )
        elif "Liquidation Defense" in close_reason:
            attr_tag = "🛡️ 强平安全垫防御退出"
            summary_reason = f"【{token}】单腿浮亏逼近强平线（安全垫 ≤ 8%），主动平仓保全本金。"
            detail_explanation = (
                f"【强平防御保全本金】：标的 **{token}** 市场突发极端单边脉冲，单腿保证金安全垫跌破防御警戒线（≤ 8%）。"
                f"系统果断执行双边主动平仓（净损益 -${abs(total_net_pnl):,.4f}），成功防范了单腿爆仓与穿仓破产的致命风险。"
            )
        elif "Rotation" in close_reason or "Ditch" in close_reason:
            attr_tag = "🔄 低产出标的主动轮换"
            summary_reason = f"【{token}】标的利差衰退，主动释放资金轮换至全市场更高日息标的。"
            detail_explanation = (
                f"【机会成本优化退出】：标的 **{token}** 利差衰减至低产出区间，系统主动平仓释放占用保证金（净损益 ${total_net_pnl:+.4f}），"
                f"以便集中资金轮动至全市场日息更高（≥ 0.12%/天）的优质品种。"
            )
        else:
            attr_tag = "⏳ 摩擦成本未完全覆盖"
            summary_reason = f"【{token}】持仓周期较短，利息尚未完全抵消开平仓摩擦即离场。"
            detail_explanation = (
                f"【摩擦回本未满退出】：标的 **{token}** 持仓历时仅 {dur_str}，收取的资金费利息 +${funding_pnl:,.4f} 尚未完全覆盖双边开平仓的点差滑点与手续费损耗（-${abs(trading_pnl):,.4f}），"
                f"最终产生小额净磨损 -${abs(total_net_pnl):,.4f}。"
            )

    open_dt_str = format_timestamp(open_ts, active_tz)
    close_dt_str = format_timestamp(close_ts, active_tz)
    
    c1_url = get_official_market_url(c1, token)
    c2_url = get_official_market_url(c2, token)
    res_urls = get_token_research_urls(token)

    return {
        "token": token,
        "connector_1": c1,
        "connector_2": c2,
        "is_win": is_win,
        "close_reason": close_reason,
        "attr_tag": attr_tag,
        "summary_reason": summary_reason,
        "detail_explanation": detail_explanation,
        "open_ts": open_ts,
        "close_ts": close_ts,
        "open_dt_str": open_dt_str,
        "close_dt_str": close_dt_str,
        "dur_sec": dur_sec,
        "dur_str": dur_str,
        "trading_pnl": trading_pnl,
        "funding_pnl": funding_pnl,
        "total_net_pnl": total_net_pnl,
        "pos_size": pos_size,
        "leverage": leverage,
        "real_margin": real_margin,
        "roe_pct": roe_pct,
        "annualized_apr": annualized_apr,
        "c1_url": c1_url,
        "c2_url": c2_url,
        "tradingview_url": res_urls["tradingview"],
        "coinglass_url": res_urls["coinglass"],
        "coingecko_url": res_urls["coingecko"],
    }

def calculate_closed_summary(analyzed_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    计算所有历史平仓单的综合汇总指标（胜率、利息总计、价差摩擦、平均时长）
    """
    if not analyzed_trades:
        return {
            "total_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate_pct": 0.0,
            "total_net_pnl": 0.0,
            "total_funding_pnl": 0.0,
            "total_trading_pnl": 0.0,
            "avg_dur_str": "0m",
            "avg_roe_pct": 0.0,
        }
    
    total_count = len(analyzed_trades)
    win_count = sum(1 for t in analyzed_trades if t["is_win"])
    loss_count = total_count - win_count
    win_rate = (win_count / total_count) * 100.0
    
    total_net = sum(t["total_net_pnl"] for t in analyzed_trades)
    total_funding = sum(t["funding_pnl"] for t in analyzed_trades)
    total_trading = sum(t["trading_pnl"] for t in analyzed_trades)
    
    total_dur_sec = sum(t["dur_sec"] for t in analyzed_trades)
    avg_dur_sec = total_dur_sec // total_count if total_count > 0 else 0
    avg_h = avg_dur_sec // 3600
    avg_m = (avg_dur_sec % 3600) // 60
    avg_dur_str = f"{avg_h}h {avg_m}m" if avg_h > 0 else f"{avg_m}m"
    
    avg_roe = sum(t["roe_pct"] for t in analyzed_trades) / total_count if total_count > 0 else 0.0

    return {
        "total_count": total_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate_pct": win_rate,
        "total_net_pnl": total_net,
        "total_funding_pnl": total_funding,
        "total_trading_pnl": total_trading,
        "avg_dur_str": avg_dur_str,
        "avg_roe_pct": avg_roe,
    }
