"""On-demand intraday mean-reversion watchlists. Research only; no orders."""
from datetime import datetime, time
from zoneinfo import ZoneInfo
import math
import pandas as pd
import streamlit as st
import yfinance as yf


def latest_quote(symbols):
    """5-minute candles can be delayed and unavailable; never label them live."""
    if not symbols:
        return {}
    raw = yf.download(symbols, period="5d", interval="5m", auto_adjust=True,
                      group_by="ticker", progress=False, threads=False)
    out = {}
    if raw.empty:
        return out
    for symbol in symbols:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                frame = raw[symbol] if symbol in raw.columns.get_level_values(0) else raw.xs(symbol, axis=1, level=1)
            else:
                frame = raw
            frame = frame.dropna(subset=["Close"])
            if not frame.empty:
                stamp = pd.Timestamp(frame.index[-1])
                out[symbol] = (float(frame["Close"].iloc[-1]), str(stamp))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def evaluate(symbol, daily, quote, threshold, capital, risk_budget):
    data = daily.dropna(subset=["Close", "Low", "Volume"]).copy()
    if len(data) < 26:
        return None
    now_et = datetime.now(ZoneInfo("America/New_York"))
    latest_day = pd.Timestamp(data.index[-1]).date()
    # Exclude current session's incomplete daily candle.
    completed = data.iloc[:-1] if latest_day == now_et.date() else data
    if len(completed) < 25:
        return None
    sma = float(completed["Close"].tail(25).mean())
    price, stamp = quote
    if not all(math.isfinite(x) and x > 0 for x in (price, sma)):
        return None
    deviation = 100 * (price / sma - 1)
    low = float(completed["Low"].tail(5).min())
    stop = min(low, price) * 0.99
    per_share = price - stop
    avg_dollar = float((completed["Close"].tail(20) * completed["Volume"].tail(20)).mean())
    rr = (sma - price) / per_share if per_share > 0 else 0
    eligible = deviation <= -threshold
    return {
        "Ticker": symbol, "Quote time": stamp, "Price $": round(price, 2),
        "SMA25 $": round(sma, 2), "Deviation %": round(deviation, 2),
        "Stop reference $": round(stop, 2), "Target $": round(sma, 2),
        "R multiple": round(rr, 2),
        "Avg daily $ volume": round(avg_dollar),
        "Action": "BUY WATCH — confirm reversal" if eligible else "NO ACTION",
    }


def render(get_universe, get_prices, universe_name, watchlist_text):
    st.header("Intraday Buy / Sell Action Scanner")
    st.caption("Discovery only: no account-size or risk-based exclusions. On-demand 5-minute Yahoo Finance price snapshots against prior completed daily closes; quotes may be delayed or stale.")
    threshold = st.number_input("Buy deviation below SMA25 (%)", value=20.0, min_value=1.0, max_value=90.0, key="action_threshold")
    n = st.select_slider("Symbols per refresh", options=[25, 50, 100, 250, 500], value=100, key="action_n")
    holdings = st.text_area("Positions to monitor for SELL/EXIT (ticker, entry price, stop price, shares; one per line)", placeholder="AAPL,150,142,2", key="action_holdings")
    if not st.button("Refresh BUY and SELL lists", type="primary", key="refresh_action"):
        st.info("Click Refresh BUY and SELL lists to fetch fresh available data.")
        return
    try:
        symbols = get_universe(universe_name, watchlist_text)[:n]
        positions = {}
        errors = []
        for line in holdings.splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            try:
                if len(parts) != 4:
                    raise ValueError("expected four comma-separated fields")
                symbol, entry, stop, qty = parts
                entry, stop, qty = float(entry), float(stop), int(qty)
                if not symbol.isalnum() or entry <= 0 or stop <= 0 or qty <= 0 or stop >= entry:
                    raise ValueError("invalid ticker, entry, stop or shares")
                positions[symbol.upper()] = (entry, stop, qty)
            except ValueError:
                errors.append(line)
        if errors:
            st.error("Invalid positions: " + "; ".join(errors[:5]) + ". Use TICKER,ENTRY,STOP,SHARES.")
            return
        all_symbols = list(dict.fromkeys(symbols + list(positions)))
        with st.spinner("Fetching daily averages and latest available 5-minute bars…"):
            daily = get_prices(all_symbols)
            intraday = latest_quote(all_symbols)
        rows = [evaluate(s, daily[s], intraday[s], threshold, None, None)
                for s in symbols if s in daily and s in intraday]
        rows = [r for r in rows if r is not None]
        buy = pd.DataFrame([r for r in rows if r["Action"].startswith("BUY")])
        sell_rows = []
        for symbol, (entry, stop, qty) in positions.items():
            if symbol not in daily or symbol not in intraday:
                sell_rows.append({"Ticker": symbol, "Action": "DATA UNAVAILABLE — review manually"})
                continue
            result = evaluate(symbol, daily[symbol], intraday[symbol], threshold, None, None)
            if result is None:
                continue
            price = result["Price $"]
            action = "SELL WATCH — stop breached" if price <= stop else "SELL WATCH — SMA target reached" if price >= result["SMA25 $"] else "HOLD / MONITOR"
            sell_rows.append({"Ticker": symbol, "Action": action, "Price $": price, "Entry $": entry,
                              "Stop $": stop, "Target SMA25 $": result["SMA25 $"], "Shares": qty,
                              "Unrealized P/L $": round((price - entry) * qty, 2), "Quote time": result["Quote time"]})
        st.caption(f"Requested {len(all_symbols)} tickers; daily histories {len(daily)}, intraday quotes {len(intraday)}. Missing/stale quotes are not actionable. Prices may not reflect the current market.")
        left, right = st.columns(2)
        with left:
            st.subheader("BUY watchlist")
            st.metric("Potential entries", len(buy))
            if buy.empty:
                st.info("No qualifying BUY candidates in available quotes.")
            else:
                st.dataframe(buy.sort_values("Deviation %"), hide_index=True, use_container_width=True)
                st.download_button("Download BUY watchlist", buy.to_csv(index=False), "buy_watch.csv", "text/csv", key="buy_watch_csv")
        with right:
            st.subheader("SELL / EXIT watchlist")
            sell = pd.DataFrame(sell_rows)
            triggered = sell[sell["Action"].str.startswith("SELL")] if not sell.empty else sell
            st.metric("Potential exits", len(triggered))
            if sell.empty:
                st.info("Enter current positions above to evaluate SELL/EXIT conditions. No holdings are inferred.")
            else:
                st.dataframe(sell, hide_index=True, use_container_width=True)
                st.download_button("Download EXIT watchlist", sell.to_csv(index=False), "exit_watch.csv", "text/csv", key="exit_watch_csv")
        st.warning("BUY WATCH and SELL WATCH are research alerts, not order instructions. Verify current executable quotes, news, halts, liquidity and position details with your broker. Stops can slip or gap; no automated orders.")
    except Exception as exc:
        st.error(f"Unable to refresh action scan: {exc}")
