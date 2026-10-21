"""Explicit settings for new paper instances; does not edit running bots."""
import streamlit as st

from frontend.components.funding_contract import complete_config,config_errors


def render_discovery_config(values):
    settings=complete_config(values)
    with st.expander('动态选币、确认与持仓预算',expanded=False):
        st.caption('此页创建新实例配置，不会热修改正在运行的模拟盘。保存结果以 YAML 预览为准。')
        settings['auto_rotation_enabled']=st.checkbox('自动发现与轮换观察标的',value=True)
        interval=st.number_input('全市场扫描调度间隔（秒；不是单轮耗时）',min_value=15.0,max_value=3600.0,value=30.0,step=15.0)
        settings['auto_rotation_interval_minutes']=interval/60
        settings['auto_rotation_max_active_tokens']=st.number_input('观察池上限',min_value=1,max_value=30,value=12,step=1)
        settings['max_concurrent_positions']=st.number_input('最多同时持仓组数',min_value=1,max_value=30,value=3,step=1)
        settings['min_position_size_quote']=st.number_input('最小单边开仓金额',min_value=1.0,max_value=float(values['position_size_quote']),value=min(100.0,float(values['position_size_quote'])),step=10.0)
        settings['scaling_tp_enabled']=st.checkbox('启用分批止盈（默认关闭）',value=False)
        settings['asset_graduation_enabled']=st.checkbox('启用黑名单自动资质复核（默认关闭）',value=False)
        st.caption('入场还需要 3 份独立双所费率快照、最短跨度 30 秒。默认每轮最多探测 3 个池外标的、变更 2 次观察池；换币不等于开仓。')
    return settings,config_errors(settings)
