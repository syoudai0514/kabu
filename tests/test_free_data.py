import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import free_data as f

OBSERVED = '2026-10-24T12:00:00+00:00'


def directory(name='Example Common Stock', symbol='AAA', etf='N'):
    return (f'Symbol|Security Name|Test Issue|ETF|Round Lot Size\n'
            f'{symbol}|{name}|N|{etf}|100\nFile Creation Time: 1023202618:00||||\n').encode()


def feed(title='8-K Example', updated='2026-10-23T16:05:00-04:00'):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>accession-1</id><title>{title}</title><updated>{updated}</updated>
    <link href="https://www.sec.gov/Archives/example.htm"/>
    <category term="8-K"/></entry></feed>'''.encode()


class FreeDataTest(unittest.TestCase):
    def test_jpx_workbook_and_candidate_classification(self):
        import openpyxl
        book = openpyxl.Workbook()
        book.active.append(['日付', 'コード', '銘柄名', '市場・商品区分', '33業種区分', '規模区分'])
        book.active.append([20260930, '123A', 'Example', 'グロース（内国株式）', '情報・通信業', '-'])
        book.active.append([20260930, 1305, 'Example ETF', 'ETF・ETN', '-', '-'])
        raw = io.BytesIO()
        book.save(raw)
        records, meta = f.parse_jpx(raw.getvalue())
        self.assertEqual(meta['source_as_of'], '2026-09-30')
        self.assertTrue(records[0]['common_stock_candidate'])
        self.assertFalse(records[1]['common_stock_candidate'])
        self.assertFalse(records[0]['instrument_type_verified'])
        self.assertIsNone(records[0]['historical_lot'])

    def test_directory_is_not_authoritative_lot_or_security_type(self):
        rows, _ = f.parse_us(directory(), 'nasdaq_listed')
        self.assertTrue(rows[0]['common_stock_candidate'])
        self.assertEqual(rows[0]['source_round_lot'], '100')
        self.assertIsNone(rows[0]['historical_lot'])
        for name in ['Example Preferred Common Stock', 'Example Depositary Common Shares', 'Example Units', 'Example Warrant', 'Example Common Stock Warrants', 'Example Preference Common Shares', 'Example Funds Common Shares']:
            self.assertFalse(f.parse_us(directory(name), 'nasdaq_listed')[0][0]['common_stock_candidate'])
        with self.assertRaises(ValueError):
            f.parse_us(directory().split(b'File Creation Time:')[0], 'nasdaq_listed')

    def test_repeated_download_preserves_first_seen_and_revision_history(self):
        db = f.connect(':memory:')
        first = f.ingest(db, 'sec_filings', feed(), OBSERVED)
        self.assertEqual(first['new_versions'], 1)
        later = '2026-10-25T12:00:00+00:00'
        self.assertEqual(f.ingest(db, 'sec_filings', feed(), later)['new_versions'], 0)
        f.ingest(db, 'sec_filings', feed('8-K Correction'), later)
        # Even a revision that returns to the original content is recorded in its batch.
        f.ingest(db, 'sec_filings', feed(), later)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM batch_items').fetchone()[0], 4)
        self.assertEqual(db.execute('SELECT MIN(first_seen_at) FROM observations').fetchone()[0], OBSERVED)
        self.assertIsNone(json.loads(db.execute('SELECT payload FROM observations LIMIT 1').fetchone()[0])['published_at'])
        with self.assertRaises(ValueError):
            f.ingest(db, 'sec_filings', feed(), OBSERVED)

    def test_future_and_naive_timestamps_rejected_without_partial_write(self):
        db = f.connect(':memory:')
        for updated in ['2027-01-01T00:00:00Z', '2026-10-20T00:00:00']:
            with self.assertRaises(ValueError):
                f.ingest(db, 'sec_filings', feed(updated=updated), OBSERVED)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 0)

    def test_duplicate_identity_and_xml_entities_rejected(self):
        raw = directory().decode().splitlines()
        with self.assertRaises(ValueError):
            f.ingest(f.connect(':memory:'), 'nasdaq_listed', '\n'.join([raw[0], raw[1], raw[1], raw[2]]).encode(), OBSERVED)
        with self.assertRaises(ValueError):
            f.parse_sec(b'<!DOCTYPE feed [<!ENTITY x "bad">]>' + feed())

    def test_empty_feed_valid_but_unexpected_html_fails(self):
        rows, meta = f.parse_sec(b'<feed xmlns="http://www.w3.org/2005/Atom"/>')
        self.assertEqual(rows, [])
        self.assertFalse(meta['full_archive'])
        with self.assertRaises(ValueError):
            f.parse_sec(b'<html>rate limit</html>')

    def test_sec_reporting_person_and_issuer_are_one_event(self):
        first = feed()
        second_entry = feed('8-K Issuer').split(b'<entry>')[1].split(b'</entry>')[0]
        combined = first.replace(b'</feed>', b'<entry>' + second_entry + b'</entry></feed>')
        records, _ = f.parse_sec(combined)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]['mentions']), 2)
        swapped = feed('8-K Issuer').replace(b'</feed>', b'<entry>' + feed().split(b'<entry>')[1].split(b'</entry>')[0] + b'</entry></feed>')
        self.assertEqual(f.parse_sec(combined), f.parse_sec(swapped))

    def test_public_allowlist_and_workbook_discovery(self):
        for url in ['http://www.sec.gov/a', 'https://paid.example/a', 'https://user:pass@www.sec.gov/a', 'https://www.sec.gov:444/a']:
            with self.assertRaises(ValueError):
                f.allowed_url(url)
        parser = f.WorkbookLinks()
        parser.feed('<a href="/markets/statistics-equities/misc/test/data_j.xlsx">file</a>')
        self.assertEqual(parser.links, ['https://www.jpx.co.jp/markets/statistics-equities/misc/test/data_j.xlsx'])

    def test_partial_failure_keeps_other_source_and_reports_gaps(self):
        with tempfile.TemporaryDirectory() as output:
            with patch.object(f, 'download', side_effect=[ValueError('bad source'), feed()]), patch.object(f, 'now', return_value=OBSERVED), patch.object(f.time, 'sleep'):
                result = f.collect(output, ['nasdaq_listed', 'sec_filings'])
            self.assertEqual([x['status'] for x in result['sources']], ['failed', 'ok'])
            self.assertFalse(result['historical_pit_complete'])
            self.assertFalse(result['universe_selection_ready'])
            self.assertEqual(result['data_subscription_cost_jpy'], 0)
            self.assertTrue(pathlib.Path(output, 'status.json').exists())
            self.assertEqual(len(list(pathlib.Path(output, 'raw').iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
