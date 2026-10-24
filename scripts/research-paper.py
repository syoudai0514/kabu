#!/usr/bin/env python3
"""Freeze a research candidate and observe NEW data, without orders or adoption.

Forward paper observation uses the same fractional close approximation as search.
It never selects a new model, calls a broker or claims executable trading returns.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np
import research_search as engine


def prefix_hash(features, end):
    count = int(np.searchsorted(features['dates'], end, side='right'))
    digest = hashlib.sha256()
    digest.update(engine.canonical(features['symbols']).encode())
    digest.update(engine.canonical(features['dates'][:count].tolist()).encode())
    for name in ('prices', 'returns', 'counts'):
        digest.update(np.ascontiguousarray(features[name][:count]).tobytes())
    return digest.hexdigest()


def freeze(report, data_bytes, space, created_at):
    if report['provenance']['data_sha256'] != hashlib.sha256(data_bytes).hexdigest():
        raise ValueError('Research input differs from the selected experiment')
    selected = report['selected']
    if selected['config_sha256'] != engine.digest(selected['config']):
        raise ValueError('Candidate config was modified after evaluation')
    f = engine.prepare_data(json.loads(data_bytes), space)
    # Do not label earlier observed dates as a forward paper experiment.
    frozen_through = max(str(f['dates'][-1]), created_at[:10])
    model = dict(schema_version=1, kind='frozen_research_observer', status='research_only',
                 broker_orders_enabled=False, adoption_approved=False,
                 id=selected['id'], config=selected['config'], space=space,
                 created_at=created_at, frozen_through=frozen_through,
                 data_last_date=str(f['dates'][-1]), input_sha256=report['provenance']['data_sha256'],
                 source_sha256=hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest(),
                 historical_prefix_sha256=prefix_hash(f, frozen_through),
                 selection_evidence=dict(train=selected['train'], validation=selected['validation']),
                 notice='Experimental fractional observer only; not adopted and never sends orders.')
    model['model_sha256'] = engine.digest(model)
    return model


def observe(model, state, f, asof):
    unsigned = {k:v for k,v in model.items() if k != 'model_sha256'}
    if engine.digest(unsigned) != model['model_sha256']:
        raise ValueError('Frozen model was modified; create a separate experiment')
    if hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest() != model['source_sha256']:
        raise ValueError('Research engine changed; frozen model requires its original version')
    if prefix_hash(f, model['frozen_through']) != model['historical_prefix_sha256']:
        raise ValueError('Pre-freeze history changed; reconcile data revisions before continuing')
    if state is None:
        count = len(f['symbols'])
        state = dict(schema_version=1, model_sha256=model['model_sha256'],
                     last_date=model['frozen_through'], prefix_sha256=model['historical_prefix_sha256'],
                     days=0, initial=model['space']['initial_jpy'], equity=float(model['space']['initial_jpy']),
                     spy_equity=float(model['space']['initial_jpy']), weights=[0.]*count,
                     spy_weights=[0.]*count, ledger=[], status='awaiting_new_data')
    else:
        state = copy.deepcopy(state)
    if state['model_sha256'] != model['model_sha256']:
        raise ValueError('State belongs to another frozen model')
    if prefix_hash(f, state['last_date']) != state['prefix_sha256']:
        raise ValueError('Previously observed history changed; no silent ledger rewrite')
    if asof < state['last_date']:
        raise ValueError('Cannot move observation backwards')
    c = model['config']
    weights, spy_weights = np.array(state['weights']), np.array(state['spy_weights'])
    for i in np.flatnonzero((f['dates'] > state['last_date']) & (f['dates'] <= asof)):
        if i < f['warmup']:
            raise ValueError('Insufficient warmup for forward observation')
        if str(f['dates'][i-1]) <= model['frozen_through']:
            continue  # Never pretend to fill before the model was frozen.
        date = str(f['dates'][i])
        fee = spy_fee = 0.
        if state['days'] % c['rebalance'] == 0:
            target = engine.desired_weights(f, i-2, [c])[0]
            fee = state['equity']*float(np.abs(target-weights)@f['cost'])
            state['equity'] -= fee
            weights = target
        if state['days'] == 0:
            spy_weights[f['spy']] = 1.
            spy_fee = state['spy_equity']*float(f['cost'][f['spy']])
            state['spy_equity'] -= spy_fee
        change, spy_change = float(weights@f['returns'][i]), float(spy_weights@f['returns'][i])
        state['equity'] *= 1+change
        state['spy_equity'] *= 1+spy_change
        weights *= (1+f['returns'][i])/(1+change)
        spy_weights *= (1+f['returns'][i])/(1+spy_change)
        state['ledger'].append(dict(date=date, equity=state['equity'], spy_equity=state['spy_equity'],
                                   fee_jpy=fee, spy_fee_jpy=spy_fee, exposure=float(weights.sum()),
                                   decision_data_through=str(f['dates'][i-2])))
        state['days'] += 1
        state['last_date'] = date
    state.update(weights=weights.tolist(), spy_weights=spy_weights.tolist(),
                 prefix_sha256=prefix_hash(f, state['last_date']),
                 status='observing_research_only' if state['days'] else 'awaiting_new_data')
    return state


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as out:
        out.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['freeze', 'observe'])
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--model', type=Path, default=root/'research/history-forward/model.json')
    parser.add_argument('--state', type=Path, default=root/'research/history-forward/state.json')
    parser.add_argument('--report', type=Path, default=root/'src/research-results.json')
    parser.add_argument('--space', type=Path, default=root/'research/search-space.json')
    parser.add_argument('--asof', default=dt.datetime.now(dt.timezone.utc).date().isoformat())
    args = parser.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    if args.action == 'freeze':
        report, space = json.loads(args.report.read_text()), json.loads(args.space.read_text())
        if hashlib.sha256(args.space.read_bytes()).hexdigest() != report['provenance']['search_space_sha256']:
            raise ValueError('Search space differs from frozen experiment')
        model = freeze(report, args.input.read_bytes(), space, now.isoformat())
        write_new(args.model, model)
        print(json.dumps(dict(model=str(args.model), id=model['id'], status=model['status'], frozen_through=model['frozen_through'])))
    else:
        if args.asof > now.date().isoformat():
            raise ValueError('Future observation date is not allowed')
        model = json.loads(args.model.read_text())
        data = json.loads(args.input.read_text())
        # Only fully completed UTC days enter an observation. Current-day bars
        # can be preliminary even when a provider labels them with a close time.
        last_complete = (now.date()-dt.timedelta(days=1)).isoformat()
        data['end'] = min(data.get('end', last_complete), last_complete)
        f = engine.prepare_data(data, model['space'])
        state = json.loads(args.state.read_text()) if args.state.exists() else None
        state = observe(model, state, f, args.asof)
        args.state.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.state.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        temporary.replace(args.state)
        print(json.dumps({k:state[k] for k in ('status','days','last_date','equity','spy_equity')}))


if __name__ == '__main__':
    main()
