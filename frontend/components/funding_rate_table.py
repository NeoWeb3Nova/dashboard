"""Ledger-only funding comparison. Daily fields already contain percentage points."""
from decimal import Decimal, InvalidOperation
import time

BN = 'Binance 日化费率 (%)'
HL = 'Hyperliquid 日化费率 (%)'
SPREAD = '预测毛利差 (%/天)'
ENGINE = '引擎上报利差 (%/天)'
APR = '毛利差简单年化 (%)'


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def funding_rate_rows(data, now=None):
    """Reconcile reported rates without guessing periods or replacing missing values by zero."""
    now = time.time() if now is None else now
    threshold = number(data.get('min_funding_rate_profitability_pct'))
    if threshold is None:
        ratio = number(data.get('min_funding_rate_profitability'))
        threshold = ratio * 100 if ratio is not None else None
    active = {p.get('token') for p in data.get('active_positions', [])}
    blacklisted = set(data.get('permanent_blacklist', []))
    audit = data.get('audit', {})
    rows = []
    for market in data.get('market_comparison', []):
        token = market.get('token', '')
        bn = number(market.get('binance_perpetual_rate_daily'))
        hl = number(market.get('hyperliquid_perpetual_rate_daily'))
        reported = number(market.get('daily_rate_diff_pct'))
        spread = abs(bn-hl) if bn is not None and hl is not None else None
        long_venue = short_venue = None
        check = '一致'
        if spread is None:
            check = '双所费率不完整，无法计算'
        elif reported is not None and abs(reported-spread) > Decimal('0.00000001'):
            check = '引擎利差与双所费率不一致'
        elif reported is None:
            check = '双所费率相同，利差为零' if spread == 0 else '缺少引擎利差，仅展示重算值'
        if spread is not None and spread > 0:
            long_venue = 'Binance' if bn < hl else 'Hyperliquid'
            short_venue = 'Hyperliquid' if bn < hl else 'Binance'
        status = '未达到费差门槛'
        if spread is None or '不一致' in check or '缺少' in check:
            status = '数据待核对'
        elif data.get('_ui', {}).get('stale') or audit.get('data_refresh', {}).get('last_error'):
            status = '费率数据待刷新'
        elif token in active:
            status = '已有持仓（本表方向为当前最优预测）'
        elif token in blacklisted:
            status = '黑名单限制'
        elif any((number(data.get(field, {}).get(token, {}).get(key)) or 0) > now
                 for field, key in [('quarantined_tokens', 'quarantine_until'),
                                    ('post_tp_lockout', 'lockout_until')]):
            status = '隔离或冷却中'
        elif any(token not in state.get('books_ready', []) or token not in state.get('rules_ready', [])
                 for state in audit.get('public_connectors', {}).values()):
            status = '盘口或规则未就绪，不能开仓'
        elif threshold is None or threshold <= 0:
            status = '开仓门槛未确认'
        elif spread >= threshold:
            status = '仅毛费差达标，仍需完整准入'
        reason = audit.get('last_rejection', {}).get(token, {})
        rows.append({
            '币种': token,
            BN: float(bn) if bn is not None else None,
            HL: float(hl) if hl is not None else None,
            SPREAD: float(spread) if spread is not None else None,
            '预测做多': long_venue or ('无方向优势' if spread == 0 else '未确认'),
            '预测做空': short_venue or ('无方向优势' if spread == 0 else '未确认'),
            '入场状态': status,
            ENGINE: float(reported) if reported is not None else None,
            '数据核对': check,
            APR: float(spread*365) if spread is not None else None,
            'Binance 下次结算': market.get('binance_perpetual_next_funding'),
            'Hyperliquid 下次结算': market.get('hyperliquid_perpetual_next_funding'),
            '最近拒绝原因': reason.get('reason', '未记录'),
            '拒绝记录时间': reason.get('timestamp'),
        })
    return sorted(rows, key=lambda row: (row[SPREAD] is None, -(row[SPREAD] or 0), row['币种']))


def render_funding_rate_table(data, bot_name, active_tz):
    import pandas as pd
    import streamlit as st
    from frontend.components.market_links import get_official_market_url, get_token_research_urls
    from frontend.components.timezone_utils import format_timestamp, get_timezone_label

    threshold = number(data.get('min_funding_rate_profitability_pct'))
    if threshold is None:
        ratio = number(data.get('min_funding_rate_profitability'))
        threshold = ratio*100 if ratio is not None else None
    label = f'{threshold:+.4f}% / 天' if threshold is not None else '未上报'
    st.caption(f'ℹ️ 策略后台实时维护的各币种利差对比（毛费差门槛：{label} | 显示时区：{get_timezone_label(active_tz)}）')
    st.caption('两所费率统一折算为日化百分比；并非交易所每期显示值。'
               '预测毛利差 = 做空所日化费率 − 做多所日化费率，未扣四笔交易手续费、滑点和基差损益。')
    refreshed = data.get('audit', {}).get('data_refresh', {}).get('last_success_at')
    stamp = format_timestamp(refreshed, active_tz) if number(refreshed) else '未上报'
    st.caption(f'费率最近成功获取：{stamp}。按当前费率外推，不保证后续结算不变；'
               '年化仅为毛利差×365，不是账户本金回报率。原始每期费率与周期未随该账本上报，不反推或补造。')
    rows = funding_rate_rows(data)
    if not rows:
        st.info('尚无双所费率对比数据。')
        return
    if any(row['入场状态'] == '数据待核对' for row in rows):
        st.warning('部分行数据缺失或不一致；缺失值显示为空，不按零费率或可交易机会处理。')
    for row in rows:
        for field in ['Binance 下次结算', 'Hyperliquid 下次结算', '拒绝记录时间']:
            stamp = number(row[field])
            row[field] = format_timestamp(float(stamp), active_tz) if stamp is not None and stamp > 0 else '未上报'
        row['Binance 官方行情'] = get_official_market_url('binance_perpetual', row['币种'])
        row['Hyperliquid 官方行情'] = get_official_market_url('hyperliquid_perpetual', row['币种'])
        row['代币资料'] = get_token_research_urls(row['币种'])['coinglass']
    config = {column: st.column_config.NumberColumn(column, format='%+.6f')
              for column in [BN, HL, SPREAD, ENGINE, APR]}
    config.update({column: st.column_config.LinkColumn(column, display_text='打开 ↗')
                   for column in ['Binance 官方行情', 'Hyperliquid 官方行情', '代币资料']})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch', column_config=config,
                 key=f'funding_rate_comparison_{bot_name}')
    with st.expander('费率口径与原始快照复核', expanded=False):
        st.write('百分比字段已经乘过100，不再重复放大。正费率通常多头支付、空头收取；'
                 '负费率相反。两所费率相同则没有资金费方向优势。空白的引擎利差与独立重算的零值分别保留。')
        st.json({'updated_at': data.get('updated_at'), 'metadata_last_success_at': refreshed,
                 'market_comparison': data.get('market_comparison', [])})
