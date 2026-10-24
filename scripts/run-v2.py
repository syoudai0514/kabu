"""Run all three periods. PIT and reference modes must be selected explicitly."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import pathlib
from v2_engine import instant, replay, validate

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=pathlib.Path)
    parser.add_argument('--mode', required=True, choices=['point_in_time_equities', 'etf_reference'])
    parser.add_argument('--config', type=pathlib.Path, default=ROOT/'research/v2-config.json')
    parser.add_argument('--out', type=pathlib.Path, default=ROOT/'research/history-v2-2026-10-24/results.json')
    parser.add_argument('--summary', type=pathlib.Path, default=ROOT/'reports/v2-reference-2026-10-24-summary.json')
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    config = json.loads(args.config.read_text())
    if data.get('mode') != args.mode:
        parser.error('Mode mismatch: reference ETFs cannot stand in for PIT equities')
    try:
        validate(data, config)
    except (ValueError, KeyError) as error:
        parser.error('Refusing incomplete/invalid data: '+str(error))
    cases = []
    for start in config['starts']:
        for label, kwargs in [('weekly_trend', {}), ('ma20_60', dict(strategy='ma20_60')),
                              ('double_costs', dict(costs_multiplier=2)), ('equal_buy_hold', dict(benchmark='equal')),
                              ('spy_buy_hold', dict(benchmark='SPY')), ('nikkei_etf_buy_hold', dict(benchmark='1346.T'))]:
            if args.mode == 'point_in_time_equities' and kwargs.get('benchmark'):
                continue  # Investable ETF benchmark needs a separate, explicitly supplied dataset.
            result = replay(data, config, start, **kwargs)
            result['summary']['case'] = label
            cases.append(result)
        delayed = copy.deepcopy(data)
        for quote in delayed['fx']:
            quote['at'] = (instant(quote['at'])+dt.timedelta(days=1)).isoformat()
        result = replay(delayed, config, start)
        result['summary']['case'] = 'extra_day_fx_delay'
        cases.append(result)
    result = dict(mode=args.mode, config=config, input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                  sources=data.get('sources', []), coverage=data['coverage'], retrieved_at=data.get('retrieved_at'),
                  notice='Reference ETF results are not a 500-stock strategy result' if args.mode == 'etf_reference' else 'Provider attestations require independent audit',
                  runs=cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    # Derived summaries only; input prices and daily/trade ledgers stay local.
    summary = dict(result, runs=[dict(summary=r['summary']) for r in cases])
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    for r in cases:
        s = r['summary']
        print(s['start'], s['case'], round(s['final']), f"{s['return_']:+.2%}", f"DD {s['max_drawdown']:.2%}", f"exposure {s['average_stock_exposure']:.2%}")


if __name__ == '__main__':
    main()
