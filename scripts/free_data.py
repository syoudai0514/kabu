"""Free public-source observations, not a historical investable equity universe.

No broker, paid API, LLM request, scheduler or automatic symbol selection.
Raw downloads and the SQLite ledger stay local. Collection time is never backdated.
"""
import csv
import datetime as dt
import hashlib
import io
import json
import pathlib
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

UTC = dt.timezone.utc
USER_AGENT = 'KabuResearch/0.1 (https://github.com/syoudai0514/kabu)'
JPX_PAGE = 'https://www.jpx.co.jp/markets/statistics-equities/misc/01.html'
SOURCES = {
    'jpx_listings': JPX_PAGE,
    'nasdaq_listed': 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',
    'other_listed': 'https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt',
    'sec_filings': 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&count=100&output=atom',
}
ALLOWED_HOSTS = {'www.jpx.co.jp', 'www.nasdaqtrader.com', 'www.sec.gov'}


def instant(value):
    value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timestamp must include a UTC offset')
    return value.astimezone(UTC)


def now():
    return dt.datetime.now(UTC).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def allowed_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Only configured public-source HTTPS hosts are allowed')
    return url


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, allowed_url(newurl))


def download(url):
    """Three attempts at most; no credentials or paid fallback. Calls are serial."""
    opener = urllib.request.build_opener(PublicRedirect())
    for attempt in range(3):
        try:
            request = urllib.request.Request(allowed_url(url), headers={'User-Agent': USER_AGENT})
            with opener.open(request, timeout=25) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError('Response exceeds 16 MiB')
                return raw
        except urllib.error.HTTPError as exc:
            if attempt == 2 or (exc.code != 429 and not 500 <= exc.code < 600):
                raise
            delay = exc.headers.get('Retry-After', '')
            time.sleep(min(10, max(1, int(delay))) if delay.isdigit() else 2 ** attempt)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


class WorkbookLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            if re.search(r'/data_j\.xlsx?$', href):
                self.links.append(allowed_url(urllib.parse.urljoin(JPX_PAGE, href)))


def text_cell(value):
    return str(int(value)) if isinstance(value, (int, float)) and value == int(value) else str(value or '').strip()


def parse_jpx_rows(rows):
    headers = [text_cell(c) for c in next(rows)]
    required = {'日付', 'コード', '銘柄名', '市場・商品区分', '33業種区分', '規模区分'}
    if not required.issubset(headers):
        raise ValueError('JPX workbook schema changed')
    records, dates = [], set()
    for row in rows:
        if not any(c is not None and c != '' for c in row):
            continue
        item = dict(zip(headers, map(text_cell, row)))
        date = dt.datetime.strptime(item['日付'], '%Y%m%d').date().isoformat()
        symbol = item['コード']
        if not re.fullmatch(r'[0-9A-Z]{4,5}', symbol) or not item['銘柄名']:
            raise ValueError('Invalid JPX security record')
        dates.add(date)
        candidate = item['市場・商品区分'] in {'プライム（内国株式）', 'スタンダード（内国株式）', 'グロース（内国株式）'}
        records.append(dict(key=symbol, market='JP', symbol=symbol, name=item['銘柄名'],
                            source_as_of=date, market_segment=item['市場・商品区分'],
                            sector=item['33業種区分'], size_label=item['規模区分'],
                            common_stock_candidate=candidate, instrument_type_verified=False,
                            permanent_id=None, historical_lot=None))
    if len(dates) != 1:
        raise ValueError('Missing or mixed JPX snapshot dates')
    return records, dict(source_as_of=next(iter(dates)), coverage='monthly_snapshot')


def parse_jpx(raw):
    if raw.startswith(b'PK'):
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            return parse_jpx_rows(iter(book.active.values))
        finally:
            book.close()
    import xlrd
    book = xlrd.open_workbook(file_contents=raw)
    sheet = book.sheet_by_index(0)
    return parse_jpx_rows(iter(sheet.row_values(i) for i in range(sheet.nrows)))


def parse_us(raw, source):
    lines = raw.decode('utf-8-sig').splitlines()
    trailers = [line for line in lines if line.startswith('File Creation Time:')]
    if len(trailers) != 1 or lines[-1] != trailers[0]:
        raise ValueError('Missing or misplaced Nasdaq file completion marker')
    reader = csv.DictReader(lines[:-1], delimiter='|')
    symbol_field = 'Symbol' if source == 'nasdaq_listed' else 'ACT Symbol'
    if not {symbol_field, 'Security Name', 'Test Issue', 'ETF', 'Round Lot Size'}.issubset(reader.fieldnames or []):
        raise ValueError('Nasdaq directory schema changed')
    records = []
    for row in reader:
        if None in row or any(v is None for v in row.values()):
            raise ValueError('Truncated directory row')
        if row['Test Issue'] not in ('Y', 'N') or row['ETF'] not in ('Y', 'N'):
            raise ValueError('Unknown directory classification')
        name, symbol = row['Security Name'].strip(), row[symbol_field].strip()
        if not name or not symbol:
            raise ValueError('Empty security identity')
        # A screening hint only. Names do not constitute an authoritative security master.
        candidate = bool(re.search(r'\b(common stock|ordinary shares|common shares)\b', name, re.I))
        candidate &= not bool(re.search(r'\b(preferred|preference|depositary|warrants?|units?|rights?|funds?|ETF)\b', name, re.I))
        candidate &= row['Test Issue'] == 'N' and row['ETF'] == 'N'
        records.append(dict(key=symbol, market='US', symbol=symbol, name=name,
                            exchange=row.get('Exchange', 'NASDAQ'), test_issue=row['Test Issue'], etf=row['ETF'],
                            source_round_lot=row['Round Lot Size'],
                            common_stock_candidate=bool(candidate), instrument_type_verified=False,
                            permanent_id=None, historical_lot=None))
    return records, dict(source_as_of_label=trailers[0].split('|')[0], coverage='current_directory',
                         round_lot_note='Exchange round lot is not a broker minimum order size')


def parse_sec(raw):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('XML declarations with entities are not accepted')
    root = ET.fromstring(raw)
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    if root.tag != '{http://www.w3.org/2005/Atom}feed':
        raise ValueError('Expected SEC Atom feed')
    records = []
    for entry in root.findall('a:entry', ns):
        key = entry.findtext('a:id', default='', namespaces=ns).strip()
        title = entry.findtext('a:title', default='', namespaces=ns).strip()
        updated = entry.findtext('a:updated', default='', namespaces=ns)
        link = next((x.get('href') for x in entry.findall('a:link', ns) if x.get('rel', 'alternate') == 'alternate'), None)
        category = entry.find('a:category', ns)
        if not key or not title or not link:
            raise ValueError('Incomplete SEC filing entry')
        if urllib.parse.urlsplit(allowed_url(link)).hostname != 'www.sec.gov':
            raise ValueError('SEC filing link has unexpected host')
        records.append(dict(key=key, title=title, source_url=link,
                            provider_updated_at=instant(updated).isoformat(), published_at=None,
                            form=category.get('term') if category is not None else None,
                            security_ids=[], mapping_status='unresolved'))
    # SEC repeats an accession for reporting persons, issuers and subjects. Keep
    # their links as mentions of ONE document, not independent sentiment events.
    grouped = {}
    for record in records:
        grouped.setdefault(record['key'], []).append(record)
    documents = []
    for key, mentions in sorted(grouped.items()):
        if len({m['form'] for m in mentions}) != 1:
            raise ValueError('Conflicting form types for one SEC accession')
        unique = sorted({canonical(m) for m in mentions})
        mentions = [json.loads(m) for m in unique]
        document = dict(mentions[0])
        document['provider_updated_at'] = max(m['provider_updated_at'] for m in mentions)
        document['mentions'] = [dict(title=m['title'], source_url=m['source_url'],
                                    provider_updated_at=m['provider_updated_at']) for m in mentions]
        documents.append(document)
    # Empty weekends are valid; this is only a latest-100 feed, never a complete archive.
    return documents, dict(coverage='latest_100_partial_feed', full_archive=False)


def connect(path):
    connection = sqlite3.connect(path)
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS batches (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, observed_at TEXT NOT NULL,
            status TEXT NOT NULL, raw_sha256 TEXT, item_count INTEGER, details TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations (
            source TEXT NOT NULL, item_key TEXT NOT NULL, content_sha256 TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, payload TEXT NOT NULL,
            PRIMARY KEY(source, item_key, content_sha256));
        CREATE TABLE IF NOT EXISTS batch_items (
            batch_id INTEGER NOT NULL, source TEXT NOT NULL, item_key TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            PRIMARY KEY(batch_id, source, item_key));
    ''')
    return connection


def ingest(connection, source, raw, observed_at):
    observed = instant(observed_at)
    if source == 'jpx_listings':
        records, metadata = parse_jpx(raw)
    elif source in ('nasdaq_listed', 'other_listed'):
        records, metadata = parse_us(raw, source)
    elif source == 'sec_filings':
        records, metadata = parse_sec(raw)
    else:
        raise ValueError('Unknown free data source')
    if source != 'sec_filings' and not records:
        raise ValueError('Empty listing snapshot')
    if len({r['key'] for r in records}) != len(records):
        raise ValueError('Duplicate identities in one source response')
    if metadata.get('source_as_of', '') > observed.date().isoformat():
        raise ValueError('Snapshot dated after collection')
    if any(instant(r['provider_updated_at']) > observed for r in records if 'provider_updated_at' in r):
        raise ValueError('Future-dated filing: quarantine source response')
    previous = connection.execute('SELECT MAX(observed_at) FROM batches WHERE source=?', (source,)).fetchone()[0]
    if previous and observed < instant(previous):
        raise ValueError('Cannot backdate collection')
    digest = hashlib.sha256(raw).hexdigest()
    added = 0
    with connection:
        batch_id = connection.execute('INSERT INTO batches(source,observed_at,status,raw_sha256,item_count,details) VALUES (?,?,?,?,?,?)',
                                      (source, observed.isoformat(), 'ok', digest, len(records), '{}')).lastrowid
        for record in records:
            # Include directory snapshot metadata so changed publication dates remain distinct versions.
            payload = canonical(dict(record, source_metadata=metadata))
            content_hash = hashlib.sha256(payload.encode()).hexdigest()
            added += connection.execute('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)',
                                        (source, record['key'], content_hash, observed.isoformat(), payload)).rowcount
            connection.execute('INSERT INTO batch_items VALUES (?,?,?,?)',
                               (batch_id, source, record['key'], content_hash))
        details = dict(metadata, new_versions=added,
                       common_stock_candidates=sum(r.get('common_stock_candidate', False) for r in records))
        connection.execute('UPDATE batches SET details=? WHERE id=?', (canonical(details), batch_id))
    return dict(source=source, status='ok', observed_at=observed.isoformat(), items=len(records),
                sha256=digest, **details)


def collect(output, sources=None):
    output = pathlib.Path(output)
    (output / 'raw').mkdir(parents=True, exist_ok=True)
    connection = connect(output / 'observations.sqlite3')
    results = []
    try:
        for source in SOURCES if sources is None else sources:
            try:
                url = SOURCES[source]
                if source == 'jpx_listings':
                    page = download(url)
                    (output / 'raw' / (hashlib.sha256(page).hexdigest() + '.bin')).write_bytes(page)
                    parser = WorkbookLinks()
                    parser.feed(page.decode('utf-8'))
                    links = sorted(set(parser.links))
                    if len(links) != 1:
                        raise ValueError('Expected one JPX listing workbook link')
                    url = links[0]
                raw = download(url)
                observed_at = now()
                raw_path = output / 'raw' / (hashlib.sha256(raw).hexdigest() + '.bin')
                if not raw_path.exists():
                    raw_path.write_bytes(raw)
                result = ingest(connection, source, raw, observed_at)
                result['url'] = url
            except Exception as exc:
                # Connector boundary: malformed workbooks/XML must not prevent
                # the remaining independent sources from being collected.
                result = dict(source=source, status='failed', observed_at=now(), error=str(exc))
                with connection:
                    connection.execute('INSERT INTO batches(source,observed_at,status,details) VALUES (?,?,?,?)',
                                       (source, result['observed_at'], 'failed', canonical(result)))
            results.append(result)
            time.sleep(0.3)
    finally:
        connection.close()
    report = dict(schema_version=1, mode='free_public_observations', collected_at=now(),
                  data_subscription_cost_jpy=0, external_llm_calls=0, broker_orders_enabled=False,
                  historical_pit_complete=False, universe_selection_ready=False, sources=results,
                  missing=['historical_permanent_security_ids', 'historical_listings_and_delistings',
                           'historical_lots_and_corporate_actions', 'equity_prices_and_liquidity',
                           'historical_sector_and_size', 'complete_jp_us_news_archive',
                           'broker_tradability_and_news_security_mapping'])
    # Atomic status update. Source versions and raw files are retained separately.
    temporary = output / 'status.json.tmp'
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(output / 'status.json')
    return report
