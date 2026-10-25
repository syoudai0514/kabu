import copy
import datetime as dt
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
from free_universe import evaluate, summarize, digest

POLICY = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'research/free-universe-policy.json').read_text())


def prices():
    end = dt.date.fromisoformat(POLICY['required_last_session'])
    days = [end-dt.timedelta(days=i) for i in range(500)]
    days = sorted(x for x in days if x.weekday() < 5)[-300:]
    return dict(bars=[dict(date=d.isoformat(), close=100, volume=2000000) for d in days],
                exchange='JPX', warnings=[], failures=[], splits=[],
                provenance=dict(sha256='fixture', retrieved_at='2026-10-25T00:00:00+00:00'))


class UniverseTest(unittest.TestCase):
    def test_liquidity_not_return_ranks_candidates(self):
        item = dict(key='JP:A', market='JP', sector='sector')
        data = prices()
        base = evaluate(item, data, POLICY)
        self.assertTrue(base['eligible'])
        # Change old returns wildly while keeping recent liquidity and quality fixed.
        data['bars'][0]['close'] = 9999
        self.assertEqual(evaluate(item, data, POLICY), base)

    def test_stale_split_and_gap_are_not_silently_filled(self):
        item = dict(key='JP:A', market='JP', sector='sector')
        data = prices()
        data['splits'] = [dict(date=data['bars'][-1]['date'], ratio=2)]
        self.assertIn('recent_split_turnover_review', evaluate(item, data, POLICY)['reasons'])
        data = prices()
        data['bars'].pop()
        self.assertIn('stale_or_missing_final_session', evaluate(item, data, POLICY)['reasons'])
        data = prices()
        del data['bars'][-40:-20]
        self.assertIn('recent_session_gap', evaluate(item, data, POLICY)['reasons'])

    def test_insufficient_history_and_zero_volume(self):
        item = dict(key='JP:A', market='JP', sector='sector')
        data = prices()
        data['bars'] = data['bars'][-200:]
        for row in data['bars'][-30:]:
            row['volume'] = 0
        result = evaluate(item, data, POLICY)
        self.assertFalse(result['eligible'])
        self.assertIn('insufficient_history', result['reasons'])
        self.assertIn('too_many_zero_volume_days', result['reasons'])

    def test_sector_cap_shortfall_and_nested_targets(self):
        policy = copy.deepcopy(POLICY)
        policy['targets'], policy['jp_sector_cap'] = [2, 3, 4], 2
        pool = [dict(key=f'JP:{i}', market='JP', symbol=str(i), sector='one' if i < 3 else 'two') for i in range(4)]
        results = {x['key']: dict(status='ok', eligible=True, median_turnover_proxy=1000-i, reasons=[]) for i, x in enumerate(pool)}
        manifest = dict(pool=pool, policy=policy, policy_sha256=digest(policy), pool_sha256=digest(pool), snapshots=[], available_candidates={'JP': 4, 'US': 0})
        summary = summarize(manifest, results)
        s = summary['markets']['JP']['selections']
        self.assertEqual([x['symbol'] for x in s['3']['members']], ['0', '1', '3'])
        self.assertEqual(s['4']['shortfall'], 1)
        self.assertEqual(s['2']['members'], s['3']['members'][:2])
        self.assertFalse(summary['historical_pit_complete'])
        self.assertFalse(summary['adoption_approved'])

    def test_failed_and_pending_are_distinct(self):
        pool = [dict(key=f'US:{i}', symbol=str(i), market='US') for i in range(2)]
        manifest = dict(pool=pool, policy=POLICY, policy_sha256=digest(POLICY), pool_sha256=digest(pool), snapshots=[], available_candidates={'JP': 0, 'US': 2})
        summary = summarize(manifest, {'US:0': dict(status='failed')})
        self.assertFalse(summary['collection_complete'])
        reasons = summary['markets']['US']['rejection_reason_counts']
        self.assertEqual(reasons, {'fetch_or_validation_failed': 1, 'not_fetched': 1})


if __name__ == '__main__':
    unittest.main()
