#!/usr/bin/env python3
"""Frozen nine-family research trials with deterministic shards/checkpoints."""
import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import platform
import resource
import time

import numpy as np

import research_families as engine


def main():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,default=root/'research/history-v2-2026-10-24/reference-input.json')
    p.add_argument('--space',type=Path,default=root/'research/family-space.json')
    p.add_argument('--output',type=Path,default=root/'research/history-families-2026-10-24')
    p.add_argument('--summary',type=Path,default=root/'src/research-families-results.json')
    p.add_argument('--batch-size',type=int,default=512)
    p.add_argument('--shards',type=int,default=1)
    p.add_argument('--shard-index',type=int,default=0)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--aggregate',action='store_true')
    args = p.parse_args()
    if args.batch_size<1:
        p.error('batch-size must be positive')
    started = time.monotonic()
    db,sb = args.input.read_bytes(),args.space.read_bytes()
    data,space = json.loads(db),json.loads(sb)
    items = engine.candidates(space)
    identity = dict(engine_version=engine.ENGINE_VERSION,data_sha256=hashlib.sha256(db).hexdigest(),
                    search_space_sha256=hashlib.sha256(sb).hexdigest(),source_sha256=engine.source_fingerprint(),
                    seed=space['seed'],expected_trials=len(items))
    args.output.mkdir(parents=True,exist_ok=True)
    manifest = args.output/'manifest.json'
    if manifest.exists():
        old = json.loads(manifest.read_text())
        if any(old.get(k)!=v for k,v in identity.items()):
            raise ValueError('Frozen manifest differs (data, space or source); use a new output directory')
    else:
        frozen = dict(**identity,candidates=items,definitions=space['families'],limitations=engine.LIMITATIONS)
        tmp = manifest.with_suffix(f'.{args.shard_index}.tmp')
        tmp.write_text(json.dumps(frozen,ensure_ascii=False,indent=2)+'\n')
        tmp.replace(manifest)
    f = engine.prepare_data(data,space)
    if args.aggregate:
        rows = []
        for path in sorted(args.output.glob('checkpoint-*.jsonl')):
            rows.extend(json.loads(line) for line in path.read_text().splitlines())
    else:
        batch_items = engine.base.shard_candidates(items,args.shard_index,args.shards)
        checkpoint = args.output/f'checkpoint-{args.shard_index:03d}-of-{args.shards:03d}.jsonl'
        rows = []
        if checkpoint.exists():
            if not args.resume:
                raise ValueError('Checkpoint exists; use --resume or a new directory')
            lines = checkpoint.read_text().splitlines()
            for k,line in enumerate(lines):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    if k != len(lines)-1:
                        raise
            checkpoint.write_text(''.join(engine.base.canonical(r)+'\n' for r in rows))
        expected = {x['id']:x['config'] for x in batch_items}
        if len(rows)!=len({r['id'] for r in rows}) or any(expected.get(r['id'])!=r['config'] for r in rows):
            raise ValueError('Checkpoint candidate mismatch')
        done = {r['id'] for r in rows}
        with checkpoint.open('a') as out:
            for family in space['families']:
                pending = [x for x in batch_items if x['family']==family and x['id'] not in done]
                for offset in range(0,len(pending),args.batch_size):
                    batch = engine.evaluate_batch(f,pending[offset:offset+args.batch_size],space)
                    for row in batch:
                        out.write(engine.base.canonical(row)+'\n')
                    out.flush()
                    rows.extend(batch)
                    print(json.dumps(dict(family=family,completed=len(rows),shard_total=len(batch_items),elapsed_seconds=round(time.monotonic()-started,2))),flush=True)
        if args.shards!=1:
            print('Shard complete; use --aggregate when all shards finish.',flush=True)
            return
    expected = {x['id']:x['config'] for x in items}
    if len(rows)!=len(expected) or len({r['id'] for r in rows})!=len(expected) or any(expected.get(r['id'])!=r['config'] for r in rows):
        raise ValueError('Aggregation requires each frozen config exactly once')
    rows.sort(key=lambda r:r['id'])
    headers = ['id','family','config_sha256','config','score','passes_train','passes_validation']
    for name in space['partitions']:
        headers += [name+'_'+k for k in rows[0][name]]
    with (args.output/'all-candidates.csv').open('w',newline='') as out:
        writer = csv.DictWriter(out,fieldnames=headers)
        writer.writeheader()
        for row in rows:
            flat = {k:row[k] for k in headers[:7]}
            flat['config'] = engine.base.canonical(flat['config'])
            for name in space['partitions']:
                flat.update({name+'_'+k:v for k,v in row[name].items()})
            writer.writerow(flat)
    provenance = dict(**identity,total_trials=len(rows),family_trials={k:sum(r['family']==k for r in rows) for k in space['families']},
                      completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),python=platform.python_version(),numpy=np.__version__,
                      all_metrics_csv=str(args.output/'all-candidates.csv'),
                      checkpoint_files=[x.name for x in sorted(args.output.glob('checkpoint-*.jsonl'))])
    summary = engine.summarize(f,rows,space,provenance)
    provenance.update(wall_seconds=round(time.monotonic()-started,3),max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    args.summary.parent.mkdir(parents=True,exist_ok=True)
    args.summary.write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(summary=str(args.summary),**provenance)),flush=True)


if __name__=='__main__':
    main()
