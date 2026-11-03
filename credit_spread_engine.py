"""Read-only, one-contract vertical credit spread research. No order endpoints."""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
import re

ACCOUNT = 5000
RISK_LIMIT = 50
DEFAULT_FEES = 3.00  # four contract-sides at $0.65 plus $0.40 allowance


def number(value, default=0.0):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default


def parse_symbols(text):
    symbols = list(dict.fromkeys(re.split(r"[\s,;]+", text.strip().upper())))
    return [s for s in symbols if re.fullmatch(r"[A-Z]{1,6}(?:-[A-Z])?", s)]


def economics(short_strike, long_strike, short_bid, long_ask, fees=DEFAULT_FEES):
    """Use natural credit, rounded down to cents, and full round-trip costs."""
    vals = [short_strike, long_strike, short_bid, long_ask, fees]
    if any(not math.isfinite(float(v)) for v in vals) or min(vals) < 0:
        raise ValueError("Invalid economics")
    width = abs(Decimal(str(short_strike)) - Decimal(str(long_strike)))
    credit = (Decimal(str(short_bid)) - Decimal(str(long_ask))).quantize(Decimal('.01'), rounding=ROUND_FLOOR)
    if width <= 0 or credit <= 0 or credit >= width or fees < DEFAULT_FEES:
        raise ValueError("Not a positive-credit defined-risk spread")
    loss = ((width - credit) * 100 + Decimal(str(fees))).quantize(Decimal('.01'), rounding=ROUND_CEILING)
    profit = credit * 100 - Decimal(str(fees))
    return float(credit), float(loss), float(profit)


def valid_leg(row, symbol, expiration, kind):
    # Exact OCC root excludes adjusted/nonstandard contract roots such as AAPL1.
    match = re.fullmatch(r"([A-Z]+)(\d{6})([CP])(\d{8})", str(row.get('contractSymbol', '')))
    if not match or match[1] != symbol.replace('-', '') or match[2] != date.fromisoformat(expiration).strftime('%y%m%d') or match[3] != kind:
        return False
    if abs(int(match[4]) / 1000 - number(row.get('strike'))) > 0.0001:
        return False
    bid, ask = number(row.get('bid')), number(row.get('ask'))
    return (row.get('contractSize') == 'REGULAR' and bid > 0 and ask >= bid
            and number(row.get('openInterest')) >= 100
            and number(row.get('volume')) >= 10 and (ask - bid) / ask <= .25)


def build_candidates(symbol, spot, expiration, puts, calls, direction='Both', fees=DEFAULT_FEES, today=None):
    today = today or datetime.now(timezone.utc).date()
    dte = (date.fromisoformat(expiration) - today).days
    if not 7 <= dte <= 45 or number(spot) <= 0:
        return [], {'risk_rejected': 0, 'pairs_checked': 0}
    rows, counts = [], {'risk_rejected': 0, 'pairs_checked': 0}
    for strategy, chain, kind in [('Bull put', puts, 'P'), ('Bear call', calls, 'C')]:
        if direction not in ('Both', strategy):
            continue
        legs = [r for r in chain if valid_leg(r, symbol, expiration, kind)]
        legs = [r for r in legs if (number(r['strike']) < spot if kind == 'P' else number(r['strike']) > spot)]
        legs.sort(key=lambda r: number(r['strike']))
        for short in legs:
            for long in legs:
                sk, lk = number(short['strike']), number(long['strike'])
                if not (0 < (sk-lk if kind == 'P' else lk-sk) <= 5):
                    continue
                counts['pairs_checked'] += 1
                try:
                    credit, loss, profit = economics(sk, lk, short['bid'], long['ask'], fees)
                except ValueError:
                    continue
                if loss > RISK_LIMIT or profit <= 0:
                    counts['risk_rejected'] += 1
                    continue
                oi = min(int(number(short['openInterest'])), int(number(long['openInterest'])))
                vol = min(int(number(short['volume'])), int(number(long['volume'])))
                rows.append({'Ticker': symbol, 'Direction': strategy, 'Expiration': expiration, 'DTE': dte,
                    'Short strike': sk, 'Long strike': lk, 'Contracts': 1,
                    'Short contract': short['contractSymbol'], 'Long contract': long['contractSymbol'],
                    'Credit / share $': credit, 'Gross credit $': round(credit*100, 2),
                    'Round-trip fees $': fees, 'Max loss incl fees $': loss,
                    'Max net profit $': round(profit, 2), 'Return on risk %': round(profit/loss*100, 2),
                    'Min leg OI': oi, 'Min leg volume': vol,
                    'Liquidity': 'Pass: OI ≥100, volume ≥10, each bid/ask ≤25% of ask',
                    'Short bid / ask': f"{short['bid']} / {short['ask']}",
                    'Long bid / ask': f"{long['bid']} / {long['ask']}",
                    'Short last trade': str(short.get('lastTradeDate', 'Unknown')),
                    'Long last trade': str(long.get('lastTradeDate', 'Unknown')),
                    'Quote freshness': 'Unverified / delayed; last trade is not quote time',
                    'Approval status': 'REVIEW REQUIRED — not approved for trading'})
    rows.sort(key=lambda r: (-r['Return on risk %'], -r['Min leg OI'], -r['Min leg volume']))
    return rows, counts

