from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO, StringIO
import json
import math
import requests
import pandas as pd
import streamlit as st
import yfinance as yf
import manual_replication
import earnings_condors
import trader_tracker
import real_estate
import kotegawa_scan
import intraday_actions
import dual_signal_scan
import credit_spread_scanner
import kotegawa_options_scanner

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
    data = yf.download(symbols, period=period, interval="1d", auto_adjust=True, group_by="ticker", progress=False, threads=False)
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



def calculate_paper_performance(history, trades):
    """Summarize realized account observations without presenting them as a forecast."""
    versioned_rows = [row for row in history if row.get("strategy_version")]
    active_version = versioned_rows[-1].get("strategy_version") if versioned_rows else None
    valid_rows = [
        row for row in history
        if as_number(row.get("equity")) > 0
        and (active_version is None or row.get("strategy_version") == active_version)
    ]
    trades = [
        trade for trade in trades
        if active_version is None or trade.get("strategy_version") == active_version
    ]

    def total_return(column):
        values = [as_number(row.get(column)) for row in valid_rows]
        values = [value for value in values if value > 0]
        return (values[-1] / values[0] - 1) * 100 if len(values) >= 2 else None

    def max_drawdown(column):
        values = [as_number(row.get(column)) for row in valid_rows]
        values = [value for value in values if value > 0]
        if len(values) < 2:
            return None
        peak = values[0]
        worst = 0.0
        for value in values:
            peak = max(peak, value)
            worst = min(worst, value / peak - 1)
        return worst * 100

    lots = {}
    closed_trade_pnl = []
    for trade in trades:
        action = str(trade.get("action", "")).upper()
        symbol = str(trade.get("symbol", ""))
        qty = max(0.0, as_number(trade.get("shares")))
        price = max(0.0, as_number(trade.get("price")))
        if not symbol or qty <= 0 or price <= 0:
            continue
        if action == "BUY":
            lots.setdefault(symbol, []).append([qty, price])
        elif action == "SELL":
            remaining = qty
            realized = 0.0
            matched = 0.0
            for lot in lots.get(symbol, []):
                portion = min(remaining, lot[0])
                if portion > 0:
                    realized += portion * (price - lot[1]) * as_number(trade.get("multiplier", 1))
                    lot[0] -= portion
                    remaining -= portion
                    matched += portion
                if remaining <= 1e-8:
                    break
            lots[symbol] = [lot for lot in lots.get(symbol, []) if lot[0] > 1e-8]
            if matched > 0 and remaining <= 1e-8:
                closed_trade_pnl.append(realized)

    win_rate = (sum(pnl > 0 for pnl in closed_trade_pnl) / len(closed_trade_pnl) * 100
                if closed_trade_pnl else None)
    return {
        "observations": len(valid_rows),
        "agent_return_pct": total_return("equity"),
        "spy_return_pct": total_return("benchmark"),
        "agent_max_drawdown_pct": max_drawdown("equity"),
        "spy_max_drawdown_pct": max_drawdown("benchmark"),
        "closed_trade_count": len(closed_trade_pnl),
        "closed_trade_win_rate_pct": win_rate,
    }

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
    capital = st.number_input("Account size (USD)", min_value=100, value=300, step=50)
    risk_pct = st.slider("Risk per trade (%)", 0.25, 2.0, 0.5, 0.25)
    st.caption(f"Planned risk at this setting: ${capital * risk_pct / 100:,.2f} per trade. This is a sizing estimate, not a guaranteed maximum loss.")
    min_price = st.number_input("Minimum share price", min_value=0.0, value=5.0, step=1.0)
    min_dollar_vol = st.number_input("Minimum average daily $ volume (M)", min_value=0.0, value=10.0, step=5.0)
    universe_name = st.selectbox("Stock universe", ["All US exchange listings", "SPY holdings (S&P 500)", "My watchlist"])
    watchlist_text = "NVDA AMD TSLA PLTR SMCI AVGO MU ARM CRWD NET SNOW DDOG SHOP COIN HOOD SOFI AFRM UPST RBLX APP RKLB IONQ HIMS CELH ELF ONON DKNG ROKU TTD MELI SE NU MSTR MARA RIOT AXON CAVA ANF DECK UBER ABNB DASH ENPH FSLR RIVN CVNA OPEN SOUN RGTI U W LULU"
    if universe_name == "My watchlist":
        watchlist_text = st.text_area("Tickers (spaces or commas are okay)", value=watchlist_text, height=110)
    limit = st.select_slider("Symbols to scan", options=[100, 250, 500, 1000, 2500, 5000, 10000], value=10000)
    if st.button("Scan selected stock universe", type="primary", use_container_width=True):
        st.session_state["market_scan_requested"] = True
    if st.button("Refresh universe and prices", use_container_width=True):
        get_universe.clear(); get_prices.clear()
        st.rerun()
    st.divider()
    st.subheader("Year-end goal tracker")
    goal = st.number_input("Target balance (USD)", min_value=capital, value=max(10000, capital), step=500)
    days_left = max((date(date.today().year, 12, 31) - date.today()).days, 1)
    req_daily = ((goal / capital) ** (1 / days_left) - 1) * 100 if capital else 0
    st.metric("Daily compounded return needed", f"{req_daily:.2f}%", help=f"Mathematical pace for {days_left} calendar days. This is not a forecast or a realistic expectation.")

if not st.session_state.get("market_scan_requested", False):
    universe = []; price_map = {}; results = pd.DataFrame(); paper_results = pd.DataFrame()
    st.info("The dashboard is ready. Click **Scan selected stock universe** in the left sidebar to load market data. Stock Lookup and the paper challenge are available without scanning the full market.")
else:
    try:
        with st.spinner("Loading listed-stock universe…"):
            universe = get_universe(universe_name, watchlist_text)
        source_note = "Nasdaq and other US exchanges" if universe_name == "All US exchange listings" else "State Street SPY fund holdings" if universe_name == "SPY holdings (S&P 500)" else "your typed tickers"
        st.caption(f"Universe: {universe_name} · Source: {source_note} · {len(universe):,} symbols received. Scanning up to {limit:,}; set the slider to 10,000 to attempt every symbol in this universe. Some symbols may have no usable Yahoo Finance price history.")
        symbols = universe[:limit]
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
            results["Quality"] = results.apply(lambda r: "A · TECHNICAL ONLY" if r["Price"] >= 5 and r["Average $ volume"] >= 20000000 and abs(r["Day %"]) < 20 else ("B · REVIEW" if r["Price"] >= 5 and r["Average $ volume"] >= 10000000 else "C · EXCLUDE"), axis=1)
            results = results[results["Quality"] != "C · EXCLUDE"].copy()
            results["Full analysis"] = results["Symbol"].map(lambda t: "https://three-lens-market-dashboard.streamlit.app/stock_research?symbol=" + t)
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

tab1, tab2, tab6, tab7, credit_tab = st.tabs(["1 · Trader breakout", "2 · Nirvana Omni inspired", "3 · Kotegawa Mean Reversion", "4 · Intraday Buy / Sell", "5 · Credit Spread Scanner"])
with credit_tab:
    credit_spread_scanner.render()
with tab6:
    kotegawa_options_scanner.render()
    with st.expander('Existing stock discovery and recorded-position tools'):
        dual_signal_scan.render("tab11", universe_name, watchlist_text, get_universe, intraday=False)

with tab7:
    dual_signal_scan.render("tab12", universe_name, watchlist_text, get_universe, intraday=True)

with tab1:
    st.subheader("Trader breakout rules")
    st.caption("Only A/B technical quality stocks are shown. Click Full analysis for company context, risk factors, moving averages and watchpoints.")
    st.caption("A transparent approximation of the rules described in your pasted notes: trend, recent range breakout, momentum, and volume. It is not a reconstruction of a named trader's full system.")
    st.caption("Numbers: Day % and 20d momentum % are price changes; Rel. volume 1.00× means volume equal to its recent average; ADR % is average daily price range. Scroll the table sideways to see every column.")
    if not results.empty:
        show = results[results["Trader score"].str[0].astype(int) >= 3].sort_values(["Breakout", "Trader score"], ascending=False)
        columns = {"Full analysis": st.column_config.LinkColumn("Full analysis", display_text="Open research"), "Price": st.column_config.NumberColumn(format="$%.2f"), "Day %": st.column_config.NumberColumn(format="%.2f%%"), "20d momentum %": st.column_config.NumberColumn(format="%.2f%%"), "Rel. volume": st.column_config.NumberColumn(format="%.2fx"), "ADR %": st.column_config.NumberColumn(format="%.2f%%"), "Trigger": st.column_config.NumberColumn(format="$%.2f"), "Reference stop": st.column_config.NumberColumn(format="$%.2f"), "Estimated position $": st.column_config.NumberColumn(format="$%.2f")}
        passed = results[(results["Breakout"] == "Yes") & (results["Volume confirms"] == "Yes")].sort_values(["Trader score", "Rel. volume"], ascending=False)
        st.markdown("**Stocks that passed both checks today**")
        st.caption("These symbols passed the 20-day breakout and volume checks, plus your sidebar filters. They are research candidates, not buy instructions.")
        if passed.empty:
            st.info("No stocks passed both breakout and volume checks today.")
        else:
            quick_columns = ["Symbol", "Full analysis", "Quality", "Price", "Day %", "Trader score", "Rel. volume", "Trigger", "Reference stop"]
            st.dataframe(passed[quick_columns], hide_index=True, use_container_width=True, column_config=columns)
        st.dataframe(show, hide_index=True, use_container_width=True, column_config=columns)
        st.download_button("Download full scan CSV", results.to_csv(index=False), "market_scan.csv", "text/csv")
with tab2:
    st.subheader("Nirvana OmniTrader · public Power Move approximation")
    st.caption("Only A/B technical quality stocks are shown. Click Full analysis for a detailed research report.")
    st.caption("Nirvana publicly describes Power Move as a breakout approach where price, momentum, and volume agree. The proprietary signal logic and licensed indicators are not reproduced here.")
    if not results.empty:
        columns = {"Full analysis": st.column_config.LinkColumn("Full analysis", display_text="Open research")}
        omni = results[results["Omni score"].str[0].astype(int) >= 3].sort_values(["Omni score", "Rel. volume"], ascending=False)
        st.dataframe(omni, hide_index=True, use_container_width=True, column_config=columns)

