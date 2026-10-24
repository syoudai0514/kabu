"""Deterministic, fractional UTC-close screening; never an execution backtest.

Signals at close t-2 set approximate targets at close t-1, earning t's return.
All inputs are known before that proxy allocation; no market opens are used.
Daily FX uses only quotes available at 00:00 UTC, already lagged by source.
"""
import datetime as dt
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np

ENGINE_VERSION = 'screen-1.0'
LIMITATIONS = [
    'Five fixed ETFs chosen with hindsight; not a 500-stock point-in-time universe.',
    'Fractional daily UTC-close approximation; no integer lots, executable fills, market liquidity or tax model.',
    'Simultaneous UTC valuation carries closed-market prices; cross-market close allocations are not executable.',
    'Lagged daily FX proxy and approximate proportional fee/slippage/FX turnover charges; dividends excluded.',
    'Volatility scaling uses a diagonal covariance proxy; correlated ETFs can exceed the nominal volatility target.',
    'Audit prices were already seen; audit and 1/5/10-year figures are diagnostics, never untouched out-of-sample evidence.',
    '24,000 correlated trials create multiple-testing and selection bias; best historical score is not adoption evidence.',
    'Full point-in-time coverage and event-time v2 replay are required before any adoption assessment.',
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def candidates(space):
    """Seeded unique configs; ID and order are stable across shards and restarts."""
    names = list(space['dimensions'])
    grid = list(itertools.product(*(space['dimensions'][k] for k in names)))
    chosen = np.random.default_rng(space['seed']).choice(len(grid), space['trials'], replace=False)
    return [dict(id=f'c{i:05d}', config=dict(zip(names, grid[int(g)]))) for i, g in enumerate(chosen)]


def shard_candidates(items, shard_index=0, shards=1):
    if shards < 1 or not 0 <= shard_index < shards:
        raise ValueError('Invalid shard')
    return [x for i, x in enumerate(items) if i % shards == shard_index]


def _instant(value):
    at = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('Timestamp needs timezone')
    return at.astimezone(dt.timezone.utc)


def prepare_data(data, space):
    if data['mode'] != 'etf_reference' or data['schema_version'] != 2:
        raise ValueError('This approximate engine only accepts v2 etf_reference data')
    assets = sorted(data['instruments'], key=lambda a: a['id'])
    symbols = [a['id'] for a in assets]
    if 'SPY' not in symbols:
        raise ValueError('SPY baseline required')
    sessions = []
    for a in assets:
        if any(e['type'] not in ('lot', 'dividend') for e in a.get('actions', [])):
            raise ValueError('Split/delist actions require explicit screening normalization')
        bars = a['bars']
        closes = [_instant(b['close_at']) for b in bars]
        if closes != sorted(set(closes)) or any(not np.isfinite(b['close']) or b['close'] <= 0 for b in bars):
            raise ValueError('Prices must be positive, finite and chronologically unique')
        sessions.append({at.date().isoformat(): b['close'] for at, b in zip(closes, bars)})
    dates = sorted(set().union(*(s.keys() for s in sessions)))
    dates = [d for d in dates if d <= data.get('end', dates[-1])]
    prices = np.full((len(dates), len(assets)), np.nan)
    counts = np.zeros_like(prices, dtype=int)
    for j, s in enumerate(sessions):
        last, count = np.nan, 0
        for i, day in enumerate(dates):
            if day in s:
                last, count = s[day], count+1
            prices[i, j], counts[i, j] = last, count
    fxquotes = sorted(data['fx'], key=lambda q: _instant(q['at']))
    fx, ptr, last = np.full(len(dates), np.nan), 0, np.nan
    for i, day in enumerate(dates):
        cutoff = _instant(day + 'T00:00:00Z')
        while ptr < len(fxquotes) and _instant(fxquotes[ptr]['at']) <= cutoff:
            last = fxquotes[ptr]['mid']
            if not np.isfinite(last) or last <= 0:
                raise ValueError('Invalid FX quote')
            ptr += 1
        fx[i] = last
    usd = np.array([a['currency'] == 'USD' for a in assets])
    jpy = prices * np.where(usd[None, :], fx[:, None], 1)
    returns = np.zeros_like(jpy)
    returns[1:] = np.nan_to_num(jpy[1:] / jpy[:-1]-1)
    means, momentum = {}, {}
    # Prefix sums replace unavailable leading prices by zero; eligibility prevents their use.
    sums = np.vstack([np.zeros(len(assets)), np.cumsum(np.nan_to_num(prices), axis=0)])
    for n in space['dimensions']['trend']:
        value = np.full_like(prices, np.nan)
        value[n-1:] = (sums[n:]-sums[:-n])/n
        means[n] = value
    for n in space['dimensions']['momentum']:
        value = np.full_like(prices, np.nan)
        value[n:] = prices[n:]/prices[:-n]-1
        momentum[n] = value
    vol = np.full_like(prices, np.nan)
    n = space['volatility_lookback']
    for i in range(n, len(dates)):
        vol[i] = np.std(returns[i-n+1:i+1], axis=0, ddof=1)*np.sqrt(252)
    # Diagonal vol proxy ignores correlations and can understate portfolio risk.
    minimum = space['minimum_history']
    ready = np.flatnonzero(np.all(counts >= minimum, axis=1) & np.all(np.isfinite(jpy), axis=1))
    warmup = max(int(ready[0])+2, max(space['dimensions']['momentum'])+2) if len(ready) else len(dates)
    cost = (space['cost_bps']['fee']+space['cost_bps']['slippage']+usd*space['cost_bps']['us_fx_proxy'])/10000
    return dict(dates=np.array(dates), symbols=symbols, prices=prices, returns=returns,
                means=means, momentum=momentum, vol=vol, warmup=warmup,
                spy=symbols.index('SPY'), cost=cost, counts=counts, minimum_history=minimum)


def desired_weights(features, index, configs):
    """Targets based on one already observed UTC close. No future values read."""
    f, n = features, len(configs)
    if index < 0:
        return np.zeros((n, len(f['symbols'])))
    trend = np.array([f['means'][c['trend']][index] for c in configs])
    mom = np.array([f['momentum'][c['momentum']][index] for c in configs])
    buffers = np.array([c['entry_buffer'] for c in configs])[:, None]
    eligible = (f['prices'][index] > trend*(1+buffers)) & (mom > 0) & (f['counts'][index] >= f['minimum_history'])
    ranks = np.argsort(np.argsort(-np.where(eligible, mom, -np.inf), axis=1, kind='stable'), axis=1, kind='stable')
    picked = eligible & (ranks < np.array([c['topk'] for c in configs])[:, None])
    inv = np.array([c['weighting'] == 'inverse_vol' for c in configs])[:, None]
    base = picked*np.where(inv, 1/np.maximum(np.nan_to_num(f['vol'][index], nan=1), .03), 1)
    weights = np.divide(base, base.sum(axis=1, keepdims=True), out=np.zeros_like(base), where=base.sum(axis=1, keepdims=True)>0)
    gross = np.array([c['gross'] for c in configs], dtype=float)
    targets = np.array([c['voltarget'] for c in configs])
    proxyvol = np.sqrt(np.sum((weights*np.nan_to_num(f['vol'][index], nan=1))**2, axis=1))
    gross *= np.where(targets>0, np.minimum(1, targets/np.maximum(proxyvol, .001)), 1)
    defensive = np.array([c['defensive_cash'] for c in configs]) & (f['prices'][index, f['spy']] < trend[:, f['spy']])
    gross[defensive] = 0
    return weights*gross[:, None]


def period_indices(f, start, end):
    return np.flatnonzero((f['dates'] >= start) & (f['dates'] <= end) & (np.arange(len(f['dates'])) >= f['warmup']))


def simulate(f, configs, start, end, multiplier=1., benchmark=False):
    """Independent fresh-cash period, including entry AND terminal liquidation charges.

    Transaction costs use pretrade turnover followed by a proportional equity
    haircut. Targets allocate remaining equity, ensuring nonnegative cash.
    Rebalance clock resets at each independent requested start.
    """
    indices = period_indices(f, start, end)
    if not len(indices):
        raise ValueError('No warmed-up rows in period')
    n, a = len(configs), len(f['symbols'])
    weights = np.zeros((n, a))
    equity, costs, turnover, exposure = np.ones(n), np.zeros(n), np.zeros(n), np.zeros(n)
    returns = np.empty((n, len(indices)))
    intervals = np.array([c['rebalance'] for c in configs])
    rates = f['cost']*multiplier
    for day, i in enumerate(indices):
        change = np.full(n, day == 0) if benchmark else day % intervals == 0
        if np.any(change):
            targets = np.zeros((int(change.sum()), a)) if benchmark else desired_weights(f, i-2, [configs[j] for j in np.flatnonzero(change)])
            if benchmark:
                targets[:, f['spy']] = 1
            delta = np.abs(targets-weights[change])
            charge = delta@rates
            costs[change] += equity[change]*charge
            turnover[change] += delta.sum(axis=1)
            equity[change] *= 1-charge
            weights[change] = targets
        else:
            charge = np.empty(0)
        exposure += weights.sum(axis=1)
        gross_return = weights@f['returns'][i]
        daily = 1+gross_return
        daily[change] *= 1-charge
        equity *= 1+gross_return
        weights *= (1+f['returns'][i])[None, :]/(1+gross_return)[:, None]
        if day == len(indices)-1:
            final_charge = weights@rates
            costs += equity*final_charge
            turnover += weights.sum(axis=1)
            equity *= 1-final_charge
            daily *= 1-final_charge
        returns[:, day] = daily-1
    return dict(returns=returns, indices=indices, costs=costs, turnover=turnover,
                average_exposure=exposure/len(indices), final=equity)


def metrics(result, f, initial=1_000_000):
    r = result['returns']
    curves = np.cumprod(1+r, axis=1)
    peak = np.maximum.accumulate(np.column_stack([np.ones(len(r)), curves]), axis=1)[:, 1:]
    dates = f['dates'][result['indices']]
    prior = f['dates'][result['indices'][0]-1]
    years = (dt.date.fromisoformat(str(dates[-1]))-dt.date.fromisoformat(str(prior))).days/365.25
    downside = np.sqrt(np.mean(np.minimum(r, 0)**2, axis=1))*np.sqrt(252)
    output = []
    for j in range(len(r)):
        volatility = float(np.std(r[j], ddof=1)*np.sqrt(252))
        final = float(curves[j, -1])
        output.append(dict(start=str(dates[0]), end=str(dates[-1]), valuation_start=str(prior),
                           initial=initial, final=initial*final, profit=initial*(final-1), return_=final-1,
                           cagr=final**(1/years)-1, max_drawdown=float(np.min(curves[j]/peak[j]-1)),
                           volatility=volatility, sharpe=float(np.mean(r[j])*252/max(volatility, 1e-12)),
                           sortino=float(np.mean(r[j])*252/max(downside[j], 1e-12)),
                           average_exposure=float(result['average_exposure'][j]),
                           turnover=float(result['turnover'][j]), fees_jpy=float(result['costs'][j]*initial),
                           observations=len(dates)))
    return output


def evaluate_batch(f, items, space):
    configs = [x['config'] for x in items]
    rows = [dict(id=x['id'], config=x['config'], config_sha256=digest(x['config'])) for x in items]
    for name, (start, end) in space['partitions'].items():
        vals = metrics(simulate(f, configs, start, end), f, space['initial_jpy'])
        base = metrics(simulate(f, [configs[0]], start, end, benchmark=True), f, space['initial_jpy'])[0]
        for row, value in zip(rows, vals):
            value['excess_cagr'] = value['cagr']-base['cagr']
            row[name] = value
    for row in rows:
        tr, va = row['train'], row['validation']
        row['passes_train'] = tr['excess_cagr'] > 0
        row['passes_validation'] = va['excess_cagr'] > 0
        row['score'] = va['excess_cagr']-.25*abs(va['max_drawdown'])-.5*max(0, -tr['excess_cagr'])
    return rows


def rank_rows(rows):
    return sorted(rows, key=lambda r: (not (r['passes_train'] and r['passes_validation']), -r['score'], r['id']))


def summaries(f, rows, space, provenance):
    ranked = rank_rows(rows)
    selected = dict(ranked[0])
    c = selected['config']
    windows = []
    for years, start in [(1, '2025-10-24'), (5, '2021-10-24'), (10, '2016-10-24')]:
        result = simulate(f, [c], start, '2026-10-23')
        base = simulate(f, [c], start, '2026-10-23', benchmark=True)
        windows.append(dict(years=years, start=start, end='2026-10-23', diagnostic_only=True,
                            selected=metrics(result, f)[0], spy=metrics(base, f)[0]))
    start = str(f['dates'][f['warmup']])
    result = simulate(f, [c], start, '2026-10-23')
    baseline = simulate(f, [c], start, '2026-10-23', benchmark=True)
    selected['full_period'] = metrics(result, f)[0]
    values = np.cumprod(1+result['returns'][0])*space['initial_jpy']
    spy = np.cumprod(1+baseline['returns'][0])*space['initial_jpy']
    idx = result['indices']
    sampled = sorted(set(range(0, len(idx), 5)) | {len(idx)-1})
    curves = [dict(date=str(f['dates'][idx[k]]), selected=round(float(values[k]), 2), spy=round(float(spy[k]), 2)) for k in sampled]
    twice = {}
    for name, (lo, hi) in space['partitions'].items():
        twice[name] = dict(selected=metrics(simulate(f, [c], lo, hi, multiplier=2), f)[0],
                           spy=metrics(simulate(f, [c], lo, hi, multiplier=2, benchmark=True), f)[0])
    neighbors = []
    for dimension, choices in space['dimensions'].items():
        pos = choices.index(c[dimension])
        for offset in (-1, 1):
            if 0 <= pos+offset < len(choices):
                cfg = dict(c, **{dimension: choices[pos+offset]})
                item = dict(id=f'neighbor_{dimension}_{offset}', config=cfg)
                nr = evaluate_batch(f, [item], space)[0]
                neighbors.append(dict(dimension=dimension, config=cfg, train=nr['train'], validation=nr['validation'], score=nr['score']))
    training_leader = sorted(rows, key=lambda r: (-r['train']['excess_cagr'], r['id']))[0]
    folds = []
    for year in (2021, 2022, 2023, 2024):
        end = min(f'{year}-12-31', '2024-10-23')
        cfg = training_leader['config']
        folds.append(dict(year=year, selected_id=training_leader['id'],
                          selected=metrics(simulate(f, [cfg], f'{year}-01-01', end), f)[0],
                          spy=metrics(simulate(f, [cfg], f'{year}-01-01', end, benchmark=True), f)[0]))
    targets = desired_weights(f, len(f['dates'])-1, [c])[0]
    return dict(schema_version=1, engine_version=ENGINE_VERSION, mode='etf_reference_screening',
                adoption_status='research_only', executable=False, limitations=LIMITATIONS,
                provenance=provenance,
                partitions=dict(**{k:dict(start=v[0],end=v[1]) for k,v in space['partitions'].items()}, selection_uses=['train', 'validation'], audit_status='previously_seen'),
                ranking=dict(objective=space['ranking'], selected_id=selected['id'],
                             positive_training_excess_count=sum(r['passes_train'] for r in rows),
                             positive_validation_excess_count=sum(r['passes_validation'] for r in rows),
                             positive_both_count=sum(r['passes_train'] and r['passes_validation'] for r in rows),
                             min_score=min(r['score'] for r in rows), median_score=float(np.median([r['score'] for r in rows])),
                             max_score=max(r['score'] for r in rows)),
                selected=selected, top20=ranked[:20], windows=windows, curves=curves,
                latest_decision_at=str(f['dates'][-1])+'T23:59:59Z', latest_targets=[dict(id=s, weight=float(w)) for s,w in zip(f['symbols'],targets)],
                diagnostics=dict(cost2x=twice, neighbors=neighbors,
                                 training_only_leader=dict(id=training_leader['id'], config=training_leader['config'], folds=folds,
                                                           note='Single training-only selection, fixed across validation-year diagnostic folds; no annual retuning. Validation folds are already seen.')))
