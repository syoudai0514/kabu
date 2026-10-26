import copy
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import equity_screen as engine


def fixture():
    t = 210
    dates = np.datetime64('2024-01-01')+np.arange(t)
    prices = 100*np.exp(np.arange(t)[:,None]*np.array([[.001,.002,.0005]]))
    return dict(prices=prices,dates=dates.astype(str),valid_observation=np.ones_like(prices,bool),
                symbols=['JP','US','SPY'],markets=['JP','US','US'],equity_mask=[True,True,False])


def config(**kw):
    return dict(dict(family='momentum_rank',topk=1,rebalance=5,gross=1,trend=0,lookback=21),**kw)


class EquityTests(unittest.TestCase):
    def test_future_perturbation_cannot_change_earlier_equity(self):
        data = fixture()
        other = copy.deepcopy(data)
        other['prices'][180:] *= 3
        import json
        space = json.loads((Path(__file__).resolve().parents[1]/'research/equity-screen-space.json').read_text())
        f,g = engine.prepare_data(data,space),engine.prepare_data(other,space)
        items = engine.candidates(space)
        for family in space['families']:
            c = next(x['config'] for x in items if x['family']==family)
            with self.subTest(family=family):
                a = engine.simulate(f,[c],'2024-05-20','2024-07-20',liquidate=False)
                b = engine.simulate(g,[c],'2024-05-20','2024-07-20',liquidate=False)
                np.testing.assert_array_equal(a['equity'][:40],b['equity'][:40])

    def test_zero_return_fee_identity_and_benchmark_parity(self):
        data = fixture()
        data['prices'][:] = 100
        f = engine.prepare_data(data,{})
        c = dict(family='buy_hold',symbol='SPY',rebalance=0)
        r = engine.simulate(f,[c],'2024-05-20','2024-06-20')
        expected = (1-.0025)/(1+.0025)
        self.assertAlmostEqual(r['final'][0],expected,places=12)
        self.assertAlmostEqual(r['fees'][0]+r['final'][0],1,places=12)
        r2 = engine.simulate(f,[c,c],'2024-05-20','2024-06-20')
        np.testing.assert_array_equal(r2['equity'][:,0],r2['equity'][:,1])

    def test_missing_prelisting_no_backfill_or_redistribution(self):
        data = fixture()
        data['prices'][:150,1] = np.nan
        data['valid_observation'][:150,1] = False
        f = engine.prepare_data(data,{})
        self.assertTrue(np.all(np.isnan(f['model_price'][:150,1])))
        self.assertTrue(np.all(f['returns'][:151,1]==0))
        r = engine.simulate(f,[config()],'2024-05-20','2024-06-20',liquidate=False,targets=True)
        self.assertTrue(np.all(r['weights'][:,:,1]==0))
        self.assertTrue(np.all(r['weights'].sum(axis=2)<.6))

    def test_missing_execution_freezes_position_and_terminal_flags_residual(self):
        data = fixture()
        data['valid_observation'][142:,0] = False
        data['prices'][142:,0] = np.nan
        f = engine.prepare_data(data,{})
        r = engine.simulate(f,[config(rebalance=1)],'2024-05-20','2024-06-20',targets=True)
        self.assertGreater(r['unliquidated'][0],0)
        self.assertTrue(np.all(r['weights']>=0))
        self.assertTrue(np.all(r['weights'].sum(axis=2)<=1+1e-12))
        self.assertGreaterEqual(r['final_cash'][0],0)

    def test_carried_native_mark_can_revalue_fx_without_native_bar(self):
        data = fixture()
        data['mark_prices'] = data['prices'].copy()
        data['valid_observation'][145,2] = False
        data['prices'][145,2] = np.nan
        data['mark_prices'][145,2] = data['mark_prices'][144,2]*1.01
        f = engine.prepare_data(data,{})
        self.assertEqual(f['counts'][145,2],f['counts'][144,2])
        self.assertAlmostEqual(f['returns'][145,2],.01)

    def test_all_declared_families_are_finite_and_unlevered(self):
        import json
        space = json.loads((Path(__file__).resolve().parents[1]/'research/equity-screen-space.json').read_text())
        f = engine.prepare_data(fixture(),space)
        items = engine.candidates(space)
        self.assertEqual(len(items),1488)
        self.assertEqual(len({x['config_sha256'] for x in items}),1488)
        for family in space['families']:
            c = next(x['config'] for x in items if x['family']==family)
            r = engine.simulate(f,[c],'2024-05-20','2024-07-20',targets=True)
            self.assertTrue(np.all(np.isfinite(r['equity'])))
            self.assertTrue(np.all(r['weights']>=0))
            self.assertTrue(np.all(r['weights'].sum(axis=2)<=1+1e-12))

    def test_space_guards_and_native_mark_consistency(self):
        data = fixture()
        f = engine.prepare_data(data,dict(minimum_history=200,max_stale_days=0,fees=dict(JP=0,US=0)))
        w,_ = engine.FamilyModel(f,[config()]).step(140,0)
        self.assertEqual(w.sum(),0)
        np.testing.assert_array_equal(f['cost'],0)
        other = copy.deepcopy(data)
        other['mark_prices'] = other['prices']*2
        with self.assertRaises(ValueError):
            engine.prepare_data(other,{})

    def test_waiting_buy_hold_preserves_initial_dollar_entry_budget(self):
        data = fixture()
        data['valid_observation'][139:145,2] = False
        data['prices'][139:145,2] = np.nan
        f = engine.prepare_data(data,{})
        c = dict(family='buy_hold',symbol='SPY',rebalance=0)
        r = engine.simulate(f,[c],'2024-05-20','2024-06-20',multiplier=0,liquidate=False,targets=True)
        self.assertTrue(np.all(r['equity'][:6]==1))
        self.assertEqual(r['weights'][6,0,2],1)
        self.assertAlmostEqual(r['final'][0],f['model_price'][r['indices'][-1],2]/f['model_price'][145,2])

    def test_country_slot_budget_and_causal_delay(self):
        data = fixture()
        f = engine.prepare_data(data,{})
        model = engine.FamilyModel(f,[config(topk=5)])
        w,_ = model.step(140,0)
        np.testing.assert_allclose(w[0],[.1,.1,0])
        c = dict(family='buy_hold',symbol='SPY',rebalance=0)
        r = engine.simulate(f,[c],'2024-05-20','2024-05-20',multiplier=0,liquidate=False)
        i = r['indices'][0]
        self.assertAlmostEqual(r['final'][0],f['model_price'][i,2]/f['model_price'][i-1,2])


if __name__ == '__main__':
    unittest.main()
