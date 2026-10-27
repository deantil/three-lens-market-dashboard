"""UI interaction tests use synthetic fixtures; no market request or order."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

try:
    import pandas as pd
    from streamlit.testing.v1 import AppTest
    import orb_dashboard as ui
    import test_orb as fixtures
    UI_DEPS=True
except ModuleNotFoundError:
    UI_DEPS=False


@unittest.skipUnless(UI_DEPS,'ORB UI checks require dashboard dependencies')
class ORBPanelTests(unittest.TestCase):
    def test_stopped_panel_has_instructions_without_network(self):
        with patch.object(ui,'load_orb_data') as load:
            app=AppTest.from_string('from orb_dashboard import render_orb\nrender_orb()').run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any('Monitoring is stopped' in i.value for i in app.info))
            load.assert_not_called()

    def test_candidate_plan_requires_checks_and_can_be_calculated(self):
        fixture=fixtures.ORBTests()
        fixture.setUp()
        with patch.object(ui,'datetime') as clock, \
             patch.object(ui,'calendar_for_date',return_value=fixture.times), \
             patch.object(ui,'load_orb_data',return_value=({'TEST':fixture.bars},{'TEST':fixture.daily},fixture.now.to_pydatetime())):
            clock.now.return_value=fixture.now.to_pydatetime()
            app=AppTest.from_string('from orb_dashboard import render_orb\nrender_orb()').run()
            app.text_area[0].set_value('TEST')
            next(b for b in app.button if b.label=='Start ORB monitoring').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(app.selectbox[0].value,'TEST')
            next(b for b in app.button if b.label=='Calculate my paper trade plan').click().run()
            self.assertTrue(any('Complete the fresh quote' in e.value for e in app.error))
            for n in app.number_input:
                if n.label=='Current paperMoney bid ($)': n.set_value(101.01)
                if n.label=='Current paperMoney ask ($)': n.set_value(101.02)
            for cb in app.checkbox:
                if cb.label.startswith(('I just checked','I checked today','I verified these','I understand')):
                    cb.check()
            next(b for b in app.button if b.label=='Calculate my paper trade plan').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any('PAPER PLAN:' in s.value for s in app.success))
            next(b for b in app.button if b.label=='Stop ORB monitoring').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertFalse(app.success)

    def test_after_cutoff_offers_no_entry_plan(self):
        fixture=fixtures.ORBTests()
        fixture.setUp()
        late=pd.Timestamp('2026-10-27 11:01',tz=ui.ET)
        with patch.object(ui,'datetime') as clock, \
             patch.object(ui,'calendar_for_date',return_value=fixture.times), \
             patch.object(ui,'load_orb_data',return_value=({'TEST':fixture.bars},{'TEST':fixture.daily},late.to_pydatetime())):
            clock.now.return_value=late.to_pydatetime()
            app=AppTest.from_string('from orb_dashboard import render_orb\nrender_orb()').run()
            app.text_area[0].set_value('TEST')
            next(b for b in app.button if b.label=='Start ORB monitoring').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertTrue(any('Entry window closed' in w.value for w in app.warning))
            self.assertFalse(app.selectbox)
