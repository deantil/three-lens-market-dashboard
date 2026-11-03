"""Daily-price mean reversion research scan; no broker orders."""
import math
import pandas as pd
import streamlit as st


def scan_one(symbol, frame, threshold=20.0, low_days=5, stop_buffer=0.01, max_risk=50.0, capital=5000.0):
    required = {"Close", "Low", "Volume"}
    if frame is None or not required.issubset(frame.columns):
        return None
    data = frame.dropna(subset=["Close", "Low", "Volume"])
    if len(data) < max(26, low_days + 2):
        return None
    close = float(data["Close"].iloc[-1])
    sma = float(data["Close"].tail(25).mean())
    if not all(math.isfinite(x) and x > 0 for x in (close, sma)):
        return None
    deviation = (close / sma - 1.0) * 100
    if deviation > -threshold:
        return None
    prior_low = float(data["Low"].iloc[-(low_days + 1):-1].min())
    stop = min(prior_low, close) * (1 - stop_buffer)
    risk_share = close - stop
    reward_share = sma - close
    if risk_share <= 0 or reward_share <= 0:
        return None
    shares = min(math.floor(max_risk / risk_share), math.floor(capital / close))
    risk = shares * risk_share
    reward = shares * reward_share
    avg_dollar_vol = float((data["Close"].tail(20) * data["Volume"].tail(20)).mean())
    return {
        "Ticker": symbol, "As of": str(data.index[-1].date()),
        "Close $": round(close, 2), "25d SMA $": round(sma, 2),
        "Below SMA %": round(-deviation, 2), "Reference stop $": round(stop, 2),
        "Target $": round(sma, 2), "Reward / risk": round(reward_share / risk_share, 2),
        "Shares": shares, "Planned risk $": round(risk, 2),
        "Target gain $": round(reward, 2),
        "Avg dollar volume $": round(avg_dollar_vol, 0),
        "Status": "WATCH — validate reversal and liquidity" if shares > 0 else "NO TRADE — cannot size within budget",
    }


def render(price_map, universe_name, watchlist_text, limit, get_universe, get_prices):
    st.header("Kotegawa · 25-Day Mean Reversion Scan")
    st.caption("Reel-inspired research rule: closing price at least 20% below its 25-session simple moving average; hypothetical entry at latest close, target at SMA25, stop below prior lows. Not a verified reconstruction of Kotegawa's actual trades.")
    a, b, c = st.columns(3)
    threshold = a.number_input("Minimum below SMA25 (%)", min_value=1.0, max_value=90.0, value=20.0, step=1.0, key="kotegawa_threshold")
    low_days = b.number_input("Prior sessions for stop", min_value=2, max_value=30, value=5, step=1, key="kotegawa_low_days")
    stop_buffer_pct = c.number_input("Stop buffer below low (%)", min_value=0.1, max_value=10.0, value=1.0, step=0.1, key="kotegawa_stop_buffer")
    d, e = st.columns(2)
    max_risk = d.number_input("Maximum planned risk ($)", min_value=1.0, max_value=50.0, value=50.0, step=1.0, key="kotegawa_risk")
    capital = e.number_input("Project T account ($)", min_value=100.0, value=5000.0, step=100.0, key="kotegawa_capital")
    st.caption("The scanner uses the stock universe and symbol limit in the left sidebar. The separate Run button scans without applying breakout filters from other tabs.")
    if st.button("Run Kotegawa mean-reversion scan", type="primary", key="run_kotegawa"):
        st.session_state["kotegawa_scan_requested"] = True
    if not st.session_state.get("kotegawa_scan_requested"):
        st.info("Click Run Kotegawa mean-reversion scan to load candidates.")
        return
    try:
        with st.spinner("Scanning daily closing prices for mean-reversion candidates…"):
            symbols = get_universe(universe_name, watchlist_text)[:limit]
            prices = get_prices(symbols)
        rows = [scan_one(s, df, threshold, low_days, stop_buffer_pct / 100, max_risk, capital) for s, df in prices.items()]
        rows = [r for r in rows if r is not None]
        st.caption(f"Universe symbols requested: {len(symbols):,} · histories returned: {len(prices):,} · price-date and coverage vary. Yahoo Finance daily data may be delayed.")
        if not rows:
            st.warning("NO TRADE — no stocks met the price-deviation rule in the available data.")
            return
        result = pd.DataFrame(rows).sort_values(["Below SMA %", "Reward / risk"], ascending=False)
        st.metric("Stocks at least threshold below SMA25", len(result))
        st.dataframe(result, hide_index=True, use_container_width=True)
        st.download_button("Download mean-reversion scan CSV", result.to_csv(index=False), "kotegawa_mean_reversion_scan.csv", "text/csv", key="kotegawa_download")
        st.caption("These are hypothetical closing-price entries, not executable quotes. A stock can keep falling; stop orders may fill worse than planned, and losses can exceed $50 due to gaps/slippage. No historical win rate, 3R/day claim, or broker execution is verified.")
    except Exception as exc:
        st.error(f"Scan unavailable: {exc}")
