import gzip
import json
import requests
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
            previous_close = float(completed["Close"].iloc[-1]) if intraday else float(completed["Close"].iloc[-2])
            closing_price = float(completed["Close"].iloc[-1])
            day_change = (price / previous_close - 1) * 100 if previous_close > 0 else None
            recent_low = float(completed["Low"].tail(5).min())
            stop = min(recent_low, price) * 0.99
            avg_dollar = float((completed["Close"].tail(20) * completed["Volume"].tail(20)).mean())
            daily_returns = completed["Close"].pct_change().dropna()
            rsi_delta = completed["Close"].diff()
            up = rsi_delta.clip(lower=0).tail(14).mean()
            down = -rsi_delta.clip(upper=0).tail(14).mean()
            rsi = 100.0 if down == 0 else 100 - 100 / (1 + up / down)
            out[symbol] = {"Entry reference $":round(price,2), "Sell target $":round(sma,2),
                           "Stop reference $":round(stop,2),
                           "Potential return %":round((sma / price - 1)*100,2),
                           "Reward/risk":round((sma-price)/(price-stop),2) if price>stop else None,
                           "Avg daily $ volume":round(avg_dollar),
                           "RSI14":round(rsi,1),
                           "Liquidity proxy":"PASS" if avg_dollar >= 10_000_000 else "LOW",
                           "Ticker":symbol, "Price $":round(price,2), "Closing price $":round(closing_price,2), "Previous close $":round(previous_close,2), "Day change %":round(day_change,2) if day_change is not None else None, "SMA25 $":round(sma,2),
                           "Deviation %":round((price/sma-1)*100,2),
                           "Price timestamp":stamp, "Source":"5m snapshot" if symbol in latest else "daily close"}
        except (KeyError, ValueError, TypeError):
            continue
    return out

@st.cache_data(ttl=300, show_spinner=False)
def load_full_snapshot():
    url="https://raw.githubusercontent.com/deantil/three-lens-market-dashboard/main/data/full_universe_daily.json.gz"
    response=requests.get(url,timeout=20)
    response.raise_for_status()
    return json.loads(gzip.decompress(response.content).decode("utf-8"))

def render(tab_id, universe_name, watchlist_text, get_universe, intraday=False):
    st.header("Kotegawa · BUY and SELL Discovery" if not intraday else "Intraday · BUY and SELL Discovery")
    st.caption("BUY = below SMA25 by selected threshold. SELL WATCH = above SMA25 by selected threshold (potential mean-reversion/overextension, NOT a sell order or short recommendation). No account-size filter.")
    a,b,c = st.columns(3)
    buy_threshold = a.number_input("BUY: % below SMA25", min_value=0.1, value=20.0, step=1.0, key=f"{tab_id}_buy")
    sell_threshold = b.number_input("SELL WATCH: % above SMA25", min_value=0.1, value=10.0, step=1.0, key=f"{tab_id}_sell")
    count = c.selectbox("Universe coverage", ["Full selected universe (background snapshot)", "Quick test: 40", "Quick test: 150", "Live full universe (slow)"], index=0, key=f"{tab_id}_count")
    st.caption("Stock quality proxy: $10M average daily dollar volume. BUY list excludes same-day gains above your selected threshold (10% default); this is an adjustable research heuristic, not a validated edge. Options-chain validation is separate in thinkorswim.")
    strict = True
    st.caption("C-grade stocks are always excluded from discovery lists.")
    max_rebound = st.number_input("Maximum same-day gain for BUY candidates (%)", min_value=0.0, max_value=100.0, value=10.0, step=1.0, key=f"{tab_id}_max_rebound")
    st.caption("SELL exits require a recorded buy entry. Enter ticker, actual buy price, and optional stop below (one position per line).")
    positions_text = st.text_area("My recorded stock entries (TICKER,BUY_PRICE,STOP_PRICE)",placeholder="AAPL,150,140",key=f"{tab_id}_entries")
    entries={}
    for line in positions_text.splitlines():
        fields=[x.strip() for x in line.split(",")]
        if not line.strip(): continue
        try:
            if len(fields) not in (2,3): raise ValueError()
            symbol=fields[0].upper()
            buy_price=float(fields[1])
            stop_price=float(fields[2]) if len(fields)==3 else None
            if not symbol.replace("-","").isalnum() or buy_price<=0 or (stop_price is not None and stop_price<=0): raise ValueError()
            entries[symbol]=(buy_price,stop_price)
        except ValueError:
            st.warning(f"Ignoring invalid entry: {line}")
    st.caption("Options-chain assessment is reserved for thinkorswim/Schwab; this dashboard does not claim to validate options liquidity.")
    if st.button("Refresh BUY and SELL lists", type="primary", key=f"{tab_id}_run"):
        st.session_state[f"{tab_id}_requested"] = True
        st.session_state.pop(f"{tab_id}_cached", None)
    if not st.session_state.get(f"{tab_id}_requested") and count != "Full selected universe (background snapshot)":
        st.info("Click Refresh BUY and SELL lists to scan the selected sidebar universe.")
        return
    try:
        if count == "Full selected universe (background snapshot)":
            try:
                snapshot=load_full_snapshot()
                st.session_state[f"{tab_id}_cached"]=(snapshot.get("rows",[]),snapshot.get("universe_size",0),snapshot.get("universe_size",0),len(snapshot.get("failed_symbols",[])))
                st.caption("Snapshot updated UTC: "+snapshot.get("generated_at_utc","unknown"))
                if intraday: st.warning("Full universe snapshot uses daily closing prices, not live five-minute bars.")
            except Exception:
                st.warning("Full universe snapshot not available until scheduled GitHub Actions scan completes. No partial list is shown as full-market coverage.")
                return
        if f"{tab_id}_cached" not in st.session_state:
            with st.spinner("Fetching stock histories for selected universe…"):
                universe = get_universe(universe_name, watchlist_text)
                limit = None if count == "Live full universe (slow)" else int(count.rsplit(" ",1)[-1])
                symbols = list(dict.fromkeys(list(entries) + (universe if limit is None else universe[:limit])))
                if not symbols:
                    st.warning("The selected universe contains no symbols.")
                    return
                results = {}
                failures = []
                progress = st.progress(0)
                for i in range(0,len(symbols),50):
                    batch = symbols[i:i+50]
                    try:
                        results.update(fetch_batch(tuple(batch), intraday))
                    except Exception as e:
                        failures.append(str(e)[:120])
                    progress.progress(min(1.0,(i+len(batch))/len(symbols)))
                progress.empty()
            st.session_state[f"{tab_id}_cached"] = (list(results.values()), len(symbols), len(universe), len(failures))
        rows, scanned_count, universe_count, failure_count = st.session_state[f"{tab_id}_cached"]
        rows = [dict(r) for r in rows]
        for r in rows:
            r["Research"] = "https://three-lens-market-dashboard.streamlit.app/stock_research?symbol=" + r["Ticker"]
        st.caption("Use the **Full analysis** link beside each ticker to open its dedicated research page in a new browser tab.")
        st.caption(f"Universe coverage: {scanned_count} of {universe_count} selected symbols requested; {len(rows)} returned usable data. Full coverage may be incomplete if data requests fail or time out.")
        for r in rows:
            reasons=[]
            if r["Price $"] < 5: reasons.append("Price below $5")
            if r["Avg daily $ volume"] < 20_000_000: reasons.append("Dollar volume below $20M")
            if r["Day change %"] is not None and abs(r["Day change %"]) >= 20: reasons.append("Extreme daily move")
            r["Quality"] = "A · TECHNICAL ONLY" if not reasons else ("B · REVIEW" if r["Price $"] >= 5 and r["Avg daily $ volume"] >= 10_000_000 else "C · EXCLUDE")
            r["Quality notes"] = "; ".join(reasons) if reasons else "Technical pass; news and options unverified"
        excluded_count=sum(r["Quality"].startswith("C") for r in rows)
        if strict: rows=[r for r in rows if not r["Quality"].startswith("C")]
        for r in rows:
            r["Options venue"]="thinkorswim — not connected"
        for r in rows:
            checks=[]
            checks.append("SMA25 deviation PASS" if r["Deviation %"] <= -buy_threshold else "SMA25 deviation FAIL")
            checks.append("Daily rebound PASS" if r.get("Day change %") is not None and r["Day change %"] <= max_rebound else "Daily rebound FAIL")
            checks.append("Liquidity PASS" if r["Price $"] >= 5 and r["Avg daily $ volume"] >= 10_000_000 else "Liquidity FAIL")
            r["Strategy qualification"] = " | ".join(checks)
        buy = pd.DataFrame([dict(r, Signal="BUY WATCH") for r in rows if r["Deviation %"] <= -buy_threshold and r.get("Day change %") is not None and r["Day change %"] <= max_rebound])
        st.subheader("Large daily declines — objective research")
        priority=[r for r in rows if r.get("Day change %") is not None and r["Day change %"]<=-15 and r["Price $"]>=5 and r["Avg daily $ volume"]>=10_000_000]
        if priority:
            st.dataframe(pd.DataFrame(priority)[["Ticker","Research","Price $","Previous close $","Day change %","Deviation %","Quality","Quality notes","Price timestamp"]],hide_index=True,use_container_width=True)
        else:
            st.info("No qualifying large daily declines in this scan.")
        a_candidates=buy[buy["Quality"]=="A · TECHNICAL ONLY"] if not buy.empty else buy
        st.subheader(f"A-grade technical BUY shortlist ({len(a_candidates)})")
        st.caption("A grade uses stock price, dollar-volume liquidity and daily price stability; reward/risk is shown for trade planning only. News and thinkorswim options are NOT verified.")
        if a_candidates.empty:
            st.info("No A-grade technical BUY candidates in this scanned sample.")
        else:
            st.dataframe(a_candidates[["Ticker","Research","Quality","Closing price $","SMA25 $","Deviation %","Strategy qualification","Entry reference $","Sell target $","Stop reference $","Potential return %","Reward/risk","Day change %","Quality notes","Price timestamp"]],hide_index=True,use_container_width=True,column_config={"Ticker":st.column_config.TextColumn("TICKER",pinned=True),"Research":st.column_config.LinkColumn("Full analysis",display_text="Open research ↗")})
            st.download_button("Download A-grade shortlist",a_candidates.to_csv(index=False),f"{tab_id}_a_grade.csv","text/csv",key=f"{tab_id}_a_csv")
        sell_rows=[]
        for r in rows:
            symbol=r["Ticker"]
            if symbol in entries:
                buy_price,manual_stop=entries[symbol]
                sell_price=r["Price $"]
                target=r["SMA25 $"]
                stop=manual_stop if manual_stop is not None else r["Stop reference $"]
                status="TARGET HIT — EXIT WATCH" if sell_price>=target else ("STOP HIT — EXIT WATCH" if sell_price<=stop else "HOLD / MONITOR")
                sell_rows.append(dict(r, **{"Actual buy entry $":buy_price,"Sell reference $":sell_price,
                              "Exit target $":target,"Exit stop $":round(stop,2),
                              "Return since buy %":round((sell_price/buy_price-1)*100,2),
                              "Signal":status}))
            elif r["Deviation %"]>=sell_threshold:
                sell_rows.append(dict(r, **{"Actual buy entry $":None,"Sell reference $":r["Price $"],
                                         "Exit target $":None,"Signal":"OVEREXTENDED — NO RECORDED ENTRY"}))
        sell=pd.DataFrame(sell_rows)
        # Always show objective overextension rankings when no stocks cross the sell threshold.
        # These are research candidates, never fabricated holdings or sell orders.
        overextended=pd.DataFrame([r for r in rows if r["Ticker"] not in entries and r["Deviation %"]>0])
        if not overextended.empty:
            overextended=overextended.sort_values("Deviation %",ascending=False)
        st.subheader(f"Overextended stocks — SELL research ({len(overextended)})")
        st.caption("Positive SMA25 deviation only. These are NOT portfolio exits, and no purchase price is assumed.")
        if overextended.empty:
            st.info("No stocks above their 25-day SMA in this sample. Broaden the universe for more coverage.")
        else:
            st.dataframe(overextended[["Ticker","Research","Closing price $","SMA25 $","Deviation %","Day change %","Quality","Price timestamp"]],hide_index=True,use_container_width=True,column_config={"Ticker":st.column_config.TextColumn("TICKER",pinned=True)})
        st.subheader("Recorded-position exits")
        if not entries:
            st.info("No buy entries have been recorded. An actual entry price and realized return cannot be calculated without a recorded purchase.")
        st.caption(f"Excluded {excluded_count} C-grade stocks from the displayed lists when the liquidity/quality filter is enabled.")
        st.caption(f"Scanned {scanned_count} requested tickers · {len(rows)} usable price histories · {failure_count} failed batches. Market data can be delayed; check each timestamp.")
        st.markdown("### BUY candidates — ticker, entry, target and stop")
        if True:
            st.subheader(f"BUY watchlist ({len(buy)})")
            if buy.empty: st.info("No stocks currently meet the BUY threshold in this sample.")
            else:
                buy=buy.sort_values("Deviation %")
                columns=["Ticker","Research","Signal","Closing price $","SMA25 $","Deviation %","Strategy qualification","Previous close $","Day change %","Entry reference $","Sell target $","Stop reference $","Potential return %","Reward/risk","Deviation %","Quality","Quality notes","Liquidity proxy","Avg daily $ volume","RSI14","Price timestamp","Source"]
                st.dataframe(buy[[c for c in columns if c in buy.columns]], hide_index=True, use_container_width=True, column_config={"Ticker":st.column_config.TextColumn("TICKER",pinned=True)}, height=340)
                st.download_button("Download BUY CSV", buy.to_csv(index=False), f"{tab_id}_buy.csv","text/csv",key=f"{tab_id}_buy_csv")
        st.markdown("### SELL / EXIT candidates — ticker and recorded entry")
        if True:
            st.subheader(f"SELL / exit signals ({len(sell)})")
            if sell.empty: st.info("No stocks currently meet the SELL threshold in this sample.")
            else:
                sell=sell.sort_values("Deviation %",ascending=False)
                columns=["Ticker","Signal","Closing price $","SMA25 $","Deviation %","Strategy qualification","Actual buy entry $","Sell reference $","Day change %","Exit target $","Exit stop $","Return since buy %","Deviation %","Quality","Price timestamp","Source"]
                st.dataframe(sell[[c for c in columns if c in sell.columns]], hide_index=True, use_container_width=True, column_config={"Ticker":st.column_config.TextColumn("TICKER",pinned=True)}, height=340)
                st.download_button("Download SELL CSV",sell.to_csv(index=False),f"{tab_id}_sell.csv","text/csv",key=f"{tab_id}_sell_csv")
        if failure_count:
            st.warning("Some data batches failed; results are incomplete. Retry with fewer symbols.")
        st.caption("Recorded entries are session inputs, not verified broker fills; they may be lost on app restart. SELL WATCH is a price-screening condition only. It does not indicate you hold the stock or should sell short. Intraday quotes may be delayed; if 5-minute data is missing, daily close is explicitly labeled. No orders are placed.")
    except Exception as e:
        st.error(f"Scan failed: {e}")
