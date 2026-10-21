"""Display engine-reported discovery state without inventing timing or limits."""
import math

import streamlit as st

from frontend.components.timezone_utils import format_timestamp


def poll_interval_text(discovery):
    try:
        value = float(discovery.get('interval_seconds'))
        if not math.isfinite(value) or value <= 0:
            return '未上报'
    except (TypeError, ValueError):
        return '未上报'
    if value < 60:
        return f'{value:g} 秒'
    return f'{value / 60:g} 分钟'


def render_discovery_status(arb_data, active_tz):
    rotation = arb_data.get('rotation_status', {})
    discovery = rotation.get('discovery', {})
    enabled = discovery.get('enabled', rotation.get('auto_rotation_enabled'))
    phase = {'ready': '本轮完成', 'scanning': '扫描中', 'waiting': '等待数据',
             'unavailable': '扫描异常'}.get(discovery.get('phase'), '未上报')
    if discovery.get('rejections', {}).get('stale metadata') and phase == '本轮完成':
        phase = '扫描完成，但存在过期行情'
    if enabled is False:
        phase = '已暂停'
    st.markdown(f'**标的池自动发现与轮换**：`{phase}`')
    stamp = discovery.get('last_scan_at')
    last = format_timestamp(stamp, active_tz, '%H:%M:%S') if stamp else '未上报'
    st.caption(f'扫描调度间隔：{poll_interval_text(discovery)} · 最近一轮开始：{last}。'
               '间隔不是单轮耗时；上一轮未结束时不会重叠启动。')
    st.caption(f"共同市场：{discovery.get('shared_markets', '未上报')} · "
               f"本轮初筛候选：{discovery.get('candidate_count', '未上报')} · "
               f"本轮池外探测：{discovery.get('attempted_probes', '未上报')} · "
               f"本轮观察池变更：{discovery.get('changes', '未上报')}")
    refresh = arb_data.get('audit', {}).get('data_refresh', {})
    duration = refresh.get('last_duration_seconds')
    if isinstance(duration, (float, int)) and math.isfinite(duration) and duration >= 0:
        st.caption(f'最近一次公共费率数据刷新耗时：{duration:.2f} 秒。'
                   '这是数据拉取耗时，整轮扫描与候选盘口验证总耗时尚未上报。')
    if discovery.get('last_error'):
        st.warning(f"最近扫描异常：{discovery['last_error']}")
    if discovery.get('cleanup_pending'):
        st.warning('有候选订阅等待清理，清理完成前暂停扩大观察池。')
    decisions = discovery.get('recent_decisions', [])
    if decisions:
        with st.expander('最近候选探测与准入记录', expanded=False):
            rows = []
            for row in decisions:
                reason = row.get('reason', '')
                if reason == 'full round-trip payback exceeds limit':
                    reason = '完整开平仓成本预计回本时间超过上限'
                rows.append({'标的': row.get('token'),
                             '结果': '已接入' if row.get('decision') == 'admitted' else '拒绝接入',
                             '原因': reason,
                             '时间': format_timestamp(row['timestamp'], active_tz) if row.get('timestamp') else '未上报'})
            st.dataframe(rows, hide_index=True)


def no_rotation_message(rotation):
    enabled = rotation.get('discovery', {}).get('enabled', rotation.get('auto_rotation_enabled'))
    if enabled is False:
        return '暂无成功的自动轮换记录；自动扫描当前已暂停。'
    return ('暂无成功的自动轮换记录。扫描及被拒绝的候选不会计为换币事件；'
            '只有通过双所盘口、完整成本、预算及替换条件检查，才会变更观察池。')
