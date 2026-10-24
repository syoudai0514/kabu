import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('family_paper',ROOT/'scripts/family-paper.py')
paper=importlib.util.module_from_spec(spec)
spec.loader.exec_module(paper)
from test_research_search import synthetic_reference


class FamilyForwardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=synthetic_reference()
        cls.space=json.loads((ROOT/'research/family-space.json').read_text())
        small=copy.deepcopy(cls.space)
        small['trials_per_family']=1
        cls.items=paper.families.candidates(small)
        historical=copy.deepcopy(cls.data)
        historical['end']='2024-12-31'
        raw=json.dumps(historical).encode()
        cls.models=[]
        for item in cls.items:
            report=dict(provenance=dict(data_sha256=hashlib.sha256(raw).hexdigest(),source_sha256=paper.families.source_fingerprint()),
                        overall_selected=dict(**item,train={},validation={}))
            cls.models.append(paper.freeze(report,raw,cls.space,'2024-12-31T23:59:59+00:00'))

    def test_every_family_forward_prefix_and_idempotency(self):
        for model in self.models:
            with self.subTest(family=model['config']['family']):
                first=paper.observe(model,None,self.data,'2025-01-01')
                self.assertEqual(first['days'],0)
                jan=paper.observe(model,first,self.data,'2025-01-31')
                feb=paper.observe(model,jan,self.data,'2025-02-28')
                self.assertEqual(jan['ledger'],feb['ledger'][:jan['days']])
                self.assertEqual(feb,paper.observe(model,feb,self.data,'2025-02-28'))
                self.assertEqual(feb,paper.observe(model,None,self.data,'2025-02-28'))
                self.assertFalse(feb['broker_orders_enabled'])

    def test_previously_observed_revision_and_changed_model_stop(self):
        model=self.models[0]
        jan=paper.observe(model,None,self.data,'2025-01-31')
        changed=copy.deepcopy(self.data)
        bar=next(b for b in changed['instruments'][0]['bars'] if b['date']=='2025-01-15')
        bar['close']*=1.01
        with self.assertRaisesRegex(ValueError,'Previously observed history changed'):
            paper.observe(model,jan,changed,'2025-02-28')
        altered=copy.deepcopy(model)
        altered['config']['gross']=42
        with self.assertRaisesRegex(ValueError,'model was modified'):
            paper.observe(altered,jan,self.data,'2025-02-28')
