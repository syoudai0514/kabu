"""Exploratory current-universe equity screen; no execution or PIT claims.

Signals at i-2 execute only on native observations at proxy close i-1 and earn
mark return i. All monetary holdings and fees are self financing.
"""
import hashlib
import itertools
import json
import numpy as np

ENGINE_VERSION = 'equity-screen-1.0'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def candidates(space):
    result = []
    for family, info in space['families'].items():
        dims = info.get('dimensions', {})
        for values in itertools.product(*(dims[k] for k in sorted(dims))):
            cfg = dict(family=family, **dict(zip(sorted(dims), values)))
            if family == 'donchian_breakout' and cfg['exit_window'] >= cfg['entry_window']:
                continue
            digest = hashlib.sha256(canonical(cfg).encode()).hexdigest()
            result.append(dict(id=family+'-'+digest[:12], family=family, config=cfg, config_sha256=digest))
    return result


def _mean(x, window):
    out = np.full_like(x, np.nan)
    if window > len(x):
        return out
    valid = np.isfinite(x)
    sums = np.vstack([np.zeros(x.shape[1]), np.cumsum(np.where(valid, x, 0), axis=0)])
    counts = np.vstack([np.zeros(x.shape[1]), np.cumsum(valid, axis=0)])
    out[window-1:] = np.where(counts[window:]-counts[:-window] == window,
                             (sums[window:]-sums[:-window])/window, np.nan)
    return out


def prepare_data(data, space):
    f = dict(data)
    raw = np.asarray(data['prices'], dtype=float)
    valid = np.asarray(data['valid_observation'], dtype=bool)
    dates = np.asarray(data['dates'], dtype='datetime64[D]')
    if raw.ndim != 2 or valid.shape != raw.shape or len(dates) != len(raw):
        raise ValueError('Aligned T by A inputs required')
    if np.any(np.isfinite(raw) & (raw <= 0)) or np.any(valid & ~np.isfinite(raw)):
        raise ValueError('Native observations require positive finite prices')
    if np.any(np.diff(dates).astype(int) <= 0):
        raise ValueError('Dates must increase strictly')
    observed = np.where(valid, np.arange(len(raw))[:, None], -1)
    last = np.maximum.accumulate(observed, axis=0)
    mark = raw[np.maximum(last, 0), np.arange(raw.shape[1])]
    mark[last < 0] = np.nan
    if 'mark_prices' in data:
        mark = np.asarray(data['mark_prices'], dtype=float).copy()
        if mark.shape != raw.shape or np.any(np.isfinite(mark) & (mark <= 0)):
            raise ValueError('Invalid mark prices')
        if not np.allclose(mark[valid],raw[valid],rtol=1e-12,atol=1e-12):
            raise ValueError('Native observations must match mark prices')
        mark[last < 0] = np.nan
        if np.any((last >= 0) & ~np.isfinite(mark)):
            raise ValueError('Marks must exist after first observation')
    returns = np.zeros_like(mark)
    np.divide(mark[1:], mark[:-1], out=returns[1:], where=np.isfinite(mark[1:]) & np.isfinite(mark[:-1]))
    returns[1:] = np.where(np.isfinite(mark[1:]) & np.isfinite(mark[:-1]), returns[1:]-1, 0)
    age = (dates[:,None]-dates[np.maximum(last,0)]).astype('timedelta64[D]').astype(int)
    age[last < 0] = 999999
    f.update(dates=dates, model_price=mark, returns=returns, valid_observation=valid,
             counts=np.cumsum(valid,axis=0), age=age,
             cost=np.array([space.get('fees',{'JP':.0015,'US':.0025})[m] for m in data['markets']]),
             minimum_history=space.get('minimum_history',126),max_stale_days=space.get('max_stale_days',7),
             equity_mask=np.asarray(data['equity_mask'],dtype=bool), space=space)
    horizons = {5,10,14,21,42,63,126}
    for info in space.get('families',{}).values():
        for key, vals in info.get('dimensions',{}).items():
            if key in ('lookback','short_window','long_window','vol_window','rsi_window','entry_window','exit_window','trend'):
                horizons.update(int(v) for v in vals if v)
    bank = {}
    delta = np.vstack([np.full(raw.shape[1],np.nan),np.diff(mark,axis=0)])
    for w in sorted(horizons):
        mom = np.full_like(mark,np.nan)
        if w < len(mark):
            mom[w:] = mark[w:]/mark[:-w]-1
        vol, high, low = (np.full_like(mark,np.nan) for _ in range(3))
        if w <= len(mark):
            rv = np.lib.stride_tricks.sliding_window_view(np.where(np.isfinite(mark),returns,np.nan),w,axis=0)
            vol[w-1:] = np.std(rv,axis=2,ddof=1)*np.sqrt(252)
            view = np.lib.stride_tricks.sliding_window_view(mark,w,axis=0)
            high[w:] = np.max(view[:-1],axis=2)
            low[w:] = np.min(view[:-1],axis=2)
        up,down = _mean(np.maximum(delta,0),w),_mean(np.maximum(-delta,0),w)
        total = up+down
        rsi = np.divide(100*up,total,out=np.full_like(mark,50),where=total>0)
        rsi[~np.isfinite(total)] = np.nan
        bank[w] = dict(momentum=mom,ma=_mean(mark,w),vol=vol,rsi=rsi,high=high,low=low)
    f['bank'] = bank
    return f


class FamilyModel:
    def __init__(self,f,configs):
        self.f,self.configs = f,configs
        self.active = np.zeros((len(configs),len(f['symbols'])),bool)

    def step(self,index,day):
        f = self.f
        result = np.zeros_like(self.active,dtype=float)
        changes = np.zeros(len(self.configs),bool)
        for j,c in enumerate(self.configs):
            fam = c['family']
            scheduled = day % max(1,c.get('rebalance',21)) == 0
            eligible = f['equity_mask'] & (f['counts'][index]>=f['minimum_history']) & (f['age'][index]<=f['max_stale_days'])
            trend = c.get('trend',0)
            if trend:
                eligible &= f['model_price'][index] > f['bank'][trend]['ma'][index]
            def allocation(kind,window,threshold=None,mask=None,invert=False,score_override=None):
                score = f['bank'][window][kind][index].copy() if score_override is None else score_override.copy()
                if invert:
                    score = -score
                ok = eligible.copy() if mask is None else eligible & mask
                if kind in ('vol','rsi'):
                    if kind == 'rsi':
                        ok &= score <= threshold
                    score = -score
                ok &= np.isfinite(score)
                out = np.zeros(len(score))
                for market in ('JP','US'):
                    picks = np.flatnonzero(ok & (np.asarray(f['markets'])==market))
                    picks = picks[np.argsort(-score[picks],kind='stable')[:c.get('topk',10)]]
                    out[picks] = .5*c.get('gross',1)/c.get('topk',10)
                return out
            if fam in ('momentum_rank','short_reversal','spy_core_satellite'):
                w = c.get('lookback',63)
                if fam == 'short_reversal':
                    result[j] = allocation('momentum',w,invert=True)
                else:
                    result[j] = allocation('momentum',w)
                if fam == 'spy_core_satellite':
                    core = c['core']
                    result[j] *= 1-core
                    spy = f['symbols'].index('SPY')
                    if f['counts'][index,spy]>=f['minimum_history'] and f['age'][index,spy]<=f['max_stale_days']:
                        result[j,spy] += core*c.get('gross',1)
            elif fam == 'low_vol':
                result[j] = allocation('vol',c.get('vol_window',63))
            elif fam == 'rsi_reversal':
                result[j] = allocation('rsi',c.get('rsi_window',14),c.get('threshold',35))
            elif fam == 'dual_momentum':
                short = f['bank'][c['short_window']]['momentum'][index]
                long = f['bank'][c['long_window']]['momentum'][index]
                result[j] = allocation('momentum',63,mask=(short>0)&(long>0),score_override=(short+long)/2)
            elif fam == 'multi_strategy_ensemble':
                result[j] = (allocation('momentum',63)+allocation('rsi',14,35)+allocation('vol',63))/3
            elif fam == 'donchian_breakout':
                price = f['model_price'][index]
                exits = price < f['bank'][c['exit_window']]['low'][index]
                entered = (price > f['bank'][c['entry_window']]['high'][index]) & eligible
                before = self.active[j].copy()
                self.active[j] &= ~exits
                if scheduled:
                    self.active[j] |= entered & ~exits
                changes[j] = np.any(before != self.active[j])
                result[j] = allocation('momentum',63,mask=self.active[j])
            elif fam == 'buy_hold':
                symbol = c.get('symbol','SPY')
                if symbol in ('countryequal','country_equal'):
                    for market in ('JP','US'):
                        ids = np.flatnonzero(f['equity_mask'] & (np.asarray(f['markets'])==market))
                        if len(ids):
                            ready = ids[(f['counts'][index,ids]>=f['minimum_history']) & (f['age'][index,ids]<=f['max_stale_days'])]
                            result[j,ready] = .5/len(ids)
                else:
                    result[j,f['symbols'].index(symbol)] = 1
                scheduled = day == 0 or (symbol in ('countryequal','country_equal') and c.get('rebalance',0)>0 and day % c['rebalance']==0)
            else:
                raise ValueError('Unknown family '+fam)
            changes[j] |= scheduled
        return result,changes


def simulate(f,configs,start,end,multiplier=1,liquidate=True,targets=False):
    """Dollar holdings, sell first, then proportional cash-funded purchases.

    A breakout entry/exit state change triggers a full target recomputation even
    off cadence; other families recompute targets only on scheduled dates.
    Buy-hold pending entries are initial-capital dollar amounts, so an asset
    awaiting its first executable close never scales with other assets' gains.
    """
    indices = np.flatnonzero((f['dates']>=np.datetime64(start)) & (f['dates']<=np.datetime64(end)) & (np.arange(len(f['dates']))>=2))
    if not len(indices):
        raise ValueError('No period observations')
    n,a = len(configs),len(f['symbols'])
    model = FamilyModel(f,configs)
    holdings,cash = np.zeros((n,a)),np.ones(n)
    fees,turnover = np.zeros(n),np.zeros(n)
    curves,weights_history = [],[]
    rates = f['cost']*multiplier
    pending = np.zeros((n,a))
    for day,i in enumerate(indices):
        before = cash+holdings.sum(axis=1)
        desired,change = model.step(i-2,day)
        # A buy-and-hold benchmark waits for each asset's first tradable close.
        for j,c in enumerate(configs):
            if c['family']=='buy_hold':
                if day == 0:
                    pending[j] = desired[j]
                elif c.get('rebalance',0)==0:
                    change[j] = np.any(pending[j]>0)
                    desired[j] = pending[j]
        tradable = f['valid_observation'][i-1] & (f['age'][i-1]<=f['max_stale_days'])
        for j in np.flatnonzero(change):
            total = cash[j]+holdings[j].sum()
            target = desired[j]*total
            if configs[j]['family']=='buy_hold' and configs[j].get('rebalance',0)==0 and day>0:
                target = holdings[j]+pending[j]
            # Frozen positions occupy capital; missing target slots stay cash.
            available = max(0,total-holdings[j,~tradable].sum())
            if target[tradable].sum()>available:
                target[tradable] *= available/target[tradable].sum()
            sell = np.where(tradable,np.maximum(holdings[j]-target,0),0)
            fee = sell@rates
            holdings[j] -= sell
            cash[j] += sell.sum()-fee
            fees[j] += fee
            turnover[j] += sell.sum()/total
            buys = np.where(tradable,np.maximum(target-holdings[j],0),0)
            needed = buys@(1+rates)
            if needed>cash[j] and needed>0:
                buys *= max(0,cash[j])/needed
            fee = buys@rates
            holdings[j] += buys
            cash[j] -= buys.sum()+fee
            cash[j] = max(0,cash[j])
            fees[j] += fee
            turnover[j] += buys.sum()/total
            if configs[j]['family']=='buy_hold' and configs[j].get('rebalance',0)==0:
                pending[j,tradable] = 0
        holdings *= 1+f['returns'][i]
        if np.any(holdings< -1e-12) or np.any(cash< -1e-12):
            raise AssertionError('Negative holdings or cash')
        total = cash+holdings.sum(axis=1)
        curves.append(total.copy())
        if targets:
            weights_history.append(holdings/total[:,None])
    residual = np.zeros(n)
    if liquidate:
        tradable = f['valid_observation'][indices[-1]]
        sold = holdings*tradable
        fee = sold@rates
        fees += fee
        turnover += sold.sum(axis=1)/(cash+holdings.sum(axis=1))
        cash += sold.sum(axis=1)-fee
        holdings -= sold
        curves[-1] = cash+holdings.sum(axis=1)
        residual = holdings.sum(axis=1)
    equity = np.asarray(curves)
    result = dict(dates=f['dates'][indices],indices=indices,equity=equity,
                  final_weights=holdings/equity[-1,:,None],final_cash=cash/equity[-1],
                  turnover=turnover,fees=fees,unliquidated=residual,
                  final=equity[-1],returns=np.diff(np.vstack([np.ones(n),equity]),axis=0)/np.vstack([np.ones(n),equity])[:-1])
    if targets:
        result['weights'] = np.asarray(weights_history)
    return result


def metrics(result,f,initial=1_000_000):
    values = result['equity']
    prior = f['dates'][result['indices'][0]-1]
    years = float((result['dates'][-1]-prior)/np.timedelta64(1,'D'))/365.25
    peak = np.maximum.accumulate(np.vstack([np.ones(values.shape[1]),values]),axis=0)
    drawdown = np.vstack([np.ones(values.shape[1]),values])/peak-1
    return [dict(start=str(result['dates'][0]),end=str(result['dates'][-1]),valuation_start=str(prior),initial=initial,final=float(values[-1,j]*initial),return_=float(values[-1,j]-1),
                 cagr=float(values[-1,j]**(1/years)-1),max_drawdown=float(drawdown[:,j].min()),
                 turnover=float(result['turnover'][j]),fees_jpy=float(result['fees'][j]*initial),
                 unliquidated_jpy=float(result['unliquidated'][j]*initial),observations=len(values))
            for j in range(values.shape[1])]
