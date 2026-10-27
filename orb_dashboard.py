"""Dashboard-only ORB research alerts and manual thinkorswim paper plans."""
from datetime import datetime, timezone
import hashlib
import json
import time

import pandas as pd
import streamlit as st
import yfinance as yf

from orb_strategy import DEFAULT_SYMBOLS, ET, analyze, paper_ticket, parse_symbols, rank_candidates, session_times


def split_download(data, symbols):
    out = {}
    for symbol in symbols:
        if isinstance(data.columns, pd.MultiIndex):
            for level in range(data.columns.nlevels):
                if symbol in data.columns.get_level_values(level):
                    out[symbol] = data.xs(symbol, axis=1, level=level).dropna(how='all')
                    break
        elif len(symbols) == 1:
            out[symbol] = data.dropna(how='all')
    return out


@st.cache_data(ttl=60, show_spinner=False)
def load_orb_data(symbols):
    kwargs = dict(tickers=list(symbols), auto_adjust=False, group_by='ticker',
                  progress=False, threads=False, timeout=12, prepost=False)
    intraday = yf.download(period='1mo', interval='5m', **kwargs)
    daily = yf.download(period='3mo', interval='1d', **kwargs)
    return split_download(intraday, symbols), split_download(daily, symbols), datetime.now(timezone.utc)


@st.cache_data(ttl=3600, show_spinner=False)
def calendar_for_date(date_string):
    return session_times(pd.Timestamp(date_string + ' 12:00', tz=ET))


def render_orb():
    st.subheader('Project T ORB alerts')
    st.write('Find a stock with unusually active opening volume, wait for its first five-minute range to break, then review a sized paper trade in thinkorswim.')
    st.warning('PAPER ONLY · Yahoo five-minute bars may be delayed. These are research alerts, not live execution instructions. No Schwab connection or orders are enabled.')
    st.caption('Watchlist scan, not a market-wide Stocks-in-Play ranking. Alerts refresh every 60 seconds only while this session is active and monitoring is enabled. No background, email or phone alerts.')
    a, b, c = st.columns(3)
    a.metric('Reference paper account', '$25,000')
    b.metric('Planned risk per trade', '0.25% · $62.50 initially')
    c.metric('New entries ET', '09:35–11:00')
    with st.expander('What should I do specifically?', expanded=True):
        st.markdown('''1. Log in to **thinkorswim paperMoney** and confirm the paper-trading indicator. Use a separate $25,000 paper ledger even if paperMoney shows more buying power.
2. At **09:35 ET**, start the scan below. **Watch for breakout** means wait; **Review breakout** means inspect that stock in paperMoney. **Skip**, **Stale data**, and **Data unavailable** mean do not enter.
3. Select a candidate and enter the current paperMoney bid and ask. Confirm its news/catalyst, trading status, current quote and your account risk. A short additionally requires eligibility and borrow checks. The calculator will give a share count and stop.
4. Review the exact quantity and price, then **manually submit only in paperMoney**. After a fill, immediately place a protective stop at the shown distance from your actual fill; recalculate size first if your entry changes. Cancel unfilled entries at **11:00 ET**.
5. Do not widen the stop, add to a loser or trade the same stock again today. Close all positions by **15:55 ET**, or five minutes before an early close. Record fills and costs in your own paper journal.''')
    with st.form('orb_scan_settings'):
        watchlist = st.text_area('US stock watchlist — up to 40 symbols', DEFAULT_SYMBOLS, key='orb_watchlist')
        st.caption('Default: 30 liquid stocks. Add your broker scanner\'s Stocks-in-Play shortlist. Ranking below is only within this list; it does not validate historical performance.')
        start = st.form_submit_button('Start ORB monitoring', type='primary')
    if start:
        try:
            symbols = parse_symbols(watchlist)
            st.session_state['orb_symbols'] = tuple(symbols)
            st.session_state['orb_monitoring'] = True
            st.session_state['orb_started'] = datetime.now(timezone.utc).isoformat()
            st.session_state.pop('orb_last_ticket', None)
        except ValueError as exc:
            st.error(str(exc))
    if st.button('Stop ORB monitoring', key='orb_stop'):
        st.session_state['orb_monitoring'] = False
        st.session_state.pop('orb_last_ticket', None)
    if st.session_state.get('orb_monitoring'):
        monitor_panel()
    else:
        st.info('Monitoring is stopped. Click Start ORB monitoring to request current research signals.')
    with st.expander('Rules, risk limits and data coverage'):
        st.markdown('''**Baseline:** 09:30–09:35 ET range; bullish opening candle permits longs, bearish permits shorts, doji skips. Price > $5; prior 14-session average daily volume > 1M; prior 14-session SMA of true range > $0.50; opening volume / prior 14 opening volumes >= 1. Rank top 20 within the supplied watchlist. Entry trigger is one cent beyond the range. Stop is 10% of prior daily ATR, rounded up to one cent. No fixed profit target; protective stop or scheduled end-of-day exit.

**Project T adaptations:** entry cutoff 11:00 ET, no chasing more than 0.25 stop distances, bid/ask spread <= the lower of $0.05 or 10% of stop distance, max 25% account notional per position, max 3 positions, 0.75% total open planned risk, 100% gross exposure, and 1% daily loss limit. These are design choices, not proven improvements. Tickets reserve spread plus an adverse per-side allowance of max($0.01, 5 basis points). Fees or gap losses may exceed the allowance.

**Coverage:** unadjusted Yahoo five-minute bars and prior completed daily bars. Today is never substituted with yesterday. Incomplete bars are excluded. No full-market ranking, consolidated real-time bid/ask, broker account state, borrow feed or automated position/exit monitoring is connected. Verify split dates and all prices in paperMoney. Missing/stale data suppresses actionable candidates. NYSE calendar handles holidays, DST and early closes.

**Account checks are manual:** entered values are private to this browser session and reset on session loss; they are not a broker ledger or guaranteed loss lockout. No trade, approval or account details are written to GitHub. No new paid feed is purchased.''')
        st.markdown('[Stocks-in-Play ORB research](https://ssrn.com/abstract=4729284) · [Schwab paperMoney](https://www.schwab.com/trading/thinkorswim/paper-trading) · [thinkScript simulated strategies](https://toslc.thinkorswim.com/center/reference/thinkScript/tutorials/Basic/Chapter-7---Creating-Strategies)')


@st.fragment(run_every='60s')
def monitor_panel():
    if not st.session_state.get('orb_monitoring'):
        return
    now = pd.Timestamp(datetime.now(timezone.utc)).tz_convert(ET)
    st.caption('Current New York time: ' + now.strftime('%Y-%m-%d %H:%M:%S %Z'))
    try:
        times = calendar_for_date(str(now.date()))
    except Exception:
        st.error('Exchange calendar unavailable. Monitoring is blocked until the calendar can be verified.')
        return
    if not times:
        st.info('Market closed today. Wait until the next NYSE session at 09:35 ET. No current entry alerts.')
        return
    market_open, market_close = times
    flatten = market_close - pd.Timedelta(minutes=5)
    st.caption(f'Today: range {market_open:%H:%M}–{market_open + pd.Timedelta(minutes=5):%H:%M} ET · flatten by {flatten:%H:%M} ET. Protective stops and exits must be managed in paperMoney.')
    if now < market_open + pd.Timedelta(minutes=5):
        st.info('Wait until 09:35 ET. The opening range is not complete.')
        return
    if now >= market_open + pd.Timedelta(minutes=90):
        st.warning('Entry window closed. Do not take new ORB trades today. Manage existing paper positions and close by the flatten time above.')
    if st.button('Refresh ORB data now', key='orb_refresh'):
        load_orb_data.clear()
    try:
        with st.spinner('Checking completed opening ranges and volume…'):
            intraday, daily, fetched = load_orb_data(st.session_state['orb_symbols'])
        rows = []
        for symbol in st.session_state['orb_symbols']:
            if symbol not in intraday or symbol not in daily:
                rows.append({'Symbol':symbol, 'Status':'Data unavailable', 'Reason':'Yahoo did not return both data series.', 'Side':'', 'RVOL':None})
            else:
                rows.append(analyze(symbol, intraday[symbol], daily[symbol], now, times))
        rank_candidates(rows)
    except Exception:
        st.error('Market data request failed. No current candidates. Retry later; never reuse an old ticket.')
        return
    st.caption('Data retrieval completed: ' + fetched.astimezone(ET).strftime('%H:%M:%S ET') + '. Retrieval time is not quote time; see each bar timestamp.')
    snapshot = [{'Stock':r['Symbol'], 'Action':r['Status'], 'Direction':r['Side'],
                 'Opening RVOL':r.get('RVOL'), 'Watchlist rank':r.get('watchlist_rank'),
                 'Entry trigger':r.get('trigger'), 'Stop distance':r.get('stop_distance'),
                 'Last completed close':r.get('price'),
                 'Bar completed ET':r['asof'].strftime('%Y-%m-%d %H:%M') if r.get('asof') is not None else '',
                 'What to do':r['Reason']} for r in rows]
    st.dataframe(pd.DataFrame(snapshot), hide_index=True, use_container_width=True)
    st.download_button('Download current ORB research alerts', pd.DataFrame(snapshot).to_csv(index=False), 'orb_research_alerts.csv', 'text/csv', key='orb_csv')
    # Session-scoped transition alerts, not repeated notifications every poll.
    previous = st.session_state.setdefault('orb_alert_status', {})
    alerts = st.session_state.setdefault('orb_alert_log', [])
    for r in rows:
        key = str(now.date()) + ':' + r['Symbol']
        if r['Status'] == 'Review breakout' and previous.get(key) != r['Status']:
            alerts.append({'Observed ET':now.strftime('%Y-%m-%d %H:%M:%S'), 'Stock':r['Symbol'],
                           'Alert':'Review breakout in paperMoney', 'Bar completed ET':str(r.get('asof'))})
            st.toast(r['Symbol'] + ': breakout observed — review in paperMoney')
        previous[key] = r['Status']
    st.session_state['orb_alert_log'] = alerts[-100:]
    if alerts:
        with st.expander('Session alert history — past alerts are not current instructions'):
            st.dataframe(pd.DataFrame(alerts[-20:][::-1]), hide_index=True)
    candidates = [r for r in rows if r['Status'] in {'Review breakout','Watch for breakout'}]
    candidates.sort(key=lambda r:(r['Status'] != 'Review breakout', -r['RVOL'], r['Symbol']))
    if not candidates:
        st.info('No eligible paper-trade candidates now. Waiting or skipping is the correct action.')
        return
    st.markdown('### Build a paperMoney trade plan')
    st.caption('A plan does not place or approve an order. All checks below expire after 60 seconds and must be repeated before manual entry. Default account figures are assumptions; replace them with your paper ledger.')
    selected = st.selectbox('Candidate to review', [r['Symbol'] for r in candidates], key='orb_candidate')
    row = next(r for r in candidates if r['Symbol'] == selected)
    st.write(f"**{selected} · {row['Side']} · {row['Status']}** — {row['Reason']}")
    # Invalidate stored tickets on symbol/session/market-state transition or expiry.
    fingerprint = hashlib.sha256(json.dumps({'symbol':selected,'date':str(now.date()), 'status':row['Status'],
        'trigger':row['trigger'],'stop':row['stop_distance']}, sort_keys=True).encode()).hexdigest()
    with st.form('orb_paper_ticket_' + selected, clear_on_submit=True):
        c1,c2,c3 = st.columns(3)
        equity = c1.number_input('Current paper equity ($)', min_value=1.0, value=25000.0, step=100.0)
        pnl = c2.number_input('Today total P/L including unrealized ($)', value=0.0, step=10.0)
        count = c3.number_input('Open positions', min_value=0, value=0, step=1)
        c1,c2 = st.columns(2)
        risk = c1.number_input('Existing open planned risk ($)', min_value=0.0, value=0.0, step=10.0)
        exposure = c2.number_input('Existing gross position value ($)', min_value=0.0, value=0.0, step=100.0)
        c1,c2 = st.columns(2)
        bid = c1.number_input('Current paperMoney bid ($)', min_value=0.0, value=0.0, step=0.01, format='%.2f')
        ask = c2.number_input('Current paperMoney ask ($)', min_value=0.0, value=0.0, step=0.01, format='%.2f')
        fresh = st.checkbox('I just checked these bid/ask quotes in paperMoney within the last 60 seconds')
        source = st.checkbox('I checked today\'s news/catalyst and this stock\'s eligibility in my Stocks-in-Play broker scan')
        portfolio = st.checkbox('I verified these account totals and have no conflicting pending orders')
        already = st.checkbox('I already traded this symbol today')
        short = st.checkbox('For a short, I verified paperMoney short eligibility and borrow assumptions')
        approved = st.checkbox('I understand this is paper only and will review the final ticket manually')
        submit = st.form_submit_button('Calculate my paper trade plan')
    if submit:
        st.session_state.pop('orb_last_ticket',None)
        if not all([fresh,source,portfolio,approved]):
            st.error('Complete the fresh quote, Stocks-in-Play, account and paper-only checks first.')
        else:
            try:
                # Refresh time-sensitive eligibility, even if the data were cached.
                real_now = pd.Timestamp(datetime.now(timezone.utc)).tz_convert(ET)
                fresh_row = analyze(selected, intraday[selected], daily[selected], real_now, times)
                ticket = paper_ticket(fresh_row,bid,ask,equity,pnl,count,risk,exposure,already,short)
                st.session_state['orb_last_ticket'] = {'ticket':ticket,'created':time.time(),'fingerprint':fingerprint}
            except ValueError as exc:
                st.error(str(exc))
    saved = st.session_state.get('orb_last_ticket')
    if saved and saved['fingerprint'] == fingerprint and time.time()-saved['created'] <= 60:
        ticket = saved['ticket']
        st.success(f"PAPER PLAN: {ticket['Side']} {ticket['Shares']} shares of {selected}. Planned entry ${ticket['Planned entry']:.2f}; reference protective stop ${ticket['Protective stop']:.2f}.")
        st.write(f"Planned risk including cost allowance: **${ticket['Planned risk including allowance']:.2f}**. Position value **${ticket['Notional']:,.2f}**. Close by **{ticket['Flatten ET']} ET**. No fixed profit target.")
        st.write('Order approach: **'+ticket['Order plan']+'**. If the entry fills at another price, use the stop distance from the actual fill and recheck the risk. Stops can slip; the budget is not a maximum loss guarantee.')
        st.warning('Next action: verify the quote again, then manually enter the reviewed order in thinkorswim paperMoney. Immediately add the protective stop after the fill. This dashboard will not manage or close your position.')
    elif saved:
        st.session_state.pop('orb_last_ticket',None)
        st.info('Previous paper plan expired or the candidate changed. Recheck quotes and calculate again.')
