import unittest
from long_call_scanner import shortlist_ideas, run_call_research
from test_long_call_ui import fixture

class ShortlistTests(unittest.TestCase):
    def test_real_spread_legs_and_fee_economics(self):
        data=fixture()
        chain=next(iter(data['calls']['AAPL']['chains'].values()))
        second=dict(chain[0],strike=90.,ask=3.,bid=2.9)
        second['contractSymbol']=second['contractSymbol'][:-8]+'00090000'
        chain.append(second)
        rows=run_call_research(data)['rows']
        for r in rows:r['Target scenario IV -25% profit $']=-1
        ideas=shortlist_ideas(rows)
        self.assertTrue(any(r['Direction']=='Bull call debit spread' for r in ideas))
        self.assertEqual(len({r['Ticker'] for r in ideas}),len(ideas))
        for idea in ideas:
            self.assertTrue(0<=idea['Estimated expiration POP %']<=100)
            if idea['Direction']=='Bull call debit spread':
                self.assertLess(idea['Long strike'],idea['Short strike'])
                self.assertAlmostEqual(idea['Max loss incl fees $'],idea['Entry debit / share $']*100+2.6)
                self.assertGreater(idea['Max profit $'],0)

    def test_no_padding_or_failed_quotes(self):
        rows=run_call_research(fixture())['rows']
        for r in rows:r['Liquidity concerns']=True
        self.assertEqual(shortlist_ideas(rows),[])

    def test_limit_deduplication_and_determinism(self):
        row=run_call_research(fixture())['rows'][0]
        rows=[dict(row,Ticker='T'+str(i),**{'Call contract':'T'+str(i)}) for i in range(70)]
        ideas=shortlist_ideas(rows+rows)
        self.assertEqual(len(ideas),50)
        self.assertEqual([r['Idea rank'] for r in ideas],list(range(1,51)))
        self.assertEqual(ideas,shortlist_ideas(list(reversed(rows+rows))))

    def test_stale_signal_and_no_positive_stress_excluded(self):
        row=run_call_research(fixture())['rows'][0]
        self.assertEqual(shortlist_ideas([dict(row,**{'Current reference deviation %':-19})]),[])
        self.assertEqual(shortlist_ideas([dict(row,**{'Target scenario IV -25% profit $':-1})]),[])

if __name__=='__main__':unittest.main()

