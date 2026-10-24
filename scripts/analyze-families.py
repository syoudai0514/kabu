#!/usr/bin/env python3
"""Independent reconciliation of family counts, selection and published metrics."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import statistics


def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trials',type=Path,default=root/'reports/family-trials-2026-10-24.csv.gz')
    p.add_argument('--summary',type=Path,default=root/'src/research-families-results.json')
    p.add_argument('--output',type=Path,default=root/'reports/family-analysis-2026-10-24.json')
    args=p.parse_args()
    with gzip.open(args.trials,'rt') as file:
        rows=list(csv.DictReader(file))
    summary=json.loads(args.summary.read_text())
    ids,hashes=set(),set()
    for row in rows:
        cfg=json.loads(row['config'])
        digest=hashlib.sha256(json.dumps(cfg,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if row['id'] in ids or digest in hashes or digest!=row['config_sha256'] or cfg['family']!=row['family']:
            raise ValueError('Duplicate or inconsistent trial configuration')
        ids.add(row['id']); hashes.add(digest)
        for key in ('train_excess_cagr','validation_excess_cagr','audit_excess_cagr','validation_max_drawdown','score'):
            row[key]=float(row[key])
        row['both']=row['train_excess_cagr']>0 and row['validation_excess_cagr']>0
        score=row['validation_excess_cagr']-.25*abs(row['validation_max_drawdown'])-.5*max(0,-row['train_excess_cagr'])
        if abs(score-row['score'])>1e-12:
            raise ValueError('Score differs from fixed selection rule')
    rank=lambda r:(not r['both'],-r['score'],r['id'])
    if len(rows)!=summary['provenance']['total_trials'] or min(rows,key=rank)['id']!=summary['overall_selected']['id']:
        raise ValueError('Overall selection/count does not reconcile')
    output=[]
    for family in summary['families']:
        group=sorted([r for r in rows if r['family']==family['id']],key=rank)
        if len(group)!=family['trials'] or group[0]['id']!=family['selected']['id'] or [r['id'] for r in group[:5]]!=[r['id'] for r in family['top5']]:
            raise ValueError('Family ranking/count differs from ledger')
        counts=dict(positive_train_count=sum(r['train_excess_cagr']>0 for r in group),
                    positive_validation_count=sum(r['validation_excess_cagr']>0 for r in group),
                    positive_both_count=sum(r['both'] for r in group))
        if any(family[key]!=value for key,value in counts.items()):
            raise ValueError('Family pass counts differ')
        for period in ('train','validation','audit'):
            if group[0][period+'_excess_cagr']!=family['selected'][period]['excess_cagr']:
                raise ValueError('Published selected metrics differ from ledger')
        output.append(dict(id=family['id'],name=family['name'],trials=len(group),**counts,
                           median_validation_excess_cagr=statistics.median(r['validation_excess_cagr'] for r in group),
                           selected_id=group[0]['id'],selected_validation_excess_cagr=group[0]['validation_excess_cagr'],
                           selected_audit_excess_cagr=group[0]['audit_excess_cagr'],
                           diagnostic_horizon_wins=sum(w['selected']['final']>w['spy']['final'] for w in family['windows'])))
    result=dict(independently_reconciled=True,trial_count=len(rows),family_count=len(output),
                overall_selected_id=summary['overall_selected']['id'],families=output,
                notice='Nine families, correlated parameter trials. Diagnostic wins are not expected future returns.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('families','notice')}))


if __name__=='__main__':
    main()
