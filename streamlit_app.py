from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO, StringIO
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
def get_universe(universe_name, custom_text=""):
    """Load broad US listings, daily SPY holdings, or the user's own ticker list."""
    if universe_name == "My watchlist":
        cleaned = [x.strip().upper().replace(".", "-") for x in custom_text.replace(",", "\n").splitlines()]
        symbols = sorted(set(x for x in cleaned if x))
        if not symbols:
            raise ValueError("Add at least one ticker to My watchlist.")
        return symbols

    if universe_name == "SPY holdings (S&P 500)":
        url = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        raw = pd.read_excel(BytesIO(response.content), header=None, dtype=str)
        header_row = None
        symbol_column = None
        for i in range(min(20, len(raw))):
            values = raw.iloc[i].astype(str).str.strip().str.lower().tolist()
            for candidate in ("ticker", "symbol"):
                if candidate in values:
                    header_row, symbol_column = i, values.index(candidate)
                    break
            if header_row is not None:
                break
        if header_row is None:
            raise ValueError("Could not find the ticker column in State Street's SPY holdings file.")
        symbols = raw.iloc[header_row + 1:, symbol_column].dropna().astype(str).str.strip().tolist()
        symbols = [x.replace(".", "-") for x in symbols if x and x.lower() not in {"nan", "cash", "usd", "spdr s&p 500 etf trust"}]
        symbols = [x for x in symbols if x.isascii() and any(ch.isalpha() for ch in x)]
        if not symbols:
            raise ValueError("State Street's SPY holdings file returned no stock tickers.")
        return sorted(set(symbols))

    """Load Nasdaq Trader's official daily files for Nasdaq and other US listings."""
    base = "https://www.nasdaqtrader.com/dynamic/SymDir/"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; market-dashboard/1.0)"}
    symbols = []
    for filename in ("nasdaqlisted.txt", "otherlisted.txt"):
        response = requests.get(base + filename, headers=headers, timeout=30)
        response.raise_for_status()
        table = pd.read_csv(StringIO(response.text), sep="|", dtype=str)
        table.columns = [column.strip() for column in table.columns]
        symbol_col = "Symbol" if "Symbol" in table.columns else "ACT Symbol"
        table = table[table[symbol_col].notna()]
        table = table[~table[symbol_col].str.startswith("File Creation Time", na=False)]
        if "Test Issue" in table.columns:
            table = table[table["Test Issue"].eq("N")]
        if "ETF" in table.columns:
            table = table[table["ETF"].ne("Y")]
        name_col = "Security Name"
        if name_col in table.columns:
            table = table[~table[name_col].str.contains(r"Warrant|Right|Unit|Preferred", case=False, na=False)]
        symbols.extend(table[symbol_col].tolist())
    cleaned = [str(symbol).replace("$", "-").replace(".", "-").strip() for symbol in symbols]
    cleaned = [symbol for symbol in cleaned if symbol and symbol.isascii()]
    if not cleaned:
        raise ValueError("Nasdaq Trader's symbol directory returned no symbols.")
    return sorted(set(cleaned))

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
    universe_name = st.selectbox("Stock universe", ["All US exchange listings", "SPY holdings (S&P 500)", "My watchlist"])
    watchlist_text = "NVDA AMD TSLA PLTR SMCI AVGO MU ARM CRWD NET SNOW DDOG SHOP COIN HOOD SOFI AFRM UPST RBLX APP RKLB IONQ HIMS CELH ELF ONON DKNG ROKU TTD MELI SE NU MSTR MARA RIOT AXON CAVA ANF DECK UBER ABNB DASH ENPH FSLR RIVN CVNA OPEN SOUN RGTI U W LULU"
    if universe_name == "My watchlist":
        watchlist_text = st.text_area("Tickers (spaces or commas are okay)", value=watchlist_text, height=110)
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
        universe = get_universe(universe_name, watchlist_text)
    source_note = "Nasdaq and other US exchanges" if universe_name == "All US exchange listings" else "State Street SPY fund holdings" if universe_name == "SPY holdings (S&P 500)" else "your typed tickers"
    st.caption(f"Universe: {universe_name} · Source: {source_note} · {len(universe):,} symbols received. Scanning up to {limit:,}; price history availability depends on Yahoo Finance.")
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
    st.info("The dashboard needs an internet connection. It uses Nasdaq Trader listings, State Street SPY holdings, and Yahoo Finance prices.")

if not results.empty:
    a, b, c = st.columns(3)
    a.metric("Symbols with data", f"{len(price_map):,}")
    b.metric("Price/volume-qualified", f"{len(results):,}")
    c.metric("Passing breakout + volume", f"{((results['Breakout'] == 'Yes') & (results['Volume confirms'] == 'Yes')).sum():,}")

tab1, tab2, tab3, tab4 = st.tabs(["1 · Trader breakout", "2 · Nirvana Omni inspired", "3 · Birbia", "4 · Stock lookup"])
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
with tab4:
    st.subheader("Look up one stock")
    st.caption("Enter any ticker, such as NVDA, U, or BRK.B. We’ll show available daily price history and calculate the same transparent technical checks.")
    with st.form("ticker_lookup_form"):
        lookup_input = st.text_input("Stock ticker", placeholder="e.g. NVDA")
        lookup_period = st.selectbox("Chart period", ["6mo", "1y", "2y", "5y"], index=1)
        lookup_submit = st.form_submit_button("Show stock details", type="primary")
    if lookup_submit:
        lookup_symbol = lookup_input.strip().upper().replace(".", "-")
        if not lookup_symbol:
            st.warning("Type a ticker first.")
        else:
            try:
                with st.spinner(f"Loading {lookup_symbol}…"):
                    lookup_data = get_prices((lookup_symbol,), period=lookup_period).get(lookup_symbol)
                if lookup_data is None or lookup_data.empty:
                    st.error(f"No price history was found for {lookup_symbol}. Check the ticker spelling or try again later.")
                else:
                    lookup_data = lookup_data.dropna(subset=["Close"])
                    detail = score_frame(lookup_symbol, lookup_data)
                    last_price = float(lookup_data["Close"].iloc[-1])
                    prior_close = float(lookup_data["Close"].iloc[-2]) if len(lookup_data) > 1 else last_price
                    one_year_return = (last_price / float(lookup_data["Close"].iloc[0]) - 1) * 100
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Latest close", f"${last_price:,.2f}", f"{(last_price / prior_close - 1) * 100:+.2f}% day")
                    m2.metric(f"Return over {lookup_period}", f"{one_year_return:+.1f}%")
                    if detail:
                        m3.metric("Trader checks", detail["Trader score"])
                        l1, l2, l3 = st.columns(3)
                        l1.metric("20-day momentum", f"{detail['20d momentum %']:+.1f}%")
                        l2.metric("Breakout trigger", f"${detail['Trigger']:,.2f}")
                        l3.metric("Reference stop", f"${detail['Reference stop']:,.2f}")
                        risk_dollars = capital * risk_pct / 100
                        risk_per_share = max(last_price - detail["Reference stop"], 0.01)
                        shares = int(risk_dollars / risk_per_share)
                        st.write(f"At your selected risk setting: about **{shares} shares** for a maximum planned risk of **${risk_dollars:,.2f}** (before gaps or slippage).")
                        st.dataframe(pd.DataFrame([detail]), hide_index=True, use_container_width=True)
                    else:
                        st.info("This ticker has price data, but fewer than 60 trading days are available for the technical checks.")
                    st.line_chart(lookup_data["Close"].rename("Adjusted close"), height=360)
                    st.caption("Price data and calculated indicators are provided for research only; they are not a recommendation to buy or sell.")
            except Exception as exc:
                st.error(f"Could not load {lookup_symbol}: {exc}")

with st.expander("How sizing and the goal tracker work"):
    st.write(f"Risk budget per trade is ${capital * risk_pct / 100:,.2f} at the selected {risk_pct:.2f}% account risk. Reference stops use the lower of the recent 10-day low or a 6% reference distance. Share counts are capped so the position value does not exceed the account balance. Actual fills, gaps, fees, and losses can differ substantially.")
    st.write("A $1,000 to $10,000 year-end target requires an exceptionally high compounded return, especially late in the year. The dashboard shows the arithmetic pace only; it does not predict or promise that outcome.")
