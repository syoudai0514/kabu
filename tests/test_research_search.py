import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('research_search', ROOT/'scripts/research_search.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def synthetic_reference():
    """Portable deterministic prices, deliberately unrelated to financial history."""
    days = []
    day = dt.date(2015, 1, 1)
    while day <= dt.date(2026, 10, 23):
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    instruments = []
    for j, (symbol, market) in enumerate([
        ('1346.T', 'JP'), ('1348.T', 'JP'), ('IWM', 'US'),
        ('QQQ', 'US'), ('SPY', 'US'),
    ]):
        bars = []
        base = 1000 if market == 'JP' else 100
        for i, day in enumerate(days):
            # Different positive drifts and oscillations create entries, exits,
            # rankings and changing volatility without random or external data.
            price = base*np.exp((.00025+j*.000025)*i
                                + .09*np.sin(i/100+j*.7)
                                + .025*np.sin(i/9+j*.4))
            date = day.isoformat()
            bars.append(dict(date=date, open=float(price*.999), close=float(price), volume=1_000_000,
                             open_at=date+('T00:00:00Z' if market == 'JP' else 'T14:30:00Z'),
                             close_at=date+('T06:00:00Z' if market == 'JP' else 'T21:00:00Z')))
        instruments.append(dict(id=symbol, symbol=symbol, market=market,
                                currency='JPY' if market == 'JP' else 'USD',
                                kind='etf', lot=1, bars=bars, actions=[]))
    fx = [dict(at=day.isoformat()+'T00:00:00Z',
               mid=float(140*np.exp(.00003*i+.03*np.sin(i/95))),
               source_date=(day-dt.timedelta(days=1)).isoformat())
          for i,day in enumerate(days)]
    return dict(schema_version=2, mode='etf_reference', end='2026-10-23',
                instruments=instruments, fx=fx)


class ScreeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.space = json.loads((ROOT/'research/search-space.json').read_text())
        cls.data = synthetic_reference()
        cls.f = engine.prepare_data(cls.data, cls.space)
        cls.items = engine.candidates(cls.space)[:4]

    def test_determinism_unique_and_shard_completeness(self):
        all_ = engine.candidates(self.space)
        self.assertEqual(all_, engine.candidates(self.space))
        self.assertEqual(len({engine.digest(x['config']) for x in all_}), 24000)
        combined = sum((engine.shard_candidates(all_, i, 7) for i in range(7)), [])
        self.assertEqual(sorted(combined, key=lambda x:x['id']), all_)

    def test_future_price_and_fx_mutation_cannot_change_prior_returns(self):
        other = copy.deepcopy(self.data)
        for a in other['instruments']:
            for bar in a['bars']:
                if bar['date'] >= '2023-01-01':
                    bar['close'] *= 1.7
                    bar['open'] *= 1.7
        for q in other['fx']:
            if q['at'] >= '2023-01-01':
                q['mid'] *= 1.3
        f2 = engine.prepare_data(other, self.space)
        configs = [x['config'] for x in self.items]
        first = engine.simulate(self.f, configs, '2021-01-01', '2022-12-31')
        second = engine.simulate(f2, configs, '2021-01-01', '2022-12-31')
        np.testing.assert_array_equal(first['returns'], second['returns'])
        np.testing.assert_array_equal(engine.desired_weights(self.f, 1500, configs), engine.desired_weights(f2, 1500, configs))

    def test_audit_mutation_does_not_change_selection(self):
        other = copy.deepcopy(self.data)
        for a in other['instruments']:
            for bar in a['bars']:
                if bar['date'] >= '2024-10-24':
                    bar['close'] *= 1.5 if a['id'] == 'SPY' else .6
        first = engine.evaluate_batch(self.f, self.items, self.space)
        second = engine.evaluate_batch(engine.prepare_data(other, self.space), self.items, self.space)
        self.assertEqual([r['id'] for r in engine.rank_rows(first)], [r['id'] for r in engine.rank_rows(second)])
        for a,b in zip(first, second):
            self.assertEqual((a['train'],a['validation'],a['score']), (b['train'],b['validation'],b['score']))
        self.assertNotEqual(first[0]['audit'], second[0]['audit'])

    def test_costs_entry_liquidation_and_zero_market_return(self):
        f = dict(self.f, returns=np.zeros_like(self.f['returns']))
        result = engine.simulate(f, [self.items[0]['config']], '2021-01-01', '2021-01-15', benchmark=True)
        rate = f['cost'][f['spy']]
        expected = (1-rate)**2
        self.assertAlmostEqual(result['final'][0], expected, places=14)
        self.assertAlmostEqual(result['costs'][0], 1-expected, places=14)
        self.assertAlmostEqual(np.prod(1+result['returns'][0]), expected, places=14)
        twice = engine.simulate(f, [self.items[0]['config']], '2021-01-01', '2021-01-15', multiplier=2, benchmark=True)
        self.assertLess(twice['final'][0], result['final'][0])

    def test_target_exposure_bounds(self):
        configs = [x['config'] for x in engine.candidates(self.space)[::67]]
        for index in [self.f['warmup'], 1000, 2000, len(self.f['dates'])-1]:
            w = engine.desired_weights(self.f, index, configs)
            self.assertTrue(np.all(w >= 0))
            self.assertTrue(np.all(w.sum(axis=1) <= np.array([c['gross'] for c in configs])+1e-12))

    def test_equal_full_spy_signal_has_benchmark_parity(self):
        f = dict(self.f)
        f['returns'] = np.zeros_like(self.f['returns'])
        f['returns'][:, f['spy']] = .001
        f['means'] = {k:self.f['prices']*.9 for k in self.f['means']}
        f['momentum'] = {k:np.full_like(self.f['prices'], -.1) for k in self.f['momentum']}
        for values in f['momentum'].values():
            values[:,f['spy']] = .1
        config = dict(self.items[0]['config'], topk=1, gross=1, weighting='equal', voltarget=0, defensive_cash=False)
        first = engine.simulate(f, [config], '2021-01-01', '2021-02-28')
        second = engine.simulate(f, [config], '2021-01-01', '2021-02-28', benchmark=True)
        np.testing.assert_allclose(first['returns'], second['returns'], atol=1e-14)


if __name__ == '__main__':
    unittest.main()
