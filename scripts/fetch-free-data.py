"""Collect free public directories and SEC filing metadata into a local ledger."""
import argparse
import json
from free_data import collect, SOURCES

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='research/history-free-data')
    parser.add_argument('--source', choices=list(SOURCES), action='append')
    args = parser.parse_args()
    report = collect(args.output, args.source)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if any(s['status'] != 'ok' for s in report['sources']) else 0)
