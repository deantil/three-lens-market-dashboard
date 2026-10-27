"""Project T ORB research rules and paper ticket arithmetic. No broker actions."""
from datetime import datetime, timedelta
import math
import re
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo('America/New_York')
DEFAULT_SYMBOLS = 'AAPL MSFT NVDA AMZN META GOOGL AMD TSLA AVGO PLTR NFLX MU ORCL JPM BAC XOM CVX UNH WMT COST HD UBER COIN HOOD SOFI INTC QCOM CRM DIS BA'


def parse_symbols(text):
    symbols = list(dict.fromkeys(re.split(r'[\s,]+', text.upper().strip())))
    if not symbols or any(not re.fullmatch(r'[A-Z][A-Z0-9-]{0,9}', s) for s in symbols):
        raise ValueError('Enter US stock symbols separated by spaces or commas.')
    if len(symbols) > 40:
        raise ValueError('Use at most 40 symbols per scan.')
    return symbols


def session_times(now):
    """NYSE calendar supplies holidays, DST and early closes; fail closed on error."""
    import pandas_market_calendars as mcal
    now = pd.Timestamp(now).tz_convert(ET)
    schedule = mcal.get_calendar('NYSE').schedule(start_date=now.date(), end_date=now.date())
    if schedule.empty:
        return None
    row = schedule.iloc[0]
    return row.market_open.tz_convert(ET), row.market_close.tz_convert(ET)


def normalized(frame, intraday=False):
    frame = frame.copy()
    if frame.empty:
        raise ValueError('No market data returned')
    if not {'Open', 'High', 'Low', 'Close', 'Volume'}.issubset(frame.columns):
        raise ValueError('Missing OHLCV columns')
    frame.index = pd.DatetimeIndex(frame.index)
    if intraday:
        if frame.index.tz is None:
            raise ValueError('Intraday timestamps have no timezone')
        frame.index = frame.index.tz_convert(ET)
    frame = frame.sort_index()
    if frame.index.has_duplicates:
        raise ValueError('Duplicate timestamps')
    return frame


def analyze(symbol, intraday, daily, now, times):
    """Five-minute completed bars only, prior-day daily metrics, no invented quote."""
    result = {'Symbol': symbol, 'Status': 'No trade', 'Reason': '', 'Side': '', 'RVOL': None}
    if times is None:
        return dict(result, Status='Market closed', Reason='NYSE holiday or weekend. Wait for the next session.')
    start, end = times
    now = pd.Timestamp(now).tz_convert(ET)
    ready = start + timedelta(minutes=5)
    cutoff = min(start + timedelta(minutes=90), end - timedelta(minutes=5))
    result.update(session_date=str(start.date()), flatten_at=end - timedelta(minutes=5), cutoff=cutoff)
    if now < ready:
        return dict(result, Status='Wait', Reason='Wait until 09:35 ET for the completed opening range.')
    try:
        bars = normalized(intraday, True)
        days = normalized(daily)
        # Five-minute timestamps describe BAR START, not the completed-bar time.
        bars = bars[bars.index + timedelta(minutes=5) <= now]
        historical = days[[d.date() < start.date() for d in days.index]].tail(15)
        if len(historical) < 15:
            raise ValueError('Need 15 completed daily bars for ATR and average volume')
        required = historical[['High', 'Low', 'Close', 'Volume']]
        if not required.map(lambda x: math.isfinite(float(x))).all().all():
            raise ValueError('Invalid historical OHLCV')
        prev = historical.Close.shift(1)
        tr = pd.concat([historical.High - historical.Low,
                        (historical.High - prev).abs(), (historical.Low - prev).abs()], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())  # explicitly SMA true range, frozen before session
        adv = float(historical.Volume.tail(14).mean())
        opens = bars[(bars.index.hour == 9) & (bars.index.minute == 30)]
        prior = opens[[d.date() < start.date() for d in opens.index]].tail(14)
        today = bars[(bars.index >= start) & (bars.index < end)]
        opening = today[today.index == start]
        if len(opening) != 1:
            raise ValueError('Today\'s completed 09:30 opening bar is missing')
        if (len(prior) != 14 or (prior.Volume <= 0).any()
                or not prior.Volume.map(lambda x: math.isfinite(float(x))).all()
                or [d.date() for d in prior.index] != [d.date() for d in historical.index[-14:]]):
            raise ValueError('Need 14 valid prior opening-volume bars')
        row = opening.iloc[0]
        if any(not math.isfinite(float(row[k])) for k in ['Open', 'High', 'Low', 'Close', 'Volume']):
            raise ValueError('Invalid opening OHLCV')
        if not (row.Low > 0 and row.Low <= min(row.Open, row.Close) <= max(row.Open, row.Close) <= row.High and row.Volume > 0):
            raise ValueError('Invalid opening prices or volume')
        rvol = float(row.Volume / prior.Volume.mean())
        last = today.iloc[-1]
        asof = today.index[-1] + timedelta(minutes=5)
        price = float(last.Close)
        if not math.isfinite(price) or price <= 0:
            raise ValueError('Invalid latest completed-bar price')
        side = 'Long' if row.Close > row.Open else 'Short' if row.Close < row.Open else ''
        stop_distance = math.ceil(atr * .10 * 100 - 1e-9) / 100
        trigger = round(float(row.High + .01 if side == 'Long' else row.Low - .01), 2)
        after = today[today.index >= ready]
        crossed = (after.High >= trigger) if side == 'Long' else (after.Low <= trigger)
        breakout_time = after.index[crossed][0] if crossed.any() else None
        result.update(Side=side, RVOL=round(rvol, 3), opening_price=float(row.Open),
                      or_high=float(row.High), or_low=float(row.Low), atr=atr, adv=adv,
                      trigger=trigger, stop_distance=stop_distance, price=price,
                      asof=asof, breakout_at=breakout_time)
        if row.Open <= 5 or adv <= 1_000_000 or atr <= .50 or rvol < 1 or not side:
            return dict(result, Reason='Fails price > $5, ADV14 > 1M, ATR14 > $0.50, RVOL >= 1, or opening direction.')
        if now >= end or now >= cutoff:
            return dict(result, Status='Entry window closed', Reason='No new trades after 11:00 ET. Manage existing paper positions only.')
        age = (now - asof).total_seconds()
        if age < 0 or age > 420:
            return dict(result, Status='Stale data', Reason='Latest completed bar is over 7 minutes old. Do not act on this alert.')
        if breakout_time is not None:
            beyond = (price - trigger) if side == 'Long' else (trigger - price)
            if beyond < 0:
                return dict(result, Status='Skip failed breakout', Reason='Price has returned inside the range after a breakout. No re-entry.')
            if breakout_time < cutoff and (now - (breakout_time + timedelta(minutes=5))).total_seconds() <= 420 and beyond <= .25 * stop_distance:
                return dict(result, Status='Review breakout', Reason='Recent completed-bar breakout. Verify a fresh paperMoney quote; do not chase.')
            return dict(result, Status='Skip missed breakout', Reason='First breakout is old or price is over 0.25 stop distances beyond entry. Do not chase.')
        return dict(result, Status='Watch for breakout', Reason='No breakout yet. Review the trigger in paperMoney; do not enter early.')
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        return dict(result, Status='Data unavailable', Reason=str(exc))


def rank_candidates(rows):
    # Ranking scope is the explicit watchlist, never advertised as market-wide.
    eligible = [r for r in rows if r['Status'] in {'Review breakout', 'Watch for breakout', 'Skip failed breakout', 'Skip missed breakout', 'Entry window closed', 'Stale data'}
                and r.get('RVOL') is not None and r['RVOL'] >= 1 and r.get('Side')]
    eligible.sort(key=lambda r: (-r['RVOL'], r['Symbol']))
    for rank, row in enumerate(eligible, 1):
        row['watchlist_rank'] = rank
        if rank > 20 and row['Status'] in {'Review breakout', 'Watch for breakout'}:
            row.update(Status='No trade', Reason='Outside top 20 RVOL in this watchlist.')
    return rows


def paper_ticket(row, bid, ask, equity=25000, day_pnl=0, open_positions=0,
                 open_risk=0, gross_exposure=0, traded=False, short_ok=False):
    """Conservative PAPER plan from a user-verified broker quote, never an order."""
    values = [bid, ask, equity, day_pnl, open_positions, open_risk, gross_exposure]
    if any(not math.isfinite(float(v)) for v in values) or equity <= 0 or min(open_risk, gross_exposure, open_positions) < 0:
        raise ValueError('Invalid account or quote values')
    if row['Status'] not in {'Review breakout', 'Watch for breakout'}:
        raise ValueError('Candidate is not currently eligible. Refresh the scan.')
    if traded:
        raise ValueError('One trade per symbol per session. No re-entry.')
    if row['Side'] == 'Short' and not short_ok:
        raise ValueError('Shorts disabled until paperMoney eligibility and borrow assumptions are checked.')
    if bid <= 0 or ask < bid:
        raise ValueError('Enter a positive, uncrossed broker bid and ask')
    distance = row['stop_distance']
    if distance <= 0:
        raise ValueError('Invalid stop distance')
    spread = ask - bid
    if spread > min(.05, .10 * distance) + 1e-9:
        raise ValueError('Spread exceeds the lower of $0.05 or 10% of the stop distance. Skip.')
    trigger = row['trigger']
    side = row['Side']
    entry = max(trigger, ask) if side == 'Long' else min(trigger, bid)
    if abs(entry - trigger) > .25 * distance + 1e-9:
        raise ValueError('Broker price is too far beyond the trigger. Skip; do not chase.')
    if day_pnl <= -equity * .01:
        raise ValueError('Daily loss limit reached: stop taking new trades today.')
    if open_positions >= 3:
        raise ValueError('Three-position limit reached.')
    # Extra adverse cost on each side, beyond crossing the quoted spread.
    cost_per_side = max(.01, entry * .0005)
    unit_risk = distance + spread + 2 * cost_per_side
    budget = min(equity * .0025, max(0, equity * .0075 - open_risk))
    notional_left = min(equity * .25, max(0, equity - gross_exposure))
    shares = math.floor(min(budget / unit_risk, notional_left / (entry + cost_per_side)))
    if shares < 1:
        raise ValueError('Available risk or buying-power budget cannot fund one share. Skip.')
    stop = round(entry - distance if side == 'Long' else entry + distance, 2)
    if stop <= 0:
        raise ValueError('Invalid protective stop price')
    return {'Symbol': row['Symbol'], 'Side': side, 'Shares': shares,
            'Planned entry': round(entry, 2), 'Protective stop': stop,
            'Stop distance': distance, 'Cost allowance per side': round(cost_per_side, 4),
            'Planned risk including allowance': round(shares * unit_risk, 2),
            'Notional': round(shares * entry, 2),
            'Flatten ET': row['flatten_at'].strftime('%Y-%m-%d %H:%M'),
            'Order plan': 'DAY buy stop' if side == 'Long' and ask < trigger else
                          'DAY sell-short stop' if side == 'Short' and bid > trigger else
                          'Manually review a marketable limit at the verified quote',
            'Scope': 'Paper only. Quote and eligibility must be checked again before manual submission.'}
