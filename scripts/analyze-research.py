#!/usr/bin/env python3
"""Independently reconcile all saved trials and summarize parameter sensitivity."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import statistics


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trials', type=Path, default=root/'reports/research-trials-2026-10-24.csv.gz')
    parser.add_argument('--summary', type=Path, default=root/'src/research-results.json')
    parser.add_argument('--output', type=Path, default=root/'reports/research-analysis-2026-10-24.json')
    args = parser.parse_args()
    with gzip.open(args.trials, 'rt') as source:
        rows = list(csv.DictReader(source))
    summary = json.loads(args.summary.read_text())
    ids, configs = set(), set()
    for row in rows:
        config = json.loads(row['config'])
        digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if row['id'] in ids or digest in configs or digest != row['config_sha256']:
            raise ValueError('Duplicate or inconsistent saved trial')
        ids.add(row['id']); configs.add(digest)
        row['config'] = config
        for period in ('train', 'validation', 'audit'):
            for field in ('excess_cagr','cagr','max_drawdown'):
                name = f'{period}_{field}'
                row[name] = float(row[name])
        tr, va = row['train_excess_cagr'], row['validation_excess_cagr']
        row['both'] = tr > 0 and va > 0
        score = va-.25*abs(row['validation_max_drawdown'])-.5*max(0,-tr)
        if abs(score-float(row['score'])) > 1e-12:
            raise ValueError('Saved score does not match frozen objective')
        row['score'] = score
    ranked = sorted(rows, key=lambda r:(not r['both'], -r['score'], r['id']))
    if len(rows) != summary['provenance']['executed_trials'] or [r['id'] for r in ranked[:20]] != [r['id'] for r in summary['top20']]:
        raise ValueError('Published trial count or ranking differs from all-trial ledger')
    dimensions = {}
    for key in ranked[0]['config']:
        groups = {}
        for row in rows:
            label = json.dumps(row['config'][key])
            groups.setdefault(label, []).append(row)
        dimensions[key] = [dict(value=json.loads(label), count=len(group),
                                median_validation_excess_cagr=statistics.median(r['validation_excess_cagr'] for r in group),
                                validation_positive_fraction=sum(r['validation_excess_cagr'] > 0 for r in group)/len(group),
                                positive_both_count=sum(r['both'] for r in group)) for label,group in groups.items()]
    analysis = dict(trials=len(rows), unique_configs=len(configs), independently_reconciled=True,
                    selected_id=ranked[0]['id'],
                    positive_train=sum(r['train_excess_cagr'] > 0 for r in rows),
                    positive_validation=sum(r['validation_excess_cagr'] > 0 for r in rows),
                    positive_both=sum(r['both'] for r in rows),
                    positive_all_three=sum(r['both'] and r['audit_excess_cagr'] > 0 for r in rows),
                    dimensions=dimensions,
                    notice='Descriptive in-sample sensitivity; correlated trials are not independent evidence or future probabilities.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(analysis, indent=2)+'\n')
    print(json.dumps({k:v for k,v in analysis.items() if k not in ('dimensions','notice')}))


if __name__ == '__main__':
    main()
