import unittest
from datetime import timedelta
from unittest.mock import patch

try:
    import pandas as pd
    import pandas_market_calendars
    from orb_strategy import ET, analyze, paper_ticket, parse_symbols, rank_candidates, session_times
    ORB_DEPS = True
except ModuleNotFoundError:
    ORB_DEPS = False


@unittest.skipUnless(ORB_DEPS, 'ORB tests require pandas and pandas_market_calendars; installed in the dedicated ORB check')
class ORBTests(unittest.TestCase):
    def setUp(self):
        self.now = pd.Timestamp('2026-10-27 09:41', tz=ET)
        self.start = self.now.normalize() + pd.Timedelta(hours=9, minutes=30)
        self.times = (self.start, self.start.normalize() + pd.Timedelta(hours=16))
        daily_index = pd.bdate_range(end='2026-10-26', periods=20)
        self.daily = pd.DataFrame({'Open':100.,'High':102.,'Low':98.,'Close':100.,'Volume':2000000.},index=daily_index)
        opens = [pd.Timestamp(str(d.date())+' 09:30',tz=ET) for d in daily_index[-14:]]
        self.bars = pd.DataFrame({'Open':100.,'High':101.,'Low':99.,'Close':100.5,'Volume':1000.},index=opens)
        self.bars.loc[self.start] = [100,101,99,100.5,2000]
        self.bars.loc[self.start+timedelta(minutes=5)] = [100.5,101.05,100.4,101.02,500]

    def row(self):
        return analyze('TEST',self.bars,self.daily,self.now,self.times)

    def test_completed_opening_relative_volume_and_direction(self):
        row = self.row()
        self.assertEqual(row['RVOL'],2)
        self.assertEqual(row['Side'],'Long')
        self.assertEqual(row['Status'],'Review breakout')
        self.assertAlmostEqual(row['trigger'],101.01)
        self.assertAlmostEqual(row['stop_distance'],.4)

    def test_current_daily_bar_cannot_leak_into_atr(self):
        expected = self.row()['atr']
        self.daily.loc[pd.Timestamp('2026-10-27')] = [100,999,1,900,999999999]
        self.assertEqual(self.row()['atr'],expected)

    def test_partial_bar_does_not_create_breakout(self):
        row = analyze('TEST',self.bars,self.daily,pd.Timestamp('2026-10-27 09:38',tz=ET),self.times)
        self.assertEqual(row['Status'],'Watch for breakout')
        self.assertEqual(row['price'],100.5)

    def test_wait_before_opening_complete(self):
        row=analyze('TEST',self.bars,self.daily,pd.Timestamp('2026-10-27 09:34',tz=ET),self.times)
        self.assertEqual(row['Status'],'Wait')

    def test_yesterday_never_substituted_for_today(self):
        bars=self.bars.iloc[:-2]
        self.assertEqual(analyze('TEST',bars,self.daily,self.now,self.times)['Status'],'Data unavailable')

    def test_missing_history_blocks_signal(self):
        self.bars=self.bars.iloc[1:]
        self.assertEqual(self.row()['Status'],'Data unavailable')

    def test_missing_recent_opening_cannot_use_older_substitute(self):
        older=pd.Timestamp('2026-10-01 09:30',tz=ET)
        self.bars.loc[older]=[100,101,99,100.5,1000]
        self.bars=self.bars.drop(self.bars.index[-4])
        self.assertEqual(self.row()['Status'],'Data unavailable')

    def test_future_timestamp_does_not_supply_last_price(self):
        self.bars.loc[self.start+timedelta(hours=1)] = [101,200,100,199,9999]
        self.assertEqual(self.row()['price'],101.02)

    def test_missing_timezone_blocks(self):
        self.bars.index=self.bars.index.tz_localize(None)
        self.assertEqual(self.row()['Status'],'Data unavailable')

    def test_daily_cutoff_and_staleness(self):
        row=analyze('TEST',self.bars,self.daily,pd.Timestamp('2026-10-27 11:00',tz=ET),self.times)
        self.assertEqual(row['Status'],'Entry window closed')
        row=analyze('TEST',self.bars,self.daily,pd.Timestamp('2026-10-27 09:48',tz=ET),self.times)
        self.assertEqual(row['Status'],'Stale data')

    def test_no_chasing_or_reentry_after_failed_breakout(self):
        self.bars.iloc[-1,self.bars.columns.get_loc('Close')]=102
        self.assertEqual(self.row()['Status'],'Skip missed breakout')
        self.bars.iloc[-1,self.bars.columns.get_loc('Close')]=100.9
        self.assertEqual(self.row()['Status'],'Skip failed breakout')

    def test_short_direction(self):
        self.bars.loc[self.start,'Close']=99.5
        self.bars.iloc[-1]=[99.5,99.6,98.95,98.98,500]
        row=self.row()
        self.assertEqual(row['Side'],'Short')
        self.assertEqual(row['Status'],'Review breakout')
        with self.assertRaisesRegex(ValueError,'Shorts disabled'):
            paper_ticket(row,98.97,98.98)
        ticket=paper_ticket(row,98.97,98.98,short_ok=True)
        self.assertGreater(ticket['Protective stop'],ticket['Planned entry'])

    def test_ticket_reserves_costs_and_caps_notional(self):
        ticket=paper_ticket(self.row(),101.01,101.02)
        self.assertLessEqual(ticket['Planned risk including allowance'],62.50)
        self.assertLessEqual(ticket['Notional'],6250)
        self.assertGreater(ticket['Shares'],0)
        self.assertLess(ticket['Protective stop'],ticket['Planned entry'])

    def test_portfolio_and_quote_limits(self):
        for kwargs in [{'day_pnl':-250},{'open_positions':3},{'open_risk':187.5},
                       {'gross_exposure':25000},{'traded':True},{'equity':float('nan')}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                paper_ticket(self.row(),101.01,101.02,**kwargs)
        with self.assertRaisesRegex(ValueError,'Spread'):
            paper_ticket(self.row(),100.9,101.02)
        with self.assertRaisesRegex(ValueError,'too far'):
            paper_ticket(self.row(),102,102.01)

    def test_top20_scope(self):
        rows=[dict(self.row(),Symbol=f'T{i}',RVOL=30-i) for i in range(25)]
        rank_candidates(rows)
        self.assertEqual(sum(r['Status']=='Review breakout' for r in rows),20)
        self.assertEqual(rows[-1]['Status'],'No trade')

    def test_calendar_holiday_and_early_close(self):
        self.assertIsNone(session_times(pd.Timestamp('2026-12-25 10:00',tz=ET)))
        start,end=session_times(pd.Timestamp('2026-11-27 10:00',tz=ET))
        self.assertEqual(end.hour,13)
        self.assertEqual((end-timedelta(minutes=5)).strftime('%H:%M'),'12:55')
        self.assertEqual(start.hour,9)

    def test_watchlist_validation(self):
        self.assertEqual(parse_symbols('aapl, MSFT AAPL'),['AAPL','MSFT'])
        for invalid in ['', 'AAPL https://example.com', ' '.join('T'+str(i) for i in range(41))]:
            with self.assertRaises(ValueError):
                parse_symbols(invalid)


if __name__=='__main__':
    unittest.main()
