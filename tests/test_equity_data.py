import pathlib
import sys
import unittest
import numpy as np

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from equity_data import align


class EquityDataTest(unittest.TestCase):
    def test_fx_holiday_mark_and_ipo_do_not_fabricate_native_bars(self):
        instruments=[dict(symbol='JP',market='JP',equity=True,bars=[dict(date=d,close=100) for d in ['2026-01-05','2026-01-06','2026-01-07']]),
                     dict(symbol='US',market='US',equity=True,bars=[dict(date='2026-01-06',close=10)])]
        fx=[dict(at='2026-01-05T00:00:00+00:00',mid=150),dict(at='2026-01-07T00:00:00+00:00',mid=160)]
        data=align(instruments,fx,'2026-01-05','2026-01-07')
        self.assertTrue(np.isnan(data['mark_prices'][0,1]))
        self.assertEqual(data['mark_prices'][2,1],1600)
        self.assertFalse(data['valid_observation'][2,1])
        self.assertTrue(np.isnan(data['prices'][2,1]))

    def test_future_fx_mutation_does_not_change_earlier_marks(self):
        instruments=[dict(symbol='US',market='US',equity=True,bars=[dict(date='2026-01-05',close=10)])]
        fx=[dict(at='2026-01-05T00:00:00+00:00',mid=150),dict(at='2026-01-06T00:00:00+00:00',mid=999)]
        a=align(instruments,fx,'2026-01-05','2026-01-05')
        fx[1]['mid']=1
        b=align(instruments,fx,'2026-01-05','2026-01-05')
        np.testing.assert_array_equal(a['mark_prices'],b['mark_prices'])

    def test_duplicate_and_stale_fx_rejected(self):
        instruments=[dict(symbol='US',market='US',equity=True,bars=[dict(date='2026-01-05',close=10)])]
        for fx in [[dict(at='2026-01-05T00:00:00+00:00',mid=150)]*2,[dict(at='2025-12-01T00:00:00+00:00',mid=150)]]:
            with self.assertRaises(ValueError):
                align(instruments,fx,'2026-01-05','2026-01-05')


if __name__=='__main__':
    unittest.main()
