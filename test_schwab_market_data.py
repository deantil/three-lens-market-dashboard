import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock
from urllib.parse import parse_qs,urlsplit,urlencode
from schwab_market_data import SchwabMarketData,SchwabDataError,normalize_chain

class SchwabTests(unittest.TestCase):
    def test_oauth_checks_callback_state_and_never_url_secret(self):
        client=SchwabMarketData('key','private-secret','https://127.0.0.1')
        url=client.authorization_url();self.assertNotIn('private-secret',url)
        state=parse_qs(urlsplit(url).query)['state'][0]
        with self.assertRaises(SchwabDataError):client.complete_authorization('https://evil.example/?code=one&state='+state)
        with self.assertRaises(SchwabDataError):client.complete_authorization('https://127.0.0.1?code=one&state=wrong')
        with self.assertRaises(SchwabDataError):client.get('/accounts',{})

    def test_token_exchange_and_quote_only_verification(self):
        session=Mock();session.post.return_value.status_code=200
        session.post.return_value.json.return_value={'access_token':'access','refresh_token':'refresh','expires_in':1800}
        session.get.return_value.status_code=200;session.get.return_value.json.return_value={'SPY':{'quote':{'lastPrice':600}}}
        client=SchwabMarketData('key','secret','https://127.0.0.1',session)
        state=parse_qs(urlsplit(client.authorization_url()).query)['state'][0]
        client.complete_authorization('https://127.0.0.1?'+urlencode({'code':'one','state':state}))
        self.assertTrue(client.verify())
        self.assertEqual(session.post.call_args.kwargs['auth'],('key','secret'))
        self.assertIn('/marketdata/v1/quotes',session.get.call_args.args[0])

    def test_normalization_expiration_deliverables_and_quote_age(self):
        now=datetime.now(timezone.utc);exp=(now.date()+timedelta(days=70)).isoformat()
        c={'symbol':'AAPL  '+(now.date()+timedelta(days=70)).strftime('%y%m%d')+'C00100000',
            'putCall':'CALL','nonStandard':False,'multiplier':100,'strikePrice':100,
            'bid':4,'ask':4.1,'volatility':40,'openInterest':1000,'totalVolume':100,
            'quoteTimeInLong':now.timestamp()*1000}
        payload={'underlyingPrice':90,'callExpDateMap':{exp+':70':{'100':[c]}}}
        result=normalize_chain('AAPL',payload,now)
        self.assertEqual(result['chains'][exp][0]['impliedVolatility'],.4)
        c['nonStandard']=True
        self.assertEqual(normalize_chain('AAPL',payload,now)['chains'],{})
        c['nonStandard']=False;c['quoteTimeInLong']=(now-timedelta(minutes=21)).timestamp()*1000
        self.assertEqual(normalize_chain('AAPL',payload,now)['chains'],{})

    def test_errors_never_echo_tokens(self):
        session=Mock();session.post.return_value.status_code=401
        session.post.return_value.text='sensitive secret response'
        client=SchwabMarketData('key','secret','https://127.0.0.1',session)
        with self.assertRaises(SchwabDataError) as e:client._exchange({'grant_type':'refresh_token'})
        self.assertNotIn('sensitive',str(e.exception))

if __name__=='__main__':unittest.main()

