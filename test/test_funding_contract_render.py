"""Render actual native strategy fixtures and exercise generated configuration without network/orders."""
import copy
import json
from pathlib import Path
import runpy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=Path(__file__).with_name('fixtures')/'funding_native_contract.json'


class ContractRenderingTests(unittest.TestCase):
    def render(self,kind,negative=False):
        code='''import json
from pathlib import Path
import frontend.components.funding_arbitrage_view as view
data=json.loads(Path(FIXTURE).read_text())[KIND]
if NEGATIVE:data['active_positions'][0]['projected_daily_funding_quote']=-3
view.get_exchange_clock_health=lambda:{'status':'ok','badge':'受控时钟'}
view.render_funding_arbitrage_kpi_cards(data,0,999999)
view.render_funding_arbitrage_tables(data,'funding-render-fixture')
'''.replace('FIXTURE',repr(str(FIXTURE))).replace('KIND',repr(kind)).replace('NEGATIVE',repr(negative))
        app=AppTest.from_string(code,default_timeout=20).run()
        self.assertFalse(app.exception,[r.value for r in app.exception])
        self.assertFalse(any('$999,999.00' in row.value for row in app.metric),'Do not mix old MQTT metrics with the selected ledger')
        text='\n'.join(r.value for r in app.markdown)
        for phrase in ['只增不减','一定能赚钱','必定为正','调低至','若有持仓将安全平仓']:
            self.assertNotIn(phrase,text)
        return app,text

    def test_native_active_report_renders(self):self.render('active')
    def test_native_closed_report_renders(self):self.render('closed')
    def test_negative_held_direction_not_replaced_by_best_direction(self):
        app,text=self.render('active',negative=True)
        self.assertTrue(any('$-3.00' in r.value for r in app.metric))
        self.assertIn('息差为负',text)

    def test_form_saves_preview_and_rejects_same_venue_deployment(self):
        fake=NS(accounts=NS(list_accounts=lambda:['funding-paper-public']),
                scripts=NS(create_or_update_script_config=Mock(),list_script_configs=lambda:[],get_script_config=Mock()),
                bot_orchestration=NS(deploy_v2_script=Mock()))
        with patch('frontend.st_utils.get_backend_api_client',return_value=fake),patch('frontend.components.funding_scanner.scan_cross_exchange_opportunities',return_value=[]):
            code='''import runpy,streamlit as st
data=runpy.run_path(PAGE)
st.session_state['_test_preview']=data['config_dict']
'''.replace('PAGE',repr(str(ROOT/'frontend/pages/config/funding_rate_arb/app.py')))
            app=AppTest.from_string(code,default_timeout=20).run()
            self.assertFalse(app.exception)
            config=app.session_state['_test_preview']
            self.assertNotIn('timezone',config)
            self.assertEqual(config['auto_rotation_interval_minutes'],.5)
            self.assertFalse(config['scaling_tp_enabled']);self.assertFalse(config['asset_graduation_enabled'])
            next(b for b in app.button if b.label.startswith('💾')).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(fake.scripts.create_or_update_script_config.call_args.kwargs['config'],config)
            next(s for s in app.selectbox if s.label.startswith('交易所 2')).select('hyperliquid_perpetual').run()
            next(b for b in app.button if b.label.startswith('🚀 立即部署')).click().run()
            self.assertFalse(app.exception)
            fake.bot_orchestration.deploy_v2_script.assert_not_called()
            self.assertEqual(fake.scripts.create_or_update_script_config.call_count,1)


if __name__=='__main__':unittest.main()
