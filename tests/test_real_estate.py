import copy
from datetime import datetime, timezone, timedelta
import unittest
import real_estate as r


class PropertyScreenTests(unittest.TestCase):
    def setUp(self):
        self.p = copy.deepcopy(r.load_data()['properties'][0])

    def test_financing_and_costs(self):
        m = r.analyze(self.p)
        self.assertAlmostEqual(m['heloc'], 42225)
        self.assertAlmostEqual(m['heloc_interest'],316.6875)
        self.assertAlmostEqual(m['total_debt'],168900)
        self.assertAlmostEqual(m['cash_flow'],m['rent']*.74-m['fixed']-m['pi']-m['heloc_interest'])
        self.assertAlmostEqual(m['noi']-m['cash_noi'],m['rent']*.05)
        self.assertGreater(m['principal_year1'],0)
        self.assertAlmostEqual(m['savings'],m['closing']+m['initial_repairs']+m['reserves'])

    def test_break_even_and_target_after_both_loans(self):
        m=r.analyze(self.p)
        self.assertAlmostEqual(r.analyze(self.p,m['break_even'])['cash_flow'],0)
        self.assertAlmostEqual(r.analyze(self.p,m['target_rent'])['cash_flow'],250)

    def passing_fixture(self):
        now=datetime.now(timezone.utc)
        p=self.p
        p.update(unrestricted_rentals=True,safety_pass=True)
        for ev in p['evidence'].values():
            ev.update(status='verified',url='https://example.org/document',observed_at=now.isoformat())
        return p, now

    def test_no_missing_evidence_can_qualify_even_with_large_rent(self):
        self.assertTrue(r.blockers(self.p,r.analyze(self.p,5000)))

    def test_all_gates_required_and_freshness(self):
        p,now=self.passing_fixture()
        m=r.analyze(p,5000)
        self.assertEqual(r.blockers(p,m,now),[])
        p['evidence']['rental_rules']['observed_at']=(now-timedelta(days=8)).isoformat()
        self.assertTrue(any('rental_rules' in x for x in r.blockers(p,m,now)))
        p['evidence']['rental_rules']['observed_at']=(now+timedelta(days=1)).isoformat()
        self.assertTrue(any('rental_rules' in x for x in r.blockers(p,m,now)))

    def test_pending_and_budget_and_cash_flow_never_qualify(self):
        p,now=self.passing_fixture()
        m=r.analyze(p,5000)
        p['listing_status']='pending'
        self.assertIn('Active status not established',r.blockers(p,m,now))
        p['listing_status']='active'
        m['savings']=40001
        self.assertIn('Outside $30k–$40k savings budget',r.blockers(p,m,now))
        m['cash_flow']=249.99
        self.assertIn('Cash flow below $250/month',r.blockers(p,m,now))

    def test_current_dataset_is_unqualified_and_has_sources(self):
        data=r.load_data()
        self.assertEqual(len(data['properties']),5)
        for p in data['properties']:
            self.assertTrue(r.blockers(p,r.analyze(p)))
            self.assertTrue(p['listing_url'].startswith('https://'))
            self.assertEqual(set(p['evidence']),set(r.REQUIRED))

    def test_streamlit_render_and_sort(self):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_string('import real_estate\nreal_estate.render()').run(timeout=20)
        self.assertEqual(len(app.exception),0)
        self.assertIn('0 strictly qualified',app.warning[0].value)
        self.assertEqual(len(app.dataframe[0].value),5)
        app.radio[0].set_value('Purchase price (lowest first)').run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual(app.dataframe[0].value.iloc[0]['Price $'],168900)


if __name__=='__main__': unittest.main()
