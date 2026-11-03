"""UI and provider tests use explicit fixtures, never displayed as real market data."""
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
import pandas as pd
from streamlit.testing.v1 import AppTest
from credit_spread_scanner import run_scan, scan_symbol, snapshot_fallback

class ScannerTests(unittest.TestCase):
    def test_fresh_snapshot_recalculates_user_fees_and_direction(self):
        now = datetime.now(timezone.utc)
        exp = (now.date() + timedelta(days=10)).isoformat()
        code = (now.date() + timedelta(days=10)).strftime('%y%m%d')
        def option(strike, bid, ask):
            return {'contractSymbol': f'AAPL{code}P{strike*1000:08d}', 'strike': strike,
                    'bid': bid, 'ask': ask, 'contractSize': 'REGULAR', 'openInterest': 500, 'volume': 50}
        data = {'stocks': {'AAPL': {'retrieved_utc': now.isoformat(), 'spot': 101., 'trend': 'Bullish',
            'chains': {exp: {'puts': [option(100,.8,.85), option(99,.2,.25)], 'calls': []}}}}}
        with patch('credit_spread_scanner.published_snapshot', return_value=data):
            rows, status = snapshot_fallback('AAPL', 'Both', 3., '7–45 DTE', {'Data errors': ''})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['Max loss incl fees $'], 48.)
            self.assertIn('Published', rows[0]['Data source'])
            self.assertEqual(snapshot_fallback('AAPL', 'Both', 6., '7–45 DTE', {'Data errors': ''})[0], [])
            self.assertEqual(snapshot_fallback('AAPL', 'Bear call', 3., '7–45 DTE', {'Data errors': ''})[0], [])

    def test_stale_snapshot_is_rejected(self):
        data = {'stocks': {'AAPL': {'retrieved_utc': (datetime.now(timezone.utc) - timedelta(minutes=21)).isoformat()}}}
        with patch('credit_spread_scanner.published_snapshot', return_value=data):
            rows, status = snapshot_fallback('AAPL', 'Both', 3., '7–45 DTE', {'Data errors': 'direct unavailable'})
        self.assertEqual(rows, [])
        self.assertIn('stale', status['Data errors'])

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

