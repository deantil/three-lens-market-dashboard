import unittest
from datetime import date
from kotegawa_options_engine import select_stock_signals, attach_spread_candidates
from credit_spread_engine import build_candidates

TODAY = date(2026, 11, 3)
STAMP = '2026-11-03T15:00:00+00:00'


def stock(ticker='AAPL', price=80., sma=100., stop=75., **changes):
    row = {'Ticker': ticker, 'Price $': price, 'SMA25 $': sma,
           'Stop reference $': stop, 'Avg daily $ volume': 20_000_000,
           'Day change %': -2., 'Price timestamp': STAMP}
    return dict(row, **changes)


def candidate(direction='Bull put', ticker='AAPL', credit=.53, fees=3.):
    short, long = (79, 78) if direction == 'Bull put' else (81, 82)
    kind = 'P' if direction == 'Bull put' else 'C'
    def leg(strike, bid, ask):
        return {'contractSymbol': f'{ticker}261113{kind}{strike*1000:08d}', 'strike': strike,
                'bid': bid, 'ask': ask, 'contractSize': 'REGULAR', 'openInterest': 500, 'volume': 50}
    chain = [leg(short, credit+.25, credit+.30), leg(long, .2, .25)]
    result = build_candidates(ticker, 80, '2026-11-13', chain if kind == 'P' else [],
                              chain if kind == 'C' else [], direction=direction, fees=fees, today=TODAY)[0]
    return result


class ConnectedStrategyTests(unittest.TestCase):
    def select(self, rows, **kwargs):
        return select_stock_signals(rows, STAMP, today=TODAY, **kwargs)

    def test_bearish_uses_symmetric_rr_not_bullish_negative_rr(self):
        result = self.select([stock(price=120, stop=115)])
        row = result['signals'][0]
        self.assertEqual(row['Direction'], 'Bear call')
        self.assertEqual(row['Stock stop $'], 125)
        self.assertEqual(row['Stock reference reward/risk'], 4)

    def test_signal_threshold_is_recomputed_not_trusted_from_snapshot(self):
        result = self.select([stock(**{'Deviation %': 0}), stock('MSFT', price=100, stop=95)])
        self.assertEqual([r['Ticker'] for r in result['signals']], ['AAPL'])
        self.assertEqual(result['stocks'][1]['Stock outcome'], 'NO SETUP')

    def test_missing_stale_future_nonfinite_and_universe_coverage(self):
        for changes in [{'Price timestamp': '2026-10-20'}, {'Price timestamp': '2026-11-04'},
                        {'Price $': float('nan')}, {'Day change %': None}]:
            self.assertEqual(self.select([stock(**changes)])['stocks'][0]['Stock outcome'], 'DATA UNAVAILABLE')
        stale = select_stock_signals([stock()], '2026-10-01', today=TODAY)
        self.assertTrue(stale['errors'])
        self.assertFalse(stale['signals'])
        result = self.select([stock(), stock('MSFT')], symbols=['AAPL', 'AMD'])
        self.assertEqual({r['Ticker'] for r in result['stocks']}, {'AAPL', 'AMD'})
        self.assertEqual(result['coverage']['stock_data_unavailable'], 1)

    def test_quality_and_directional_rebound_gates(self):
        for changes in [{'Price $': 4}, {'Avg daily $ volume': 9_000_000},
                        {'Day change %': -20}, {'Day change %': 11}, {'Stop reference $': 69}]:
            self.assertFalse(self.select([stock(**changes)])['signals'])
        # Same-day rebound gate applies to bullish signals, not bearish extension.
        self.assertTrue(self.select([stock(price=120, stop=115, **{'Day change %': 11})])['signals'])

    def test_matching_direction_and_50_dollar_fee_boundary(self):
        selection = self.select([stock()])
        reports = {'AAPL': {'rows': candidate()+candidate('Bear call'), 'status': {'Chains loaded': 1}}}
        report = attach_spread_candidates(selection, reports)
        self.assertEqual(len(report['rows']), 1)
        self.assertEqual(report['rows'][0]['Direction'], 'Bull put')
        self.assertEqual(report['rows'][0]['Max loss incl fees $'], 50)
        self.assertIn('REVIEW REQUIRED', report['rows'][0]['Approval status'])
        self.assertFalse(candidate(credit=.52))
        self.assertFalse(candidate(fees=3.1))

    def test_no_setup_missing_chain_and_no_suitable_trade_are_distinct(self):
        selection = self.select([stock(), stock('MSFT'), stock('AMD', price=100)])
        result = attach_spread_candidates(selection, {'MSFT': {'rows': [], 'status': {'Chains loaded': 1}}})
        outcomes = {r['Ticker']: r['Stock outcome'] for r in result['stocks']}
        self.assertEqual(outcomes, {'AAPL': 'DATA UNAVAILABLE — OPTIONS', 'MSFT': 'NO SUITABLE TRADE', 'AMD': 'NO SETUP'})

    def test_defense_rejects_malformed_spread_and_orders_stock_before_option_yield(self):
        selection = self.select([stock(), stock('MSFT', price=70, stop=65)])
        aapl, msft = candidate()[0], candidate(ticker='MSFT')[0]
        aapl['Return on risk %'] = 10000
        reports = {'AAPL': {'rows': [aapl], 'status': {'Chains loaded': 1}},
                   'MSFT': {'rows': [msft, dict(msft, Contracts=2)], 'status': {'Chains loaded': 1}}}
        result = attach_spread_candidates(selection, reports)
        self.assertEqual([r['Ticker'] for r in result['rows']], ['MSFT', 'AAPL'])


if __name__ == '__main__':
    unittest.main()
