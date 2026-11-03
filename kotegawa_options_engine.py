"""Pure Kotegawa-inspired stock selection and protected-spread matching.

Thresholds and the symmetric bearish stop are transparent research heuristics,
not a reconstruction of Kotegawa's complete system. No network or order access.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import math

from credit_spread_engine import DEFAULT_FEES, RISK_LIMIT


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def select_stock_signals(rows, generated_at_utc, today=None, buy_threshold=20.,
                         sell_threshold=10., max_rebound=10., symbols=None,
                         max_age_days=4):
    """Return eligible signals, every covered stock's outcome, and coverage.

    BUY uses supplied five-day-low stop. Bearish stop mirrors that distance
    above price; target is SMA25 for both directions. Both require >=2:1 stock
    reference reward/risk. This is distinct from options maximum loss.
    """
    today = _date(today) or datetime.now(timezone.utc).date()
    allowed = set(symbols) if symbols is not None else None
    generated = _date(generated_at_utc)
    generation_ok = generated is not None and 0 <= (today-generated).days <= max_age_days
    settings = [buy_threshold, sell_threshold, max_rebound, max_age_days]
    if any(_finite(v) is None for v in settings) or min(buy_threshold, sell_threshold) <= 0 or min(max_rebound, max_age_days) < 0:
        raise ValueError('Invalid stock scan settings')
    stocks, signals, seen = [], [], set()
    for raw in rows:
        row = dict(raw)
        ticker = str(row.get('Ticker', '')).strip().upper()
        if allowed is not None and ticker not in allowed:
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        row['Ticker'] = ticker
        row['Stock outcome'] = 'NO SETUP'
        stamp = _date(row.get('Price timestamp'))
        keys = ['Price $', 'SMA25 $', 'Avg daily $ volume', 'Day change %', 'Stop reference $']
        values = {key: _finite(row.get(key)) for key in keys}
        if not generation_ok or stamp is None or not 0 <= (today-stamp).days <= max_age_days:
            row.update({'Stock outcome': 'DATA UNAVAILABLE', 'Signal reason': 'Missing, future-dated or stale stock snapshot/price timestamp'})
        elif not ticker or any(v is None for v in values.values()) or values['Price $'] <= 0 or values['SMA25 $'] <= 0:
            row.update({'Stock outcome': 'DATA UNAVAILABLE', 'Signal reason': 'Missing or nonfinite stock inputs'})
        else:
            price, sma = values['Price $'], values['SMA25 $']
            deviation = float((Decimal(str(price))/Decimal(str(sma))-1)*100)
            row['Deviation %'] = round(deviation, 4)
            direction = 'Bull put' if deviation <= -buy_threshold else 'Bear call' if deviation >= sell_threshold else None
            reasons = []
            if price < 5: reasons.append('Price below $5')
            if values['Avg daily $ volume'] < 10_000_000: reasons.append('Average daily dollar volume below $10M')
            if abs(values['Day change %']) >= 20: reasons.append('Extreme daily move (20% or more)')
            if direction == 'Bull put' and values['Day change %'] > max_rebound: reasons.append('Bullish rebound exceeds limit')
            if direction:
                distance = price-values['Stop reference $']
                reward = abs(sma-price)
                rr = reward/distance if distance > 0 else None
                if values['Stop reference $'] <= 0 or rr is None or rr < 2:
                    reasons.append('Reference stock reward/risk below 2:1 or invalid stop')
                row.update({'Direction': direction,
                            'Signal direction': 'Bullish' if direction == 'Bull put' else 'Bearish',
                            'Stock target $': sma,
                            'Stock stop $': values['Stop reference $'] if direction == 'Bull put' else price+distance,
                            'Stock reference reward/risk': rr,
                            'Stop method': 'Five-day-low stop proxy' if direction == 'Bull put' else 'Mirrored five-day-low distance proxy (bearish heuristic)'})
            if reasons:
                row.update({'Stock outcome': 'EXCLUDED', 'Signal reason': '; '.join(reasons)})
            elif direction:
                row.update({'Stock outcome': 'SIGNAL — OPTIONS PENDING',
                            'Signal reason': 'SMA25 mean-reversion threshold, liquidity and reference reward/risk passed; event/entry confirmation unverified'})
                signals.append(row)
            else:
                row['Signal reason'] = 'Neither SMA25 deviation threshold met'
        stocks.append(row)
    if allowed is not None:
        for ticker in sorted(allowed-seen):
            stocks.append({'Ticker': ticker, 'Stock outcome': 'DATA UNAVAILABLE', 'Signal reason': 'No stock history in snapshot'})
    signals.sort(key=lambda r: (-abs(r['Deviation %']), -r['Avg daily $ volume'], r['Ticker']))
    for rank, row in enumerate(signals, 1):
        row['Signal rank'] = rank
    return {'signals': signals, 'stocks': stocks,
            'coverage': {'stocks_evaluated': len(stocks), 'eligible_signals': len(signals),
                         'stock_data_unavailable': sum(r['Stock outcome'] == 'DATA UNAVAILABLE' for r in stocks),
                         'stocks_excluded': sum(r['Stock outcome'] == 'EXCLUDED' for r in stocks)},
            'errors': [] if generation_ok else ['Stock snapshot generation timestamp is missing, stale or future-dated']}


def _protected_candidate(row, direction):
    keys = ['Short strike', 'Long strike', 'Credit / share $', 'Max loss incl fees $', 'Round-trip fees $', 'Max net profit $']
    vals = {k: _finite(row.get(k)) for k in keys}
    if any(v is None for v in vals.values()) or row.get('Contracts') != 1 or row.get('Direction') != direction:
        return False
    short, long = vals['Short strike'], vals['Long strike']
    width = short-long if direction == 'Bull put' else long-short
    credit, fees = vals['Credit / share $'], vals['Round-trip fees $']
    if not 0 < credit < width or fees < DEFAULT_FEES or vals['Max net profit $'] <= 0:
        return False
    # Recheck reported economics without accepting binary-float boundary drift.
    expected = (Decimal(str(width))-Decimal(str(credit)))*100+Decimal(str(fees))
    return expected <= RISK_LIMIT and expected > 0 and abs(float(expected)-vals['Max loss incl fees $']) < .011


def attach_spread_candidates(selection, option_reports):
    """Join outputs of existing scan_symbol/build_candidates to stock signals.

    option_reports maps ticker to {'rows': [...], 'status': {...}}. Missing
    reports or zero loaded chains are DATA UNAVAILABLE, never no-trade signals.
    """
    stocks = [dict(row) for row in selection['stocks']]
    by_ticker = {row['Ticker']: row for row in stocks}
    rows = []
    for signal in selection['signals']:
        ticker = signal['Ticker']
        report = option_reports.get(ticker, {})
        status = report.get('status', {})
        stock = by_ticker[ticker]
        stock['Option chains loaded'] = status.get('Chains loaded', 0)
        stock['Option data errors'] = status.get('Data errors', '')
        if not status.get('Chains loaded', 0):
            stock['Stock outcome'] = 'DATA UNAVAILABLE — OPTIONS'
            stock['Spread candidates'] = 0
            continue
        found = []
        for candidate in report.get('rows', []):
            if candidate.get('Ticker') != ticker or not _protected_candidate(candidate, signal['Direction']):
                continue
            joined = dict(candidate)
            for key in ['Signal rank', 'Signal direction', 'Deviation %', 'Stock target $', 'Stock stop $', 'Stock reference reward/risk', 'Signal reason', 'Price timestamp', 'Stop method']:
                joined[key] = signal.get(key)
            joined['Approval status'] = 'REVIEW REQUIRED — not approved for trading'
            found.append(joined)
        stock['Spread candidates'] = len(found)
        stock['Stock outcome'] = 'REVIEW REQUIRED — SPREAD FOUND' if found else 'NO SUITABLE TRADE'
        rows.extend(found)
    rows.sort(key=lambda r: (r['Signal rank'], -(_finite(r.get('Min leg OI')) or 0),
                             -(_finite(r.get('Min leg volume')) or 0), -(_finite(r.get('Return on risk %')) or 0)))
    for rank, row in enumerate(rows, 1):
        row['Rank'] = rank
    coverage = dict(selection['coverage'], spread_candidates=len(rows),
                    option_data_unavailable=sum(r['Stock outcome'] == 'DATA UNAVAILABLE — OPTIONS' for r in stocks),
                    no_suitable_trade=sum(r['Stock outcome'] == 'NO SUITABLE TRADE' for r in stocks))
    return {'rows': rows, 'stocks': stocks, 'coverage': coverage, 'errors': list(selection.get('errors', []))}
