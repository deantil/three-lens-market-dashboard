"""Automatic stock discovery -> protected option spread research. No orders."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import json
import pandas as pd
import requests
import streamlit as st
from credit_spread_scanner import scan_symbol, snapshot_fallback, published_snapshot
from kotegawa_options_engine import select_stock_signals, attach_spread_candidates

VERSION = 'kotegawa-options-v2.0'

@st.cache_data(ttl=120, show_spinner=False)
def load_stock_snapshot():
    response = requests.get('https://raw.githubusercontent.com/deantil/three-lens-market-dashboard/main/data/full_universe_daily.json.gz', timeout=25)
    response.raise_for_status()
    data = json.loads(gzip.decompress(response.content))
    if not isinstance(data.get('rows'), list) or not data['rows']:
        raise ValueError('Stock snapshot contains no usable rows')
    return data

def run_connected_scan(snapshot, fees=3., window='7–45 DTE', limit=20,
                       buy_threshold=20., sell_threshold=10., max_rebound=10., progress=None):
    selection = select_stock_signals(snapshot['rows'], snapshot.get('generated_at_utc'),
        buy_threshold=buy_threshold, sell_threshold=sell_threshold, max_rebound=max_rebound)
    chosen = selection['signals'][:limit]
    reports = {}
    # Prefer the fresh published chain feed; direct requests cover feed gaps.
    def collect(signal):
        symbol, direction = signal['Ticker'], signal['Direction']
        rows, status = snapshot_fallback(symbol, direction, fees, window, {'Ticker':symbol, 'Data errors':''})
        if status.get('Chains loaded'):
            status['Data errors'] = 'Using fresh published chain feed; direct request not needed.'
        if not status.get('Chains loaded'):
            rows, status = scan_symbol(symbol, direction, fees, window, allow_fallback=False)
        return symbol, {'rows': rows, 'status': status}
    if chosen:
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {pool.submit(collect, signal):signal['Ticker'] for signal in chosen}
            for index, future in enumerate(as_completed(jobs), 1):
                try:
                    symbol, report = future.result()
                    reports[symbol] = report
                except Exception as exc:
                    reports[jobs[future]] = {'rows':[], 'status':{'Chains loaded':0, 'Data errors':str(exc)[:200]}}
                if progress: progress(index / len(chosen))
    report = attach_spread_candidates(selection, reports)
    skipped = {s['Ticker'] for s in selection['signals'][limit:]}
    for stock in report['stocks']:
        if stock['Ticker'] in skipped:
            stock['Stock outcome'] = 'OPTIONS NOT ASSESSED — RUN LIMIT'
    report['coverage']['option_data_unavailable'] = sum(s['Stock outcome']=='DATA UNAVAILABLE — OPTIONS' for s in report['stocks'])
    report.update(version=VERSION, completed_utc=datetime.now(timezone.utc).isoformat(),
        stock_snapshot_utc=snapshot.get('generated_at_utc'),
        universe_size=snapshot.get('universe_size',len(snapshot['rows'])),
        failed_stock_symbols=snapshot.get('failed_symbols',[]),
        settings={'fees':fees,'window':window,'option_stocks_requested':len(chosen),'option_stocks_deferred':len(skipped),
                  'buy_threshold':buy_threshold,'sell_threshold':sell_threshold,'max_rebound':max_rebound})
    return report

def render():
    st.header('Kotegawa stock scanner → defined-risk options')
    st.caption(VERSION + ' · Automatic discovery across the published US listed-stock universe. No ticker entry needed.')
    a,b,c = st.columns(3)
    a.metric('Strategy account', '$5,000')
    b.metric('Maximum planned risk', '$50 including fees')
    c.metric('Position size', '1 protected spread')
    st.write('Scan stocks first, then evaluate bull put spreads for bullish mean-reversion setups and bear call spreads for bearish overextension setups. Each spread has a protective long option on the same stock and expiration.')
    st.caption('Kotegawa-inspired research rules: price ≥$5, average daily dollar volume ≥$10M, daily move under 20%, reference stock reward/risk ≥2. Bullish target is SMA25; bearish screening is a symmetric research extension, with a mirrored stop-distance proxy. These rules are not a verified reconstruction or a proven edge.')
    with st.form('kotegawa_options_form'):
        a,b,c = st.columns(3)
        buy = a.number_input('Bullish: % below SMA25',min_value=0.1,value=20.,step=1.)
        sell = b.number_input('Bearish: % above SMA25',min_value=0.1,value=10.,step=1.)
        rebound = c.number_input('Maximum bullish same-day rebound %',min_value=0.,value=10.,step=1.)
        a,b,c = st.columns(3)
        window = a.selectbox('Options expiration range',['7–45 DTE','7–14 DTE','30–45 DTE'])
        fees = b.number_input('Total opening + closing fees ($)',min_value=3.,value=3.,step=.1)
        limit = c.selectbox('Top stock setups to assess options',[10,20,40],index=1)
        st.caption('All available stock histories are screened. Options requests are bounded to the top setups shown above; remaining setups are explicitly marked not assessed. Default $3 fees are an estimate; increase for actual charges.')
        run = st.form_submit_button('Run stock → options scan',type='primary')
    if run:
        try:
            load_stock_snapshot.clear()
            published_snapshot.clear()
            with st.spinner('Discovering stocks, then checking option chains…'):
                snapshot = load_stock_snapshot()
                progress = st.progress(0.)
                st.session_state['kotegawa_options_report'] = run_connected_scan(snapshot,fees,window,limit,buy,sell,rebound,progress.progress)
                progress.empty()
        except Exception as exc:
            st.session_state.pop('kotegawa_options_report',None)
            st.error('DATA UNAVAILABLE — stock discovery could not finish: '+str(exc)[:240])
    report = st.session_state.get('kotegawa_options_report')
    if not report:
        st.info('Click Run stock → options scan. The system discovers tickers automatically; you do not need to choose them.')
        st.caption('Current source: delayed Yahoo stock histories and option chains, including the public background feed. Schwab/thinkorswim is not connected.')
        return
    st.caption(f"Completed UTC: {report['completed_utc']} · Stock snapshot UTC: {report['stock_snapshot_utc']} · Settings used: {report['settings']}")
    coverage = report['coverage']
    a,b,c,d = st.columns(4)
    a.metric('Stock histories screened',coverage['stocks_evaluated'])
    b.metric('Stock setups',coverage['eligible_signals'])
    c.metric('Spread candidates',len(report['rows']))
    d.metric('Options data unavailable',coverage['option_data_unavailable'])
    st.caption(f"Published universe: {report['universe_size']} requested symbols; {len(report['failed_stock_symbols'])} histories failed upstream. Stock dates must be within four calendar days; option snapshots must be within 20 minutes. Retrieval time does not verify quote freshness.")
    for error in report['errors']: st.error(error)
    if not coverage['eligible_signals']:
        st.info('No stock setups passed the selected rules in the usable data. Review stock coverage below.')
    elif not report['rows']:
        st.info('No suitable trade was found in the chains actually returned. Stocks with unavailable chains remain DATA UNAVAILABLE; they have not been ruled out.')
    else:
        st.success(f"{len(report['rows'])} protected spread research candidates. Every candidate needs live broker and event review.")
    stocks = pd.DataFrame(report['stocks'])
    signals = stocks[stocks.get('Signal rank',pd.Series(index=stocks.index,dtype=object)).notna()]
    st.subheader('Stock setups and options assessment')
    cols=['Ticker','Signal rank','Signal direction','Stock outcome','Price $','SMA25 $','Deviation %','Stock target $','Stock stop $','Stock reference reward/risk','Spread candidates','Option chains loaded','Option data errors','Signal reason','Price timestamp']
    if not signals.empty:
        st.dataframe(signals[[c for c in cols if c in signals]],hide_index=True,use_container_width=True)
    if report['rows']:
        frame = pd.DataFrame(report['rows'])
        lead=['Rank','Ticker','Signal direction','Deviation %','Direction','Expiration','Short strike','Long strike','Credit / share $','Max loss incl fees $','Max net profit $','Liquidity','Approval status']
        st.subheader('Ranked spread candidates')
        st.caption('Rank: originating stock signal strength, then minimum leg open interest, volume and net return on risk. A rank is not a win probability. Stock target/stop prices are references, not options stop orders.')
        st.dataframe(frame[lead+[c for c in frame if c not in lead]],hide_index=True,use_container_width=True)
        st.download_button('Download stock + options candidates CSV',frame.to_csv(index=False),'kotegawa_options.csv','text/csv')
    with st.expander('All stock outcomes and data coverage'):
        st.dataframe(stocks,hide_index=True,use_container_width=True)
        st.download_button('Download complete stock coverage CSV',stocks.to_csv(index=False),'kotegawa_stock_coverage.csv','text/csv')
    st.warning('REVIEW REQUIRED: delayed Yahoo data; Schwab/thinkorswim is not connected. Credit uses short bid minus long ask. Both legs require OI ≥100, volume ≥10 and bid/ask width ≤25% of ask. Verify current signal, executable quotes, contract deliverables, earnings/dividends and actual fees. No trades are submitted. Planned loss assumes the protected spread remains intact; close before expiration to avoid assignment/expiration stock exposure.')
