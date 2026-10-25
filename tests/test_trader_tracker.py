import unittest
from trader_tracker import validate, pnl, export_csv

class TrackerTests(unittest.TestCase):
    def row(self, **changes):
        return dict(trade_id='sample', trader='Alfredo Holguin', source_url='https://example.com/alert',
            published_utc='2026-01-01T14:00:00Z', received_utc='2026-01-01T14:00:05Z',
            contract='SPY 2026-02-20 600 C', action='BTO', quantity='1', signal_limit='2',
            status='Paper closed', paper_entry='2', paper_exit='2.4', fees='1.3', notes='', **changes)

    def test_round_trip_pnl_and_short_exclusion(self):
        row = validate([self.row()])[0]
        self.assertAlmostEqual(pnl(row), 38.7)
        self.assertIsNone(pnl(row | {'action': 'STO'}))

    def test_bad_records(self):
        for patch in [{'quantity': '1.5'}, {'paper_entry': 'NaN'}, {'fees': ''},
                      {'published_utc': '2026-01-01'}, {'source_url': 'javascript:alert(1)'},
                      {'received_utc': '2025-01-01T00:00:00Z'}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate([self.row() | patch])
        with self.assertRaises(ValueError):
            validate([self.row(), self.row()])

    def test_export_formula_protection(self):
        self.assertIn("'=danger", export_csv([self.row() | {'notes': '=danger'}]))

    def test_ui_render_and_refresh(self):
        import importlib.util
        if importlib.util.find_spec('streamlit') is None:
            self.skipTest('Streamlit UI test runs in the dashboard environment; collector CI uses requests only')
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('from unittest.mock import patch\nimport trader_tracker\nwith patch("trader_monitor_ui.render_monitor"), patch("trader_monitor_ui.render_trader_sections"):\n trader_tracker.render()', default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.tabs), 6)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIn('trader_refreshed', app.session_state)

if __name__ == '__main__':
    unittest.main()
