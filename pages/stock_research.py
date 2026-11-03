"""Per-ticker research page. Read-only, not a trade recommendation."""
import re
import pandas as pd
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="Stock Research", layout="wide")
symbol=str(st.query_params.get("symbol","")).upper().strip()
st.title("Stock research")
if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,12}",symbol):
    st.info("Select a ticker from the scanner to view its research.")
    st.stop()
st.header(symbol)
st.caption("Independent research from Yahoo Finance; not a Schwab/thinkorswim quote. Check timestamps, corporate actions, and company filings before trading.")

@st.cache_data(ttl=600,show_spinner=False)
def research(ticker):
    obj=yf.Ticker(ticker)
    hist=obj.history(period="6mo",auto_adjust=False)
    try: info=obj.info or {}
    except Exception: info={}
    try: news=obj.news or []
    except Exception: news=[]
    return hist,info,news

try:
    hist,info,news=research(symbol)
    if hist.empty or len(hist)<26:
        st.error("Insufficient history to analyze this ticker.")
        st.stop()
    close=hist["Close"].dropna()
    last=float(close.iloc[-1])
    prior=float(close.iloc[-2])
    sma25=float(close.tail(25).mean())
    deviation=(last/sma25-1)*100
    daily=(last/prior-1)*100
    avg_dollar=float((hist["Close"].tail(20)*hist["Volume"].tail(20)).mean())
    low5=float(hist["Low"].tail(5).min())
    stop=min(low5,last)*0.99
    target=sma25
    rr=(target-last)/(last-stop) if last>stop else None
    a,b,c,d=st.columns(4)
    a.metric("Latest daily close",f"${last:,.2f}",f"{daily:+.2f}% vs prior close")
    b.metric("25-day SMA",f"${sma25:,.2f}")
    c.metric("Deviation from SMA25",f"{deviation:+.2f}%")
    d.metric("20-day avg dollar volume",f"${avg_dollar/1e6:,.1f}M")
    st.caption(f"Latest daily bar: {hist.index[-1]} · Close is unadjusted; SMA uses the same unadjusted series.")
    st.subheader("Price history and 25-day moving average")
    chart=pd.DataFrame({"Close":close,"SMA25":close.rolling(25).mean()})
    st.line_chart(chart)
    st.subheader("Strategy qualification and proposed trade levels")
    reasons=[]
    if last<5: reasons.append("Price below $5")
    if avg_dollar<10_000_000: reasons.append("Average dollar volume below $10M")
    if deviation>-20: reasons.append("Not 20% below SMA25")
    if daily>10: reasons.append("Daily rebound exceeds 10%")
    grade="A · TECHNICAL ONLY" if last>=5 and avg_dollar>=20_000_000 and abs(daily)<20 else ("B · REVIEW" if last>=5 and avg_dollar>=10_000_000 else "C · EXCLUDE")
    st.write(f"**Technical grade:** {grade}")
    st.write("**BUY screen:** "+("PASS" if not reasons else "FAIL — "+"; ".join(reasons)))
    levels=pd.DataFrame([{"Ticker":symbol,"Reference entry $":round(last,2),"Target SMA25 $":round(target,2),"Stop reference $":round(stop,2),"Potential return %":round((target/last-1)*100,2),"Reward/risk":round(rr,2) if rr is not None else None}])
    st.dataframe(levels,hide_index=True,use_container_width=True)
    st.caption("Reference levels are calculated, not executable quotes or orders. A technical grade does not verify fundamentals, event risk or options liquidity.")
    st.subheader("Company and business context")
    st.write(info.get("longName") or symbol)
    st.write("Sector:",info.get("sector","Unavailable")," · Industry:",info.get("industry","Unavailable"))
    if info.get("longBusinessSummary"):
        st.write(info["longBusinessSummary"][:1600])
    metrics={"Market cap":info.get("marketCap"),"Trailing P/E":info.get("trailingPE"),"Forward P/E":info.get("forwardPE"),"52-week high":info.get("fiftyTwoWeekHigh"),"52-week low":info.get("fiftyTwoWeekLow"),"Earnings growth":info.get("earningsGrowth"),"Debt/equity":info.get("debtToEquity")}
    st.dataframe(pd.DataFrame([{"Metric":k,"Value":v} for k,v in metrics.items()]),hide_index=True,use_container_width=True)
    st.subheader("What to watch next")
    st.markdown("- **Earnings / guidance:** Check the next earnings announcement and any revisions to guidance.\n- **Company-specific catalysts:** Investigate clinical, regulatory, financing, dilution, or restructuring news before treating a collapse as a bargain.\n- **Price confirmation:** Watch whether price stabilizes above recent lows and moves toward SMA25.\n- **Volume and spreads:** Check for persistent liquidity and abnormal volume.\n- **Options:** Verify bid/ask, open interest, volume, expiration and implied volatility directly in thinkorswim.")
    earnings=info.get("earningsTimestamp")
    if earnings:
        st.write("Reported next earnings timestamp:",pd.to_datetime(earnings,unit="s",utc=True))
    st.subheader("Recent headlines (verify original sources)")
    if not news:
        st.info("No recent headlines available from this feed. This is NOT confirmation of no material news.")
    for item in news[:8]:
        node=item.get("content",item)
        title=node.get("title") or item.get("title") or "Untitled"
        link=(node.get("canonicalUrl") or {}).get("url") if isinstance(node.get("canonicalUrl"),dict) else item.get("link")
        if link and str(link).startswith("https://"):
            st.markdown(f"- [{title}]({link})")
        else: st.write("•",title)
except Exception as exc:
    st.error(f"Research data unavailable for {symbol}: {exc}")
