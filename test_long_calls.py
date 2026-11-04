import unittest
from datetime import date, timedelta
from long_call_engine import analyze_calls, black_scholes_call

TODAY = date(2026, 11, 3)
EXP = TODAY+timedelta(days=75)


def call(strike=100, **changes):
    row = {'contractSymbol': f'AAPL{EXP:%y%m%d}C{int(strike*1000):08d}', 'strike': strike,
           'ask': 10., 'bid': 9.8, 'openInterest': 500, 'volume': 50,
           'contractSize': 'REGULAR', 'impliedVolatility': .3}
    return dict(row, **changes)


class LongCallTests(unittest.TestCase):
    def analyze(self, rows, **kw):
        return analyze_calls('AAPL', 100., 120., EXP.isoformat(), rows, today=TODAY, **kw)

    def test_known_black_scholes_greeks_and_finite_differences(self):
        model = black_scholes_call(100., 100., 1., .2, .05)
        self.assertAlmostEqual(model['price'], 10.45058357, places=6)
        self.assertAlmostEqual(model['delta'], .63683065, places=6)
        self.assertAlmostEqual(model['theta'], -.01757268, places=6)
        self.assertAlmostEqual(model['vega'], .37524035, places=6)
        delta = (black_scholes_call(100.01,100,1,.2,.05)['price']-black_scholes_call(99.99,100,1,.2,.05)['price'])/.02
        self.assertAlmostEqual(model['delta'], delta, places=6)

    def test_ask_cost_breakeven_and_no_account_or_risk_cap(self):
        row = self.analyze([call(50, ask=75., bid=74.)], fees=2.)[0]
        self.assertEqual(row['Premium $'], 7500.)
        self.assertEqual(row['Max loss incl fees $'], 7502.)
        self.assertAlmostEqual(row['Expiration breakeven $'], 125.02)
        self.assertEqual(row['Expiration target profit $'], -502.)

    def test_gamma_rho_finite_differences_and_pricing_breakdown(self):
        model = black_scholes_call(100,100,1,.2,.05)
        gamma = (black_scholes_call(100.01,100,1,.2,.05)['delta']-
                 black_scholes_call(99.99,100,1,.2,.05)['delta'])/.02
        rho = (black_scholes_call(100,100,1,.2,.05001)['price']-
               black_scholes_call(100,100,1,.2,.04999)['price'])/.00002/100
        self.assertAlmostEqual(model['gamma'], gamma, places=7)
        self.assertAlmostEqual(model['rho'], rho, places=7)
        row = self.analyze([call(95,ask=10,bid=9.8)])[0]
        self.assertEqual(row['Moneyness'], 'ITM')
        self.assertEqual(row['Intrinsic value / share $'],5)
        self.assertEqual(row['Ask extrinsic value / share $'],5)
        self.assertAlmostEqual(row['Bid/ask width % of ask'],2)
        self.assertGreater(row['Estimated gamma'],0)
        self.assertGreater(row['Estimated rho $/rate point'],0)

    def test_halfway_scenarios_iv_shock_and_expiry_target(self):
        row = self.analyze([call()])[0]
        self.assertEqual(row['Scenario horizon days'], 37)
        expected = black_scholes_call(120,100,38/365,.3,.04)['price']*100-1001.3
        self.assertAlmostEqual(row['Target scenario profit $'], expected, places=2)
        self.assertGreater(row['Target scenario profit $'], row['Flat scenario profit $'])
        self.assertGreater(row['Flat scenario profit $'], row['Down 10% scenario profit $'])
        self.assertLess(row['Flat scenario IV -25% profit $'], row['Flat scenario profit $'])
        self.assertEqual(row['Expiration target profit $'], 998.7)
        self.assertIn('not broker Greeks', row['Greek source'])

    def test_missing_zero_nonfinite_iv_retained_without_model_metrics(self):
        for iv in [None, 0., -1., float('nan'), float('inf')]:
            row = self.analyze([call(impliedVolatility=iv)])[0]
            self.assertIsNone(row['Estimated delta'])
            self.assertIsNone(row['Target scenario profit $'])
            self.assertIn('UNAVAILABLE', row['Model status'])

    def test_poor_liquidity_flagged_not_filtered_and_all_strikes(self):
        rows = self.analyze([call(10, ask=.01, bid=0, openInterest=0, volume=0), call(1000)])
        self.assertEqual(len(rows), 2)
        self.assertTrue(next(r for r in rows if r['Strike']==10)['Liquidity concerns'])

    def test_put_wrong_root_expiry_adjusted_and_invalid_ask_rejected(self):
        for change in [{'contractSymbol': f'AAPL{EXP:%y%m%d}P00100000'},
                       {'contractSymbol': f'AAPL1{EXP:%y%m%d}C00100000'},
                       {'contractSymbol': f'MSFT{EXP:%y%m%d}C00100000'},
                       {'contractSymbol': f'AAPL{EXP+timedelta(days=1):%y%m%d}C00100000'},
                       {'contractSize': 'MINI'}, {'ask': 0}, {'ask': float('nan')}]:
            self.assertEqual(self.analyze([call(**change)]), [])
        for days, count in [(59,0),(60,1),(90,1),(91,0)]:
            exp = TODAY+timedelta(days=days)
            row = call(contractSymbol=f'AAPL{exp:%y%m%d}C00100000')
            self.assertEqual(len(analyze_calls('AAPL',100,120,exp.isoformat(),[row],today=TODAY)),count)

    def test_expiry_model_intrinsic_and_invalid_inputs(self):
        self.assertEqual(black_scholes_call(120,100,0,.3)['price'],20)
        with self.assertRaises(ValueError): black_scholes_call(100,100,1,0)
        with self.assertRaises(ValueError): self.analyze([call()],fees=-1)


if __name__ == '__main__': unittest.main()
