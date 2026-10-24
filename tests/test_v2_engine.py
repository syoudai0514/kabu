import copy
import datetime as dt
import importlib.util
import json
import pathlib
import unittest
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('v2_engine', ROOT/'scripts/v2_engine.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def fixture():
    config = json.loads((ROOT/'research/v2-config.json').read_text())
    config.update(starts=['2025-01-06'], end='2025-04-30')
    instruments = []
    for market, symbol in [('JP', 'J'), ('US', 'U')]:
        date, bars = dt.date(2024, 1, 1), []
        while date <= dt.date(2025, 4, 30):
            if date.weekday() < 5:
                zone = ZoneInfo('Asia/Tokyo' if market == 'JP' else 'America/New_York')
                hour = dt.time(9) if market == 'JP' else dt.time(9, 30)
                price = (1000 if market == 'JP' else 10)*(1+len(bars)*.001)
                bars.append(dict(date=date.isoformat(), open=price*.999, close=price, volume=1000000,
                                 open_at=dt.datetime.combine(date, hour, zone).isoformat(),
                                 close_at=dt.datetime.combine(date, dt.time(15,30) if market == 'JP' else dt.time(16), zone).isoformat()))
            date += dt.timedelta(days=1)
        instruments.append(dict(id=symbol, symbol=symbol, market=market, currency='JPY' if market == 'JP' else 'USD', kind='etf', lot=10 if market == 'JP' else 1, bars=bars, actions=[]))
    data = dict(schema_version=2, mode='etf_reference', coverage={}, instruments=instruments,
                fx=[dict(at='2024-01-01T00:00:00Z', mid=140), dict(at='2025-02-01T00:00:00Z', mid=145)])
    return data, config


class V2Tests(unittest.TestCase):
    def test_start_at_open_does_not_fill_at_decision_timestamp(self):
        data, config = fixture()
        result = engine.replay(data, config, config['starts'][0], benchmark='equal')
        self.assertTrue(result['trades'])
        for trade in result['trades']:
            self.assertLess(engine.instant(trade['placed']), engine.instant(trade['at']))

    def test_same_open_fills_follow_signal_rank_not_symbol(self):
        data, config = fixture()
        asset = copy.deepcopy(data['instruments'][0])
        asset['id'] = 'A'
        data['instruments'].append(asset)
        sim = engine.Replay(data, config, config['starts'][0])
        sim.pending = {
            symbol: dict(asset=symbol, side='BUY', qty=10, placed='2025-01-05T00:00:00Z', reason='test_rank')
            for symbol in ('J', 'A')
        }
        result = sim.run()
        self.assertEqual([t['asset'] for t in result['trades'][:2]], ['J', 'A'])

    def test_future_changes_do_not_change_past_orders_or_balances(self):
        data, config = fixture()
        base = engine.replay(data, config, config['starts'][0])
        modified = copy.deepcopy(data)
        for asset in modified['instruments']:
            for bar in asset['bars']:
                if bar['date'] >= '2025-02-01':
                    bar['open'] *= 1.5
                    bar['close'] *= 1.5
                    bar['volume'] *= 2
        modified['fx'][1]['mid'] = 250
        other = engine.replay(modified, config, config['starts'][0])
        self.assertEqual([t for t in base['trades'] if t['at'] < '2025-02-01'], [t for t in other['trades'] if t['at'] < '2025-02-01'])
        self.assertEqual([h for h in base['history'] if h['at'] < '2025-02-01'], [h for h in other['history'] if h['at'] < '2025-02-01'])
        self.assertTrue(base['trades'])

    def test_unknown_same_day_us_open_does_not_affect_japan_fills(self):
        data, config = fixture()
        base = engine.replay(data, config, config['starts'][0])
        other = copy.deepcopy(data)
        for b in other['instruments'][1]['bars']:
            if b['date'] == '2025-01-13':
                b['open'] *= 3
        changed = engine.replay(other, config, config['starts'][0])
        jp = lambda r: [t for t in r['trades'] if t['asset'] == 'J' and t['at'] < '2025-01-13T12']
        self.assertTrue(jp(base))
        self.assertEqual(jp(base), jp(changed))

    def test_integer_lots_costs_fx_availability_and_value_reconcile(self):
        data, config = fixture()
        result = engine.replay(data, config, config['starts'][0])
        for t in result['trades']:
            self.assertLess(engine.instant(t['placed']), engine.instant(t['at']))
            self.assertLessEqual(engine.instant(t['fx_at']), engine.instant(t['at']))
            self.assertGreaterEqual(min(t['cash_jpy'], t['cash_usd']), -1e-7)
            self.assertEqual(t['qty'] % (10 if t['asset'] == 'J' else 1), 0)
            if t['status'] == 'filled':
                self.assertAlmostEqual(t['fee_jpy'], t['qty']*t['price']*t['fx']*.001)
        s = result['summary']
        self.assertAlmostEqual(s['final'], s['cash']['JPY']+s['cash']['USD']*s['last_fx']+sum(p['value_jpy'] for p in s['holdings']))

    def test_split_preserves_value_and_pending_share_units(self):
        data, config = fixture()
        sim = engine.Replay(data, config, config['starts'][0])
        sim.positions['J'] = dict(qty=100, cost_jpy=100000)
        sim.mark['J'] = 1000
        sim.pending['J'] = dict(qty=100)
        before = sim.value()
        sim.action('J', dict(type='split', ratio=5))
        self.assertEqual(sim.positions['J']['qty'], 500)
        self.assertEqual(sim.pending['J']['qty'], 500)
        self.assertEqual(sim.value(), before)

    def test_dividend_cash_arrives_only_at_payment(self):
        data, config = fixture()
        config['dividends'] = True
        sim = engine.Replay(data, config, config['starts'][0])
        sim.positions['J'] = dict(qty=100, cost_jpy=100000)
        sim.mark['J'] = 1000
        before = sim.cash['JPY']
        sim.action('J', dict(type='dividend', amount=10, pay_at='2025-02-01T00:00:00Z'))
        self.assertEqual(sim.cash['JPY'], before)
        self.assertEqual(sim.receivable, 1000)
        self.assertTrue(sim.payments)

    def test_delisted_position_cannot_be_carried_forever_without_settlement(self):
        data, config = fixture()
        sim = engine.Replay(data, config, config['starts'][0])
        sim.positions['J'] = dict(qty=100, cost_jpy=100000)
        with self.assertRaisesRegex(ValueError, 'settlement'):
            sim.action('J', dict(type='delist'))

    def test_current_survivors_cannot_be_declared_full_pit(self):
        data, config = fixture()
        data['mode'] = 'point_in_time_equities'
        with self.assertRaisesRegex(ValueError, 'PIT coverage incomplete'):
            engine.replay(data, config, config['starts'][0])

    def test_bad_split_prices_are_quarantined(self):
        data, config = fixture()
        data['instruments'][0]['bars'][100]['close'] *= 10
        with self.assertRaisesRegex(ValueError, 'Quarantine'):
            engine.validate(data, config)

    def test_missing_historical_month_is_rejected(self):
        data, config = fixture()
        data['mode'] = 'point_in_time_equities'
        data['coverage'] = dict(includes_delisted=True, complete_market_history=True, permanent_ids=True, historical_lots=True, corporate_actions_complete=True,
                                evidence='synthetic test', from_date='2024-01-01', through=config['end'])
        for a in data['instruments']:
            a['kind'] = 'common_stock'
        with self.assertRaisesRegex(ValueError, 'Missing monthly PIT snapshot'):
            engine.validate(data, config)

    def test_initial_universe_uses_month_start_not_start_day_volume(self):
        data, config = fixture()
        data['mode'] = 'point_in_time_equities'
        config['min_median_turnover'] = dict(JP=1, US=1)
        data['coverage'] = dict(includes_delisted=True, complete_market_history=True, permanent_ids=True, historical_lots=True, corporate_actions_complete=True,
                                evidence='synthetic test', from_date='2024-01-01', through=config['end'])
        snapshots = []
        for a in data['instruments']:
            a['kind'] = 'common_stock'
            for month in ('01','02','03','04'):
                at = f'2025-{month}-01T00:00:00Z'
                snapshots.append(dict(market=a['market'], effective_at=at, known_at=at, complete=True, provenance='fixture',
                                      members=[dict(id=a['id'], sector='industry', size_bucket='large', lot=a['lot'])]))
        data['membership_snapshots'] = snapshots
        base = engine.replay(data, config, config['starts'][0])
        other = copy.deepcopy(data)
        for a in other['instruments']:
            for b in a['bars']:
                if b['date'] >= '2025-01-01':
                    b['volume'] = 0
        changed = engine.replay(other, config, config['starts'][0])
        january = lambda r: [u for u in r['universe_history'] if u['at'] < '2025-02-01']
        self.assertEqual(january(base), january(changed))
        self.assertEqual(january(base)[0]['at'], '2025-01-01T00:00:00+00:00')
        self.assertEqual(len(january(base)), 2)


if __name__ == '__main__':
    unittest.main()
