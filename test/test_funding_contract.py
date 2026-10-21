"""Contract regressions for real ledger shape and legacy command transport."""
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock
from pathlib import Path

from frontend.components.funding_contract import (
    complete_config,config_errors,gross_notional,legs,load_instance_config,
    request_command,select_snapshot,remaining_seconds,store_new_config,
)
from frontend.components.trade_attribution import analyze_trade_attribution

NOW=1800001000
def snapshot(stamp=NOW):
    return dict(schema_version=2,is_dry_run=True,updated_at=stamp,watchlist=['BTC'],
                active_positions=[],closed_trades=[],market_comparison=[])


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bot='funding-example'
        self.directory=self.root/self.bot;(self.directory/'data').mkdir(parents=True)
        self.path=self.directory/'data/arbitrage_trades.json';self.put(snapshot())
        conf=self.directory/'conf/scripts';conf.mkdir(parents=True)
        (conf/'funding.yml').write_text('script_file_name: v2_funding_rate_arb.py\nauto_rotation_max_active_tokens: 12\n')

    def put(self,data):self.path.write_text(json.dumps(data))
    def select(self,performance):return select_snapshot(self.bot,performance,roots=[self.root],now=NOW)
    def request(self,command):return request_command(self.bot,command,roots=[self.root],now=NOW)

    def test_new_local_ledger_wins_over_stale_mqtt(self):
        old=snapshot(NOW-100);old['closed_trades']=[{'total_net_pnl':999}]
        known,data=self.select({'funding_rate_arb':{'custom_info':old}})
        self.assertTrue(known);self.assertEqual(data['_ui']['source'],'本地账本')
        self.assertEqual(data['closed_trades'],[])

    def test_new_mqtt_wins_over_stale_local(self):
        self.put(snapshot(NOW-100));data=self.select({'funding_rate_arb':{'custom_info':snapshot()}})[1]
        self.assertEqual(data['_ui']['source'],'MQTT')

    def test_missing_ledger_is_not_a_healthy_empty_account(self):
        self.path.unlink();data=self.select({})[1]
        self.assertFalse(data['_ui']['available']);self.assertNotIn('is_dry_run',data)

    def test_stale_snapshot_preserved_with_explicit_stale_status(self):
        self.put(snapshot(NOW-100));data=self.select({})[1]
        self.assertTrue(data['_ui']['stale']);self.assertEqual(data['updated_at'],NOW-100)

    def test_future_mqtt_cannot_replace_current_ledger(self):
        bad=snapshot(NOW+300)
        self.assertEqual(self.select({'funding_rate_arb':{'custom_info':bad}})[1]['updated_at'],NOW)

    def test_basis_data_cannot_be_misclassified(self):
        self.assertEqual(self.select({'basis_stat_arb':{}}),(False,None))

    def test_sell_leg_mapping_uses_actual_quantities_and_schedule(self):
        p=dict(side='SELL',connector_1='bn',connector_2='hl',amount_1='2',amount_2='3',
               entry_price_1=100,entry_price_2=70,current_price_1=101,current_price_2=72,
               next_funding_1=NOW+800,next_funding_2=NOW+100)
        result=legs(p)
        self.assertEqual(result['long']['quantity'],3);self.assertEqual(result['long']['next_funding'],NOW+100)
        self.assertEqual(result['short']['pnl'],-2);self.assertEqual(result['long']['pnl'],6)
        self.assertEqual(gross_notional(p),410)

    def test_missing_quantity_is_not_reconstructed_from_equal_dollars(self):
        self.assertIsNone(legs(dict(side='BUY',position_size=1000,entry_price_1=100))['long']['quantity'])

    def test_cooldown_uses_published_until_not_missing_minutes(self):
        self.assertEqual(remaining_seconds({'lockout_until':NOW+600},NOW,'lockout_until'),600)
        self.assertEqual(remaining_seconds({'quarantine_until':NOW-1},NOW,'quarantine_until'),0)

    def test_full_request_is_published_without_overwriting_pending(self):
        command={'action':'add_token','token':'eth'}
        self.assertTrue(self.request(command))
        path=self.directory/'data/strategy_commands.json'
        self.assertEqual(json.loads(path.read_text())['token'],'ETH')
        self.assertFalse(self.request({'action':'trigger_rotation'}))
        self.assertEqual(json.loads(path.read_text())['token'],'ETH')
        self.assertEqual(list(path.parent.glob('.funding-command-*')),[])

    def test_held_market_remove_is_rejected_instead_of_claiming_close(self):
        data=snapshot();data['active_positions']=[{'token':'BTC'}];self.put(data)
        self.assertFalse(self.request({'action':'remove_token','token':'BTC'}))

    def test_stale_snapshot_rejects_control(self):
        self.put(snapshot(NOW-21));self.assertFalse(self.request({'action':'trigger_rotation'}))

    def test_no_traversal_or_shared_data_fallback(self):
        self.assertFalse(request_command('../funding-example',{'action':'trigger_rotation'},roots=[self.root],now=NOW))
        self.assertFalse(request_command('missing',{'action':'trigger_rotation'},roots=[self.root],now=NOW))

    def test_disabled_graduation_not_offered_as_working_feature(self):
        self.assertFalse(self.request({'action':'trigger_graduation_audit'}))

    def test_manual_add_respects_deployed_pool_capacity(self):
        conf=self.directory/'conf/scripts'
        (conf/'funding.yml').write_text('script_file_name: v2_funding_rate_arb.py\nauto_rotation_max_active_tokens: 1\n')
        self.assertFalse(self.request({'action':'add_token','token':'ETH'}))

    def test_live_and_unknown_commands_are_not_accepted(self):
        self.assertFalse(self.request({'action':'close_all'}))
        data=snapshot();data['is_dry_run']=False;self.put(data)
        self.assertFalse(self.request({'action':'trigger_rotation'}))

    def test_explicit_generated_config_and_invalid_minimum(self):
        config=complete_config(dict(dry_run=True,connectors=['binance_perpetual','hyperliquid_perpetual'],
            tokens=['BTC'],leverage=1,position_size_quote=500,max_payback_hours=12,max_orderbook_depth_ratio=.15,
            min_funding_rate_profitability=.0012,funding_rate_diff_stop_loss=-.001))
        self.assertEqual(config_errors(config),[])
        self.assertFalse(config['asset_graduation_enabled']);self.assertFalse(config['scaling_tp_enabled'])
        self.assertEqual(config['auto_rotation_interval_minutes'],.5)
        config['position_size_quote']=10;self.assertTrue(config_errors(config))

    def test_zero_profit_and_negative_funding_are_not_described_as_profit(self):
        result=analyze_trade_attribution(dict(token='BTC',total_net_pnl=0,trading_pnl=2,funding_pnl=-2,close_reason='Take Profit'))
        self.assertFalse(result['is_win']);self.assertIn('净损益为零',result['attr_tag'])
        self.assertNotIn('确保',result['detail_explanation']);self.assertIn('$-2.0000',result['detail_explanation'])

    def test_changed_existing_config_cannot_be_overwritten_by_new_instance_form(self):
        client=Mock();client.list_script_configs.return_value=[{'config_name':'running'}]
        client.get_script_config.return_value={'position_size_quote':500}
        with self.assertRaises(ValueError):store_new_config(client,'running',{'position_size_quote':1000})
        client.create_or_update_script_config.assert_not_called()

    def test_identical_existing_config_reused_without_mutation(self):
        client=Mock();client.list_script_configs.return_value=[{'config_name':'running'}]
        config={'position_size_quote':500};client.get_script_config.return_value=config
        store_new_config(client,'running',config);client.create_or_update_script_config.assert_not_called()

    def test_new_configuration_stored_through_api_only(self):
        client=Mock();client.list_script_configs.return_value=[]
        store_new_config(client,'new-config',{'dry_run':True})
        client.create_or_update_script_config.assert_called_once_with(config_name='new-config',config={'dry_run':True})


if __name__=='__main__':unittest.main()
