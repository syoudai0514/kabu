"""Nine distinct algorithm families, causal research approximations, no broker I/O.

All models observe JPY-denominated synchronized UTC-close wealth indices. The
existing daily reference loader supplies lagged FX and stale holiday marks.
Signal close t-2 sets weights at proxy close t-1 and earns return t. Stateful
exits run daily and force a proxy rebalance; new entries follow each cadence.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path

import numpy as np

import research_search as base

ENGINE_VERSION = 'families-1.0'
LIMITATIONS = base.LIMITATIONS[:5] + [
    'Nine different algorithm families with 2,500 configurations each: configurations and outcomes are correlated, not 22,500 independent hypotheses.',
    'Every historical period, including audit and 1/5/10-year windows, is already seen. Ranking uses training/validation only but is exploratory.',
    'JPY indicators use lagged FX and carried close marks on a union JP/US calendar; 252-day volatility annualization is approximate.',
    'RSI is rolling arithmetic-average (Cutler) RSI; breakout channels use prior CLOSES, not unavailable intraday high/low.',
    'Portfolio volatility scaling uses a diagonal variance proxy and can understate risk of correlated ETFs; nominal targets are not guaranteed.',
    'Stateful exits force reallocation at the next modeled proxy close after the signal delay, not at an executable exchange fill.',
    'No fundamental, earnings, stock master, delisted-stock, or point-in-time 500-stock data is supplied. Full v2 event-time replay remains required.',
]


def source_fingerprint():
    files = [Path(__file__), Path(base.__file__)]
    return hashlib.sha256(''.join(p.name+':'+hashlib.sha256(p.read_bytes()).hexdigest() for p in files).encode()).hexdigest()


def candidates(space):
    """Uniform grid draws with deterministic rejection and config deduplication.

    Rejected short>=long windows do not constitute executed trials. Inactive
    drawdown-window keys are removed for the explicit no-rule configuration.
    """
    output = []
    for family_index, (family, info) in enumerate(space['families'].items()):
        rng = np.random.default_rng(space['seed']+family_index)
        seen = set()
        while len(seen) < space['trials_per_family']:
            config = {'family':family}
            config.update({key:values[int(rng.integers(len(values)))] for key,values in info['dimensions'].items()})
            if family == 'dual_momentum' and config['short_window'] >= config['long_window']:
                continue
            if family == 'donchian_breakout' and config['exit_window'] >= config['entry_window']:
                continue
            if family == 'low_volatility' and config['topk']==5 and config['weighting']=='equal' and config['voltarget']==0:
                continue
            if family == 'spy_volatility_target' and config['risk_rule'] == 'none':
                del config['drawdown_window']
            key = base.canonical(config)
            if key in seen:
                continue
            seen.add(key)
            output.append(dict(id=f'{family_index+1:02d}-{len(seen):05d}', family=family,
                               config=config, config_sha256=base.digest(config)))
    return output


def _rolling_mean(values, window):
    result = np.full_like(values, np.nan)
    sums = np.vstack([np.zeros(values.shape[1]), np.cumsum(values, axis=0)])
    result[window-1:] = (sums[window:]-sums[:-window])/window
    return result


def prepare_data(data, space):
    """Reusable family feature bank; only accepts the declared ETF reference."""
    # Loader dimensions do not constrain family indicators; 252 is the warmup.
    loader_space = dict(space, volatility_lookback=63,
                        dimensions={'trend':[252], 'momentum':[252]})
    f = base.prepare_data(data, loader_space)
    price = 100*np.cumprod(1+f['returns'], axis=0)
    f['model_price'] = price
    horizons = set([2,3,5,7,10,14,21,40,42,60,63,100,126,150,189,200,252])
    for info in space['families'].values():
        for name, values in info['dimensions'].items():
            if name in ('trend','momentum','short_window','long_window','entry_window','exit_window','rsi_window','vol_window','relative_window','drawdown_window'):
                horizons.update(x for x in values if x)
    horizons = sorted(horizons)
    stores = {k:[] for k in ('ma','momentum','vol','rsi','high','low','drawdown')}
    delta = np.vstack([np.zeros(price.shape[1]), np.diff(price, axis=0)])
    for window in horizons:
        ma = _rolling_mean(price, window)
        mom = np.full_like(price,np.nan)
        mom[window:] = price[window:]/price[:-window]-1
        vol = np.full_like(price,np.nan)
        high, low = np.full_like(price,np.nan), np.full_like(price,np.nan)
        peak = np.full_like(price,np.nan)
        if window <= len(price):
            slices = np.lib.stride_tricks.sliding_window_view(price, window, axis=0)
            # Prior channel excludes the observed current close.
            high[window:] = slices[:-1].max(axis=2)
            low[window:] = slices[:-1].min(axis=2)
            peak[window-1:] = slices.max(axis=2)
            rv = np.lib.stride_tricks.sliding_window_view(f['returns'], window, axis=0)
            vol[window-1:] = rv.std(axis=2,ddof=1)*np.sqrt(252)
        up = _rolling_mean(np.maximum(delta,0),window)
        down = _rolling_mean(np.maximum(-delta,0),window)
        total = up+down
        rsi = np.divide(100*up,total,out=np.full_like(up,50),where=total>0)
        for name, value in [('ma',ma),('momentum',mom),('vol',vol),('rsi',rsi),('high',high),('low',low),('drawdown',price/peak-1)]:
            stores[name].append(value)
    f['bank'] = {name:np.stack(arrays) for name,arrays in stores.items()}
    f['horizon_codes'] = {n:i for i,n in enumerate(horizons)}
    f['space'] = space
    return f


def _pick(eligible, score, topk):
    rank = np.argsort(np.argsort(-np.where(eligible, score,-np.inf), axis=1,kind='stable'),axis=1,kind='stable')
    return eligible & (rank < topk[:,None])


def _allocate(eligible, score, topk, inverse, vol, gross, voltarget=None):
    selected = _pick(eligible,score,topk)
    amount = selected*np.where(inverse[:,None],1/np.maximum(np.nan_to_num(vol,nan=1),.03),1)
    denom = amount.sum(axis=1,keepdims=True)
    weights = np.divide(amount,denom,out=np.zeros_like(amount),where=denom>0)
    scale = np.array(gross,dtype=float,copy=True)
    if voltarget is not None:
        proxy = np.sqrt(np.sum((weights*np.nan_to_num(vol,nan=1))**2,axis=1))
        scale *= np.where(voltarget>0,np.minimum(1,voltarget/np.maximum(proxy,.001)),1)
    return weights*scale[:,None]


class FamilyModel:
    def __init__(self, f, configs, exclude=()):
        self.f, self.configs = f, configs
        self.family = configs[0]['family']
        if any(c['family'] != self.family for c in configs):
            raise ValueError('Model batches must contain one family')
        self.n, self.a = len(configs),len(f['symbols'])
        self.p = {}
        for key in set().union(*(c.keys() for c in configs)) - {'family','blend','risk_rule','weighting'}:
            self.p[key] = np.array([c.get(key,21) for c in configs])
        self.codes = {}
        self.state = np.zeros((self.n,self.a),dtype=bool)
        self.age = np.zeros((self.n,self.a),dtype=int)
        self.last_risk_off = np.zeros(self.n,dtype=bool)
        self.allowed = np.array([s not in exclude for s in f['symbols']])
        self.inverse = np.array([c.get('weighting') == 'inverse_vol' for c in configs])
        self.gross = np.array([c.get('gross',1) for c in configs],dtype=float)
        self.topk = np.array([c.get('topk',1) for c in configs])

    def feature(self, kind, param, index, default=None):
        key = (kind,param,default)
        if key not in self.codes:
            vals = self.p.get(param,np.full(self.n,default))
            self.codes[key] = np.array([self.f['horizon_codes'][int(v)] if v else self.f['horizon_codes'][252] for v in vals])
        return self.f['bank'][kind][self.codes[key],index,:]

    def _trend_ok(self,index):
        p = self.p['trend']
        return (p[:,None]==0) | (self.f['model_price'][index] > self.feature('ma','trend',index))

    def _state_step(self,index,scheduled,eligible,kind):
        """Existing states retain slots; daily exits, cadence-gated entries."""
        self.age += self.state
        if kind == 'rsi':
            rsi = self.feature('rsi','rsi_window',index)
            exits = (rsi >= self.p['exit_rsi'][:,None]) | (self.age >= self.p['max_hold'][:,None])
            entries = rsi < self.p['entry_rsi'][:,None]
            if self.family == 'rsi_mean_reversion':
                entries &= self._trend_ok(index)
            strength = 100-rsi
        else:
            hi = self.feature('high','entry_window',index)
            lo = self.feature('low','exit_window',index)
            price = self.f['model_price'][index]
            exits = (price<lo) | (self.age >= self.p['max_hold'][:,None])
            entries = price > hi*(1+self.p['entry_buffer'][:,None])
            strength = price/hi-1
        exited = self.state & exits
        held = self.state & ~exits & eligible
        new = entries & eligible & scheduled[:,None] & ~exited
        picked = _pick(held|new,np.where(held,1e6+strength,strength),self.topk)
        changed = np.any(picked != self.state,axis=1)
        self.age[~(self.state & picked)] = 0
        self.state = picked
        return picked,changed

    def step(self,index,day):
        scheduled = day % self.p['rebalance'] == 0
        eligible = np.broadcast_to((self.f['counts'][index]>=self.f['minimum_history']) & self.allowed,(self.n,self.a)).copy()
        vol = self.feature('vol','vol_window',index,default=63)
        family = self.family
        change = scheduled.copy()
        if family == 'trend_rotation':
            momentum = self.feature('momentum','momentum',index)
            eligible &= (self.f['model_price'][index] > self.feature('ma','trend',index)*(1+self.p['entry_buffer'][:,None])) & (momentum>0)
            weights = _allocate(eligible,momentum,self.topk,self.inverse,vol,self.gross,self.p['voltarget'])
        elif family in ('donchian_breakout','rsi_mean_reversion'):
            held,changed = self._state_step(index,scheduled,eligible,'rsi' if family == 'rsi_mean_reversion' else 'breakout')
            change |= changed
            weights = _allocate(held,np.zeros_like(vol),self.topk,self.inverse,vol,self.gross)
        elif family == 'cross_sectional_reversal':
            momentum = self.feature('momentum','short_window',index)
            eligible &= self._trend_ok(index) & (momentum < self.p['weakness_threshold'][:,None])
            weights = _allocate(eligible,-momentum,self.topk,self.inverse,vol,self.gross)
        elif family == 'low_volatility':
            weights = _allocate(eligible,-vol,self.topk,self.inverse,vol,self.gross,self.p['voltarget'])
        elif family == 'dual_momentum':
            short = self.feature('momentum','short_window',index)
            long = self.feature('momentum','long_window',index)
            eligible &= (short>self.p['absolute_threshold'][:,None]) & (long>self.p['absolute_threshold'][:,None])
            score = short*self.p['short_weight'][:,None]+long*(1-self.p['short_weight'][:,None])
            weights = _allocate(eligible,score,self.topk,self.inverse,vol,self.gross)
        elif family == 'spy_core_satellite':
            momentum = self.feature('momentum','relative_window',index)
            relative = momentum-momentum[:,self.f['spy'],None]
            eligible[:,self.f['spy']] = False
            eligible &= self._trend_ok(index) & (relative>self.p['relative_threshold'][:,None])
            weights = _allocate(eligible,relative,self.topk,self.inverse,vol,self.gross*(1-self.p['core']))
            weights[:,self.f['spy']] = self.gross*self.p['core']
        elif family == 'spy_volatility_target':
            weights = np.zeros_like(vol)
            size = np.minimum(self.p['cap'],self.p['target_vol']/np.maximum(vol[:,self.f['spy']],.01))
            rules = [c['risk_rule'] for c in self.configs]
            drawdown = self.feature('drawdown','drawdown_window',index,default=21)[:,self.f['spy']]
            thresholds = np.array([float(rule.split('_')[1])/100 if rule!='none' else np.inf for rule in rules])
            off = drawdown < -thresholds
            factor = np.array([.5 if r.startswith('half') else 0 if r.startswith('cash') else 1 for r in rules])
            size *= np.where(off,factor,1)
            change |= off != self.last_risk_off
            self.last_risk_off = off
            weights[:,self.f['spy']] = size
        elif family == 'multi_strategy_ensemble':
            momentum = self.feature('momentum','momentum',index)
            trend = self.f['model_price'][index] > self.feature('ma','trend',index)
            eq = np.zeros(self.n,dtype=bool)
            one = np.ones(self.n)
            trend_weights = _allocate(eligible & trend & (momentum>0),momentum,self.topk,eq,vol,one)
            held,changed = self._state_step(index,scheduled,eligible,'rsi')
            change |= changed
            reverse_weights = _allocate(held,np.zeros_like(vol),self.topk,eq,vol,one)
            low_weights = _allocate(eligible,-vol,self.topk,np.ones(self.n,dtype=bool),vol,one)
            blend = np.array([c['blend'] for c in self.configs])
            weights = (trend_weights*blend[:,0,None]+reverse_weights*blend[:,1,None]+low_weights*blend[:,2,None])*self.gross[:,None]
        else:
            raise ValueError('Unknown algorithm family: '+family)
        if np.any(~np.isfinite(weights)) or np.any(weights<0) or np.any(weights.sum(axis=1)>1+1e-10):
            raise AssertionError('Invalid target allocation')
        return weights,change


def simulate(f, configs, start, end, multiplier=1, benchmark=False, exclude=(), targets=False, liquidate=True):
    indices = base.period_indices(f,start,end)
    if not len(indices):
        raise ValueError('No warmed-up period')
    n,a = len(configs),len(f['symbols'])
    model = None if benchmark else FamilyModel(f,configs,exclude)
    held = np.zeros((n,a))
    equity,costs,turnover,exposure = np.ones(n),np.zeros(n),np.zeros(n),np.zeros(n)
    returns = np.empty((n,len(indices)))
    rates = f['cost']*multiplier
    maximum,concentration = np.zeros(n),np.zeros(n)
    target_history = []
    last_targets = np.zeros_like(held)
    for day,i in enumerate(indices):
        if benchmark:
            desired = np.zeros_like(held)
            desired[:,f['spy']] = 1
            change = np.full(n,day==0)
        else:
            desired,change = model.step(i-2,day)
        # Targets here represent actual scheduled/forced allocations, not an
        # unexecuted daily signal. Current portfolio drifts between reallocations.
        last_targets[change] = desired[change]
        charge = np.zeros(n)
        if np.any(change):
            delta = np.abs(desired[change]-held[change])
            charge[change] = delta@rates
            costs += equity*charge
            turnover[change] += delta.sum(axis=1)
            equity *= 1-charge
            held[change] = desired[change]
        if np.any(held.sum(axis=1)>1+1e-10):
            raise AssertionError('Leverage after drift')
        exposure += held.sum(axis=1)
        maximum = np.maximum(maximum,held.sum(axis=1))
        concentration += np.sum(held**2,axis=1)
        gross_return = held@f['returns'][i]
        daily = (1-charge)*(1+gross_return)
        equity *= 1+gross_return
        held *= (1+f['returns'][i])[None,:]/(1+gross_return)[:,None]
        if targets:
            target_history.append(held.copy())
        if liquidate and day == len(indices)-1:
            terminal_charge = held@rates
            costs += equity*terminal_charge
            turnover += held.sum(axis=1)
            equity *= 1-terminal_charge
            daily *= 1-terminal_charge
        returns[:,day] = daily-1
    result = dict(returns=returns,indices=indices,costs=costs,turnover=turnover,
                  average_exposure=exposure/len(indices),final=equity,
                  maximum_exposure=maximum,average_squared_weights=concentration/len(indices),
                  final_preliquidation_weights=held,last_targets=last_targets)
    if targets:
        result['weights'] = np.array(target_history)
    return result


def evaluate_batch(f,items,space):
    configs = [item['config'] for item in items]
    rows = [dict(item) for item in items]
    for name,(start,end) in space['partitions'].items():
        result = simulate(f,configs,start,end)
        vals = base.metrics(result,f,space['initial_jpy'])
        spy = base.metrics(simulate(f,[configs[0]],start,end,benchmark=True),f,space['initial_jpy'])[0]
        for j,(row,value) in enumerate(zip(rows,vals)):
            value['excess_cagr'] = value['cagr']-spy['cagr']
            value['maximum_exposure'] = float(result['maximum_exposure'][j])
            value['average_squared_weights'] = float(result['average_squared_weights'][j])
            row[name] = value
    for row in rows:
        tr,va = row['train'],row['validation']
        row['passes_train'] = tr['excess_cagr']>0
        row['passes_validation'] = va['excess_cagr']>0
        row['score'] = va['excess_cagr']-.25*abs(va['max_drawdown'])-.5*max(0,-tr['excess_cagr'])
    return rows


def targets_for(f,config,index=None,start='2016-10-24',include_cash=False):
    """Replay exact state/cadence up to a known signal close for paper planning.

    This yields the target at the *next* proxy allocation opportunity associated
    with the supplied signal day. It is never an executable order. State starts
    from cash at explicit start; callers must persist that same origin.
    """
    index = len(f['dates'])-1 if index is None else index
    begin = base.period_indices(f,start,str(f['dates'][index]))
    if not len(begin):
        raise ValueError('No warmed-up observations')
    first_signal = max(int(begin[0])-2,0)
    model = FamilyModel(f,[config])
    target = np.zeros(len(f['symbols']))
    for day,i in enumerate(range(first_signal,index+1)):
        desired,change = model.step(i,day)
        if change[0]:
            target = desired[0]
    output = [dict(id=s,weight=float(w)) for s,w in zip(f['symbols'],target)]
    if include_cash:
        output.append(dict(id='JPY_CASH',weight=float(1-target.sum())))
    return output


def _curves(result,spy,f,initial):
    values = np.cumprod(1+result['returns'][0])*initial
    baseline = np.cumprod(1+spy['returns'][0])*initial
    indices = result['indices']
    sampled = sorted(set(range(0,len(indices),5))|{len(indices)-1})
    return [dict(date=str(f['dates'][indices[k]]),selected=round(float(values[k]),2),spy=round(float(baseline[k]),2)) for k in sampled]


def summarize(f,rows,space,provenance):
    families = []
    initial = space['initial_jpy']
    all_start = space['partitions']['train'][0]
    end = space['partitions']['audit'][1]
    for family,info in space['families'].items():
        members = [r for r in rows if r['family']==family]
        ranked = base.rank_rows(members)
        selected = ranked[0]
        cfg = selected['config']
        windows = []
        for years,start in [(1,'2025-10-24'),(5,'2021-10-24'),(10,'2016-10-24')]:
            result = simulate(f,[cfg],start,end)
            baseline = simulate(f,[cfg],start,end,benchmark=True)
            windows.append(dict(years=years,start=start,end=end,diagnostic_only=True,
                                selected=base.metrics(result,f,initial)[0],spy=base.metrics(baseline,f,initial)[0]))
        doubled = {}
        for name,(start,finish) in space['partitions'].items():
            doubled[name] = dict(selected=base.metrics(simulate(f,[cfg],start,finish,multiplier=2),f,initial)[0],
                                 spy=base.metrics(simulate(f,[cfg],start,finish,multiplier=2,benchmark=True),f,initial)[0])
        full = simulate(f,[cfg],all_start,end)
        baseline = simulate(f,[cfg],all_start,end,benchmark=True)
        families.append(dict(id=family,name=info['name'],definition=info['definition'],trials=len(members),
                             positive_train_count=sum(r['passes_train'] for r in members),
                             positive_validation_count=sum(r['passes_validation'] for r in members),
                             positive_both_count=sum(r['passes_train'] and r['passes_validation'] for r in members),
                             selected=selected,windows=windows,cost2x=doubled,top5=ranked[:5],
                             curves=_curves(full,baseline,f,initial),full_period=base.metrics(full,f,initial)[0],
                             latest_targets=targets_for(f,cfg),latest_decision_at=str(f['dates'][-1])+'T23:59:59Z'))
    overall = base.rank_rows(rows)[0]
    chosen = next(x for x in families if x['id']==overall['family'])
    exclusion = {}
    for name,(start,finish) in space['partitions'].items():
        r = simulate(f,[overall['config']],start,finish,exclude=('QQQ',))
        val = base.metrics(r,f,initial)[0]
        benchmark = base.metrics(simulate(f,[overall['config']],start,finish,benchmark=True),f,initial)[0]
        val['excess_cagr'] = val['cagr']-benchmark['cagr']
        exclusion[name] = val
    concentration = {}
    for name,(start,finish) in space['partitions'].items():
        r = simulate(f,[overall['config']],start,finish,targets=True)
        w = r['weights'][:,0,:]
        concentration[name] = dict(average_weights=[dict(id=s,weight=float(v)) for s,v in zip(f['symbols'],w.mean(axis=0))],
                                   maximum_single_weight=float(w.max()),average_squared_weights=float(np.mean(np.sum(w*w,axis=1))))
    return dict(schema_version=1,engine_version=ENGINE_VERSION,mode='etf_reference_algorithm_families',
                adoption_status='research_only',executable=False,limitations=LIMITATIONS,
                provenance=provenance,
                partitions=dict(**{k:dict(start=v[0],end=v[1]) for k,v in space['partitions'].items()},
                                selection_uses=['train','validation'],audit_status='previously_seen_diagnostic_only'),
                ranking=dict(objective=space['ranking'],overall_selected_id=overall['id'],
                             positive_both_count=sum(r['passes_train'] and r['passes_validation'] for r in rows)),
                families=families,overall_selected=overall,curves=chosen['curves'],
                diagnostics=dict(exclude_qqq=dict(note='QQQ removed from eligible signals/weights; SPY benchmark unchanged. All already-seen diagnostic periods.',**exclusion),
                                 concentration=concentration))
