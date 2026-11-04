import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from long_call_scanner import run_call_research

def fixture():
    now=datetime.now(timezone.utc);exp=(now.date()+timedelta(days=70)).isoformat(); code=(now.date()+timedelta(days=70)).strftime('%y%m%d')
    rows=[];companies={};calls={}
    for symbol,cap in [('AAPL',100_000_000_000),('MSFT',200_000_000_000),('AMD',None)]:
        rows.append({'Ticker':symbol,'Price $':80.,'SMA25 $':100.,'Stop reference $':75.,'Avg daily $ volume':20_000_000,'Day change %':-2.,'Price timestamp':now.isoformat()})
        companies[symbol]={'market_cap':cap,'currency':'USD','retrieved_utc':now.isoformat()}
        calls[symbol]={'retrieved_utc':now.isoformat(),'spot':80.,'chains':{exp:[{'contractSymbol':symbol+code+'C00085000','strike':85.,'ask':5.,'bid':4.9,'contractSize':'REGULAR','openInterest':500,'volume':50,'impliedVolatility':.5}]}}
    return {'stock_snapshot':{'rows':rows,'generated_at_utc':now.isoformat()},'companies':companies,'calls':calls,'collected_utc':now.isoformat()}

class LongCallUI(unittest.TestCase):
    def test_market_cap_order_unlimited_premium_and_all_stocks(self):
        report=run_call_research(fixture())
        self.assertEqual([s['Ticker'] for s in report['stocks']],['MSFT','AAPL','AMD'])
        self.assertEqual(len(report['rows']),3)
        self.assertGreater(report['rows'][0]['Max loss incl fees $'],500)
        self.assertEqual(report['rows'][0]['Direction'],'Long call')
    def test_stale_feed_not_no_opportunity(self):
        data=fixture()
        data['calls']['AAPL']['retrieved_utc']=(datetime.now(timezone.utc)-timedelta(minutes=21)).isoformat()
        report=run_call_research(data)
        self.assertEqual(next(s for s in report['stocks'] if s['Ticker']=='AAPL')['Stock outcome'],'DATA UNAVAILABLE — CALLS')
    def test_evening_utc_timestamp_is_not_a_future_us_market_day(self):
        from kotegawa_options_engine import select_stock_signals
        data=fixture()
        row=dict(data['stock_snapshot']['rows'][0],**{'Price timestamp':'2026-11-04T00:30:00+00:00'})
        report=select_stock_signals([row],'2026-11-04T00:30:00+00:00',today=datetime(2026,11,3).date())
        self.assertEqual(len(report['signals']),1)
    def test_run_button_table_download_without_ticker_or_risk_limit(self):
        app=AppTest.from_string('from long_call_scanner import render\nrender()',default_timeout=30).run()
        self.assertFalse(app.text_area)
        with patch('long_call_scanner.load_call_feed',return_value=fixture()): app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.session_state['long_call_report']['rows']),3)
        self.assertTrue(any(b.label=='Download all call comparisons' for b in app.get('download_button')))

if __name__=='__main__':unittest.main()
