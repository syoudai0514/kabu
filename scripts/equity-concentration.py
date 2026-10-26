#!/usr/bin/env python3
"""Post-hoc fixed-winner concentration diagnostic; never selects a new model."""
import hashlib
import json
from pathlib import Path
import numpy as np
import equity_screen as engine

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT/'research/history-equity-screen-2026-10-26'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    summary_path = DIRECTORY/'comparison-v1/summary.json'
    summary = json.loads(summary_path.read_text())
    winner = next(x for x in summary['selected'] if x['id']=='short_reversal-7a31d80180bd')
    config = winner['config']
    assert hashlib.sha256(engine.canonical(config).encode()).hexdigest()==winner['config_sha256']
    manifest = json.loads((DIRECTORY/'input-manifest.json').read_text())
    assert sha(DIRECTORY/'input.npz')==manifest['input_sha256']
    space_path = ROOT/'research/equity-screen-space.json'
    assert sha(space_path)==summary['provenance']['space_sha256']
    assert sha(engine.__file__)==summary['provenance']['engine_sha256']
    space = json.loads(space_path.read_text())
    with np.load(DIRECTORY/'input.npz',allow_pickle=False) as archive:
        data = {k:archive[k] for k in archive.files}
    data['symbols'],data['markets'] = data['symbols'].tolist(),data['markets'].tolist()
    f = engine.prepare_data(data,space)
    initial = space['initial']
    concentrations = {}
    for name,bounds in space['periods'].items():
        r = engine.simulate(f,[config],bounds['start'],bounds['end'],targets=True,liquidate=False)
        ending_holdings = r['weights'][:,0,:]*r['equity'][:,0,None]
        returns = f['returns'][r['indices']]
        pnl = np.sum(ending_holdings*returns/(1+returns),axis=0)*initial
        fees = float(r['fees'][0]*initial)
        net = float((r['final'][0]-1)*initial)
        discrepancy = float(pnl.sum()-fees-net)
        if not np.isclose(discrepancy,0,atol=1e-6):
            raise AssertionError('Contribution reconciliation failed')
        order = np.argsort(-pnl,kind='stable')
        rows = [dict(symbol=f['symbols'][j],capital_gain_jpy=float(pnl[j]),
                     fraction_of_net_gain=float(pnl[j]/net) if net else None,
                     peak_weight=float(r['weights'][:,0,j].max()),
                     peak_marked_holdings_jpy=float(ending_holdings[:,j].max()*initial)) for j in order]
        concentrations[name] = dict(liquidate=False,capital_gains_jpy=float(pnl.sum()),fees_jpy=fees,net_gain_jpy=net,
                                    reconciliation_error_jpy=discrepancy,assets=rows)
    top3 = [x['symbol'] for x in concentrations['development']['assets'][:3]]
    exclusions = {}
    for name,symbols in [('exclude_development_top3',top3),('exclude_7746_T',['7746.T'])]:
        modified = dict(f,equity_mask=f['equity_mask'].copy())
        for symbol in symbols:
            modified['equity_mask'][f['symbols'].index(symbol)] = False
        vals = {}
        for mult in (1,2):
            vals[str(mult)+'x'] = {}
            for period,bounds in space['periods'].items():
                r = engine.simulate(modified,[config],bounds['start'],bounds['end'],multiplier=mult)
                metric = engine.metrics(r,modified,initial)[0]
                metric['excess_cagr_vs_spy'] = metric['cagr']-summary['benchmarks' if mult==1 else 'benchmarks_cost2x']['SPY'][period]['cagr']
                vals[str(mult)+'x'][period] = metric
        exclusions[name] = dict(excluded_symbols=symbols,results=vals)
    output = dict(schema_version=1,posthoc_diagnostic=True,used_for_selection=False,
                  adoption_approved=False,historical_pit_complete=False,
                  winner_id=winner['id'],unchanged_config=config,config_sha256=winner['config_sha256'],
                  source_sha256=sha(__file__),engine_sha256=sha(engine.__file__),space_sha256=sha(space_path),
                  input_sha256=sha(DIRECTORY/'input.npz'),original_summary_sha256=sha(summary_path),
                  contribution_method='Sum of post-return marked holdings times r/(1+r), no terminal liquidation; fees reported separately. Peak weights are end-of-day marked weights.',
                  original_concentration=concentrations,exclusions=exclusions)
    output_path = DIRECTORY/'comparison-v1/concentration.json'
    with output_path.open('x') as stream:
        json.dump(output,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(output=str(output_path),development_top3=top3,
                         top3_net_gain_share=sum(x['fraction_of_net_gain'] for x in concentrations['development']['assets'][:3]),
                         exclusion_one_year_returns={k:v['results']['1x']['one_year']['return_'] for k,v in exclusions.items()})))


if __name__=='__main__':
    main()
