from __future__ import annotations

from datetime import date, timedelta
from io import StringIO
import requests
import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Three Lens Market Dashboard", page_icon="📈", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 1.6rem; max-width: 1500px}
[data-testid="stMetric"] {background:#111827; border:1px solid #263247; padding:16px; border-radius:12px}
.stTabs [data-baseweb="tab-list"] {gap:12px}
</style>
""", unsafe_allow_html=True)

st.title("Three Lens Market Dashboard")
st.caption("A research and risk-planning tool. Signals are educational, delayed market data may apply, and no return is guaranteed.")

@st.cache_data(ttl=3600, show_spinner=False)
def get_universe():
    """Fetch current listed equity symbols from Nasdaq's public screener endpoint."""
    url = "https://api.nasdaq.com/api/screener/stocks"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
    frames = []
    for exchange in ("nasdaq", "nyse", "amex"):
        response = requests.get(url, params={"tableonly": "true", "limit": "10000", "exchange": exchange}, headers=headers, timeout=30)
        response.raise_for_status()
        frames.append(pd.DataFrame(response.json().get("data", {}).get("rows", [])))
    df = pd.concat(frames, ignore_index=True)
    if df.empty or "symbol" not in df:
        raise ValueError("The exchange screener returned no symbols.")
    df["symbol"] = df["symbol"].astype(str).str.replace("$", "-", regex=False).str.replace(".", "-", regex=False)
    if "name" in df:
        df = df[~df["name"].str.contains(r"ETF|Fund|Warrant|Right|Unit|Preferred", case=False, na=False)]
    return sorted(set(df["symbol"].dropna()) - {""})

@st.cache_data(ttl=900, show_spinner=False)
def get_prices(symbols, period="1y"):
    data = yf.download(symbols, period=period, interval="1d", auto_adjust=True, group_by="ticker", progress=False, threads=True)
    out = {}
    if data.empty: return out
    if isinstance(data.columns, pd.MultiIndex):
        for symbol in symbols:
            if symbol in data.columns.get_level_values(0):
                frame = data[symbol].dropna(how="all")
                if not frame.empty: out[symbol] = frame
    else:
        out[symbols[0]] = data.dropna(how="all")
    return out

def score_frame(symbol, df):
    close, high, low, volume = (df[k].dropna() for k in ("Close", "High", "Low", "Volume"))
    if len(close) < 60: return None
    c = close.iloc[-1]; prev = close.iloc[-2]
    ma21 = close.rolling(21).mean().iloc[-1]; ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else float("nan")
    prior_high = high.iloc[-21:-1].max()
    avg_vol = volume.iloc[-21:-1].mean()
    relvol = volume.iloc[-1] / avg_vol if avg_vol else 0
    momentum = (c / close.iloc[-21] - 1) * 100
    adr = ((high / low - 1).rolling(20).mean().iloc[-1] * 100) if len(high) >= 20 else float("nan")
    volume_ok = relvol >= 1.5
    trend_ok = c > ma21 > ma50
    breakout = c > prior_high
    trader = sum([trend_ok, breakout, volume_ok, momentum > 0, (pd.isna(ma200) or c > ma200)])
    omni = sum([breakout, volume_ok, momentum > 3, c > ma50])
    stop = min(float(low.iloc[-10:].min()), float(c * .94))
    return {"Symbol": symbol, "Price": float(c), "Day %": float((c / prev - 1) * 100), "Trader score": f"{trader}/5", "Omni score": f"{omni}/4", "20d momentum %": float(momentum), "Rel. volume": float(relvol), "ADR %": float(adr), "Trigger": float(prior_high), "Reference stop": stop, "Above 21/50d": "Yes" if trend_ok else "No", "Breakout": "Yes" if breakout else "No", "Volume confirms": "Yes" if volume_ok else "No"}

with st.sidebar:
    st.header("Scan settings")
    capital = st.number_input("Account size (USD)", min_value=100, value=1000, step=100)
    risk_pct = st.slider("Risk per trade (%)", 0.25, 2.0, 0.5, 0.25)
    min_price = st.number_input("Minimum share price", min_value=0.0, value=5.0, step=1.0)
    min_dollar_vol = st.number_input("Minimum average daily $ volume (M)", min_value=0.0, value=10.0, step=5.0)
    limit = st.select_slider("Symbols to scan", options=[100, 250, 500, 1000, 2500, 5000, 10000], value=10000)
    if st.button("Refresh universe and prices", type="primary", use_container_width=True):
        get_universe.clear(); get_prices.clear()
        st.rerun()
    st.divider()
    st.subheader("Year-end goal tracker")
    goal = st.number_input("Target balance (USD)", min_value=capital, value=max(10000, capital), step=500)
    days_left = max((date(date.today().year, 12, 31) - date.today()).days, 1)
    req_daily = ((goal / capital) ** (1 / days_left) - 1) * 100 if capital else 0
    st.metric("Daily compounded return needed", f"{req_daily:.2f}%", help=f"Mathematical pace for {days_left} calendar days. This is not a forecast or a realistic expectation.")

try:
    with st.spinner("Loading listed-stock universe…"):
        universe = get_universe()
    st.caption(f"Universe source: Nasdaq stock screener (Nasdaq, NYSE, NYSE American) · {len(universe):,} listed symbols received. Scanning up to {limit:,}; price data coverage depends on Yahoo Finance availability.")
    symbols = universe[:limit]
    with st.spinner(f"Downloading daily history for {len(symbols):,} symbols…"):
        price_map = get_prices(symbols)
    rows = [score_frame(s, d) for s, d in price_map.items()]
    rows = [r for r in rows if r and r["Price"] >= min_price]
    results = pd.DataFrame(rows)
    if not results.empty:
        results = results.sort_values(["Trader score", "Omni score"], ascending=False).reset_index(drop=True)
        risk_usd = capital * risk_pct / 100
        results["Risk-sized shares"] = ((risk_usd / (results["Price"] - results["Reference stop"]).clip(lower=.01))).astype(int)
        results["Estimated position $"] = results["Risk-sized shares"] * results["Price"]
        results = results[(results["Estimated position $"] <= capital) & (results["Risk-sized shares"] > 0)]
        liquid = []
        for _, row in results.iterrows():
            frame = price_map.get(row["Symbol"])
            dv = (frame["Close"] * frame["Volume"]).tail(20).mean() / 1_000_000 if frame is not None else 0
            if dv >= min_dollar_vol: liquid.append(row["Symbol"])
        results = results[results["Symbol"].isin(liquid)].copy()
    else:
        st.warning("No daily price histories were returned. Try refreshing, or use a smaller symbol limit.")
except Exception as exc:
    universe = []; results = pd.DataFrame()
    st.error(f"Market data could not be loaded: {exc}")
    st.info("The dashboard needs an internet connection and currently uses public Nasdaq screener and Yahoo Finance endpoints.")

if not results.empty:
    a, b, c = st.columns(3)
    a.metric("Symbols with data", f"{len(price_map):,}")
    b.metric("Price/volume-qualified", f"{len(results):,}")
    c.metric("Passing breakout + volume", f"{((results['Breakout'] == 'Yes') & (results['Volume confirms'] == 'Yes')).sum():,}")

tab1, tab2, tab3 = st.tabs(["1 · Trader breakout", "2 · Nirvana Omni inspired", "3 · Birbia"])
with tab1:
    st.subheader("Trader breakout rules")
    st.caption("A transparent approximation of the rules described in your pasted notes: trend, recent range breakout, momentum, and volume. It is not a reconstruction of a named trader's full system.")
    if not results.empty:
        show = results[results["Trader score"].str[0].astype(int) >= 3].sort_values(["Breakout", "Trader score"], ascending=False)
        st.dataframe(show, hide_index=True, use_container_width=True, column_config={"Price": st.column_config.NumberColumn(format="$%.2f"), "Trigger": st.column_config.NumberColumn(format="$%.2f"), "Reference stop": st.column_config.NumberColumn(format="$%.2f"), "Estimated position $": st.column_config.NumberColumn(format="$%.2f")})
        st.download_button("Download full scan CSV", results.to_csv(index=False), "market_scan.csv", "text/csv")
with tab2:
    st.subheader("Nirvana OmniTrader · public Power Move approximation")
    st.caption("Nirvana publicly describes Power Move as a breakout approach where price, momentum, and volume agree. The proprietary signal logic and licensed indicators are not reproduced here.")
    if not results.empty:
        omni = results[results["Omni score"].str[0].astype(int) >= 3].sort_values(["Omni score", "Rel. volume"], ascending=False)
        st.dataframe(omni, hide_index=True, use_container_width=True)
with tab3:
    st.subheader("Birbia")
    st.warning("Strategy rules are not specified yet, so this tab is reserved and does not generate signals.")
    st.write("Send the exact name, a link, or the entry/exit rules you mean by “Birbia” and I can implement a transparent version.")

with st.expander("How sizing and the goal tracker work"):
    st.write(f"Risk budget per trade is ${capital * risk_pct / 100:,.2f} at the selected {risk_pct:.2f}% account risk. Reference stops use the lower of the recent 10-day low or a 6% reference distance. Share counts are capped so the position value does not exceed the account balance. Actual fills, gaps, fees, and losses can differ substantially.")
    st.write("A $1,000 to $10,000 year-end target requires an exceptionally high compounded return, especially late in the year. The dashboard shows the arithmetic pace only; it does not predict or promise that outcome.")
