import copy
import json
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import research_families as engine
from test_research_search import synthetic_reference


class FamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.space = json.loads((ROOT/'research/family-space.json').read_text())
        cls.data = synthetic_reference()
        cls.f = engine.prepare_data(cls.data,cls.space)
        cls.all_items = engine.candidates(cls.space)
        cls.items = [next(x for x in cls.all_items if x['family']==name) for name in cls.space['families']]

    def test_deterministic_balanced_unique_configs_and_active_spy_parameters(self):
        self.assertEqual(self.all_items,engine.candidates(self.space))
        self.assertEqual(len(self.all_items),22500)
        self.assertEqual(len({x['config_sha256'] for x in self.all_items}),22500)
        for family in self.space['families']:
            self.assertEqual(sum(x['family']==family for x in self.all_items),2500)
        for item in self.all_items:
            cfg = item['config']
            if cfg['family']=='spy_volatility_target':
                self.assertNotIn('topk',cfg)
                self.assertEqual('drawdown_window' in cfg,cfg['risk_rule']!='none')
            if cfg['family']=='low_volatility':
                self.assertFalse(cfg['topk']==5 and cfg['weighting']=='equal' and cfg['voltarget']==0)
        shards = sum((engine.base.shard_candidates(self.all_items,i,7) for i in range(7)),[])
        self.assertEqual(sorted(shards,key=lambda x:x['id']),self.all_items)

    def test_future_price_fx_mutations_are_causal_for_every_family(self):
        other = copy.deepcopy(self.data)
        for asset in other['instruments']:
            for bar in asset['bars']:
                if bar['date']>='2023-01-01':
                    bar['close'] *= 1.8
        for quote in other['fx']:
            if quote['at']>='2023-01-01':
                quote['mid'] *= 1.4
        f2 = engine.prepare_data(other,self.space)
        for item in self.items:
            with self.subTest(family=item['family']):
                first = engine.simulate(self.f,[item['config']],'2021-01-01','2022-12-31',liquidate=False)
                second = engine.simulate(f2,[item['config']],'2021-01-01','2022-12-31',liquidate=False)
                np.testing.assert_array_equal(first['returns'],second['returns'])

    def test_appending_days_preserves_open_paper_path_for_every_family(self):
        for item in self.items:
            with self.subTest(family=item['family']):
                before = engine.simulate(self.f,[item['config']],'2021-01-01','2021-10-29',liquidate=False)
                longer = engine.simulate(self.f,[item['config']],'2021-01-01','2022-02-28',liquidate=False)
                np.testing.assert_array_equal(before['returns'],longer['returns'][:,:len(before['indices'])])

    def test_audit_mutation_never_changes_family_or_overall_ranking(self):
        other = copy.deepcopy(self.data)
        for asset in other['instruments']:
            for bar in asset['bars']:
                if bar['date']>='2024-10-24':
                    bar['close'] *= 1.8 if asset['id']=='SPY' else .5
        f2 = engine.prepare_data(other,self.space)
        first,second = [],[]
        for item in self.items:
            first += engine.evaluate_batch(self.f,[item],self.space)
            second += engine.evaluate_batch(f2,[item],self.space)
        self.assertEqual([r['id'] for r in engine.base.rank_rows(first)], [r['id'] for r in engine.base.rank_rows(second)])
        for a,b in zip(first,second):
            self.assertEqual((a['train'],a['validation'],a['score']), (b['train'],b['validation'],b['score']))
        self.assertTrue(any(a['audit']!=b['audit'] for a,b in zip(first,second)))

    def test_all_families_are_long_only_unlevered_and_cash_is_allowed(self):
        for item in self.items:
            with self.subTest(family=item['family']):
                result = engine.simulate(self.f,[item['config']],'2021-01-01','2022-12-31',targets=True)
                w = result['weights']
                self.assertTrue(np.all(w>=0))
                self.assertTrue(np.all(w.sum(axis=2)<=1+1e-12))
                self.assertGreaterEqual(result['costs'][0],0)
                self.assertAlmostEqual(np.prod(1+result['returns'][0]),result['final'][0],places=12)

    def test_rsi_and_breakout_take_different_actions_after_price_shock(self):
        f = copy.deepcopy(self.f)
        index = int(np.flatnonzero(f['dates']>='2021-01-01')[0])
        # A newly observed broad downside shock is below yesterday's channel,
        # and short RSI is oversold: reverse buys while breakout stays in cash.
        f['model_price'][index] *= .8
        f['bank']['rsi'][:,index,:] = 5
        trend = dict(next(i['config'] for i in self.items if i['family']=='donchian_breakout'),rebalance=1)
        reverse = dict(next(i['config'] for i in self.items if i['family']=='rsi_mean_reversion'),trend=0,rebalance=1)
        breakout_w,_ = engine.FamilyModel(f,[trend]).step(index,0)
        reverse_w,_ = engine.FamilyModel(f,[reverse]).step(index,0)
        self.assertEqual(float(breakout_w.sum()),0)
        self.assertGreater(float(reverse_w.sum()),0)

    def test_exit_forces_rebalance_between_entry_cadence_and_maxhold_is_effective(self):
        item = next(i for i in self.items if i['family']=='rsi_mean_reversion')
        cfg = dict(item['config'],trend=0,rebalance=5,max_hold=3)
        model = engine.FamilyModel(self.f,[cfg])
        index = 1500
        # Set observed RSI low throughout, so recovery cannot explain the exit.
        f = copy.deepcopy(self.f)
        f['bank']['rsi'][:,index:index+5,:] = 5
        model.f = f
        w,_ = model.step(index,0)
        self.assertGreater(w.sum(),0)
        for day in [1,2]:
            w,change = model.step(index+day,day)
            self.assertFalse(change[0])
            self.assertGreater(w.sum(),0)
        w,change = model.step(index+3,3)
        self.assertTrue(change[0])
        self.assertEqual(w.sum(),0)

    def test_spy_sizing_full_exposure_has_benchmark_parity_and_costs(self):
        config = dict(family='spy_volatility_target',vol_window=21,target_vol=.3,cap=1,rebalance=1,risk_rule='none')
        f = dict(self.f)
        f['returns'] = np.zeros_like(f['returns'])
        f['bank'] = dict(f['bank'],vol=np.full_like(f['bank']['vol'],.1))
        first = engine.simulate(f,[config],'2021-01-01','2021-03-31')
        base = engine.simulate(f,[config],'2021-01-01','2021-03-31',benchmark=True)
        np.testing.assert_allclose(first['returns'],base['returns'],atol=1e-14)
        rate = f['cost'][f['spy']]
        self.assertAlmostEqual(first['final'][0],(1-rate)**2,places=14)
        open_path = engine.simulate(f,[config],'2021-01-01','2021-03-31',liquidate=False)
        self.assertAlmostEqual(open_path['final'][0],1-rate,places=14)

    def test_channel_does_not_include_current_close(self):
        index = 1500
        window = 21
        code = self.f['horizon_codes'][window]
        np.testing.assert_array_equal(self.f['bank']['high'][code,index],self.f['model_price'][index-window:index].max(axis=0))
        np.testing.assert_array_equal(self.f['bank']['low'][code,index],self.f['model_price'][index-window:index].min(axis=0))


if __name__=='__main__':
    unittest.main()
