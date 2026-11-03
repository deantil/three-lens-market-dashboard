"""UI and provider tests use explicit fixtures, never displayed as real market data."""
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
import pandas as pd
from streamlit.testing.v1 import AppTest
from credit_spread_scanner import run_scan, scan_symbol

class ScannerTests(unittest.TestCase):
    def test_provider_failure_is_not_no_trade(self):
        with patch('credit_spread_scanner.yf.Ticker', side_effect=RuntimeError('provider denied')):
            report = run_scan(['AAPL'], 'Both', 3., '7–45 DTE')
        self.assertEqual(report['rows'], [])
        self.assertIn('provider denied', report['stocks'][0]['Data errors'])

    def test_ui_run_scan_and_existing_tabs(self):
        app = AppTest.from_file('streamlit_app.py', default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertEqual([t.label for t in app.tabs][:5], [
            '1 · Trader breakout', '2 · Nirvana Omni inspired', '3 · Kotegawa Mean Reversion',
            '4 · Intraday Buy / Sell', '5 · Credit Spread Scanner'])
        with patch('credit_spread_scanner.scan_symbol', return_value=([], {
                'Ticker': 'AAPL', 'Chains loaded': 0, 'Candidates': 0, 'Data errors': 'TEST provider unavailable'})):
            button = next(b for b in app.button if b.label == 'Run Scan')
            button.click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('DATA UNAVAILABLE' in e.value for e in app.error))
        self.assertTrue(any(b.label == 'Download scan coverage CSV' for b in app.get('download_button')))

if __name__ == '__main__':
    unittest.main()

