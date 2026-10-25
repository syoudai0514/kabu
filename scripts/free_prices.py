"""Free, cached Yahoo daily observations; revised split-adjusted data, never PIT prices."""
import datetime as dt
import hashlib
import json
import math
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc


class PriceError(ValueError):
    """An explicit retrieval, integrity, or data validation failure."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status
        self.code = status


def _date(value):
    return dt.date.fromisoformat(value)


def _number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PriceError('Invalid ' + label)
    if value < 0 or (positive and value == 0):
        raise PriceError('Invalid ' + label)
    return value


def _normalize(raw, symbol, market, start, end, now):
    try:
        chart = json.loads(raw)['chart']
        if chart.get('error'):
            raise PriceError('Yahoo error: ' + str(chart['error']))
        result = chart['result'][0]
        meta = result['meta']
        expected_currency, expected_zone = {'JP': ('JPY', 'Asia/Tokyo'),
                                           'US': ('USD', 'America/New_York')}[market]
        if meta.get('instrumentType') != 'EQUITY':
            raise PriceError('Instrument type must be EQUITY')
        if meta['symbol'] != symbol or meta['currency'] != expected_currency:
            raise PriceError('Symbol/currency mismatch')
        if meta['exchangeTimezoneName'] != expected_zone:
            raise PriceError('Exchange timezone mismatch')
        exchange = meta.get('exchangeName', '')
        allowed = {'JP': {'JPX', 'TYO'},
                   'US': {'NYQ', 'NMS', 'NGM', 'NCM', 'PCX', 'ASE', 'BTS', 'BATS', 'PNK', 'OQX', 'OQB'}}
        if exchange not in allowed[market]:
            raise PriceError('Exchange/market mismatch: ' + exchange)
        zone = ZoneInfo(expected_zone)
        today = now.astimezone(zone).date()
        times = result.get('timestamp') or []
        quote = result['indicators']['quote'][0]
        fields = ('open', 'high', 'low', 'close', 'volume')
        if any(len(quote.get(key, [])) != len(times) for key in fields):
            raise PriceError('Inconsistent daily array lengths')
        splits = []
        for event in result.get('events', {}).get('splits', {}).values():
            day = dt.datetime.fromtimestamp(event['date'], zone).date()
            ratio = (_number(event['numerator'], 'split numerator', True) /
                     _number(event['denominator'], 'split denominator', True))
            if start <= day < end and day < today:
                splits.append({'date': day.isoformat(), 'ratio': ratio})
        splits.sort(key=lambda row: row['date'])
        seen, bars, failures, warnings = set(), [], [], []
        for i, timestamp in enumerate(times):
            day = dt.datetime.fromtimestamp(timestamp, zone).date()
            if day in seen:
                raise PriceError('Duplicate session: ' + day.isoformat())
            seen.add(day)
            if day >= end or day >= today or day < start:
                failures.append({'date': day.isoformat(), 'reason': 'outside_request_or_incomplete'})
                continue
            values = {key: quote[key][i] for key in fields}
            if any(value is None for value in values.values()):
                failures.append({'date': day.isoformat(), 'reason': 'missing_daily_value'})
                continue
            for key, value in values.items():
                _number(value, key + ' on ' + day.isoformat(), key != 'volume')
            if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
                raise PriceError('Inconsistent OHLC on ' + day.isoformat())
            bars.append({'date': day.isoformat(), **values})
        bars.sort(key=lambda row: row['date'])
        for previous, current in zip(bars, bars[1:]):
            ratio = current['close'] / previous['close']
            if ratio > 1.8 or ratio < 1 / 1.8:
                nearby = [s for s in splits if previous['date'] < s['date'] <= current['date']]
                warnings.append({'date': current['date'], 'reason': 'suspicious_close_discontinuity',
                                 'ratio': ratio, 'splits': nearby,
                                 'note': 'Yahoo OHLC is already split-adjusted; no split correction applied'})
        if not bars:
            raise PriceError('No complete daily observations in requested range')
        return {'symbol': symbol, 'market': market, 'currency': expected_currency,
                'exchange': exchange, 'timezone': expected_zone, 'bars': bars, 'splits': splits,
                'failures': failures, 'warnings': warnings,
                'price_basis': 'Yahoo revised split-adjusted OHLC; liquidity proxy only; not PIT'}
    except PriceError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
        raise PriceError('Malformed Yahoo daily response: ' + str(exc)) from exc


def fetch(symbol, market, start, end_exclusive, cache_dir, offline=False):
    """Return bars/splits/provenance/failures; raise PriceError on unusable data.

    Bounds are exchange session dates. Today's session is always omitted. Cache
    is immutable by request key; corrupt/incomplete cache raises without refetch.
    """
    now = dt.datetime.now(UTC)
    if market not in ('JP', 'US'):
        raise PriceError('market must be JP or US')
    if not isinstance(symbol, str) or not symbol.strip():
        raise PriceError('Nonempty symbol required')
    try:
        first, end = _date(start), _date(end_exclusive)
    except (TypeError, ValueError) as exc:
        raise PriceError('Explicit ISO date bounds required') from exc
    zone = ZoneInfo('Asia/Tokyo' if market == 'JP' else 'America/New_York')
    if first >= end or first >= now.astimezone(zone).date() or end > now.astimezone(zone).date() + dt.timedelta(days=1):
        raise PriceError('Invalid or future date bounds')
    request_key = {'symbol': symbol, 'market': market, 'start': start, 'end_exclusive': end_exclusive}
    digest = hashlib.sha256(json.dumps(request_key, sort_keys=True).encode()).hexdigest()
    directory = pathlib.Path(cache_dir)
    raw_path, metadata_path = directory / (digest + '.json'), directory / (digest + '.meta.json')
    def stamp(day):
        return int(dt.datetime.combine(day, dt.time(), zone).timestamp())
    query = urllib.parse.urlencode({'period1': stamp(first), 'period2': stamp(end),
                                    'interval': '1d', 'events': 'splits'})
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol, safe='') + '?' + query
    cached = raw_path.exists() or metadata_path.exists()
    if cached:
        try:
            raw = raw_path.read_bytes()
            provenance = json.loads(metadata_path.read_text())
            if provenance['request'] != request_key or provenance['sha256'] != hashlib.sha256(raw).hexdigest() or provenance['url'] != url:
                raise PriceError('Cache integrity mismatch')
            retrieved = dt.datetime.fromisoformat(provenance['retrieved_at'])
            if retrieved.utcoffset() != dt.timedelta(0) or retrieved > now:
                raise PriceError('Invalid cache retrieval timestamp')
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise PriceError('Cache integrity failure: ' + str(exc)) from exc
    else:
        if offline:
            raise PriceError('Offline cache missing for ' + symbol)
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'KabuResearch/0.1 (https://github.com/syoudai0514/kabu)'})
                with urllib.request.urlopen(request, timeout=45) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise PriceError('Yahoo response exceeds 16 MiB')
                retrieved = dt.datetime.now(UTC)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429) or exc.code < 500 or attempt == 2:
                    raise PriceError('Yahoo HTTP ' + str(exc.code) + '; retrieval stopped', status=exc.code) from exc
                time.sleep(attempt + 1)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == 2:
                    raise PriceError('Yahoo retrieval failed after 3 attempts: ' + str(exc)) from exc
                time.sleep(attempt + 1)
        provenance = {'request': request_key, 'url': url, 'retrieved_at': retrieved.isoformat(),
                      'sha256': hashlib.sha256(raw).hexdigest()}
        # Validate before persisting so an error response cannot poison the cache.
        _normalize(raw, symbol, market, first, end, retrieved)
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with raw_path.open('xb') as stream:
                stream.write(raw)
            with metadata_path.open('x') as stream:
                json.dump(provenance, stream, sort_keys=True)
        except FileExistsError as exc:
            raise PriceError('Concurrent cache write; retry using existing cache') from exc
    result = _normalize(raw, symbol, market, first, end, retrieved)
    result['provenance'] = {**provenance, 'cache_hit': cached, 'raw_path': str(raw_path)}
    return result
