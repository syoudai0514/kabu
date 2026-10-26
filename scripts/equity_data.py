"""Build a local-only 500-equity research matrix from verified existing caches."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from free_prices import fetch

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def align(instruments, fx, start, end):
    dates = sorted({b['date'] for x in instruments for b in x['bars'] if start <= b['date'] <= end})
    if not dates:
        raise ValueError('No dates')
    quote_by_at = {}
    for q in fx:
        at = dt.datetime.fromisoformat(q['at'])
        if at.tzinfo is None or not np.isfinite(q['mid']) or q['mid'] <= 0:
            raise ValueError('Invalid timestamped FX')
        at = at.astimezone(dt.timezone.utc)
        if at > dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc):
            continue
        if at in quote_by_at:
            raise ValueError('Duplicate FX availability timestamp')
        quote_by_at[at] = q['mid']
    quotes = sorted(quote_by_at.items())
    fx_values, cursor, latest = [], 0, None
    for date in dates:
        cutoff = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc)
        while cursor < len(quotes) and quotes[cursor][0] <= cutoff:
            latest = quotes[cursor]
            cursor += 1
        if latest is None or (cutoff-latest[0]).days > 7:
            raise ValueError('Missing or stale available FX: ' + date)
        fx_values.append(latest[1])
    fx_values = np.asarray(fx_values)
    native = np.full((len(dates), len(instruments)), np.nan)
    date_codes = {v:i for i,v in enumerate(dates)}
    for a, instrument in enumerate(instruments):
        seen = set()
        for bar in instrument['bars']:
            date = bar['date']
            if date in seen:
                raise ValueError('Duplicate native session')
            seen.add(date)
            if date not in date_codes:
                continue
            if not np.isfinite(bar['close']) or bar['close'] <= 0:
                raise ValueError('Invalid close')
            native[date_codes[date], a] = bar['close']
    valid = np.isfinite(native)
    last = np.maximum.accumulate(np.where(valid, np.arange(len(dates))[:, None], -1), axis=0)
    carried = native[np.maximum(last, 0), np.arange(len(instruments))]
    carried[last < 0] = np.nan
    usd = np.asarray([x['market']=='US' for x in instruments])
    rates = np.where(usd[None,:], fx_values[:,None], 1)
    return dict(dates=np.asarray(dates), symbols=np.asarray([x['symbol'] for x in instruments]),
                markets=np.asarray([x['market'] for x in instruments]),
                equity_mask=np.asarray([x['equity'] for x in instruments]),
                prices=native*rates, mark_prices=carried*rates, valid_observation=valid,
                fx=fx_values)


def prepare(output):
    universe = ROOT / 'research/history-free-universe-2026-10-25-final'
    reference = ROOT / 'research/history-v2-2026-10-24'
    summary = json.loads((universe/'summary.json').read_text())
    audit = json.loads((ROOT/'reports/free-universe-2026-10-25.json').read_text())
    checkpoint = {x['key']: x for x in map(json.loads,(universe/'checkpoint.jsonl').read_text().splitlines())}
    instruments, sources = [], []
    for market in ('JP','US'):
        members = summary['markets'][market]['selections']['250']['members']
        digest = hashlib.sha256(json.dumps(members,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if len(members) != 250 or digest != audit['markets'][market]['selections']['250']['members_sha256']:
            raise ValueError('Frozen selection differs from published selection hash')
        for member in members:
            p = fetch(member['ticker'],market,'2025-01-01','2026-10-24',universe/'prices',offline=True)
            if p['provenance']['sha256'] != checkpoint[member['key']]['raw_sha256']:
                raise ValueError('Price cache differs from selection checkpoint')
            instruments.append(dict(symbol=member['ticker'],market=market,equity=True,bars=p['bars']))
            sources.append(dict(symbol=member['ticker'],**p['provenance']))
    ref = json.loads((reference/'reference-input.json').read_text())
    ref_sources = {x['symbol']:x for x in ref['sources']}
    fx = []
    for symbol, market in [('SPY','US'),('1348.T','JP'),('1346.T','JP'),('JPY=X','FX')]:
        raw = reference/'raw'/(symbol+'.json')
        if sha(raw) != ref_sources[symbol]['sha256']:
            raise ValueError('Reference raw hash mismatch')
        r = json.loads(raw.read_text())['chart']['result'][0]
        if r['meta']['symbol'] != symbol or r['meta']['currency'] != ('USD' if market=='US' else 'JPY'):
            raise ValueError('Reference symbol/currency mismatch')
        zone = ZoneInfo(r['meta']['exchangeTimezoneName'])
        closes = r['indicators']['quote'][0]['close']
        if len(closes) != len(r['timestamp']):
            raise ValueError('Reference array mismatch')
        bars = []
        for timestamp, close in zip(r['timestamp'],closes):
            date = dt.datetime.fromtimestamp(timestamp,zone).date().isoformat()
            if close is None:
                continue
            if market=='FX':
                available = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc)+dt.timedelta(days=2)
                fx.append(dict(at=available.isoformat(),mid=close))
            elif '2025-01-01' <= date <= '2026-10-23':
                bars.append(dict(date=date,close=close))
        if market != 'FX':
            instruments.append(dict(symbol=symbol,market=market,equity=False,bars=bars))
        sources.append(dict(symbol=symbol,sha256=sha(raw),url=ref_sources[symbol]['url']))
    data = align(instruments,fx,'2025-01-01','2026-10-23')
    if len(set(data['symbols'])) != 503 or np.sum(data['equity_mask']) != 500:
        raise ValueError('Expected 500 unique equities and 3 benchmark ETFs')
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    path = output/'input.npz'
    if path.exists():
        raise ValueError('Input already exists; use a new output directory')
    np.savez_compressed(path, **data)
    manifest = dict(schema_version=1,mode='current_survivor_exploratory',historical_pit_complete=False,
                    input_sha256=sha(path),selection_sha256=sha(universe/'summary.json'),
                    preparation_source_sha256=sha(__file__),start=str(data['dates'][0]),end=str(data['dates'][-1]),
                    dates=len(data['dates']),equities=500,benchmark_etfs=3,
                    sources=sources,fx_policy='source local date plus 2 calendar days at UTC midnight',
                    omissions={'dividends':True,'integer_lots':True,'historical_membership':True})
    (output/'input-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in manifest.items() if k!='sources'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'research/history-equity-screen-2026-10-26'))
    prepare(parser.parse_args().output)
