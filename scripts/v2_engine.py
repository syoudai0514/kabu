"""Causal research engine. No broker execution and no inferred historical universe."""
import copy
import datetime as dt
import math
import statistics
from collections import Counter, defaultdict

UTC = dt.timezone.utc


def instant(value):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('UTC offset required: ' + value)
    return result.astimezone(UTC)


def midnight(date):
    return instant(date + 'T00:00:00+00:00')


def validate(data, config):
    if data.get('schema_version') != 2 or data.get('mode') not in ('point_in_time_equities', 'etf_reference'):
        raise ValueError('Explicit v2 schema and mode required')
    ids = set()
    for instrument in data['instruments']:
        if instrument['id'] in ids:
            raise ValueError('Duplicate permanent ID')
        ids.add(instrument['id'])
        if instrument['market'] not in ('JP', 'US') or instrument['currency'] != ('JPY' if instrument['market'] == 'JP' else 'USD'):
            raise ValueError('Unsupported market/currency')
        if not isinstance(instrument['lot'], int) or instrument['lot'] < 1:
            raise ValueError('Positive integer lot required')
        previous = None
        previous_bar = None
        for bar in instrument['bars']:
            opened, closed = instant(bar['open_at']), instant(bar['close_at'])
            if closed <= opened or (previous is not None and opened <= previous):
                raise ValueError('Overlapping, duplicate or unsorted sessions')
            if any(not math.isfinite(bar[k]) or bar[k] <= 0 for k in ('open', 'close')) or bar['volume'] < 0:
                raise ValueError('Invalid price or volume')
            if previous_bar:
                factor = math.prod(e['ratio'] for e in instrument.get('actions', []) if e['type'] == 'split' and previous < instant(e['at']) <= closed)
                ratio = bar['close']*factor/previous_bar['close']
                if not .25 < ratio < 4:
                    raise ValueError(f"Quarantine discontinuous prices: {instrument['id']} {bar['date']} ratio={ratio}")
            previous = closed
            previous_bar = bar
        for event in instrument.get('actions', []):
            if event['type'] not in ('split', 'lot', 'dividend', 'delist'):
                raise ValueError('Unsupported corporate action: normalize it explicitly')
            instant(event['at'])
            if event['type'] == 'dividend' and config['dividends']:
                if instant(event['pay_at']) < instant(event['at']):
                    raise ValueError('Dividend payable before entitlement')
    if not data['fx']:
        raise ValueError('Timestamped FX quotes required')
    if data['mode'] == 'point_in_time_equities':
        coverage = data['coverage']
        required = ['includes_delisted', 'complete_market_history', 'permanent_ids', 'historical_lots', 'corporate_actions_complete']
        if not all(coverage.get(k) is True for k in required) or not coverage.get('evidence'):
            raise ValueError('PIT coverage incomplete: delistings, full historical master, IDs, lots, actions and evidence required')
        if coverage['from_date'] > min(config['starts']) or coverage['through'] < config['end']:
            raise ValueError('PIT coverage does not span requested periods')
        if config['dividends'] and coverage.get('dividend_mode') != 'payable_events':
            raise ValueError('Payment-timestamped dividends required')
        if any(a['kind'] != 'common_stock' for a in data['instruments']):
            raise ValueError('Equity mode requires ordinary common stock')
        snapshots = data.get('membership_snapshots', [])
        for s in snapshots:
            if not s.get('complete') or not s.get('provenance') or instant(s['known_at']) > instant(s['effective_at']):
                raise ValueError('Complete, sourced, historically available snapshots required')
            if len({m['id'] for m in s['members']}) != len(s['members']):
                raise ValueError('Duplicate master ID')
            for m in s['members']:
                if m['id'] not in ids or m['size_bucket'] not in config['size_quota'] or not m['sector'] or m['lot'] < 1:
                    raise ValueError('Incomplete historical master member')
        # Require a complete snapshot for both countries at every monthly decision.
        month = min(config['starts'])[:7]
        first = dt.date.fromisoformat(month + '-01')
        last = dt.date.fromisoformat(config['end'])
        while first <= last:
            for market in ('JP', 'US'):
                effective = midnight(first.isoformat())
                candidates = [s for s in snapshots if s['market'] == market and instant(s['effective_at']) <= effective and instant(s['known_at']) <= effective]
                if not candidates or max(instant(s['effective_at']) for s in candidates).date() < first:
                    raise ValueError(f'Missing monthly PIT snapshot: {first} {market}')
            first = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)


def signal(bars, strategy):
    closes = [b['close'] for b in bars]
    if strategy == 'ma20_60':
        if len(closes) < 60:
            return False, False, 0
        momentum = closes[-1] / closes[-21] - 1
        entry = closes[-1] > statistics.fmean(closes[-60:]) and statistics.fmean(closes[-20:]) > statistics.fmean(closes[-60:]) and momentum > 0
        return entry, not entry, momentum
    if len(closes) < 201:
        return False, False, 0
    slow = statistics.fmean(closes[-200:])
    momentum = closes[-1] / closes[-127] - 1
    entry = closes[-1] > slow and statistics.fmean(closes[-50:]) > slow and momentum > 0
    exit_ = closes[-1] < slow or closes[-1] / closes[-64] - 1 < 0
    return entry, exit_, momentum


class Replay:
    def __init__(self, data, config, start, strategy=None, costs_multiplier=1, benchmark=None):
        self.data, self.cfg, self.start = data, config, midnight(start)
        self.end = midnight(config['end']) + dt.timedelta(days=1)
        self.strategy = strategy or config['strategy']
        self.multiplier, self.benchmark = costs_multiplier, benchmark
        self.assets = {a['id']: a for a in data['instruments']}
        self.bars = {a: [] for a in self.assets}
        self.lots = {a: i['lot'] for a, i in self.assets.items()}
        self.meta = {a: dict(sector='reference', market=i['market']) for a, i in self.assets.items()}
        self.mark, self.positions, self.pending = {}, {}, {}
        self.cash = dict(JPY=float(config['initial']), USD=0.)
        self.fx, self.fx_at = None, None
        self.peak = config['initial']
        self.halted = False
        self.trades, self.history, self.universes, self.rejections = [], [], [], Counter()
        self.candidates, self.month = set(), None
        self.fees, self.fx_cost = 0., 0.
        self.receivable, self.payments = 0., defaultdict(list)
        self.now = None
        self.benchmark_bought = set()

    def value(self):
        return self.cash['JPY'] + self.cash['USD'] * (self.fx or 0) + sum(self.position_value(a) for a in self.positions) + self.receivable

    def position_value(self, asset):
        return self.positions[asset]['qty'] * self.mark[asset] * (self.fx if self.assets[asset]['market'] == 'US' else 1)

    def amounts(self):
        country, sector = defaultdict(float), defaultdict(float)
        for a in self.positions:
            value = self.position_value(a)
            country[self.assets[a]['market']] += value
            sector[(self.assets[a]['market'], self.meta[a]['sector'])] += value
        return country, sector

    def choose(self):
        month = self.now.strftime('%Y-%m')
        if month == self.month:
            return
        self.month = month
        if self.data['mode'] == 'etf_reference':
            self.candidates = {a for a, bars in self.bars.items() if len(bars) >= self.cfg['minimum_history']}
            self.universes.append(dict(at=self.now.isoformat(), members=sorted(self.candidates), mode='fixed_etf_reference'))
            return
        chosen = set()
        for market in ('JP', 'US'):
            snapshots = [s for s in self.data['membership_snapshots'] if s['market'] == market and instant(s['known_at']) <= self.now and instant(s['effective_at']) <= self.now]
            snap = max(snapshots, key=lambda s: instant(s['effective_at']))
            eligible, reasons = [], Counter()
            for member in snap['members']:
                a = member['id']
                self.meta[a] = member
                self.lots[a] = member['lot']
                bars = self.bars[a]
                if len(bars) < self.cfg['minimum_history']:
                    reasons['history'] += 1
                    continue
                # No current price from today or future volume is inspected here.
                turnover = statistics.median(b['close'] * b['volume'] for b in bars[-self.cfg['liquidity_window']:])
                if turnover < self.cfg['min_median_turnover'][market]:
                    reasons['liquidity'] += 1
                    continue
                eligible.append((turnover, a, member))
            sectors, sizes, selected = Counter(), Counter(), []
            for turnover, a, member in sorted(eligible, key=lambda x: (-x[0], x[1])):
                if sizes[member['size_bucket']] >= self.cfg['size_quota'][member['size_bucket']] or sectors[member['sector']] >= self.cfg['candidate_sector_cap']:
                    reasons['stratum_cap'] += 1
                    continue
                selected.append(a)
                sectors[member['sector']] += 1
                sizes[member['size_bucket']] += 1
            chosen.update(selected)
            self.universes.append(dict(at=self.now.isoformat(), market=market, members=selected, shortage=max(0, self.cfg['candidate_count_per_market']-len(selected)), reasons=dict(reasons), master_provenance=snap['provenance']))
        self.candidates = chosen

    def reserve_order(self, asset, side, qty, reason):
        if asset in self.pending or qty <= 0:
            return
        self.pending[asset] = dict(asset=asset, side=side, qty=qty, placed=self.now.isoformat(), reason=reason)

    def decide(self, market, date):
        if not (self.start <= self.now < self.end) or self.fx is None:
            return
        self.choose()
        friday = dt.date.fromisoformat(date).weekday() == 4
        if self.benchmark:
            symbols = set(self.assets) if self.benchmark == 'equal' else {self.benchmark}
            for a in sorted(symbols):
                if self.assets[a]['market'] != market or a in self.benchmark_bought or a in self.pending or not self.bars[a]:
                    continue
                allocation = self.cfg['initial'] / len(symbols)
                price = self.mark[a] * (self.fx if market == 'US' else 1)
                qty = math.floor(allocation * .995 / price / self.lots[a]) * self.lots[a]
                self.reserve_order(a, 'BUY', qty, 'benchmark')
            return
        for a, p in list(self.positions.items()):
            if self.assets[a]['market'] != market:
                continue
            if self.halted or self.position_value(a) <= p['cost_jpy'] * (1-self.cfg['stop']):
                self.pending.pop(a, None)
                self.reserve_order(a, 'SELL', p['qty'], 'circuit' if self.halted else 'stop')
        if self.strategy != 'ma20_60' and not friday:
            return
        ranked = []
        for a, asset in self.assets.items():
            if asset['market'] != market or not self.bars[a] or self.bars[a][-1]['date'] != date:
                continue
            entry, exit_, momentum = signal(self.bars[a], self.strategy)
            if a in self.positions and (exit_ or a not in self.candidates):
                self.reserve_order(a, 'SELL', self.positions[a]['qty'], 'exit_or_universe')
            elif a not in self.positions and entry and a in self.candidates and not self.halted:
                ranked.append((momentum, a))
        # Deterministic within-market ranking; funds are checked again at execution.
        for _, a in sorted(ranked, key=lambda x: (-x[0], x[1])):
            budget = self.value() * min(self.cfg['position_cap'], self.cfg['trade_risk']/self.cfg['stop']) * .97
            unit = self.mark[a] * (self.fx if market == 'US' else 1) * self.lots[a]
            qty = math.floor(budget/unit) * self.lots[a]
            if not qty:
                self.rejections['lot_budget_at_signal'] += 1
                continue
            volume = statistics.fmean(b['volume'] for b in self.bars[a][-20:]) * self.cfg['volume_participation']
            if qty > volume:
                self.rejections['past_volume_at_signal'] += 1
                continue
            self.reserve_order(a, 'BUY', qty, self.strategy)

    def fund(self, currency, amount):
        """Convert only the shortage, retaining USD sale proceeds."""
        shortage = amount-self.cash[currency]
        if shortage <= 1e-8:
            return True
        cost = self.cfg['fx_bps']/10000*self.multiplier
        if currency == 'USD':
            required = shortage*self.fx*(1+cost)
            if required > self.cash['JPY']+1e-7:
                return False
            self.cash['JPY'] -= required
            self.cash['USD'] += shortage
            self.fx_cost += required-shortage*self.fx
        else:
            dollars = shortage/(self.fx*(1-cost))
            if dollars > self.cash['USD']+1e-7:
                return False
            self.cash['USD'] -= dollars
            self.cash['JPY'] += shortage
            self.fx_cost += dollars*self.fx-shortage
        return True

    def opened(self, a, bar):
        self.mark[a] = bar['open']
        if not (self.start <= self.now < self.end) or a not in self.pending:
            return
        order = self.pending.pop(a)
        if instant(order['placed']) >= self.now:
            self.pending[a] = order
            return  # A decision at the opening timestamp must wait for a later open.
        qty = order['qty']
        asset = self.assets[a]
        rate = self.fx if asset['market'] == 'US' else 1
        slip = self.cfg['slip_bps']/10000*self.multiplier
        price = bar['open']*(1+slip if order['side'] == 'BUY' else 1-slip)
        gross = qty*price
        fee = gross*self.cfg['fee_bps']/10000*self.multiplier
        reason = None
        if order['side'] == 'BUY':
            total = self.value()
            country, sector = self.amounts()
            invested = sum(country.values())
            if not self.benchmark:
                if self.halted:
                    reason = 'halted'
                elif gross*rate > total*self.cfg['position_cap']+1e-7:
                    reason = 'position_cap'
                elif invested+gross*rate > total*min(self.cfg['exposure_cap'], self.cfg['portfolio_risk']/self.cfg['stop'])+1e-7:
                    reason = 'exposure_or_total_risk'
                elif country[asset['market']]+gross*rate > total*self.cfg['country_cap']+1e-7:
                    reason = 'country_cap'
                elif asset['kind'] == 'common_stock' and sector[(asset['market'], self.meta[a]['sector'])]+gross*rate > total*self.cfg['sector_cap']+1e-7:
                    reason = 'sector_cap'
            if not reason and not self.fund(asset['currency'], gross+fee):
                reason = 'insufficient_cash'
            if not reason:
                self.cash[asset['currency']] -= gross+fee
                self.positions[a] = dict(qty=qty, cost_jpy=(gross+fee)*rate)
                self.benchmark_bought.add(a)
        else:
            p = self.positions.get(a)
            if not p or p['qty'] < qty:
                reason = 'insufficient_position'
            else:
                self.cash[asset['currency']] += gross-fee
                p['cost_jpy'] *= (p['qty']-qty)/p['qty']
                p['qty'] -= qty
                if p['qty'] <= 1e-9:
                    del self.positions[a]
        if reason:
            self.rejections[reason] += 1
        else:
            self.fees += fee*rate
        self.trades.append(dict(**order, at=self.now.isoformat(), price=price, fx=rate, fx_at=self.fx_at.isoformat() if self.fx_at else None,
                                fee_jpy=0 if reason else fee*rate, status='rejected' if reason else 'filled', message=reason,
                                cash_jpy=self.cash['JPY'], cash_usd=self.cash['USD']))
        if min(self.cash.values()) < -1e-6:
            raise AssertionError('Negative cash')

    def action(self, a, event):
        kind = event['type']
        p = self.positions.get(a)
        if kind == 'split':
            ratio = event['ratio']
            if p:
                p['qty'] *= ratio
            if a in self.pending:
                self.pending[a]['qty'] *= ratio
            if a in self.mark:
                self.mark[a] /= ratio
            for b in self.bars[a]:
                b['open'] /= ratio
                b['close'] /= ratio
                b['volume'] *= ratio
        elif kind == 'lot':
            self.lots[a] = event['lot']
        elif kind == 'dividend' and self.cfg['dividends'] and p:
            amount = p['qty']*event['amount']
            rate = self.fx if self.assets[a]['currency'] == 'USD' else 1
            self.receivable += amount*rate
            self.payments[instant(event['pay_at'])].append((self.assets[a]['currency'], amount, amount*rate))
        elif kind == 'delist':
            self.pending.pop(a, None)
            if p:
                if 'cash_per_share' not in event:
                    raise ValueError('Delisting settlement missing; refusing last-price carry forever')
                self.cash[self.assets[a]['currency']] += p['qty']*event['cash_per_share']
                del self.positions[a]
            self.candidates.discard(a)

    def run(self):
        events = []
        decisions = set()
        openings = defaultdict(dict)
        for a, instrument in self.assets.items():
            for bar in instrument['bars']:
                opened, closed = instant(bar['open_at']), instant(bar['close_at'])
                events.append((opened, 2, a, 'open', bar))
                openings[opened][a] = bar
                events.append((closed, 3, a, 'close', bar))
                decisions.add((closed, instrument['market'], bar['date']))
            for action in instrument.get('actions', []):
                events.append((instant(action['at']), 1, a, 'action', action))
                if action['type'] == 'dividend' and self.cfg['dividends']:
                    events.append((instant(action['pay_at']), 1, a, 'payment', {}))
        for fx in self.data['fx']:
            events.append((instant(fx['at']), 0, '', 'fx', fx))
        for at, bars in openings.items():
            events.append((at, 2.5, '', 'fills', bars))
        events.append((self.start, -0.5, '', 'activate', {}))
        for at, market, date in decisions:
            events.append((at, 4, market, 'decision', dict(market=market, date=date)))
        # Monthly universe decisions precede opening prices; use only previous data.
        first = self.start.date().replace(day=1)
        while midnight(first.isoformat()) < self.end:
            events.append((midnight(first.isoformat()), -1, '', 'universe', {}))
            first = (first.replace(day=28)+dt.timedelta(days=4)).replace(day=1)
        date = self.start.date()
        while midnight(date.isoformat()) < self.end:
            events.append((midnight(date.isoformat())+dt.timedelta(hours=23, minutes=59), 5, '', 'valuation', {}))
            date += dt.timedelta(days=1)
        self.history.append(dict(at=self.start.isoformat(), value=self.cfg['initial'], cash_jpy=self.cfg['initial'], cash_usd=0, stock_value=0))
        for at, _, a, kind, payload in sorted(events, key=lambda e: (e[0], e[1], e[2])):
            if at >= self.end:
                break
            self.now = at
            if kind == 'fx':
                self.fx, self.fx_at = payload['mid'], at
            elif kind == 'open':
                self.mark[a] = payload['open']
            elif kind == 'fills':
                # All simultaneous opening marks are known before risk checks.
                # Dict insertion order preserves signal ranking, unlike ticker sorting.
                for asset in list(self.pending):
                    if asset in payload:
                        self.opened(asset, payload[asset])
            elif kind == 'close':
                self.mark[a] = payload['close']
                self.bars[a].append(copy.copy(payload))
            elif kind == 'decision':
                self.decide(payload['market'], payload['date'])
            elif kind == 'universe':
                self.choose()
            elif kind == 'activate' and self.benchmark:
                for market in ('JP', 'US'):
                    self.decide(market, self.start.date().isoformat())
            elif kind == 'action':
                self.action(a, payload)
            elif kind == 'payment':
                for currency, amount, carrying in self.payments.pop(at, []):
                    self.cash[currency] += amount
                    self.receivable -= carrying
            elif kind == 'valuation' and at >= self.start:
                value = self.value()
                self.peak = max(self.peak, value)
                if not self.benchmark and value/self.peak-1 <= -self.cfg['circuit']:
                    self.halted = True
                self.history.append(dict(at=at.isoformat(), value=value, cash_jpy=self.cash['JPY'], cash_usd=self.cash['USD'],
                                         stock_value=sum(self.position_value(a) for a in self.positions)))
        return self.result()

    def result(self):
        initial, final = self.cfg['initial'], self.value()
        peak, dd = initial, 0
        for h in self.history:
            peak = max(peak, h['value'])
            dd = min(dd, h['value']/peak-1)
        years = (self.end-self.start).total_seconds()/86400/365.25
        filled = [t for t in self.trades if t['status'] == 'filled']
        summary = dict(start=self.start.date().isoformat(), end=self.cfg['end'], strategy=self.strategy, mode=self.data['mode'], benchmark=self.benchmark,
                       initial=initial, final=final, profit=final-initial, return_=final/initial-1, cagr=(final/initial)**(1/years)-1, max_drawdown=dd,
                       average_stock_exposure=statistics.fmean(h['stock_value']/h['value'] for h in self.history[1:]),
                       fills=len(filled), traded_symbols=sorted({t['asset'] for t in filled}), fees_jpy=self.fees, fx_cost_jpy=self.fx_cost,
                       cash=self.cash, last_fx=self.fx, last_fx_at=self.fx_at.isoformat(), halted=self.halted, rejection_reasons=dict(self.rejections),
                       holdings=[dict(id=a, **p, value_jpy=self.position_value(a)) for a, p in self.positions.items()], pending_orders=list(self.pending.values()))
        return dict(summary=summary, history=self.history, trades=self.trades, universe_history=self.universes)


def replay(data, config, start, **kwargs):
    validate(data, config)
    return Replay(data, config, start, **kwargs).run()
