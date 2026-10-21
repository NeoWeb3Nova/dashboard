"""Financial display regressions; no Streamlit server or exchange network."""
import io
import json
import unittest
from unittest.mock import patch

from frontend.components.funding_scanner import fetch_binance_perpetual_funding_rates
from frontend.components.pnl_analytics import _parse_trades_dataframe, _calculate_pnl_metrics


class FinancialDisplayTests(unittest.TestCase):
    def setUp(self):
        import frontend.components.funding_http as public_http
        state_patch = patch.dict(public_http._blocked, clear=True)
        file_patch = patch.object(public_http, 'BACKOFF_PATH', None)
        state_patch.start(); file_patch.start()
        self.addCleanup(state_patch.stop); self.addCleanup(file_patch.stop)

    def test_drawdown_includes_first_losing_trade_from_zero(self):
        trades = [dict(token='BTC', open_timestamp=100, close_timestamp=200+i,
                       trading_pnl=n, funding_pnl=0, total_net_pnl=n, position_size=1000,
                       leverage=1,initial_margin_quote=1999) for i,n in enumerate([-10,-20,5])]
        df = _parse_trades_dataframe(trades)
        self.assertEqual(df.iloc[0]['real_margin'],1999)
        metrics = _calculate_pnl_metrics(df,[])
        self.assertEqual(metrics['max_drawdown_usd'],30)
        self.assertEqual(metrics['realized_net_pnl'],-25)

    def test_funding_counted_once_in_account_total(self):
        trades = [dict(token='BTC',open_timestamp=100,close_timestamp=200,
                       trading_pnl=-2,funding_pnl=3,total_net_pnl=1,position_size=1000,leverage=1)]
        active = [dict(unrealized_trading_pnl=-4,funding_income=2,net_pnl=-2)]
        metrics = _calculate_pnl_metrics(_parse_trades_dataframe(trades),active)
        self.assertEqual(metrics['total_global_net_pnl'],-1)
        self.assertEqual(metrics['total_global_funding'],5)

    def test_binance_symbol_interval_used_in_scanner(self):
        replies = [[],[dict(symbol='BTCUSDT',fundingIntervalHours=4)],
                   [dict(symbol='BTCUSDT',lastFundingRate='.001',markPrice='100',nextFundingTime=1000)]]
        def respond(*args,**kwargs):
            return io.BytesIO(json.dumps(replies.pop(0)).encode())
        with patch('urllib.request.urlopen',side_effect=respond):
            row = fetch_binance_perpetual_funding_rates()['BTC']
        self.assertEqual(row['funding_interval_hours'],4)
        self.assertAlmostEqual(row['rate_daily'],.006)

    def test_unknown_interval_response_does_not_fabricate_signal(self):
        with patch('urllib.request.urlopen',side_effect=OSError('offline')):
            self.assertEqual(fetch_binance_perpetual_funding_rates(),{})


if __name__=='__main__':
    unittest.main()
