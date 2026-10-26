#!/usr/bin/env python3
"""Offline frozen-grid runner; outputs are immutable and outcome rows precede ranking."""
import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
from pathlib import Path
import time
import numpy as np
import equity_screen as engine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT/'research/history-equity-screen-2026-10-26'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path,obj):
    with path.open('x') as stream:
        json.dump(obj,stream,indent=2,allow_nan=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input',type=Path,default=DEFAULT/'input.npz')
    p.add_argument('--space',type=Path,default=ROOT/'research/equity-screen-space.json')
    p.add_argument('--output',type=Path,default=DEFAULT/'comparison-v1')
    args = p.parse_args()
    started = time.monotonic()
    manifest_path = args.input.with_name('input-manifest.json')
    manifest = json.loads(manifest_path.read_text())
    if sha(args.input) != manifest['input_sha256']:
        raise ValueError('Input hash mismatch')
    space = json.loads(args.space.read_text())
    items = engine.candidates(space)
    counts = dict(collections.Counter(x['family'] for x in items))
    if counts != space['expected_counts'] or len(items)!=space['expected_trials'] or len({x['config_sha256'] for x in items})!=len(items):
        raise ValueError('Frozen grid count or uniqueness mismatch')
    with np.load(args.input,allow_pickle=False) as archive:
        data = {k:archive[k] for k in archive.files}
    data['symbols'],data['markets'] = data['symbols'].tolist(),data['markets'].tolist()
    mask = np.asarray(data['equity_mask'],bool)
    if mask.sum()!=500 or len(mask)!=503 or set(np.asarray(data['symbols'])[~mask])!={'SPY','1348.T','1346.T'}:
        raise ValueError('Expected 500 equities and three exact benchmarks')
    if any(np.sum(mask & (np.asarray(data['markets'])==m))!=250 for m in ('JP','US')):
        raise ValueError('Expected 250 equities per country')
    args.output.mkdir(parents=True,exist_ok=False)
    provenance = dict(schema_version=1,input_sha256=sha(args.input),input_manifest_sha256=sha(manifest_path),
                      engine_sha256=sha(engine.__file__),runner_sha256=sha(__file__),space_sha256=sha(args.space),
                      config_sequence_sha256=hashlib.sha256(engine.canonical(items).encode()).hexdigest(),
                      trials=len(items),counts=counts,started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                      frozen_protocol_commit='9bfc73e',adoption_approved=False,historical_pit_complete=False)
    write_json(args.output/'run-provenance.json',provenance)
    f = engine.prepare_data(data,space)
    initial = space['initial']
    periods = space['periods']
    def evaluate(configs,multiplier=1):
        return {name:engine.metrics(engine.simulate(f,configs,bounds['start'],bounds['end'],multiplier=multiplier),f,initial)
                for name,bounds in periods.items()}
    bench_configs = {name:dict(family='buy_hold',symbol=symbol,rebalance=cadence)
                     for name,symbol,cadence in [('SPY','SPY',0),('TOPIX','1348.T',0),('NIKKEI','1346.T',0),('country_equal','country_equal',21)]}
    benchmarks = {name:{period:vals[0] for period,vals in evaluate([cfg]).items()} for name,cfg in bench_configs.items()}
    rows = []
    for family in space['families']:
        group = [x for x in items if x['family']==family]
        vals = evaluate([x['config'] for x in group])
        family_rows = []
        for j,item in enumerate(group):
            row = dict(item)
            for period in periods:
                metric = vals[period][j]
                metric['excess_cagr'] = metric['cagr']-benchmarks['SPY'][period]['cagr']
                row[period] = metric
            family_rows.append(row)
        write_json(args.output/(family+'-metrics.json'),family_rows)
        rows.extend(family_rows)
        print(f'{family}: {len(group)} configurations evaluated; {time.monotonic()-started:.1f}s',flush=True)
    # Persist every metric before making any selection or computing ranking scores.
    columns = ['id','family','config_sha256','config']+[period+'_'+k for period in periods for k in rows[0][period]]
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text,fieldnames=columns)
    writer.writeheader()
    for row in rows:
        flat = {k:row[k] for k in ('id','family','config_sha256')}
        flat['config'] = engine.canonical(row['config'])
        flat.update({period+'_'+k:v for period in periods for k,v in row[period].items()})
        writer.writerow(flat)
    with (args.output/'all-trials.csv.gz').open('xb') as raw:
        with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as stream:
            stream.write(text.getvalue().encode())
    def score(row):
        return row['development']['excess_cagr']-.5*abs(row['development']['max_drawdown'])
    def rank(group):
        return sorted(group,key=lambda row:(-score(row),row['id']))
    selected = []
    for family in space['families']:
        group = [x for x in rows if x['family']==family]
        chosen = dict(rank(group)[0])
        chosen['selection_score'] = score(chosen)
        chosen['cost2x'] = {period:vals[0] for period,vals in evaluate([chosen['config']],2).items()}
        chosen['counts'] = dict(trials=len(group),positive_development=sum(x['development']['excess_cagr']>0 for x in group),
                               positive_diagnostic=sum(x['diagnostic']['excess_cagr']>0 for x in group),
                               positive_both=sum(x['development']['excess_cagr']>0 and x['diagnostic']['excess_cagr']>0 for x in group))
        selected.append(chosen)
    full = periods['one_year']
    curve_configs = [x['config'] for x in selected]+list(bench_configs.values())
    curve_names = [x['family'] for x in selected]+list(bench_configs)
    run = engine.simulate(f,curve_configs,full['start'],full['end'])
    curves = [dict(date=str(day),**{name:float(run['equity'][i,j]*initial) for j,name in enumerate(curve_names)}) for i,day in enumerate(run['dates'])]
    summary = dict(schema_version=1,mode='current_survivor_exploratory',adoption_approved=False,historical_pit_complete=False,
                   provenance=provenance,periods=periods,ranking=space['selection'],trials=len(rows),counts=counts,
                   overall_selected_id=rank(rows)[0]['id'],selected=selected,benchmarks=benchmarks,
                   benchmarks_cost2x={name:{period:vals[0] for period,vals in evaluate([cfg],2).items()} for name,cfg in bench_configs.items()},
                   curves=curves,all_trials_sha256=sha(args.output/'all-trials.csv.gz'),elapsed_seconds=time.monotonic()-started)
    write_json(args.output/'summary.json',summary)
    for row in selected:
        print(f"{row['family']}: development excess {row['development']['excess_cagr']:.2%}; diagnostic excess {row['diagnostic']['excess_cagr']:.2%}; one-year return {row['one_year']['return_']:.2%}",flush=True)
    print(f'Completed {len(rows)} trials in {time.monotonic()-started:.1f}s: {args.output}',flush=True)


if __name__=='__main__':
    main()
