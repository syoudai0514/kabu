"""Independent CSV/grid/ranking and closed-form ETF benchmark reconciliation."""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import itertools
import json
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]


def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def analyze(folder,input_path,published=False):
    folder=Path(folder)
    summary=json.loads((ROOT/'src/equity-screen-results.json' if published else folder/'summary.json').read_text())
    trials_path=ROOT/'reports/equity-screen-trials-2026-10-26.csv.gz' if published else folder/'all-trials.csv.gz'
    space=json.loads((ROOT/'research/equity-screen-space.json').read_text())
    rows=list(csv.DictReader(gzip.open(trials_path,'rt')))
    assert len(rows)==space['expected_trials']==1488
    assert len({x['id'] for x in rows})==len(rows)
    assert digest_bytes(trials_path.read_bytes())==summary['all_trials_sha256']
    if input_path is not None:
        assert digest_bytes(Path(input_path).read_bytes())==summary['provenance']['input_sha256']
    for name,key in [('equity_screen.py','engine_sha256'),('run-equity-screen.py','runner_sha256')]:
        assert digest_bytes((ROOT/'scripts'/name).read_bytes())==summary['provenance'][key]
    expected=set()
    for family,info in space['families'].items():
        keys=sorted(info['dimensions'])
        for values in itertools.product(*(info['dimensions'][k] for k in keys)):
            cfg=dict(family=family,**dict(zip(keys,values)))
            if family=='donchian_breakout' and cfg['exit_window']>=cfg['entry_window']:
                continue
            expected.add(digest_bytes(json.dumps(cfg,sort_keys=True,separators=(',',':')).encode()))
    assert {x['config_sha256'] for x in rows}==expected
    for x in rows:
        assert digest_bytes(json.dumps(json.loads(x['config']),sort_keys=True,separators=(',',':')).encode())==x['config_sha256']
        for period in space['periods']:
            prefix=period+'_'
            final=float(x[prefix+'final']); initial=float(x[prefix+'initial'])
            assert final>0 and initial==space['initial']
            assert abs(final/initial-1-float(x[prefix+'return_']))<1e-12
            days=(dt.date.fromisoformat(x[prefix+'end'])-dt.date.fromisoformat(x[prefix+'valuation_start'])).days
            assert abs((final/initial)**(365.25/days)-1-float(x[prefix+'cagr']))<1e-10
            assert -1<float(x[prefix+'max_drawdown'])<=0 and float(x[prefix+'fees_jpy'])>=0
            assert abs(float(x[prefix+'excess_cagr'])-(float(x[prefix+'cagr'])-summary['benchmarks']['SPY'][period]['cagr']))<1e-12
    def rank(row):
        return (-float(row['development_excess_cagr'])+.5*abs(float(row['development_max_drawdown'])),row['id'])
    assert sorted(rows,key=rank)[0]['id']==summary['overall_selected_id']
    both=0
    for selected in summary['selected']:
        group=[x for x in rows if x['family']==selected['family']]
        chosen=sorted(group,key=rank)[0]
        assert chosen['id']==selected['id']
        count=sum(float(x['development_excess_cagr'])>0 and float(x['diagnostic_excess_cagr'])>0 for x in group)
        assert count==selected['counts']['positive_both']
        both+=count
        for period in space['periods']:
            for metric in ['final','cagr','max_drawdown','fees_jpy','unliquidated_jpy']:
                assert abs(float(chosen[period+'_'+metric])-selected[period][metric])<1e-8
        assert abs(summary['curves'][-1][selected['family']]-selected['one_year']['final'])<1e-7
    # Check buy-and-hold independently, without importing the simulation engine.
    if input_path is not None:
        with np.load(input_path,allow_pickle=False) as z:
            dates=z['dates'].tolist(); symbols=z['symbols'].tolist(); prices=z['mark_prices']; valid=z['valid_observation']
            for name,symbol,market in [('SPY','SPY','US'),('TOPIX','1348.T','JP'),('NIKKEI','1346.T','JP')]:
                a=symbols.index(symbol)
                for period,bounds in space['periods'].items():
                    i=next(i for i,d in enumerate(dates) if d>=bounds['start'])
                    j=max(i for i,d in enumerate(dates) if d<=bounds['end'])
                    assert valid[i-1,a] and valid[j,a]
                    for multiplier,key in [(1,'benchmarks'),(2,'benchmarks_cost2x')]:
                        rate=space['fees'][market]*multiplier
                        final=space['initial']*prices[j,a]/prices[i-1,a]*(1-rate)/(1+rate)
                        assert abs(final-summary[key][name][period]['final'])<1e-6,(name,period,final)
    return dict(independently_reconciled=True,trials=len(rows),families=len(summary['selected']),
                positive_both=both,overall_selected_id=summary['overall_selected_id'],
                representative_diagnostic_wins=sum(x['diagnostic']['excess_cagr']>0 for x in summary['selected']),
                benchmark_closed_form_checks=18 if input_path is not None else 0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',default=str(ROOT/'research/history-equity-screen-2026-10-26/comparison-v1'))
    parser.add_argument('--input',default=str(ROOT/'research/history-equity-screen-2026-10-26/input.npz'))
    parser.add_argument('--published-only',action='store_true')
    args=parser.parse_args()
    print(json.dumps(analyze(args.folder,None if args.published_only else args.input,args.published_only)))
