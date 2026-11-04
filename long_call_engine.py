"""Purchased-call research with explicit Black-Scholes scenario estimates.

No account, premium, delta, strike, or planned-risk limits. No orders. Models
assume European exercise, no dividends and constant IV except stated shocks;
American equity options may differ. Ask-based economics include round-trip fees.
"""
from datetime import date, datetime, timezone
import math
import re


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _normal(value):
    return .5*(1+math.erf(value/math.sqrt(2)))


def black_scholes_call(spot, strike, years, iv, rate=.04):
    """Price/share, delta, theta/share/calendar-day, vega/share per IV point."""
    if any(_number(v) is None for v in (spot, strike, years, iv, rate)) or min(spot, strike, iv) <= 0 or years < 0:
        raise ValueError('Model requires finite positive spot, strike and IV')
    if years == 0:
        return {'price': max(spot-strike, 0.), 'delta': None, 'theta': None, 'vega': None}
    root = math.sqrt(years)
    d1 = (math.log(spot/strike)+(rate+.5*iv*iv)*years)/(iv*root)
    d2 = d1-iv*root
    density = math.exp(-.5*d1*d1)/math.sqrt(2*math.pi)
    discount = math.exp(-rate*years)
    return {'price': max(0., spot*_normal(d1)-strike*discount*_normal(d2)),
            'delta': _normal(d1),
            'theta': (-spot*density*iv/(2*root)-rate*strike*discount*_normal(d2))/365,
            'vega': spot*density*root/100}


def analyze_calls(symbol, spot, target, expiration, calls, today=None, rate=.04, fees=1.3):
    """All usable regular calls expiring 60-90 days; poor liquidity is flagged.

    Scenarios value the option halfway to expiry at target, unchanged stock,
    and -10% stock; each is repeated with IV reduced by 25% (relative change).
    Scenario P/L is modeled exit value less ask premium and round-trip fees.
    Greeks are one-share estimates; contract theta/vega multiply these by 100.
    """
    today = today or datetime.now(timezone.utc).date()
    if isinstance(today, datetime): today = today.date()
    try:
        exp = date.fromisoformat(expiration)
    except (ValueError, TypeError):
        return []
    dte = (exp-today).days
    if not 60 <= dte <= 90:
        return []
    spot, target, rate, fees = (_number(v) for v in (spot, target, rate, fees))
    if any(v is None for v in (spot, target, rate, fees)) or min(spot, target) <= 0 or fees < 0:
        raise ValueError('Invalid call research inputs')
    horizon = dte//2
    remaining = (dte-horizon)/365
    rows = []
    for call in calls:
        match = re.fullmatch(r'([A-Z]+)(\d{6})([CP])(\d{8})', str(call.get('contractSymbol', '')))
        strike, ask, bid = (_number(call.get(k)) for k in ('strike', 'ask', 'bid'))
        if (not match or match[1] != symbol.replace('-', '').upper() or match[2] != exp.strftime('%y%m%d')
            or match[3] != 'C' or strike is None or strike <= 0 or abs(int(match[4])/1000-strike) > .0001
            or call.get('contractSize') != 'REGULAR' or ask is None or ask <= 0):
            continue
        premium = ask*100
        cost = premium+fees
        oi, volume = _number(call.get('openInterest')), _number(call.get('volume'))
        problems = []
        if bid is None or bid <= 0: problems.append('Missing/zero bid')
        elif bid > ask: problems.append('Crossed bid/ask')
        elif (ask-bid)/ask > .25: problems.append('Bid/ask wider than 25% of ask')
        if oi is None or oi < 100: problems.append('OI below 100 or unavailable')
        if volume is None or volume < 10: problems.append('Volume below 10 or unavailable')
        row = {'Ticker': symbol, 'Direction': 'Long call', 'Expiration': expiration, 'DTE': dte,
               'Strike': strike, 'Contracts': 1, 'Call contract': call['contractSymbol'],
               'Ask entry / share $': ask, 'Bid / share $': bid,
               'Premium $': round(premium, 2), 'Round-trip fees $': fees,
               'Max loss incl fees $': round(cost, 2), 'Expiration breakeven $': strike+cost/100,
               'Stock reference $': spot, 'Stock target $': target,
               'Open interest': oi, 'Volume': volume,
               'Liquidity': 'REVIEW — '+ '; '.join(problems) if problems else 'Pass: OI ≥100, volume ≥10, bid/ask ≤25% of ask',
               'Liquidity concerns': bool(problems), 'Scenario horizon days': horizon,
               'Estimated model value / share $': None,
               'Estimated delta': None, 'Estimated theta $/day': None, 'Estimated vega $/IV point': None,
               'Greek source': 'Black-Scholes estimates per share; not broker Greeks; no dividends',
               'Model assumptions': 'European call; no dividends; rate '+str(rate)+'; IV unchanged or reduced 25% relative; no execution guarantee',
               'Expiration target profit $': round(max(target-strike, 0)*100-cost, 2),
               'Approval status': 'REVIEW REQUIRED — research only',
               'Quote freshness': 'Unverified; retrieval/last-trade time is not quote time',
               'Last trade': str(call.get('lastTradeDate', 'Unknown'))}
        scenarios = [('Target', target), ('Flat', spot), ('Down 10%', spot*.9)]
        for name, _ in scenarios:
            row[name+' scenario profit $'] = None
            row[name+' scenario IV -25% profit $'] = None
            row[name+' scenario value / share $'] = None
            row[name+' scenario IV -25% value / share $'] = None
        iv = _number(call.get('impliedVolatility'))
        row['IV %'] = iv*100 if iv is not None else None
        row['Model status'] = 'UNAVAILABLE — missing/nonpositive/nonfinite IV'
        if iv is not None and iv > 0:
            try:
                model = black_scholes_call(spot, strike, dte/365, iv, rate)
                row.update({'Estimated model value / share $': model['price'],
                            'Estimated delta': model['delta'], 'Estimated theta $/day': model['theta'],
                            'Estimated vega $/IV point': model['vega'], 'Model status': 'ESTIMATE — assumptions require review'})
                for name, scenario_spot in scenarios:
                    for multiplier, suffix in [(1., ''), (.75, ' IV -25%')]:
                        value = black_scholes_call(scenario_spot, strike, remaining, iv*multiplier, rate)['price']
                        row[name+' scenario'+suffix+' value / share $'] = value
                        row[name+' scenario'+suffix+' profit $'] = round(value*100-cost, 2)
            except (ValueError, OverflowError, ZeroDivisionError):
                row['Model status'] = 'UNAVAILABLE — model inputs cannot be evaluated'
        rows.append(row)
    rows.sort(key=lambda r: (r['Target scenario profit $'] is None,
                             -(r['Target scenario profit $'] or 0), r['Liquidity concerns'],
                             -(r['Open interest'] or 0), -(r['Volume'] or 0), r['Strike']))
    for rank, row in enumerate(rows, 1): row['Rank'] = rank
    return rows
