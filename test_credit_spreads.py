import unittest
from datetime import date
from credit_spread_engine import economics, build_candidates, parse_symbols

EXP = '2026-11-13'
TODAY = date(2026, 11, 3)

def leg(strike, kind='P', bid=.80, ask=.85, **changes):
    row = dict(contractSymbol=f'AAPL261113{kind}{int(strike*1000):08d}', strike=strike,
               bid=bid, ask=ask, contractSize='REGULAR', openInterest=500, volume=50)
    row.update(changes)
    return row

class Safeguards(unittest.TestCase):
    def test_fee_inclusive_boundary_and_conservative_rounding(self):
        self.assertEqual(economics(100, 99, .78, .25), (.53, 50.0, 50.0))
        self.assertEqual(economics(100, 99, .779, .25)[1], 51.0)
        self.assertEqual(economics(100, 99, .78, .25, 3.1)[1], 50.1)

    def test_both_directions_are_protected_and_same_expiration(self):
        rows, _ = build_candidates('AAPL', 101, EXP,
            [leg(100), leg(99, bid=.20, ask=.25)],
            [leg(102, 'C'), leg(103, 'C', bid=.20, ask=.25)], today=TODAY)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertLessEqual(row['Max loss incl fees $'], 50)
            self.assertIn('REVIEW REQUIRED', row['Approval status'])
            if row['Direction'] == 'Bull put':
                self.assertLess(row['Long strike'], row['Short strike'])
            else:
                self.assertGreater(row['Long strike'], row['Short strike'])

    def test_naked_and_over_budget_rejected(self):
        self.assertEqual(build_candidates('AAPL', 101, EXP, [leg(100)], [], today=TODAY)[0], [])
        rows, counts = build_candidates('AAPL', 101, EXP,
            [leg(100, bid=.50, ask=.55), leg(99, bid=.20, ask=.25)], [], today=TODAY)
        self.assertEqual(rows, [])
        self.assertEqual(counts['risk_rejected'], 1)

    def test_bad_quotes_liquidity_adjusted_or_mismatched_legs_rejected(self):
        for change in [dict(bid=0), dict(ask=.01), dict(ask=float('nan')),
                       dict(openInterest=99), dict(volume=9), dict(contractSize='MINI'),
                       dict(contractSymbol='AAPL1261113P00099000'),
                       dict(contractSymbol='AAPL261120P00099000'),
                       dict(contractSymbol='AAPL261113C00099000')]:
            with self.subTest(change=change):
                rows, _ = build_candidates('AAPL', 101, EXP,
                    [leg(100), leg(99, **dict({'bid': .20, 'ask': .25}, **change))], [], today=TODAY)
                self.assertEqual(rows, [])

    def test_expiration_direction_and_bad_credit(self):
        puts = [leg(100), leg(99, bid=.20, ask=.25)]
        self.assertEqual(build_candidates('AAPL',101,EXP,puts,[],direction='Bear call',today=TODAY)[0], [])
        self.assertEqual(build_candidates('AAPL',101,EXP,puts,[],today=date(2026,11,12))[0], [])
        for bid, ask in [(0,.2),(.2,.2),(1.5,.2)]:
            with self.assertRaises(ValueError):
                economics(100,99,bid,ask)

    def test_symbol_parsing(self):
        self.assertEqual(parse_symbols('aapl, MSFT\nAAPL BRK-B;TSLA'), ['AAPL','MSFT','BRK-B','TSLA'])

if __name__ == '__main__':
    unittest.main()

