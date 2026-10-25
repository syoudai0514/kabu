"""Freeze a sampled universe, retrieve cached free prices, and screen current candidates."""
import argparse
import concurrent.futures
import json
import pathlib
import threading
import time
from free_prices import fetch, PriceError
from free_universe import build_pool, digest, evaluate, summarize
from checkpoints import load_jsonl

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=str(ROOT / 'research/history-free-data/observations.sqlite3'))
    parser.add_argument('--policy', default=str(ROOT / 'research/free-universe-policy.json'))
    parser.add_argument('--output', default=str(ROOT / 'research/history-free-universe-2026-10-25'))
    parser.add_argument('--limit', type=int, default=0, help='0: all pending; a positive number: diagnostic batch')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit cannot be negative')
    policy = json.loads(pathlib.Path(args.policy).read_text())
    output = pathlib.Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['policy_sha256'] != digest(policy) or manifest['pool_sha256'] != digest(manifest['pool']):
            raise ValueError('Manifest mismatch: use a new output for changed policy or pool')
    else:
        manifest = build_pool(args.database, policy)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    engine_hash = digest({f: (ROOT / 'scripts' / f).read_text() for f in ('free_prices.py', 'free_universe.py', 'select-free-universe.py', 'checkpoints.py')})
    guard = output / 'engine-sha256.txt'
    if guard.exists() and guard.read_text() != engine_hash:
        raise ValueError('Engine changed: use a new output directory (copy verified price cache if desired)')
    guard.write_text(engine_hash)
    checkpoint = output / 'checkpoint.jsonl'
    results = {}
    for result in load_jsonl(checkpoint):
        if result['key'] in results:
            raise ValueError('Duplicate checkpoint result')
        results[result['key']] = result
    if any(k not in {x['key'] for x in manifest['pool']} for k in results):
        raise ValueError('Checkpoint contains a security outside the frozen pool')
    if not args.offline and any(r.get('http_status') in (403, 429) for r in results.values()):
        raise ValueError('Prior access/rate block: no automatic network resume')
    pending = [x for x in manifest['pool'] if x['key'] not in results]
    if args.limit:
        pending = pending[:args.limit]
    stop = threading.Event()

    def work(item):
        if stop.is_set():
            return None
        try:
            prices = fetch(item['ticker'], item['market'], policy['start'], policy['end_exclusive'], output / 'prices', offline=args.offline)
            return evaluate(item, prices, policy)
        except PriceError as exc:
            if exc.status in (403, 429):
                stop.set()
            return dict(key=item['key'], market=item['market'], status='failed', error=str(exc), http_status=exc.status)
        finally:
            if not args.offline:
                time.sleep(0.8)

    with checkpoint.open('a') as log, concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(work, pending):
            if result is None:
                continue
            results[result['key']] = result
            log.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
            log.flush()
            if len(results) % 100 == 0:
                print(json.dumps(dict(processed=len(results), total=len(manifest['pool']))), flush=True)
    report = summarize(manifest, results)
    if args.offline:
        for item in manifest['pool']:
            previous = results.get(item['key'])
            if previous and previous['status'] == 'ok':
                prices = fetch(item['ticker'], item['market'], policy['start'], policy['end_exclusive'], output / 'prices', offline=True)
                if evaluate(item, prices, policy) != previous:
                    raise ValueError('Cached replay differs from checkpoint: ' + item['key'])
    report['engine_sha256'] = engine_hash
    temporary = output / 'summary.json.tmp'
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    temporary.replace(output / 'summary.json')
    print(json.dumps({m: {k: v for k, v in row.items() if k != 'selections'} for m, row in report['markets'].items()}, ensure_ascii=False), flush=True)
    if stop.is_set():
        raise SystemExit(2)


if __name__ == '__main__':
    main()
