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
    
    pos_size = float(trade.get("position_size", trade.get("position_size_quote", 0)))
    leverage = int(trade.get("leverage", 3))
    # 双边实缴保证金总额 = 2 * 单边名义货值 / 杠杆
    real_margin = float(trade.get("initial_margin_quote", (2.0 * pos_size) / leverage if leverage > 0 else 0))
    roe_pct = (total_net_pnl / real_margin) * 100.0 if real_margin > 0 else 0.0
    
    dur_days = max(dur_sec / 86400.0, 1.0 / 24.0)
    annualized_apr = (roe_pct / dur_days) * 365.0

    is_win = total_net_pnl > 0

    # A ledger explains arithmetic, not counterfactual causes or guaranteed risk avoidance.
    if funding_pnl > 0 and trading_pnl < 0:
        attr_tag = "资金费收入与交易成本共同影响"
    elif funding_pnl < 0:
        attr_tag = "包含资金费净支出"
    elif trading_pnl > 0:
        attr_tag = "交易价格损益贡献为正"
    else:
        attr_tag = "按账本损益复核"
    if total_net_pnl == 0:
        attr_tag = "账本净损益为零"
    summary_reason = f"记录退出原因：{close_reason}。不能仅凭原因名称推断退出盈利或避免了多少损失。"
    detail_explanation = (
        f"{token} 账本记录的交易损益（含模型计入的费用）为 ${trading_pnl:+,.4f}，"
        f"资金费净额为 ${funding_pnl:+,.4f}，总净损益为 ${total_net_pnl:+,.4f}。"
        "资金费可能为支出；迟到历史、估算参考价格或历史缺项可能影响最终结论。"
    )
    is_legacy_unshielded = trade.get('accounting_quality') == 'legacy_reconstructed'
    if trade.get('funding_pending'):
        detail_explanation += " 本条资金费仍待补记。"

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
        "is_legacy_unshielded": is_legacy_unshielded,
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
