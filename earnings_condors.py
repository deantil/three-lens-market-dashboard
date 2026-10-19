"""Research-only earnings iron condor scanner.

Uses Yahoo Finance option-chain snapshots. It never creates broker orders. The
probability figure is a simple constant-volatility lognormal estimate, not an
empirically calibrated probability and not suitable as a guarantee.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
import math

import pandas as pd
import yfinance as yf


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def upcoming_earnings(symbol: str, today: date | None = None, days_ahead: int = 14) -> list[date]:
    """Return forward earnings dates only; callers display that Yahoo dates can change."""
    today = today or date.today()
    try:
        frame = yf.Ticker(symbol).get_earnings_dates(limit=12)
        if frame is None or frame.empty:
            return []
        dates = []
        for stamp in frame.index:
            event = pd.Timestamp(stamp).date()
            if today < event <= today + timedelta(days=days_ahead):
                dates.append(event)
        return sorted(set(dates))
    except Exception:
        return []


@lru_cache(maxsize=128)
def historical_post_earnings_moves(symbol: str) -> tuple[dict, ...]:
    """Measure each prior event's max high/low excursion over its next 3 sessions.

    Excursions are measured from the last close before the release. Including the
    earnings session and following sessions is intentionally conservative when
    Yahoo's reported event time is incomplete.
    """
    ticker = yf.Ticker(symbol)
    try:
        events = ticker.get_earnings_dates(limit=24)
        prices = ticker.history(period="10y", interval="1d", auto_adjust=True, actions=False)
    except Exception:
        return ()
    if events is None or events.empty or prices is None or prices.empty:
        return ()
    prices = prices.dropna(subset=["High", "Low", "Close"]).copy()
    sessions = [pd.Timestamp(stamp).date() for stamp in prices.index]
    moves = []
    for stamp in events.index:
        event_stamp = pd.Timestamp(stamp)
        local_event = event_stamp
        if event_stamp.tzinfo is not None:
            try:
                local_event = event_stamp.tz_convert("America/New_York")
            except (TypeError, ValueError):
                pass
        event_day = local_event.date()
        if event_day >= date.today():
            continue
        previous = [i for i, session in enumerate(sessions) if session < event_day]
        first_after = [i for i, session in enumerate(sessions) if session >= event_day]
        if not previous or not first_after:
            continue
        prev_i, start_i = previous[-1], first_after[0]
        # If Yahoo marks an after-close release, exclude that already-finished
        # session so the sample begins with the next session's price action.
        if local_event.tzinfo is not None:
            try:
                if local_event.date() == sessions[start_i] and local_event.hour >= 16:
                    start_i += 1
            except (TypeError, ValueError):
                pass
        if start_i >= len(sessions):
            continue
        window = prices.iloc[start_i:start_i + 3]
        if len(window) < 2:
            continue
        base = _number(prices["Close"].iloc[prev_i])
        if base <= 0:
            continue
        up = max(0.0, _number(window["High"].max()) / base - 1.0)
        down = max(0.0, 1.0 - _number(window["Low"].min()) / base)
        moves.append({"event_date": event_day.isoformat(), "up_pct": up * 100,
                      "down_pct": down * 100})
    return tuple(moves[-20:])


def _nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _clean_chain(frame: pd.DataFrame, option_type: str, spot: float,
                 min_open_interest: int, max_spread_pct: float) -> list[dict]:
    result = []
    if frame is None or frame.empty:
        return result
    for _, row in frame.iterrows():
        strike = _number(row.get("strike"))
        bid, ask = _number(row.get("bid")), _number(row.get("ask"))
        oi = int(_number(row.get("openInterest")))
        iv = _number(row.get("impliedVolatility"))
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 100 if mid > 0 else math.inf
        if (strike <= 0 or bid < 0 or ask <= 0 or ask < bid or mid <= 0 or iv <= 0
                or oi < min_open_interest or spread > max_spread_pct):
            continue
        otm = strike < spot if option_type == "put" else strike > spot
        distance = abs(strike / spot - 1) if spot else 1
        if otm and 0.01 <= distance <= 0.20:
            result.append({"strike": strike, "bid": bid, "ask": ask, "mid": mid,
                           "iv": iv, "oi": oi, "spread_pct": spread})
    return result


def _pop_between(spot: float, lower: float, upper: float, iv: float, years: float) -> float:
    """Risk-neutral, zero-rate lognormal chance of expiring between breakevens."""
    if spot <= 0 or lower <= 0 or upper <= lower or iv <= 0 or years <= 0:
        return 0.0
    sigma_t = iv * math.sqrt(years)
    def above(level):
        d2 = (math.log(spot / level) - 0.5 * iv * iv * years) / sigma_t
        return _cdf(d2)
    return max(0.0, min(1.0, above(lower) - above(upper)))


def scan_symbol(symbol: str, spot: float, capital: float = 100.0,
                today: date | None = None, min_open_interest: int = 50,
                max_spread_pct: float = 35.0) -> list[dict]:
    """Build defined-risk condors whose expiry follows a near-term earnings event."""
    today = today or date.today()
    if spot <= 0:
        return []
    ticker = yf.Ticker(symbol)
    earnings = upcoming_earnings(symbol, today=today)
    if not earnings:
        return []
    historical_moves = list(historical_post_earnings_moves(symbol))
    if len(historical_moves) < 4:
        return []
    historical_up80 = _nearest_rank([row["up_pct"] for row in historical_moves], 0.80)
    historical_down80 = _nearest_rank([row["down_pct"] for row in historical_moves], 0.80)
    try:
        expirations = sorted(pd.Timestamp(x).date() for x in ticker.options)
    except Exception:
        return []
    output = []
    max_loss_budget = min(5.0, max(0.0, capital * 0.05))
    for event in earnings:
        valid_expiries = [expiry for expiry in expirations
                          if event < expiry <= event + timedelta(days=30)
                          and 1 <= (expiry - today).days <= 45]
        for expiry in valid_expiries[:2]:
            try:
                chain = ticker.option_chain(expiry.isoformat())
            except Exception:
                continue
            puts = _clean_chain(chain.puts, "put", spot, min_open_interest, max_spread_pct)
            calls = _clean_chain(chain.calls, "call", spot, min_open_interest, max_spread_pct)
            if len(puts) < 2 or len(calls) < 2:
                continue
            atm_put = min(puts, key=lambda row: abs(row["strike"] / spot - 1))
            atm_call = min(calls, key=lambda row: abs(row["strike"] / spot - 1))
            iv = (atm_put["iv"] + atm_call["iv"]) / 2
            dte = (expiry - today).days
            years = dte / 365.0
            short_puts = sorted(puts, key=lambda row: abs(row["strike"] / spot - 0.92))[:16]
            short_calls = sorted(calls, key=lambda row: abs(row["strike"] / spot - 1.08))[:16]
            for short_put in short_puts:
                put_wings = [row for row in puts
                             if 0 < short_put["strike"] - row["strike"] <= 5]
                put_wings = sorted(put_wings, key=lambda row: short_put["strike"] - row["strike"])[:3]
                for long_put in put_wings:
                    put_width = short_put["strike"] - long_put["strike"]
                    for short_call in short_calls:
                        if short_call["strike"] <= short_put["strike"]:
                            continue
                        call_wings = [row for row in calls
                                      if 0 < row["strike"] - short_call["strike"] <= 5]
                        call_wings = sorted(call_wings, key=lambda row: row["strike"] - short_call["strike"])[:3]
                        for long_call in call_wings:
                            call_width = long_call["strike"] - short_call["strike"]
                            # Credit uses short-leg bids and long-leg asks, a conservative
                            # leg-by-leg estimate. A real multi-leg fill can differ.
                            credit = (short_put["bid"] + short_call["bid"]
                                      - long_put["ask"] - long_call["ask"])
                            if credit < 0.05:
                                continue
                            max_profit = credit * 100
                            max_loss = max(put_width, call_width) * 100 - max_profit
                            if max_loss <= 0:
                                continue
                            lower_be = short_put["strike"] - credit
                            upper_be = short_call["strike"] + credit
                            up_room_pct = (upper_be / spot - 1) * 100
                            down_room_pct = (1 - lower_be / spot) * 100
                            covered_events = sum(
                                row["up_pct"] <= up_room_pct and row["down_pct"] <= down_room_pct
                                for row in historical_moves
                            )
                            historical_coverage = covered_events / len(historical_moves) * 100
                            # Require the proposed expiration breakevens to contain
                            # at least 80% of this stock's sampled 3-session earnings ranges.
                            if historical_coverage < 80:
                                continue
                            pop = _pop_between(spot, lower_be, upper_be, iv, years) * 100
                            output.append({
                                "Symbol": symbol, "Earnings date (Yahoo estimate)": event.isoformat(),
                                "Expiration": expiry.isoformat(), "DTE": dte,
                                "Spot $": round(spot, 2), "Short put $": short_put["strike"],
                                "Long put $": long_put["strike"], "Short call $": short_call["strike"],
                                "Long call $": long_call["strike"], "Credit / spread $": round(max_profit, 2),
                                "Max profit $": round(max_profit, 2), "Max loss $": round(max_loss, 2),
                                "Lower breakeven $": round(lower_be, 2), "Upper breakeven $": round(upper_be, 2),
                                "Est. expiry profit probability %": round(pop, 1), "ATM IV %": round(iv * 100, 1),
                                "Historical event coverage %": round(historical_coverage, 1),
                                "Historical events n": len(historical_moves),
                                "Historical 80th pct up move %": round(historical_up80, 1),
                                "Historical 80th pct down move %": round(historical_down80, 1),
                                "Price warning below $": round(short_put["strike"] * 1.01, 2),
                                "Price warning above $": round(short_call["strike"] * 0.99, 2),
                                "Price/time management": "Watch underlying price; review/exit at either short strike; time exit after 3 sessions post-earnings. No IV-based adjustment.",
                                "Reward / max loss": round(max_profit / max_loss, 3),
                                "Fits $100 risk cap": "Yes" if max_loss <= max_loss_budget else "No",
                                "Risk cap $": round(max_loss_budget, 2),
                                "Quote quality": "Yahoo Finance; verify live bid/ask before any paper order",
                            })
    # Deduplicate repeated strike structures across adjacent earnings estimates.
    unique = {}
    for row in output:
        key = tuple(row[k] for k in ("Symbol", "Earnings date (Yahoo estimate)", "Expiration",
                                     "Short put $", "Long put $", "Short call $", "Long call $"))
        unique[key] = row
    return sorted(unique.values(), key=lambda row: (
        row["Fits $100 risk cap"] != "Yes", -row["Est. expiry profit probability %"],
        -row["Reward / max loss"]
    ))


def scan_universe(results: pd.DataFrame, capital: float = 100.0,
                  max_symbols: int = 20, min_open_interest: int = 50,
                  max_spread_pct: float = 35.0) -> tuple[pd.DataFrame, dict]:
    """Check only the most liquid already-loaded stock rows to keep free scans bounded."""
    if results is None or results.empty:
        return pd.DataFrame(), {"checked": 0, "failures": 0, "symbols": []}
    selected = results.sort_values("Average $ volume", ascending=False).head(max_symbols)
    candidates, failures = [], 0
    checked = []
    for _, row in selected.iterrows():
        symbol = str(row.get("Symbol", "")).strip().upper()
        if not symbol:
            continue
        checked.append(symbol)
        try:
            candidates.extend(scan_symbol(symbol, _number(row.get("Price")), capital,
                                          min_open_interest=min_open_interest,
                                          max_spread_pct=max_spread_pct))
        except Exception:
            failures += 1
    frame = pd.DataFrame(candidates)
    if not frame.empty:
        frame = frame.sort_values(["Fits $100 risk cap", "Est. expiry profit probability %", "Reward / max loss"],
                                  ascending=[False, False, False]).head(25).reset_index(drop=True)
    return frame, {"checked": len(checked), "failures": failures, "symbols": checked}

