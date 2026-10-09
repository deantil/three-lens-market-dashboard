from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO, StringIO
import json
import math
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
    data = yf.download(symbols, period=period, interval="1d", auto_adjust=True, group_by="ticker", progress=False, threads=4)
    out = {}
    if data.empty: return out
    if isinstance(data.columns, pd.MultiIndex):
        for symbol in symbols:
            frame = None
            # yfinance may put the ticker on either MultiIndex level, especially
            # when only one symbol is requested from the Stock Lookup tab.
            for level in range(data.columns.nlevels):
                if symbol in data.columns.get_level_values(level):
                    frame = data.xs(symbol, axis=1, level=level, drop_level=True)
                    if isinstance(frame.columns, pd.MultiIndex):
                        frame.columns = frame.columns.get_level_values(-1)
                    break
            if frame is not None and set(("Open", "High", "Low", "Close", "Volume")).issubset(frame.columns):
                frame = frame.dropna(how="all")
                if not frame.empty: out[symbol] = frame
    else:
        frame = data.dropna(how="all")
        if set(("Open", "High", "Low", "Close", "Volume")).issubset(frame.columns):
            out[symbols[0]] = frame
    return out

@st.cache_data(ttl=300, show_spinner=False)
def get_paper_state():
    url = "https://raw.githubusercontent.com/deantil/three-lens-market-dashboard/paper-state/paper_challenge_state.json"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl=300, show_spinner=False)
def get_option_expirations(symbol):
    return list(yf.Ticker(symbol).options)

@st.cache_data(ttl=300, show_spinner=False)
def get_option_chain(symbol, expiration):
    chain = yf.Ticker(symbol).option_chain(expiration)
    return chain.calls, chain.puts

def normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def as_number(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default

def option_candidates(chain, option_type, spot, expiration, account_size, min_open_interest, max_spread_pct):
    exp_date = pd.Timestamp(expiration).date()
    dte = max((exp_date - date.today()).days, 1)
    t = dte / 365
    rows = []
    for _, option in chain.iterrows():
        strike = as_number(option.get("strike"))
        bid = as_number(option.get("bid"))
        ask = as_number(option.get("ask"))
        iv = as_number(option.get("impliedVolatility"))
        volume = int(as_number(option.get("volume")))
        oi = int(as_number(option.get("openInterest")))
        if strike <= 0 or bid <= 0 or ask < bid or iv <= 0 or oi < min_open_interest:
            continue
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid * 100 if mid else 999
        if spread_pct > max_spread_pct:
            continue
        if option_type == "Cash-secured put" and strike >= spot:
            continue
        if option_type == "Covered call" and strike <= spot:
            continue
        if option_type == "Cash-secured put":
            breakeven = strike - mid
            collateral = strike * 100
            max_profit = mid * 100
            max_loss = max(breakeven, 0) * 100
            # Lognormal expiration-price model: zero drift/rates and constant IV.
            if breakeven <= 0:
                pop = 100.0
            else:
                d2 = (math.log(spot / breakeven) - 0.5 * iv * iv * t) / (iv * math.sqrt(t))
                pop = normal_cdf(d2) * 100
        else:
            breakeven = spot - mid
            collateral = spot * 100
            max_profit = max(0, strike - spot + mid) * 100
            max_loss = max(breakeven, 0) * 100
            if strike - spot + mid <= 0:
                pop = 0.0
            else:
                d2 = (math.log(spot / breakeven) - 0.5 * iv * iv * t) / (iv * math.sqrt(t)) if breakeven > 0 else float("inf")
                pop = normal_cdf(d2) * 100
        rows.append({
            "Expiration": expiration, "DTE": dte, "Type": option_type, "Strike $": strike,
            "Bid $": bid, "Ask $": ask, "Mid premium $/share": mid,
            "Premium / contract $": mid * 100, "Break-even $": breakeven,
            "Cash / shares needed $": collateral, "Fits account": "Yes" if collateral <= account_size else "No",
            "Max gain $": max_profit, "Max loss $": max_loss,
            "Reward / max loss": max_profit / max_loss if max_loss else float("inf"),
            "Est. profit probability %": pop, "Annualized premium / collateral %": mid * 100 / collateral * 365 / dte * 100,
            "OTM distance %": abs(strike / spot - 1) * 100, "IV %": iv * 100,
            "Bid-ask spread %": spread_pct, "Open interest": oi, "Volume": volume,
        })
    return pd.DataFrame(rows)

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
    avg_dollar_volume = float((close.tail(20) * volume.tail(20)).mean())
    return {"Symbol": symbol, "Price": float(c), "Average $ volume": avg_dollar_volume, "Day %": float((c / prev - 1) * 100), "Trader score": f"{trader}/5", "Omni score": f"{omni}/4", "20d momentum %": float(momentum), "Rel. volume": float(relvol), "ADR %": float(adr), "Trigger": float(prior_high), "Reference stop": stop, "Above 21/50d": "Yes" if trend_ok else "No", "Breakout": "Yes" if breakout else "No", "Volume confirms": "Yes" if volume_ok else "No"}

with st.sidebar:
    st.header("Scan settings")
    capital = st.number_input("Account size (USD)", min_value=100, value=1000, step=100)
    risk_pct = st.slider("Risk per trade (%)", 0.25, 2.0, 0.5, 0.25)
    min_price = st.number_input("Minimum share price", min_value=0.0, value=5.0, step=1.0)
    min_dollar_vol = st.number_input("Minimum average daily $ volume (M)", min_value=0.0, value=10.0, step=5.0)
    universe_name = st.selectbox("Stock universe", ["All US exchange listings", "SPY holdings (S&P 500)", "My watchlist"], index=1)
    watchlist_text = "NVDA AMD TSLA PLTR SMCI AVGO MU ARM CRWD NET SNOW DDOG SHOP COIN HOOD SOFI AFRM UPST RBLX APP RKLB IONQ HIMS CELH ELF ONON DKNG ROKU TTD MELI SE NU MSTR MARA RIOT AXON CAVA ANF DECK UBER ABNB DASH ENPH FSLR RIVN CVNA OPEN SOUN RGTI U W LULU"
    if universe_name == "My watchlist":
        watchlist_text = st.text_area("Tickers (spaces or commas are okay)", value=watchlist_text, height=110)
    limit = st.select_slider("Symbols to scan", options=[100, 250, 500, 1000], value=500)
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
    st.caption(f"Universe: {universe_name} · Source: {source_note} · {len(universe):,} symbols received. Scanning up to {limit:,}; large universes are sampled evenly to stay within the free app's resource limits. Price history availability depends on Yahoo Finance.")
    if len(universe) <= limit:
        symbols = universe
    else:
        # Sample across the full sorted universe instead of silently scanning
        # only tickers near the beginning of the alphabet.
        positions = [round(i * (len(universe) - 1) / (limit - 1)) for i in range(limit)]
        symbols = [universe[position] for position in positions]
    with st.spinner(f"Downloading daily history for {len(symbols):,} symbols…"):
        price_map = get_prices(symbols)
    rows = [score_frame(s, d) for s, d in price_map.items()]
    rows = [r for r in rows if r and r["Price"] >= min_price]
    results = pd.DataFrame(rows)
    paper_results = results.copy()
    if not results.empty:
        results = results.sort_values(["Trader score", "Omni score"], ascending=False).reset_index(drop=True)
        risk_usd = capital * risk_pct / 100
        results["Risk-sized shares"] = ((risk_usd / (results["Price"] - results["Reference stop"]).clip(lower=.01))).astype(int)
        results["Estimated position $"] = results["Risk-sized shares"] * results["Price"]
        results = results[(results["Estimated position $"] <= capital) & (results["Risk-sized shares"] > 0)]
        results = results[results["Average $ volume"] >= min_dollar_vol * 1_000_000].copy()
    else:
        st.warning("No daily price histories were returned. Try refreshing, or use a smaller symbol limit.")
except Exception as exc:
    universe = []; price_map = {}; results = pd.DataFrame(); paper_results = pd.DataFrame()
    st.error(f"Market data could not be loaded: {exc}")
    st.info("The dashboard needs an internet connection. It uses Nasdaq Trader listings, State Street SPY holdings, and Yahoo Finance prices.")

if not results.empty:
    a, b, c = st.columns(3)
    a.metric("Symbols with data", f"{len(price_map):,}")
    b.metric("Price/volume-qualified", f"{len(results):,}")
    c.metric("Passing breakout + volume", f"{((results['Breakout'] == 'Yes') & (results['Volume confirms'] == 'Yes')).sum():,}")
    st.caption("Read the boxes left to right: symbols with data have price history; price/volume-qualified pass your sidebar filters; passing breakout + volume also need a 20-day-high breakout and today's volume at least 1.5× its recent average. A zero means none meet both checks today.")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["1 · Trader breakout", "2 · Nirvana Omni inspired", "3 · Birbia inspired", "4 · Stock lookup", "5 · Options income scan", "6 · $100 paper challenge"])
with tab1:
    st.subheader("Trader breakout rules")
    st.caption("A transparent approximation of the rules described in your pasted notes: trend, recent range breakout, momentum, and volume. It is not a reconstruction of a named trader's full system.")
    st.caption("Numbers: Day % and 20d momentum % are price changes; Rel. volume 1.00× means volume equal to its recent average; ADR % is average daily price range. Scroll the table sideways to see every column.")
    if not results.empty:
        show = results[results["Trader score"].str[0].astype(int) >= 3].sort_values(["Breakout", "Trader score"], ascending=False)
        columns = {"Price": st.column_config.NumberColumn(format="$%.2f"), "Day %": st.column_config.NumberColumn(format="%.2f%%"), "20d momentum %": st.column_config.NumberColumn(format="%.2f%%"), "Rel. volume": st.column_config.NumberColumn(format="%.2fx"), "ADR %": st.column_config.NumberColumn(format="%.2f%%"), "Trigger": st.column_config.NumberColumn(format="$%.2f"), "Reference stop": st.column_config.NumberColumn(format="$%.2f"), "Estimated position $": st.column_config.NumberColumn(format="$%.2f")}
        passed = results[(results["Breakout"] == "Yes") & (results["Volume confirms"] == "Yes")].sort_values(["Trader score", "Rel. volume"], ascending=False)
        st.markdown("**Stocks that passed both checks today**")
        st.caption("These symbols passed the 20-day breakout and volume checks, plus your sidebar filters. They are research candidates, not buy instructions.")
        if passed.empty:
            st.info("No stocks passed both breakout and volume checks today.")
        else:
            quick_columns = ["Symbol", "Price", "Day %", "Trader score", "Rel. volume", "Trigger", "Reference stop"]
            st.dataframe(passed[quick_columns], hide_index=True, use_container_width=True, column_config=columns)
        st.dataframe(show, hide_index=True, use_container_width=True, column_config=columns)
        st.download_button("Download full scan CSV", results.to_csv(index=False), "market_scan.csv", "text/csv")
with tab2:
    st.subheader("Nirvana OmniTrader · public Power Move approximation")
    st.caption("Nirvana publicly describes Power Move as a breakout approach where price, momentum, and volume agree. The proprietary signal logic and licensed indicators are not reproduced here.")
    if not results.empty:
        omni = results[results["Omni score"].str[0].astype(int) >= 3].sort_values(["Omni score", "Rel. volume"], ascending=False)
        st.dataframe(omni, hide_index=True, use_container_width=True, column_config=columns)
with tab3:
    st.subheader("Birbia-inspired research")
    st.caption("Birbia's public page describes AI stock analysis, short- and long-term trade ideas, options-income strategies, and a trade journal. Its exact private scoring rules are not public. This tab uses the dashboard's visible price/volume checks; it is not Birbia's AI or trade advice.")
    if not results.empty:
        long_term = results[(results["Above 21/50d"] == "Yes") & (results["20d momentum %"] > 0)].sort_values(["20d momentum %", "Trader score"], ascending=False)
        short_term = results[(results["Breakout"] == "Yes") & (results["Volume confirms"] == "Yes")].sort_values("Rel. volume", ascending=False)
        long_col, short_col = st.columns(2)
        with long_col:
            st.markdown("**Longer-term trend examples**")
            st.caption("Above the 21- and 50-day averages, with positive 20-day momentum.")
            st.dataframe(long_term.head(15), hide_index=True, use_container_width=True, column_config=columns)
        with short_col:
            st.markdown("**Short-term breakout examples**")
            st.caption("Above the previous 20-day high, with volume at least 1.5× its recent average.")
            st.dataframe(short_term.head(15), hide_index=True, use_container_width=True, column_config=columns)
    else:
        st.info("The stock scan did not return rows. Check the market-data message above or choose a smaller universe.")
    with st.expander("Options-income strategies Birbia mentions"):
        st.write("A cash-secured put means setting aside enough cash to buy 100 shares if assigned. A covered call means owning 100 shares before selling a call. These strategies can lose money, and the dashboard does not fetch or evaluate option-chain prices, so it does not suggest specific contracts.")
    with st.expander("Simple trade journal"):
        journal_seed = pd.DataFrame([{"Ticker": "", "Date": "", "Plan / strategy": "", "Entry $": None, "Exit $": None, "Shares": None, "Notes": ""}])
        journal = st.data_editor(journal_seed, num_rows="dynamic", hide_index=True, use_container_width=True, key="birbia_journal")
        st.download_button("Download journal CSV", journal.to_csv(index=False), "trade_journal.csv", "text/csv", key="download_birbia_journal")
        st.caption("Download your journal to keep a copy. Entries stay in this browser session and are not saved to a permanent database.")
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

with tab5:
    st.subheader("Options income scan")
    st.caption("Automatically screen the stocks already checked by the dashboard, then compare liquid cash-secured puts and covered calls. The scan prioritizes contracts that fit your account and ranks them by modeled profit probability, then premium yield. A standard equity option contract represents 100 shares.")
    st.warning("Option selling can lose substantially more than the premium collected. A cash-secured put can require buying 100 shares; a covered call requires owning 100 shares. This tool is educational and does not place trades.")
    with st.form("options_scan_form"):
        scan_strategies = st.selectbox("Strategies to compare", ["Both", "Cash-secured puts", "Covered calls"])
        dte_range = st.slider("Days until expiration", min_value=7, max_value=90, value=(14, 45))
        underlyings_to_scan = st.select_slider("Liquid stocks to inspect", options=[10, 20, 30, 50], value=20)
        minimum_oi = st.number_input("Minimum open interest per contract", min_value=0, value=100, step=50)
        maximum_spread = st.slider("Maximum bid/ask spread (%)", min_value=5, max_value=50, value=20, step=5)
        run_options_scan = st.form_submit_button("Find options income candidates", type="primary")
    if run_options_scan:
        if results.empty:
            st.session_state["option_scan_rows"] = pd.DataFrame()
            st.session_state["option_scan_note"] = "No screened stocks are available. Wait for the stock scan to finish or lower its price/volume requirements."
        else:
            # Pre-screen the stock universe before requesting option chains. Underlyings
            # are sorted by recent dollar volume and roughly constrained by 100-share
            # collateral, which avoids attempting thousands of unsupported bulk calls.
            max_spot = capital / 100 * (1.5 if scan_strategies != "Covered calls" else 1.0)
            underlyings = results[(results["Price"] <= max_spot) & (results["Average $ volume"] >= min_dollar_vol * 1_000_000)]
            underlyings = underlyings.sort_values("Average $ volume", ascending=False).head(underlyings_to_scan)
            candidate_frames = []
            checked, failures = 0, 0
            progress = st.progress(0, text="Finding eligible option chains…")
            for i, (_, stock) in enumerate(underlyings.iterrows(), start=1):
                symbol = stock["Symbol"]
                try:
                    expirations = get_option_expirations(symbol)
                    eligible_expirations = []
                    for expiry in expirations:
                        days = (pd.Timestamp(expiry).date() - date.today()).days
                        if dte_range[0] <= days <= dte_range[1]:
                            eligible_expirations.append((expiry, days))
                    center = sum(dte_range) / 2
                    eligible_expirations = sorted(eligible_expirations, key=lambda item: abs(item[1] - center))[:2]
                    for expiry, _ in eligible_expirations:
                        calls, puts = get_option_chain(symbol, expiry)
                        if scan_strategies in ("Both", "Cash-secured puts"):
                            frame = option_candidates(puts, "Cash-secured put", float(stock["Price"]), expiry, capital, minimum_oi, maximum_spread)
                            if not frame.empty:
                                frame.insert(0, "Symbol", symbol); frame.insert(1, "Stock price $", float(stock["Price"]))
                                candidate_frames.append(frame)
                        if scan_strategies in ("Both", "Covered calls"):
                            frame = option_candidates(calls, "Covered call", float(stock["Price"]), expiry, capital, minimum_oi, maximum_spread)
                            if not frame.empty:
                                frame.insert(0, "Symbol", symbol); frame.insert(1, "Stock price $", float(stock["Price"]))
                                candidate_frames.append(frame)
                    checked += 1
                except Exception:
                    failures += 1
                progress.progress(i / max(len(underlyings), 1), text=f"Checking option chains: {i} of {len(underlyings)} stocks")
            progress.empty()
            option_rows = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
            if not option_rows.empty:
                option_rows = option_rows[option_rows["Cash / shares needed $"] <= capital].copy()
                option_rows["Premium / collateral %"] = option_rows["Premium / contract $"] / option_rows["Cash / shares needed $"] * 100
                option_rows = option_rows.sort_values(["Est. profit probability %", "Premium / collateral %"], ascending=False).head(15).reset_index(drop=True)
            st.session_state["option_scan_rows"] = option_rows
            st.session_state["option_scan_note"] = f"Checked {checked} liquid, price-screened stocks; {failures} option-chain requests failed or were unavailable. This is a shortlist, not a scan of every listed option contract."
    if "option_scan_rows" in st.session_state:
        st.caption(st.session_state.get("option_scan_note", ""))
        option_rows = st.session_state["option_scan_rows"]
        if option_rows.empty:
            st.info("No contracts passed the quote-quality, open-interest, expiration, strategy, and account-size filters. Try a larger account size, wider expiration window, or smaller minimum open interest.")
        else:
            st.markdown("**Top candidates, sorted by estimated profit probability and then premium yield**")
            st.dataframe(option_rows, hide_index=True, use_container_width=True, column_config={
                "Stock price $": st.column_config.NumberColumn(format="$%.2f"),
                "Strike $": st.column_config.NumberColumn(format="$%.2f"),
                "Bid $": st.column_config.NumberColumn(format="$%.2f"),
                "Ask $": st.column_config.NumberColumn(format="$%.2f"),
                "Mid premium $/share": st.column_config.NumberColumn(format="$%.2f"),
                "Premium / contract $": st.column_config.NumberColumn(format="$%.2f"),
                "Break-even $": st.column_config.NumberColumn(format="$%.2f"),
                "Cash / shares needed $": st.column_config.NumberColumn(format="$%.2f"),
                "Max gain $": st.column_config.NumberColumn(format="$%.2f"),
                "Max loss $": st.column_config.NumberColumn(format="$%.2f"),
                "Est. profit probability %": st.column_config.NumberColumn(format="%.1f%%"),
                "Premium / collateral %": st.column_config.NumberColumn(format="%.2f%%"),
                "Annualized premium / collateral %": st.column_config.NumberColumn(format="%.1f%%"),
                "OTM distance %": st.column_config.NumberColumn(format="%.2f%%"),
                "IV %": st.column_config.NumberColumn(format="%.1f%%"),
                "Bid-ask spread %": st.column_config.NumberColumn(format="%.1f%%"),
                "Reward / max loss": st.column_config.NumberColumn(format="%.3f"),
            })
            st.download_button("Download options scan CSV", option_rows.to_csv(index=False), "options_income_candidates.csv", "text/csv")
    with st.expander("How probability, reward, and risk are calculated"):
        st.write("Profit probability is an estimate from the option's implied volatility using a simple lognormal expiration-price model with zero rates and dividends. It is not a guaranteed chance or a backtested win rate; actual returns can differ because prices move, volatility changes, quotes may be delayed, and execution costs/early assignment are not modeled. Public options educators describe Delta as a probability proxy, but this dashboard uses a separate breakeven-based model.")
        st.write("Premium uses the bid/ask midpoint, which may not be an executable fill. Premium yield is the midpoint per contract divided by the cash or shares required. Annualized yield simply scales that one-cycle yield by 365 ÷ days to expiration; it assumes the same yield could repeat and is not a forecast. Maximum loss assumes the stock can fall to zero. Contracts are ranked by modeled profit probability, then one-cycle premium yield—not by a hidden AI score.")
        st.markdown("Learn more: [Cash-secured puts](https://www.optionseducation.org/strategies/all-strategies/cash-secured-put) · [Covered calls](https://www.optionseducation.org/strategies/all-strategies/covered-call-buy-write) · [Options probability calculator](https://www.optionseducation.org/Options-Quotes-Calculators) · [OCC options risk disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document)")

with tab6:
    st.subheader("$100 to $1,000 · free paper-trading challenge")
    st.warning("Paper trading only. This agent cannot access a brokerage or place real orders. It cannot promise or guarantee a 10× return.")
    st.caption("A free, rule-based GitHub Action scans SPY holdings after US market days. It looks for a 20-day breakout, confirming volume, and a Trader score of at least 4/5. Signals are filled at the next available daily close, with up to three fractional-share positions and a trailing reference stop. It compares the result with buying SPY. This is an experiment, not a validated strategy or an AI that rewrites trading rules.")
    st.link_button("Open the paper-agent runs", "https://github.com/deantil/three-lens-market-dashboard/actions/workflows/paper-challenge.yml")
    try:
        agent_state = get_paper_state()
        history = agent_state.get("equity_history", [])
        if not history:
            st.info("The free paper agent has not completed its first scan yet. Use the link above and choose **Run workflow** to start it, or wait for its next scheduled run.")
        else:
            latest = history[-1]
            equity = float(latest.get("equity", agent_state.get("cash", 100)))
            target = float(agent_state.get("target_balance", 1000))
            start = float(agent_state.get("starting_cash", 100))
            progress = max(0.0, min(1.0, (equity - start) / max(target - start, 1)))
            m1, m2, m3 = st.columns(3)
            m1.metric("Paper balance", f"${equity:,.2f}", f"{equity - start:+,.2f} vs. start")
            m2.metric("Cash", f"${float(agent_state.get('cash', 0)):,.2f}")
            m3.metric("SPY comparison", f"${float(latest.get('benchmark', start)):,.2f}")
            st.progress(progress)
            st.caption(f"Target: ${target:,.0f} · Latest daily bar: {agent_state.get('last_bar_date') or 'waiting'} · Last agent run (UTC): {agent_state.get('last_run_utc') or 'not run yet'} · State is public in this repository.")

            if history:
                history_frame = pd.DataFrame(history)
                if {"date", "equity", "benchmark"}.issubset(history_frame.columns):
                    st.line_chart(history_frame.set_index("date")[["equity", "benchmark"]], height=260)
            if agent_state.get("positions"):
                st.markdown("**Open paper positions**")
                st.dataframe(pd.DataFrame(agent_state["positions"]), hide_index=True, use_container_width=True)
            else:
                st.info("No open paper positions. The agent waits when no stocks pass its rules.")
            if agent_state.get("pending"):
                st.markdown("**Signals waiting for the next daily close**")
                st.dataframe(pd.DataFrame(agent_state["pending"]), hide_index=True, use_container_width=True)
            if agent_state.get("trades"):
                st.markdown("**Recent paper trades**")
                st.dataframe(pd.DataFrame(agent_state["trades"][-20:]).iloc[::-1], hide_index=True, use_container_width=True)
            st.download_button("Download paper challenge history", json.dumps(agent_state, indent=2), "paper_challenge_state.json", "application/json", key="agent_state_download")
    except Exception as exc:
        st.error(f"Could not load the latest paper-agent report: {exc}")
    st.caption("This free agent runs once after the US stock market closes on weekdays; it is not a 24/7 quote feed or an AI software engineer. GitHub's scheduled runs can be delayed. Data can be stale or unavailable. Simulated fills ignore commissions, spread, taxes, and slippage; real stop orders can fill worse during price gaps.")


with st.expander("How sizing and the goal tracker work"):
    st.write(f"Risk budget per trade is ${capital * risk_pct / 100:,.2f} at the selected {risk_pct:.2f}% account risk. Reference stops use the lower of the recent 10-day low or a 6% reference distance. Share counts are capped so the position value does not exceed the account balance. Actual fills, gaps, fees, and losses can differ substantially.")
    st.write("A $1,000 to $10,000 year-end target requires an exceptionally high compounded return, especially late in the year. The dashboard shows the arithmetic pace only; it does not predict or promise that outcome.")

