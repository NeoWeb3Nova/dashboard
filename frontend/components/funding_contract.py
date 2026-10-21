"""Funding UI contract: preserve source data, units, freshness and request scope."""
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def market_data_issues(data):
    issues=[];audit=data.get('audit',{})
    for name,row in audit.get('public_connectors',{}).items():
        count=row.get('configured_pairs')
        if count is not None and (len(row.get('books_ready',[]))!=count or len(row.get('rules_ready',[]))!=count):
            issues.append(name+'：新鲜盘口或交易规则不齐')
    if 'watchlist' in data and len(data.get('market_comparison',[]))!=len(data['watchlist']):
        issues.append('部分观察标的缺少可用费率元数据')
    discovery=data.get('rotation_status',{}).get('discovery',{})
    if discovery.get('rejections',{}).get('stale metadata',0):
        issues.append('本轮存在过期元数据，扫描完成不代表行情健康')
    if audit.get('data_refresh',{}).get('last_error'):
        issues.append('最近一次公共费率刷新失败：'+str(audit['data_refresh']['last_error']))
    return issues


def instance_dir(bot_name, roots=None):
    if not isinstance(bot_name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', bot_name):
        return None
    if roots is None:
        roots = [Path('/home/neo/projects/hummingbot/hummingbot-api/bots/instances'),
                 Path(__file__).resolve().parents[3]/'hummingbot-api/bots/instances']
    for root in roots:
        root = Path(root).resolve()
        path = (root/bot_name).resolve()
        if path.parent == root and path.is_dir():
            return path
    return None


def funding_payload(value):
    return (isinstance(value, dict) and value.get('strategy') != 'basis_stat_arb'
            and isinstance(value.get('active_positions'), list)
            and isinstance(value.get('closed_trades'), list)
            and isinstance(value.get('market_comparison'), list))


def select_snapshot(bot_name, performance, *, roots=None, now=None):
    now = time.time() if now is None else now
    performance = performance if isinstance(performance, dict) else {}
    if 'basis' in bot_name.lower() or 'basis_stat_arb' in performance:
        return False, None
    rows = []
    ctrl = performance.get('funding_rate_arb', {})
    mqtt = ctrl.get('custom_info') if isinstance(ctrl, dict) else None
    if funding_payload(mqtt):
        rows.append(('MQTT', mqtt))
    directory = instance_dir(bot_name, roots)
    if directory:
        try:
            local = json.loads((directory/'data/arbitrage_trades.json').read_text())
            if funding_payload(local):
                rows.append(('本地账本', local))
        except (OSError, ValueError):
            pass
    known = bool(rows) or 'funding_rate_arb' in performance or 'funding' in bot_name.lower()
    valid = [(source,data) for source,data in rows
             if number(data.get('updated_at')) is not None and number(data['updated_at']) <= now+5]
    if not valid:
        return known, {'active_positions': [],'closed_trades': [],'market_comparison': [],
                       '_ui': {'available':False,'stale':True,'source':'未获取','age_seconds':None}}
    source,data = max(valid,key=lambda row:float(row[1]['updated_at']))
    result = copy.deepcopy(data)
    age = max(0,now-float(data['updated_at']))
    result['_ui'] = {'available':True,'stale':age>20,'source':source,'age_seconds':age}
    return known, result


def legs(position):
    long_i = 1 if position.get('side') == 'BUY' else 2
    result = {}
    for side,i in [('long',long_i),('short',3-long_i)]:
        quantity = number(position.get(f'amount_{i}'))
        entry = number(position.get(f'entry_price_{i}'))
        current = number(position.get(f'current_price_{i}'))
        # Actual quantized contract units; never reconstruct two different quantities from dollar size.
        pnl = None if None in (quantity,entry,current) else (current-entry)*quantity*(1 if side=='long' else -1)
        result[side] = dict(index=i,connector=position.get(f'connector_{i}'),quantity=quantity,
                           entry=entry,current=current,pnl=pnl,next_funding=position.get(f'next_funding_{i}'))
    return result


def gross_notional(position):
    values = [number(position.get(f'amount_{i}')) for i in (1,2)]
    prices = [number(position.get(f'entry_price_{i}',position.get(f'price_{i}'))) for i in (1,2)]
    if any(v is None for v in values+prices):
        size=number(position.get('position_size',position.get('position_size_quote')))
        return 2*size if size is not None else None  # legacy approximation, flagged by caller
    return sum(q*p for q,p in zip(values,prices))


def remaining_seconds(record, now, until_key):
    until=number(record.get(until_key))
    if until is not None:
        return max(0,until-now)
    return number(record.get('remaining_seconds'))


def load_instance_config(bot_name, roots=None):
    import yaml
    directory=instance_dir(bot_name,roots)
    if directory is None:return {}
    matches=[]
    for path in (directory/'conf/scripts').glob('*.y*ml'):
        try:
            data=yaml.safe_load(path.read_text())
            if isinstance(data,dict) and data.get('script_file_name')=='v2_funding_rate_arb.py':
                matches.append(data)
        except (OSError,ValueError,yaml.YAMLError):pass
    return matches[0] if len(matches)==1 else {}


def request_command(bot_name, command, *, roots=None, now=None):
    """Publish one complete legacy-compatible request; never overwrite a pending request."""
    directory = instance_dir(bot_name,roots)
    if directory is None or 'basis' in bot_name.lower():
        return False
    now=time.time() if now is None else now
    try:
        state=json.loads((directory/'data/arbitrage_trades.json').read_text())
        stamp=number(state.get('updated_at'))
        if not funding_payload(state) or state.get('is_dry_run') is not True or stamp is None or not 0<=now-stamp<=20:
            return False
        action=command.get('action')
        if action not in {'add_token','remove_token','trigger_rotation','clear_quarantine','unblacklist_token','trigger_graduation_audit'}:
            return False
        token=str(command.get('token','')).strip().upper()
        if action not in {'trigger_rotation','trigger_graduation_audit'} and not re.fullmatch(r'[A-Z0-9]{1,32}',token):
            return False
        if action=='remove_token' and any(p.get('token')==token for p in state['active_positions']):
            return False  # backend refuses held-token removal; this is not a close-position command
        if action=='add_token':
            limit=number(load_instance_config(bot_name,roots).get('auto_rotation_max_active_tokens'))
            if limit is None or (token not in state.get('watchlist',[]) and len(state.get('watchlist',[]))>=limit):
                return False
        if action=='trigger_graduation_audit' and not state.get('graduation_status',{}).get('asset_graduation_enabled',False):
            return False
        data=dict(command)
        if token:data['token']=token
        parent=directory/'data';destination=parent/'strategy_commands.json'
        if destination.exists():return False
        descriptor,tmp=tempfile.mkstemp(prefix='.funding-command-',dir=parent)
        try:
            with os.fdopen(descriptor,'w') as f:
                json.dump(data,f,allow_nan=False);f.flush();os.fsync(f.fileno())
            # Same-directory hard link is atomic and fails if a pending request already exists.
            os.link(tmp,destination)
        finally:
            os.unlink(tmp)
        return True
    except (OSError,ValueError,TypeError,KeyError):
        return False


def config_errors(config):
    errors=[]
    if config.get('dry_run') is not True:errors.append('本版本只支持 Dry-run。')
    if set(config.get('connectors',[]))!={'binance_perpetual','hyperliquid_perpetual'}:errors.append('请选择 Binance 与 Hyperliquid 两个不同场所。')
    if not config.get('tokens'):errors.append('至少需要一个启动标的。')
    size=number(config.get('position_size_quote'));minimum=number(config.get('min_position_size_quote',100))
    if size is None or minimum is None or not 0<minimum<=size:errors.append('目标单边金额必须不低于最小开仓金额。')
    if number(config.get('leverage')) not in (1,2,3):errors.append('模拟杠杆范围为 1–3x。')
    for key,low,high in [('max_payback_hours',0,12),('max_orderbook_depth_ratio',0,.30)]:
        v=number(config.get(key))
        if v is None or not low<v<=high:errors.append(f'{key} 超出策略允许范围。')
    for key in ['liquidation_buffer_pct','tier_1_liquidation_buffer','tier_2_liquidation_buffer','tier_3_liquidation_buffer']:
        v=number(config.get(key,.08))
        if v is None or not .08<=v<1:errors.append(f'{key} 必须至少为 8% 且低于 100%。')
    value=number(config.get('min_funding_rate_profitability'))
    if value is None or value<=0:errors.append('日费差门槛必须大于零。')
    value=number(config.get('funding_rate_diff_stop_loss'))
    if value is None or value>-.001:errors.append('费率倒挂阈值须不高于 -0.1%/天。')
    if len(config.get('tokens',[]))>config.get('auto_rotation_max_active_tokens',12):
        errors.append('启动标的数量不能超过观察池上限。')
    return errors


def complete_config(values):
    """Explicit paper settings avoid accidentally inheriting obsolete feature defaults."""
    settings=dict(min_position_size_quote=100,max_concurrent_positions=3,
                  scaling_tp_enabled=False,asset_graduation_enabled=False,
                  liquidation_buffer_pct=.08,tier_1_liquidation_buffer=.08,
                  tier_2_liquidation_buffer=.08,tier_3_liquidation_buffer=.12,
                  max_entry_spread_pct=.001,max_leg_price_impact_pct=.002,
                  max_hedge_mismatch_pct=.001,max_portfolio_drawdown_pct=.05,
                  auto_rotation_enabled=True,auto_rotation_interval_minutes=.5,
                  auto_rotation_max_active_tokens=12,auto_rotation_min_apr=.35,
                  auto_rotation_min_apr_delta=.05,funding_signal_min_snapshots=3,
                  funding_signal_min_seconds=30,universe_min_residence_seconds=300,
                  universe_max_probes_per_scan=3,universe_max_rotations_per_scan=2,
                  universe_failed_candidate_cooldown_seconds=300,universe_mount_timeout_seconds=20)
    settings.update(values)
    return settings


def store_new_config(client,name,config):
    """This page creates a configuration; editing an existing live template is separate."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',name or ''):
        raise ValueError('配置名不能为空，且只能包含字母、数字、下划线或短横线。')
    # Listing names avoids relying on SDK-specific exception types for missing files.
    existing=client.list_script_configs()
    names={row.get('config_name') for row in existing if isinstance(row,dict)}
    if name in names:
        current=client.get_script_config(name)
        if current!=config:
            raise ValueError('同名配置已经存在。此页只创建新实例配置，请换一个文件名，避免模板与运行实例产生分歧。')
        preserve_local_config_owner(name)
        return
    client.create_or_update_script_config(config_name=name,config=config)
    preserve_local_config_owner(name)


def preserve_local_config_owner(name, roots=None):
    """Keep API-written bytes unchanged while returning local mounted ownership to the dashboard user."""
    if not hasattr(os,'getuid') or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*',name or ''):
        return
    roots=roots or [Path('/home/neo/projects/hummingbot/hummingbot-api/bots/conf/scripts'),
                    Path(__file__).resolve().parents[3]/'hummingbot-api/bots/conf/scripts']
    for root in roots:
        root=Path(root).resolve();path=root/(name+'.yml')
        if not path.exists():continue
        if path.is_symlink() or path.resolve().parent!=root:raise ValueError('配置路径不是预期的普通模板文件。')
        if path.stat().st_uid==os.getuid():return
        raw=path.read_bytes();descriptor,tmp=tempfile.mkstemp(prefix='.funding-config-',dir=root)
        try:
            with os.fdopen(descriptor,'wb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
            os.chmod(tmp,0o644)
            if path.read_bytes()!=raw:raise ValueError('配置在权限归还时发生变化，请重新核对。')
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
        return
