"""Unit, sign, missing-data and reconciliation checks for the funding comparison."""
import unittest
from frontend.components.funding_rate_table import funding_rate_rows, BN, HL, SPREAD, ENGINE


def snapshot(bn=.03, hl=.01, reported=.02, **extra):
    row={'token':'BTC','binance_perpetual_rate_daily':bn,
         'hyperliquid_perpetual_rate_daily':hl,'daily_rate_diff_pct':reported,
         'best_path':'binance_perpetual -> hyperliquid_perpetual'}
    return dict(market_comparison=[row],min_funding_rate_profitability_pct=.012,**extra)


class FundingRateTableTests(unittest.TestCase):
    def test_daily_percentage_is_not_multiplied_by_100_again(self):
        r=funding_rate_rows(snapshot())[0]
        self.assertEqual(r[BN],.03);self.assertEqual(r[HL],.01);self.assertEqual(r[SPREAD],.02)

    def test_direction_follows_rates_not_sorted_connector_arrow(self):
        r=funding_rate_rows(snapshot())[0]
        self.assertEqual((r['预测做多'],r['预测做空']),('Hyperliquid','Binance'))

    def test_negative_rates_keep_sign_and_select_lower_for_long(self):
        r=funding_rate_rows(snapshot(-.04,-.01,.03))[0]
        self.assertEqual(r[BN],-.04);self.assertEqual(r[SPREAD],.03)
        self.assertEqual((r['预测做多'],r['预测做空']),('Binance','Hyperliquid'))

    def test_equal_rates_and_unreported_spread_are_distinct(self):
        r=funding_rate_rows(snapshot(.03,.03,None))[0]
        self.assertEqual(r[SPREAD],0);self.assertIsNone(r[ENGINE])
        self.assertEqual(r['预测做多'],'无方向优势')

    def test_missing_venue_rate_cannot_be_a_zero_rate_opportunity(self):
        r=funding_rate_rows(snapshot(None,.03,.03))[0]
        self.assertIsNone(r[BN]);self.assertIsNone(r[SPREAD])
        self.assertEqual(r['入场状态'],'数据待核对')

    def test_nan_inf_and_boolean_rates_do_not_create_opportunities(self):
        for bad in ['NaN','Infinity',True]:
            with self.subTest(bad=bad):self.assertIsNone(funding_rate_rows(snapshot(bad))[0][SPREAD])

    def test_mismatch_is_visible_and_does_not_get_candidate_status(self):
        r=funding_rate_rows(snapshot(reported=2))[0]
        self.assertEqual(r[ENGINE],2);self.assertEqual(r[SPREAD],.02)
        self.assertIn('不一致',r['数据核对']);self.assertEqual(r['入场状态'],'数据待核对')

    def test_unreported_nonzero_engine_spread_remains_unconfirmed(self):
        self.assertEqual(funding_rate_rows(snapshot(reported=None))[0]['入场状态'],'数据待核对')

    def test_gross_threshold_is_not_full_entry_approval(self):
        r=funding_rate_rows(snapshot(audit={'last_rejection':{'BTC':{'reason':'full round-trip payback exceeds limit','timestamp':1}}}))[0]
        self.assertEqual(r['入场状态'],'仅毛费差达标，仍需完整准入')
        self.assertIn('payback',r['最近拒绝原因'])

    def test_expired_cooldown_is_not_displayed_as_current(self):
        d=snapshot(post_tp_lockout={'BTC':{'lockout_until':100}})
        self.assertNotIn('冷却',funding_rate_rows(d,now=200)[0]['入场状态'])
        self.assertIn('冷却',funding_rate_rows(d,now=50)[0]['入场状态'])

    def test_sort_uses_numeric_spread_and_missing_last(self):
        d=snapshot();d['market_comparison'] += [dict(snapshot(.2,0,.2)['market_comparison'][0],token='SOL'),
                                               dict(snapshot(None,0,0)['market_comparison'][0],token='ETH')]
        self.assertEqual([r['币种'] for r in funding_rate_rows(d)],['SOL','BTC','ETH'])

    def test_tiny_rates_keep_precision_and_stale_data_is_not_candidate(self):
        r=funding_rate_rows(snapshot(.000006,.000001,.000005,_ui={'stale':True}))[0]
        self.assertEqual(r[SPREAD],.000005);self.assertEqual(r['入场状态'],'费率数据待刷新')

    def test_fresh_funding_with_missing_books_is_not_trade_ready(self):
        d=snapshot(audit={'public_connectors':{'binance_perpetual':{'books_ready':[], 'rules_ready':['BTC']}}})
        r=funding_rate_rows(d)[0]
        self.assertEqual(r['入场状态'],'盘口或规则未就绪，不能开仓')
        self.assertEqual(r[SPREAD],.02)


if __name__=='__main__':unittest.main()
