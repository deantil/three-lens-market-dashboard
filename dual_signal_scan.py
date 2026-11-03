"""Fast bounded research scanner for both discovery tabs."""
import pandas as pd
import streamlit as st
import yfinance as yf

@st.cache_data(ttl=120, show_spinner=False)
def fetch_batch(symbols, intraday):
    if not symbols:
        return {}
    daily = yf.download(list(symbols), period="3mo", interval="1d", group_by="ticker",
                        auto_adjust=True, progress=False, threads=True, timeout=8)
    latest = {}
    if intraday:
        try:
            bars = yf.download(list(symbols), period="1d", interval="5m", group_by="ticker",
                               auto_adjust=True, progress=False, threads=True, timeout=8)
            for symbol in symbols:
                try:
                    x = bars[symbol] if isinstance(bars.columns, pd.MultiIndex) else bars
                    x = x.dropna(subset=["Close"])
                    if len(x):
                        latest[symbol] = (float(x["Close"].iloc[-1]), str(x.index[-1]))
                except (KeyError, ValueError, TypeError):
                    continue
        except Exception:
            pass
    out = {}
    for symbol in symbols:
        try:
            x = daily[symbol] if isinstance(daily.columns, pd.MultiIndex) else daily
            x = x.dropna(subset=["Close"])
            if len(x) < 26:
                continue
            # Completed 25 daily bars; for intraday, exclude today's daily candle.
            completed = x
            if intraday and pd.Timestamp(x.index[-1]).date() == pd.Timestamp.now(tz="America/New_York").date():
                completed = x.iloc[:-1]
            if len(completed) < 25:
                continue
            sma = float(completed["Close"].tail(25).mean())
            price, stamp = latest.get(symbol, (float(x["Close"].iloc[-1]), str(x.index[-1])))
            if price <= 0 or sma <= 0:
                continue
            out[symbol] = {"Ticker":symbol, "Price $":round(price,2), "SMA25 $":round(sma,2),
                           "Deviation %":round((price/sma-1)*100,2),
                           "Price timestamp":stamp, "Source":"5m snapshot" if symbol in latest else "daily close"}
        except (KeyError, ValueError, TypeError):
            continue
    return out

def render(tab_id, universe_name, watchlist_text, get_universe, intraday=False):
    st.header("Kotegawa · BUY and SELL Discovery" if not intraday else "Intraday · BUY and SELL Discovery")
    st.caption("BUY = below SMA25 by selected threshold. SELL WATCH = above SMA25 by selected threshold (potential mean-reversion/overextension, NOT a sell order or short recommendation). No account-size filter.")
    a,b,c = st.columns(3)
    buy_threshold = a.number_input("BUY: % below SMA25", min_value=0.1, value=20.0, step=1.0, key=f"{tab_id}_buy")
    sell_threshold = b.number_input("SELL WATCH: % above SMA25", min_value=0.1, value=10.0, step=1.0, key=f"{tab_id}_sell")
    count = c.selectbox("Max symbols per scan", [20, 40, 80, 150], index=1, key=f"{tab_id}_count")
    st.caption("Start with 20–40 symbols for speed. A scan cannot guarantee a match; adjust thresholds to explore more names.")
    if st.button("Refresh BUY and SELL lists", type="primary", key=f"{tab_id}_run"):
        st.session_state[f"{tab_id}_requested"] = True
    if not st.session_state.get(f"{tab_id}_requested"):
        st.info("Click Refresh BUY and SELL lists to scan the selected sidebar universe.")
        return
    try:
        with st.spinner(f"Fetching latest available prices for up to {count} symbols…"):
            symbols = get_universe(universe_name, watchlist_text)[:count]
            results = {}
            failures = []
            progress = st.progress(0)
            for i in range(0,len(symbols),10):
                batch = symbols[i:i+10]
                try:
                    results.update(fetch_batch(tuple(batch), intraday))
                except Exception as e:
                    failures.append(str(e)[:120])
                progress.progress(min(1.0,(i+len(batch))/len(symbols)))
            progress.empty()
        rows = list(results.values())
        buy = pd.DataFrame([dict(r, Signal="BUY WATCH") for r in rows if r["Deviation %"] <= -buy_threshold])
        sell = pd.DataFrame([dict(r, Signal="SELL WATCH") for r in rows if r["Deviation %"] >= sell_threshold])
        st.caption(f"Scanned {len(symbols)} requested tickers · {len(rows)} usable price histories · {len(failures)} failed batches. Market data can be delayed; check each timestamp.")
        left,right=st.columns(2)
        with left:
            st.subheader(f"BUY watchlist ({len(buy)})")
            if buy.empty: st.info("No stocks currently meet the BUY threshold in this sample.")
            else:
                buy=buy.sort_values("Deviation %")
                st.dataframe(buy, hide_index=True, use_container_width=True)
                st.download_button("Download BUY CSV", buy.to_csv(index=False), f"{tab_id}_buy.csv","text/csv",key=f"{tab_id}_buy_csv")
        with right:
            st.subheader(f"SELL watchlist ({len(sell)})")
            if sell.empty: st.info("No stocks currently meet the SELL threshold in this sample.")
            else:
                sell=sell.sort_values("Deviation %",ascending=False)
                st.dataframe(sell, hide_index=True, use_container_width=True)
                st.download_button("Download SELL CSV",sell.to_csv(index=False),f"{tab_id}_sell.csv","text/csv",key=f"{tab_id}_sell_csv")
        if failures:
            st.warning("Some data batches failed; results are incomplete. Retry with fewer symbols.")
        st.caption("SELL WATCH is a price-screening condition only. It does not indicate you hold the stock or should sell short. Intraday quotes may be delayed; if 5-minute data is missing, daily close is explicitly labeled. No orders are placed.")
    except Exception as e:
        st.error(f"Scan failed: {e}")
