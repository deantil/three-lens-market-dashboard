import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from streamlit.testing.v1 import AppTest
from test_kotegawa_options import stock, candidate

class ConnectedUI(unittest.TestCase):
    def snapshot(self):
        row = stock()
        row['Price timestamp'] = datetime.now(timezone.utc).isoformat()
        return {'rows':[row], 'generated_at_utc':row['Price timestamp'], 'universe_size':1}

    def test_automatic_run_displays_matching_stock_and_spread_without_ticker_entry(self):
        app = AppTest.from_string('from kotegawa_options_scanner import render\nrender()',default_timeout=30).run()
        self.assertFalse(app.text_area)
        with patch('kotegawa_options_scanner.load_stock_snapshot',return_value=self.snapshot()), patch(
                'kotegawa_options_scanner.snapshot_fallback',return_value=(candidate(),{'Chains loaded':1,'Data errors':''})):
            next(b for b in app.button if b.label=='Run stock → options scan').click().run()
        self.assertFalse(app.exception)
        report = app.session_state['kotegawa_options_report']
        self.assertEqual(report['rows'][0]['Direction'],'Bull put')
        self.assertEqual(report['rows'][0]['Max loss incl fees $'],50.)
        self.assertEqual(report['stocks'][0]['Stock outcome'],'REVIEW REQUIRED — SPREAD FOUND')
        self.assertTrue(any(b.label=='Download stock + options candidates CSV' for b in app.get('download_button')))

    def test_missing_options_is_not_no_trade_or_success(self):
        app = AppTest.from_string('from kotegawa_options_scanner import render\nrender()',default_timeout=30).run()
        unavailable=([],{'Chains loaded':0,'Data errors':'TEST FIXTURE: unavailable'})
        with patch('kotegawa_options_scanner.load_stock_snapshot',return_value=self.snapshot()), patch(
                'kotegawa_options_scanner.snapshot_fallback',return_value=unavailable), patch(
                'kotegawa_options_scanner.scan_symbol',return_value=unavailable):
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.success)
        self.assertEqual(app.session_state['kotegawa_options_report']['stocks'][0]['Stock outcome'],'DATA UNAVAILABLE — OPTIONS')

    def test_existing_dashboard_tabs_are_preserved(self):
        app=AppTest.from_file('streamlit_app.py',default_timeout=40).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.tabs),5)
        self.assertTrue(any('Kotegawa' in tab.label for tab in app.tabs))

if __name__=='__main__': unittest.main()
