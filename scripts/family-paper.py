#!/usr/bin/env python3
"""Freeze one algorithm family and observe subsequent data without broker orders."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np
import research_families as families
import research_search as base


def source_hash():
    h = hashlib.sha256()
    h.update(Path(__file__).read_bytes())
    for module in (families, base):
        h.update(Path(module.__file__).read_bytes())
    return h.hexdigest()


def history_hash(data, through):
    # Metadata and future rows do not participate; a correction of observed
    # prices, timestamps, corporate actions, or FX must stop continuation.
    cutoff = through+'T23:59:59+00:00'
    norm = lambda t: dt.datetime.fromisoformat(t.replace('Z','+00:00')).astimezone(dt.timezone.utc).isoformat()
    history = dict(instruments=[dict(id=a['id'], market=a['market'], currency=a['currency'],
                                    bars=[b for b in a['bars'] if norm(b['close_at']) <= cutoff],
                                    actions=[e for e in a.get('actions',[]) if norm(e['at']) <= cutoff])
                               for a in sorted(data['instruments'],key=lambda a:a['id'])],
                   fx=[q for q in data['fx'] if norm(q['at']) <= cutoff])
    return base.digest(history)


def freeze(report, data_bytes, space, created_at):
    if report['provenance']['data_sha256'] != hashlib.sha256(data_bytes).hexdigest():
        raise ValueError('Input differs from the evaluated experiment')
    if report['provenance']['source_sha256'] != families.source_fingerprint():
        raise ValueError('Algorithm source differs from evaluated experiment')
    selected = report['overall_selected']
    if base.digest(selected['config']) != selected['config_sha256']:
        raise ValueError('Evaluated candidate config was modified')
    data = json.loads(data_bytes)
    through = max(data['end'], created_at[:10])
    model = dict(schema_version=1, kind='frozen_family_research_observer', status='research_only',
                 broker_orders_enabled=False, adoption_approved=False,
                 id=selected['id'], config=selected['config'], space=space,
                 created_at=created_at, frozen_through=through,
                 source_sha256=source_hash(), input_sha256=report['provenance']['data_sha256'],
                 historical_prefix_sha256=history_hash(data,through),
                 selection_evidence=dict(train=selected['train'],validation=selected['validation']),
                 notice='Approximate forward research only. Not approved for trading.')
    model['model_sha256'] = base.digest(model)
    return model


def observe(model, state, data, asof):
    if base.digest({k:v for k,v in model.items() if k!='model_sha256'}) != model['model_sha256']:
        raise ValueError('Frozen model was modified')
    if source_hash() != model['source_sha256']:
        raise ValueError('Frozen algorithm source changed')
    if history_hash(data,model['frozen_through']) != model['historical_prefix_sha256']:
        raise ValueError('Pre-freeze history changed')
    if state:
        if state['model_sha256'] != model['model_sha256']:
            raise ValueError('State belongs to another model')
        if asof < state['last_date']:
            raise ValueError('Cannot observe backwards')
        if history_hash(data,state['last_date']) != state['prefix_sha256']:
            raise ValueError('Previously observed history changed')
    f = families.prepare_data(data,model['space'])
    dates = f['dates']
    future = np.flatnonzero((dates > model['frozen_through']) & (dates <= asof))
    initial = model['space'].get('initial_jpy',1_000_000)
    result = dict(schema_version=1, model_sha256=model['model_sha256'], status='awaiting_new_data',
                  last_date=model['frozen_through'], initial=initial, equity=initial,spy_equity=initial,days=0,ledger=[],
                  broker_orders_enabled=False)
    if len(future)>=2:
        # First possible proxy allocation is the first NEW complete close.
        # Its return is first observed at the following close.
        start = str(dates[future[1]])
        candidate = families.simulate(f,[model['config']],start,asof,liquidate=False)
        spy = families.simulate(f,[model['config']],start,asof,benchmark=True,liquidate=False)
        values = np.cumprod(1+candidate['returns'][0])*initial
        baseline = np.cumprod(1+spy['returns'][0])*initial
        ledger = [dict(date=str(dates[i]),equity=float(v),spy_equity=float(b))
                  for i,v,b in zip(candidate['indices'],values,baseline)]
        if state and ledger[:len(state['ledger'])] != state['ledger']:
            raise ValueError('Replay would rewrite previously observed balances')
        result.update(status='observing_research_only',last_date=ledger[-1]['date'],
                      equity=ledger[-1]['equity'],spy_equity=ledger[-1]['spy_equity'],days=len(ledger),ledger=ledger)
    result['prefix_sha256']=history_hash(data,result['last_date'])
    return result


def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['freeze','observe'])
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--model',type=Path,default=root/'research/history-family-forward/model.json')
    p.add_argument('--state',type=Path,default=root/'research/history-family-forward/state.json')
    p.add_argument('--report',type=Path,default=root/'src/research-families-results.json')
    p.add_argument('--space',type=Path,default=root/'research/family-space.json')
    p.add_argument('--asof',default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    args=p.parse_args()
    now=dt.datetime.now(dt.timezone.utc)
    if args.action=='freeze':
        report=json.loads(args.report.read_text())
        if hashlib.sha256(args.space.read_bytes()).hexdigest()!=report['provenance']['search_space_sha256']:
            raise ValueError('Search space differs from the evaluated experiment')
        model=freeze(report,args.input.read_bytes(),json.loads(args.space.read_text()),now.isoformat())
        args.model.parent.mkdir(parents=True,exist_ok=True)
        with args.model.open('x') as out:
            out.write(json.dumps(model,indent=2,allow_nan=False)+'\n')
        print(json.dumps(dict(id=model['id'],status=model['status'],frozen_through=model['frozen_through'])))
    else:
        if args.asof>now.date().isoformat():
            raise ValueError('Future observation date')
        model=json.loads(args.model.read_text())
        data=json.loads(args.input.read_text())
        last_complete=(now.date()-dt.timedelta(days=1)).isoformat()
        data['end']=min(data.get('end',last_complete),last_complete)
        state=json.loads(args.state.read_text()) if args.state.exists() else None
        result=observe(model,state,data,args.asof)
        args.state.parent.mkdir(parents=True,exist_ok=True)
        temp=args.state.with_suffix('.tmp')
        temp.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
        temp.replace(args.state)
        print(json.dumps({k:result[k] for k in ('status','days','last_date','equity','spy_equity')}))


if __name__=='__main__':
    main()
