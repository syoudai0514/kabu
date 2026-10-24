import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
spec = importlib.util.spec_from_file_location('research_paper', ROOT/'scripts/research-paper.py')
paper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paper)
from test_research_search import synthetic_reference


class ForwardResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.space = json.loads((ROOT/'research/search-space.json').read_text())
        cls.data = synthetic_reference()
        cls.features = paper.engine.prepare_data(cls.data, cls.space)
        historical = copy.deepcopy(cls.data)
        historical['end'] = '2024-12-31'
        raw = json.dumps(historical).encode()
        cfg = paper.engine.candidates(cls.space)[0]['config']
        report = dict(provenance=dict(data_sha256=hashlib.sha256(raw).hexdigest()),
                      selected=dict(id='fixture', config=cfg, config_sha256=paper.engine.digest(cfg), train={}, validation={}))
        cls.model = paper.freeze(report, raw, cls.space, '2024-12-31T23:59:59+00:00')

    def test_only_post_freeze_proxy_fills_and_no_duplicate_days(self):
        first = paper.observe(self.model, None, self.features, '2025-01-01')
        self.assertEqual(first['days'], 0)  # The prior close is before freeze.
        result = paper.observe(self.model, first, self.features, '2025-02-01')
        self.assertEqual(result['ledger'][0]['date'], '2025-01-02')
        self.assertEqual(result, paper.observe(self.model, result, self.features, '2025-02-01'))
        self.assertFalse(self.model['broker_orders_enabled'])

    def test_incremental_matches_bulk_and_future_does_not_change_past(self):
        jan = paper.observe(self.model, None, self.features, '2025-01-31')
        incremental = paper.observe(self.model, jan, self.features, '2025-02-28')
        bulk = paper.observe(self.model, None, self.features, '2025-02-28')
        self.assertEqual(incremental, bulk)
        self.assertEqual(jan['ledger'], bulk['ledger'][:len(jan['ledger'])])
        self.assertTrue(all(row['decision_data_through'] < row['date'] for row in bulk['ledger']))

    def test_history_revision_and_model_replacement_are_refused(self):
        state = paper.observe(self.model, None, self.features, '2025-01-31')
        f = copy.deepcopy(self.features)
        f['prices'][1000,0] *= 1.01
        with self.assertRaisesRegex(ValueError, 'history changed'):
            paper.observe(self.model, state, f, '2025-02-28')
        model = copy.deepcopy(self.model)
        model['config']['gross'] = 42
        with self.assertRaisesRegex(ValueError, 'model was modified'):
            paper.observe(model, state, self.features, '2025-02-28')
