import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
import trader_agent as agent

class SourceTests(unittest.TestCase):
    def test_rss_and_untrusted_link(self):
        source=agent.SOURCES[3]
        xml='''<rss><channel><item><title>Company update</title><link>https://www.liquidmercury.com/news/test</link><pubDate>Mon, 28 Sep 2026 09:30:00 GMT</pubDate></item><item><title>Offsite</title><link>https://evil.example/phish</link></item></channel></rss>'''
        rows=agent.extract(source,xml)
        self.assertEqual(len(rows),1)
        self.assertFalse(rows[0]['copy_ready'])
        self.assertIn('NOT A TRADE',rows[0]['classification'])
        self.assertIsNotNone(rows[0]['published_utc'])

    def test_duplicates_baseline_and_failures(self):
        def fetch(source):
            if source['kind']=='rss':
                return '<rss><channel><item><title>News</title><link>'+source['url'].split('/news/')[0]+'/news/test</link></item></channel></rss>'
            return '<a href="/video/test">Recorded appearance</a>'
        first=agent.collect(fetcher=fetch)
        self.assertTrue(all(r['baseline'] for r in first['items']))
        again=agent.collect(first,fetcher=fetch)
        self.assertEqual(again['new_items_this_run'],0)
        self.assertEqual(len(first['items']),len(again['items']))
        def fail(source):
            raise ValueError('Access failed')
        failed=agent.collect(first,fetcher=fail)
        self.assertEqual(failed['items'],first['items'])
        self.assertTrue(all(s['status']=='error' for s in failed['sources']))

    def test_holguin_restricted_history_not_archived(self):
        self.assertEqual(agent.extract(agent.SOURCES[0],'<table>proprietary trade data</table>'),[])

    def test_does_not_invent_current_positions(self):
        report=agent.collect(fetcher=lambda source:'<rss><channel/></rss>')
        self.assertEqual(report['current_positions'],[])
        self.assertEqual(report['trade_alerts'],[])
        self.assertEqual(len(report['holdings_coverage']),6)
        self.assertTrue(all(not s['complete'] for s in report['holdings_coverage'].values()))

    def test_article_title_survives_generic_duplicate(self):
        rows=agent.extract(agent.SOURCES[5],'<a href="/daily/options-free/test">Actual publication title</a><a href="/daily/options-free/test">Watch Now</a>')
        self.assertEqual(rows[0]['title'],'Actual publication title')

    def test_signal_never_invents_sizing_or_price(self):
        signal=agent.parse_signal('BTO SPY 2026-11-20 600 C')
        self.assertIsNone(signal['quantity'])
        self.assertIsNone(signal['signal_limit'])
        self.assertFalse(signal['copy_ready'])
        self.assertEqual(agent.parse_signal('old education about SPY calls')['classification'],'REQUIRES HUMAN PARSING')

    def test_trade_headline_is_unconfirmed_not_a_holding(self):
        record={'id':'id','trader':'CNBC Investing Club','source_url':'https://www.cnbc.com/investingclub/test',
                'title':"Here's how we're adjusting our portfolio",'published_utc':'2026-10-24T10:00:00Z',
                'first_seen_utc':'2026-10-25T10:00:00Z','baseline':True}
        alert=agent.trade_related_update(record)
        self.assertFalse(alert['actual_transaction_confirmed'])
        self.assertIsNone(alert['quantity'])
        self.assertIn('BASELINE',alert['alert_state'])
        self.assertIsNone(agent.trade_related_update(record|{'trader':'John Carter','author':'Other author'}))

    def test_x_requires_explicit_approval_before_network(self):
        with patch('requests.Session.get') as get:
            with self.assertRaises(ValueError):
                agent.collect_x_private('unused-private.json')
            get.assert_not_called()

    def test_x_reserves_budget_before_uncertain_call(self):
        import json,requests
        with TemporaryDirectory() as folder:
            path=Path(folder)/'private.json'
            with patch.dict('os.environ',{'PROJECT_T_X_APPROVED':'yes','PROJECT_T_X_BEARER_TOKEN':'test-not-a-real-token','PROJECT_T_X_TOTAL_BUDGET_USD':'.05'}),patch('requests.Session.get',side_effect=requests.Timeout()):
                with self.assertRaises(requests.Timeout):
                    agent.collect_x_private(path,True)
                self.assertEqual(json.loads(path.read_text())['estimated_read_cost_usd'],.05)
                with self.assertRaises(ValueError):
                    agent.collect_x_private(path,True)

if __name__=='__main__':
    unittest.main()
