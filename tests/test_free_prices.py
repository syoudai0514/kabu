"""Synthetic provider tests: missing observations, metadata, integrity and bounds."""
import datetime as dt
import importlib.util
import json
import pathlib
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

MODULE = pathlib.Path(__file__).resolve().parents[1] / 'scripts/free_prices.py'
spec = importlib.util.spec_from_file_location('free_prices', MODULE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def payload():
    return {'chart': {'error': None, 'result': [{
        'meta': {'symbol': 'TEST', 'instrumentType': 'EQUITY', 'currency': 'USD', 'exchangeTimezoneName': 'America/New_York', 'exchangeName': 'NYQ'},
        'timestamp': [int(dt.datetime(2020, 1, day, 0, 30, tzinfo=dt.timezone.utc).timestamp()) for day in (3, 4)],
        'indicators': {'quote': [{'open': [10, 11], 'high': [12, 12], 'low': [9, 10], 'close': [11, 12], 'volume': [10, 20]}]}
    }]}}


class PricesTest(unittest.TestCase):
    def normalize(self, data):
        return p._normalize(json.dumps(data).encode(), 'TEST', 'US', dt.date(2020, 1, 1),
                            dt.date(2020, 1, 4), dt.datetime(2020, 1, 4, 20, tzinfo=p.UTC))

    def test_exchange_dates_and_missing_are_not_filled(self):
        data = payload()
        data['chart']['result'][0]['indicators']['quote'][0]['open'][1] = None
        result = self.normalize(data)
        self.assertEqual([b['date'] for b in result['bars']], ['2020-01-02'])
        self.assertEqual(result['failures'][0]['reason'], 'missing_daily_value')

    def test_invalid_numeric_duplicates_and_metadata(self):
        for field, value in [('volume', -1), ('open', 0), ('close', float('inf'))]:
            data = payload()
            data['chart']['result'][0]['indicators']['quote'][0][field][0] = value
            with self.assertRaises(p.PriceError):
                self.normalize(data)
        data = payload()
        data['chart']['result'][0]['timestamp'][1] = data['chart']['result'][0]['timestamp'][0]
        with self.assertRaisesRegex(p.PriceError, 'Duplicate'):
            self.normalize(data)
        for field, value in [('instrumentType', 'ETF'), ('currency', 'JPY'), ('symbol', 'JPY=X'), ('exchangeTimezoneName', 'Asia/Tokyo'), ('exchangeName', 'CCY')]:
            data = payload()
            data['chart']['result'][0]['meta'][field] = value
            with self.assertRaises(p.PriceError):
                self.normalize(data)

    def test_incomplete_today_and_exclusive_end(self):
        data = payload()
        result = p._normalize(json.dumps(data).encode(), 'TEST', 'US', dt.date(2020, 1, 1),
                              dt.date(2020, 1, 3), dt.datetime(2020, 1, 3, 20, tzinfo=p.UTC))
        self.assertEqual(len(result['bars']), 1)
        self.assertEqual(result['failures'][0]['reason'], 'outside_request_or_incomplete')

    def test_split_is_reported_without_second_adjustment(self):
        data = payload()
        row = data['chart']['result'][0]
        row['events'] = {'splits': {'s': {'date': row['timestamp'][1], 'numerator': 2, 'denominator': 1}}}
        result = self.normalize(data)
        self.assertEqual(result['splits'], [{'date': '2020-01-03', 'ratio': 2}])
        self.assertEqual(result['bars'][0]['close'], 11)

    def test_cache_reuse_tampering_and_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(p.PriceError, 'Offline cache missing'):
                p.fetch('TEST', 'US', '2020-01-01', '2020-01-04', directory, offline=True)
            with patch.object(p.urllib.request, 'urlopen') as get:
                get.return_value.__enter__.return_value.read.return_value = json.dumps(payload()).encode()
                result = p.fetch('TEST', 'US', '2020-01-01', '2020-01-04', directory)
                self.assertFalse(result['provenance']['cache_hit'])
                cached = p.fetch('TEST', 'US', '2020-01-01', '2020-01-04', directory, offline=True)
                self.assertTrue(cached['provenance']['cache_hit'])
                self.assertEqual(get.call_count, 1)
            pathlib.Path(result['provenance']['raw_path']).write_text('{}')
            with self.assertRaisesRegex(p.PriceError, 'Cache integrity'):
                p.fetch('TEST', 'US', '2020-01-01', '2020-01-04', directory)

    def test_cache_preserves_retrieval_asof_when_clock_advances(self):
        class Clock(dt.datetime):
            current = dt.datetime(2020, 1, 3, 20, tzinfo=p.UTC)

            @classmethod
            def now(cls, tz=None):
                return cls.current

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(p.dt, 'datetime', Clock), patch.object(p.urllib.request, 'urlopen') as get:
                get.return_value.__enter__.return_value.read.return_value = json.dumps(payload()).encode()
                original = p.fetch('TEST', 'US', '2020-01-01', '2020-01-04', directory)
                Clock.current = dt.datetime(2020, 1, 6, 20, tzinfo=p.UTC)
                replay = p.fetch('TEST', 'US', '2020-01-01', '2020-01-04', directory, offline=True)
                self.assertEqual(original['bars'], replay['bars'])
                self.assertEqual(original['failures'], replay['failures'])
                self.assertEqual(len(replay['bars']), 1)

    def test_future_bounds_and_rate_limit_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(p.PriceError, 'future'):
                p.fetch('TEST', 'US', '2099-01-01', '2099-01-02', directory)
            for code in (403, 429):
                with patch.object(p.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('url', code, 'blocked', {}, None)) as get:
                    with self.assertRaisesRegex(p.PriceError, str(code)) as raised:
                        p.fetch('TEST', 'US', '2020-01-01', '2020-01-04', directory)
                    self.assertEqual(get.call_count, 1)
                    self.assertEqual(raised.exception.status, code)


if __name__ == '__main__':
    unittest.main()
