"""Download local-only Yahoo ETF prices for an explicitly limited reference run."""
import concurrent.futures
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'research/history-v2-2026-10-24'
END = '2026-10-23'
PRODUCTS = [('1348.T', 'JP', 10, 'TOPIX ETF'), ('1346.T', 'JP', 1, 'Nikkei 225 ETF'),
            ('SPY', 'US', 1, 'S&P 500 ETF'), ('QQQ', 'US', 1, 'NASDAQ 100 ETF'),
            ('IWM', 'US', 1, 'Russell 2000 ETF')]


def stamp(date):
    return int(dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp())


def iso(date, hour, zone):
    return dt.datetime.combine(dt.date.fromisoformat(date), dt.time.fromisoformat(hour), ZoneInfo(zone)).astimezone(dt.timezone.utc).isoformat()


def fetch(symbol, cached=False):
    query = urllib.parse.urlencode(dict(period1=stamp('2015-01-01'), period2=stamp('2026-10-24'), interval='1d', events='div,splits'))
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='') + '?' + query
    cache = OUT / 'raw' / (symbol + '.json')
    if cached:
        raw = cache.read_bytes()
    else:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60) as response:
            raw = response.read()
        cache.write_bytes(raw)
    data = json.loads(raw)['chart']['result'][0]
    if data['meta']['symbol'] != symbol:
        raise ValueError('Wrong symbol: ' + symbol)
    zone = ZoneInfo(data['meta']['exchangeTimezoneName'])
    q = data['indicators']['quote'][0]
    splits = [{'date': dt.datetime.fromtimestamp(s['date'], zone).date().isoformat(), 'ratio': s['numerator'] / s['denominator']}
              for s in data.get('events', {}).get('splits', {}).values()]
    bars = []
    for i, timestamp in enumerate(data['timestamp']):
        date = dt.datetime.fromtimestamp(timestamp, zone).date().isoformat()
        if date > END or q['open'][i] is None or q['close'][i] is None:
            continue
        # Yahoo OHLC/volume are split-normalized. Restore contemporaneous units.
        factor = 1
        for split in splits:
            if date < split['date'] <= END:
                factor *= split['ratio']
        bars.append(dict(date=date, open=q['open'][i] * factor, close=q['close'][i] * factor,
                         volume=(q['volume'][i] or 0) / factor))
    if bars[-1]['date'] != END:
        raise ValueError('Missing final session: ' + symbol)
    return dict(symbol=symbol, bars=bars, splits=splits, source=dict(url=url, sha256=hashlib.sha256(raw).hexdigest(),
                rows=len(bars), first=bars[0]['date'], last=bars[-1]['date']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cached', action='store_true', help='Normalize existing raw downloads without refetching')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'raw').mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        all_data = list(pool.map(lambda symbol: fetch(symbol, args.cached), [x[0] for x in PRODUCTS] + ['JPY=X']))
    fetched = {s['symbol']: s for s in all_data}
    instruments = []
    for symbol, market, lot, name in PRODUCTS:
        item = fetched[symbol]
        bars = item['bars']
        for bar in bars:
            bar['open_at'] = iso(bar['date'], '09:00' if market == 'JP' else '09:30', 'Asia/Tokyo' if market == 'JP' else 'America/New_York')
            jp_close = '15:30' if bar['date'] >= '2024-11-05' else '15:00'
            bar['close_at'] = iso(bar['date'], jp_close if market == 'JP' else '16:00', 'Asia/Tokyo' if market == 'JP' else 'America/New_York')
        actions = [dict(type='split', at=iso(s['date'], '08:00' if market == 'JP' else '08:30', 'Asia/Tokyo' if market == 'JP' else 'America/New_York'), ratio=s['ratio']) for s in item['splits'] if s['date'] <= END]
        if symbol == '1348.T':
            actions.append(dict(type='lot', at=iso('2025-10-16', '08:00', 'Asia/Tokyo'), lot=1,
                                source='https://www.jpx.co.jp/news/1030/20251002-01.html'))
        instruments.append(dict(id=symbol, symbol=symbol, name=name, market=market, currency='JPY' if market == 'JP' else 'USD',
                                kind='etf', lot=lot, bars=bars, actions=actions))
    fx = [dict(at=(dt.datetime.fromisoformat(b['date']).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=2)).isoformat(),
               mid=b['close'], source_date=b['date']) for b in fetched['JPY=X']['bars']]
    data = dict(schema_version=2, mode='etf_reference', end=END,
                coverage=dict(from_date='2015-01-01', through=END, includes_delisted=False, complete_market_history=False,
                              dividend_mode='excluded', fx_execution='lagged_daily_proxy', note='Five fixed ETFs; not a 500-equity PIT universe'),
                retrieved_at=None if args.cached else dt.datetime.now(dt.timezone.utc).isoformat(),
                normalized_at=dt.datetime.now(dt.timezone.utc).isoformat(), instruments=instruments, fx=fx,
                sources=[dict(symbol=s['symbol'], **s['source']) for s in all_data])
    (OUT / 'reference-input.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    print(json.dumps(dict(path=str(OUT / 'reference-input.json'), sources=data['sources'], splits={s['symbol']: s['splits'] for s in all_data}), ensure_ascii=False))


if __name__ == '__main__':
    main()
