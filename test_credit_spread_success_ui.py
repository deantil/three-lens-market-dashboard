import unittest
from unittest.mock import patch
from datetime import date
from streamlit.testing.v1 import AppTest
from credit_spread_engine import build_candidates
from test_credit_spreads import leg, EXP, TODAY

class SuccessUI(unittest.TestCase):
    def test_candidate_table_and_csv_after_run_scan(self):
        rows, _ = build_candidates('AAPL', 101, EXP,
            [leg(100), leg(99, bid=.20, ask=.25)], [], today=TODAY)
        for row in rows:
            row['Trend alignment'] = 'Aligned'
        app = AppTest.from_file('streamlit_app.py', default_timeout=30).run()
        with patch('credit_spread_scanner.scan_symbol', return_value=(rows, {
            'Ticker': 'AAPL', 'Chains loaded': 1, 'Candidates': 1, 'Data errors': 'TEST FIXTURE'})):
            next(b for b in app.button if b.label == 'Run Scan').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.success)
        frame = app.dataframe[-1].value
        self.assertTrue((frame['Max loss incl fees $'] <= 50).all())
        self.assertTrue(frame['Approval status'].str.contains('REVIEW REQUIRED').all())
        self.assertTrue(any(b.label == 'Download credit spread candidates CSV' for b in app.get('download_button')))

if __name__ == '__main__':
    unittest.main()

