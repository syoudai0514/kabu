#!/usr/bin/env python3
"""Restartable/shardable NumPy screening. No broker, network or LLM calls."""
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
import research_search as engine


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=root/'research/history-v2-2026-10-24/reference-input.json')
    parser.add_argument('--space', type=Path, default=root/'research/search-space.json')
    parser.add_argument('--output', type=Path, default=root/'research/history-search-2026-10-24')
    parser.add_argument('--summary', type=Path, default=root/'src/research-results.json')
    parser.add_argument('--batch-size', type=int, default=512)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--aggregate', action='store_true', help='Merge existing shard checkpoints; requires every frozen candidate exactly once')
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('batch-size must be positive')
    started = time.monotonic()
    data_bytes, space_bytes = args.input.read_bytes(), args.space.read_bytes()
    data, space = json.loads(data_bytes), json.loads(space_bytes)
    all_items = engine.candidates(space)
    data_hash, space_hash = hashlib.sha256(data_bytes).hexdigest(), hashlib.sha256(space_bytes).hexdigest()
    identity = dict(engine_version=engine.ENGINE_VERSION, data_sha256=data_hash, search_space_sha256=space_hash,
                    source_sha256=hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest(),
                    seed=space['seed'], expected_trials=len(all_items))
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output/'manifest.json'
    manifest = dict(**identity, candidates=all_items, limitations=engine.LIMITATIONS,
                    selection_uses=['train','validation'], audit_status='previously_seen_diagnostic_only')
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if 'source_sha256' not in old:
            raise ValueError('Frozen manifest predates source fingerprints; preserve it and use a new output directory')
        if any(old.get(k) != identity[k] for k in identity):
            raise ValueError('Frozen manifest differs; use a new output directory')
    else:
        tmp = manifest_path.with_suffix(f'.{args.shard_index}.tmp')
        tmp.write_text(json.dumps(manifest, indent=2)+'\n')
        tmp.replace(manifest_path)
    f = engine.prepare_data(data, space)
    checkpoint = args.output/f'checkpoint-{args.shard_index:03d}-of-{args.shards:03d}.jsonl'
    if not args.aggregate:
        items = engine.shard_candidates(all_items, args.shard_index, args.shards)
        rows = []
        if checkpoint.exists():
            if not args.resume:
                raise ValueError('Checkpoint exists; pass --resume or use new output directory')
            # A torn final append may be discarded; all complete rows must match frozen configs.
            lines = checkpoint.read_text().splitlines()
            for k,line in enumerate(lines):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    if k != len(lines)-1:
                        raise
            checkpoint.write_text(''.join(engine.canonical(r)+'\n' for r in rows))
        expected = {x['id']: x['config'] for x in items}
        if len({r['id'] for r in rows}) != len(rows) or any(expected.get(r['id']) != r['config'] for r in rows):
            raise ValueError('Invalid checkpoint candidates')
        done = {r['id'] for r in rows}
        pending = [x for x in items if x['id'] not in done]
        with checkpoint.open('a') as out:
            for offset in range(0, len(pending), args.batch_size):
                batch = engine.evaluate_batch(f, pending[offset:offset+args.batch_size], space)
                for row in batch:
                    out.write(engine.canonical(row)+'\n')
                out.flush()
                rows.extend(batch)
                print(json.dumps(dict(completed=len(rows), shard=args.shard_index, shard_total=len(items), elapsed_seconds=round(time.monotonic()-started,2))), flush=True)
        if args.shards != 1:
            print('Shard complete; merge with --aggregate after all workers finish.', flush=True)
            return
    else:
        rows = []
        for path in sorted(args.output.glob('checkpoint-*.jsonl')):
            rows.extend(json.loads(line) for line in path.read_text().splitlines())
    expected = {x['id']: x['config'] for x in all_items}
    if len(rows) != len(expected) or len({r['id'] for r in rows}) != len(expected) or any(expected.get(r['id']) != r['config'] for r in rows):
        raise ValueError('Aggregation requires every frozen candidate exactly once')
    rows.sort(key=lambda r:r['id'])
    columns = ['id', 'config_sha256', 'config', 'score', 'passes_train', 'passes_validation']
    for period in space['partitions']:
        columns += [f'{period}_{k}' for k in rows[0][period]]
    with (args.output/'all-candidates.csv').open('w', newline='') as out:
        writer = csv.DictWriter(out, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            flat = {k:row[k] for k in columns[:6]}
            flat['config'] = engine.canonical(flat['config'])
            for period in space['partitions']:
                flat.update({f'{period}_{k}':v for k,v in row[period].items()})
            writer.writerow(flat)
    provenance = dict(**identity, executed_trials=len(rows), completed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                      python=platform.python_version(), numpy=np.__version__,
                      checkpoint_files=[p.name for p in sorted(args.output.glob('checkpoint-*.jsonl'))],
                      all_metrics_csv=str(args.output/'all-candidates.csv'),
                      wall_seconds=round(time.monotonic()-started,3), max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    summary = engine.summaries(f, rows, space, provenance)
    provenance['wall_seconds'] = round(time.monotonic()-started,3)
    provenance['max_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    print(json.dumps(dict(summary=str(args.summary), **provenance)), flush=True)


if __name__ == '__main__':
    main()
