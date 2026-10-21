"""Render ledger-backed risk, financial and data-quality information."""
import time
import streamlit as st

from frontend.components.funding_contract import load_instance_config,remaining_seconds,gross_notional,legs
from frontend.components.timezone_utils import format_timestamp


def render_data_status(data):
    status=data.get('_ui',{})
    if status.get('available') is False:
        st.warning('尚未取得有效账本；空白列表不代表空仓或零盈亏。')
        return False
    if status.get('stale'):
        st.warning(f"当前展示的是旧快照（距更新 {status['age_seconds']:.0f} 秒），不能当作实时持仓估值；控制请求已禁用。")
    if status.get('source'):
        st.caption(f"数据来源：{status['source']} · 距账本更新 {status['age_seconds']:.0f} 秒。")
    pending=[p for p in data.get('active_positions',[])+data.get('closed_trades',[]) if p.get('funding_pending')]
    estimated=sum(bool(p.get('amount_estimated')) for r in data.get('active_positions',[])+data.get('closed_trades',[]) for p in r.get('funding_payments',[]))
    if pending:st.info(f'有 {len(pending)} 条持仓/平仓记录仍待补记资金费，净损益可能继续修订。')
    if estimated:st.caption(f'有 {estimated} 笔资金费金额使用估算参考价；历史费率已公布不等于取得真实账户收款证明。')
    return True


def render_risk_status(data,bot_name,send_command):
    now=time.time();blacklist=data.get('permanent_blacklist',[])
    quarantine={t:r for t,r in data.get('quarantined_tokens',{}).items() if (remaining_seconds(r,now,'quarantine_until') or 0)>0}
    cooldown={t:r for t,r in data.get('post_tp_lockout',{}).items() if (remaining_seconds(r,now,'lockout_until') or 0)>0}
    probation=data.get('probationary_tokens',{})
    count=len(set(blacklist)|set(quarantine)|set(cooldown)|set(probation))
    enabled=data.get('graduation_status',{}).get('asset_graduation_enabled')
    with st.expander(f'风控名单与冷却状态（{count} 个标的）',expanded=bool(count)):
        st.caption('下表来自当前账本；黑名单阈值和冷却时长以部署配置及实际到期时间为准。')
        st.caption('自动资质复核：'+('已启用' if enabled is True else '已关闭' if enabled is False else '未上报'))
        if enabled and st.button('请求资质复核',key=f'grad_audit_{bot_name}'):
            if send_command(bot_name,{'action':'trigger_graduation_audit'}):st.info('复核请求已写入，等待引擎处理；尚未确认复核结果。')
            else:st.warning('请求未写入：请检查快照新鲜度、功能状态或待处理请求。')
        if blacklist:
            st.write('当前黑名单：'+', '.join(blacklist))
            for token in blacklist:
                if st.button(f'请求解除 {token} 黑名单',key=f'unbl_{token}_{bot_name}'):
                    if send_command(bot_name,{'action':'unblacklist_token','token':token}):st.info('解除请求已写入，等待下一次账本确认；暂未确认已解除。')
                    else:st.warning('请求未写入：数据可能过期，或已有待处理请求。')
        rows=[]
        for kind,items,key in [('风险隔离',quarantine,'quarantine_until'),('止盈后冷却',cooldown,'lockout_until')]:
            for token,row in items.items():
                seconds=remaining_seconds(row,now,key)
                rows.append({'标的':token,'限制':kind,'剩余分钟':round(seconds/60,1),'原因':row.get('reason','账本未记录原因')})
        if rows:st.dataframe(rows,hide_index=True)
        if probation:
            st.dataframe([{'标的':t,'探针目标金额':p.get('probe_size'),
                           '是否已尝试探针':p.get('probe_executed',False),'记录原因':p.get('reason','未记录')}
                          for t,p in probation.items()],hide_index=True)
        if not count:st.write('当前没有生效的黑名单、隔离或冷却记录。')


def render_execution_diagnostics(data,bot_name,active_tz):
    st.markdown('##### 运行约束与成交模型核对')
    config=load_instance_config(bot_name)
    if config:
        st.caption('以下是该实例磁盘中的部署配置，不是其他模板或新建配置页的默认值。动态观察名单以当前账本为准。')
        labels={'position_size_quote':'单边目标金额','min_position_size_quote':'最小开仓金额',
                'max_concurrent_positions':'最多持仓组数','max_payback_hours':'完整成本回本上限（小时）',
                'max_orderbook_depth_ratio':'最大盘口占用比例','liquidation_buffer_pct':'基础强平防御比例',
                'max_entry_spread_pct':'最大入场基差比例','max_entry_spread_friction_pct':'最大完整往返摩擦比例',
                'funding_rate_diff_stop_loss':'倒挂阈值（日比例）','auto_rotation_interval_minutes':'扫描间隔（分钟）',
                'funding_signal_min_snapshots':'独立费率快照数','funding_signal_min_seconds':'确认最短跨度（秒）',
                'scaling_tp_enabled':'分批止盈','asset_graduation_enabled':'自动资质复核'}
        st.dataframe([{'参数':label,'配置值':str(config[key]) if key in config else '未显式配置，不能从此文件确认默认值'}
                      for key,label in labels.items()],hide_index=True)
    else:st.info('无法读取唯一的实例部署配置；不使用模板默认值冒充实际参数。')
    st.caption('安全容量由两所开仓和退出方向的实际盘口共同约束。24h 成交额不能替代盘口，也不能据此给出安全下单额度。')
    market=data.get('market_comparison',[])
    if market:
        st.dataframe([{'标的':m['token'],'预测日费差（%）':m.get('daily_rate_diff_pct'),
                       '候选方向（做多→做空）':m.get('best_path','无有效费差')}
                      for m in market],hide_index=True)
    positions=data.get('active_positions',[])
    if positions:
        rows=[]
        for p in positions:
            row={'标的':p['token'],'估值状态':p.get('valuation_status','未上报'),
                 '双腿入场名义合计':gross_notional(p),'入场手续费':p.get('entry_fee_quote'),
                 '净损益（含模型费用）':p.get('net_pnl'),'按当前持仓方向预测日资金费':p.get('projected_daily_funding_quote')}
            for side,leg in legs(p).items():
                row[side+'合约数量']=leg['quantity'];row[side+'交易所']=leg['connector']
            rows.append(row)
        st.dataframe(rows,hide_index=True)
    else:st.info('暂无持仓；没有真实盘口逐档快照，不能绘制假设深度或虚构可成交金额。')
    rejected=data.get('audit',{}).get('last_rejection',{})
    if rejected:
        with st.expander('各观察标的最近一次入场拒绝原因'):
            st.dataframe([{'标的':t,'拒绝原因':r.get('reason'),'时间':format_timestamp(r['timestamp'],active_tz)}
                          for t,r in rejected.items() if r.get('timestamp')],hide_index=True)
