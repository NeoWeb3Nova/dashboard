import io
import json
import tempfile
from pathlib import Path
import unittest
import urllib.error
from unittest.mock import Mock,patch

from frontend.components import funding_http as http
from frontend.components.funding_contract import market_data_issues


class PublicBackoffTests(unittest.TestCase):
    def setUp(self):
        self.patches=[patch.object(http,'BACKOFF_PATH',None),patch.dict(http._blocked,clear=True)]
        for item in self.patches:item.start();self.addCleanup(item.stop)

    def error(self,status,body=b'{}',headers=None):
        return urllib.error.HTTPError('https://fapi.binance.com/test',status,'limited',headers or {},io.BytesIO(body))

    def test_418_reported_ban_suppresses_repeated_requests(self):
        error=self.error(418,b'{"msg":"IP(test) banned until 2000000000000"}')
        with patch.object(http.time,'time',return_value=1900000000),patch('urllib.request.urlopen',side_effect=error) as transport:
            for _ in range(2):
                with self.assertRaises(http.PublicRateLimited):http.public_urlopen('https://fapi.binance.com/test')
            self.assertEqual(transport.call_count,1)
            self.assertEqual(http.public_rate_limits()['fapi.binance.com']['until'],2000000000)

    def test_retry_after_expiry_allows_later_request(self):
        error=self.error(429,headers={'Retry-After':'30'})
        with patch.object(http.time,'time',return_value=1000),patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(http.PublicRateLimited):http.public_urlopen('https://fapi.binance.com/test')
        with patch.object(http.time,'time',return_value=1031),patch('urllib.request.urlopen',return_value=io.BytesIO(b'ok')) as transport:
            self.assertEqual(http.public_urlopen('https://fapi.binance.com/test').read(),b'ok')
            transport.assert_called_once()

    def test_unrelated_host_is_not_paused(self):
        http._blocked['fapi.binance.com']={'until':2000000000,'status':418}
        with patch.object(http.time,'time',return_value=1900000000),patch('urllib.request.urlopen',return_value=io.BytesIO(b'ok')):
            self.assertEqual(http.public_urlopen('https://api.hyperliquid.xyz/info').read(),b'ok')

    def test_persisted_limit_survives_empty_module_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'backoff.json';path.write_text(json.dumps({'fapi.binance.com':{'until':2000000000,'status':418}}))
            with patch.object(http,'BACKOFF_PATH',path),patch.object(http.time,'time',return_value=1900000000),patch('urllib.request.urlopen') as transport:
                with self.assertRaises(http.PublicRateLimited):http.public_urlopen('https://fapi.binance.com/test')
                transport.assert_not_called()

    def test_unrelated_http_error_not_mislabeled_as_rate_limit(self):
        with patch('urllib.request.urlopen',side_effect=self.error(500)):
            with self.assertRaises(urllib.error.HTTPError):http.public_urlopen('https://fapi.binance.com/test')
        self.assertEqual(http.public_rate_limits(),{})

    def test_completed_scan_cannot_hide_incomplete_market_data(self):
        data={'watchlist':['BTC'],'market_comparison':[],
              'rotation_status':{'discovery':{'phase':'ready','rejections':{'stale metadata':174}}},
              'audit':{'public_connectors':{'binance_perpetual':{'configured_pairs':1,'books_ready':[],'rules_ready':['BTC']}}}}
        issues=market_data_issues(data)
        self.assertEqual(len(issues),3)
        self.assertTrue(any('扫描完成不代表行情健康' in issue for issue in issues))


if __name__=='__main__':unittest.main()
