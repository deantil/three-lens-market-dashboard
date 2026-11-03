"""Project T scanner UI and Yahoo data adapter; never places orders."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import re
import requests
import pandas as pd
import streamlit as st
import yfinance as yf
from credit_spread_engine import build_candidates, parse_symbols, DEFAULT_FEES

VERSION = 'credit-spreads-v1.2'
DEFAULT_SYMBOLS = 'AAPL MSFT AMD INTC SOFI F HOOD PLTR'


@st.cache_data(ttl=60, show_spinner=False)
def published_snapshot():
    response = requests.get('https://raw.githubusercontent.com/deantil/three-lens-market-dashboard/credit-scan-state/credit_market_snapshot.json', timeout=15)
    response.raise_for_status()
    return response.json()


def snapshot_fallback(symbol, direction, fees, window, direct_status):
    now = datetime.now(timezone.utc)
    try:
        snapshot = published_snapshot()
        stock = snapshot.get('stocks', {}).get(symbol)
        if not stock:
            raise ValueError('Ticker not covered by published feed')
        observed = datetime.fromisoformat(stock['retrieved_utc'])
        age = (now - observed).total_seconds() / 60
        if not 0 <= age <= 20:
            raise ValueError(f'Published data stale: {age:.1f} minutes; maximum 20')
        rows, pairs, rejected, loaded = [], 0, 0, 0
        for exp, chain in stock['chains'].items():
            dte = (pd.Timestamp(exp).date() - now.date()).days
            if not (7 <= dte <= 14 if window == '7–14 DTE' else 30 <= dte <= 45 if window == '30–45 DTE' else 7 <= dte <= 45):
                continue
            found, counts = build_candidates(symbol, stock['spot'], exp, chain['puts'], chain['calls'], direction, fees, now.date())
            loaded += 1
            pairs += counts['pairs_checked']
            rejected += counts['risk_rejected']
            for row in found:
                row['Trend alignment'] = 'Aligned' if (row['Direction'] == 'Bull put') == (stock['trend'] == 'Bullish') else 'Countertrend'
                row['Retrieved UTC'] = stock['retrieved_utc']
                row['Data source'] = 'Published Yahoo snapshot via GitHub Actions; delayed'
                row['Snapshot age minutes'] = round(age, 1)
            rows.extend(found)
        if not loaded:
            raise ValueError('Published feed has no chains in selected window')
        return rows, dict(direct_status, **{'Chains loaded': loaded, 'Candidates': len(rows),
            'Expirations requested': loaded, 'Spot reference $': stock['spot'], '20-day trend': stock['trend'],
            'Price date': stock.get('price_date', 'See snapshot retrieval time'),
            'Pairs checked': pairs, 'Over budget / unprofitable': rejected,
            'Data source': 'Published Yahoo snapshot via GitHub Actions',
            'Snapshot age minutes': round(age, 1), 'Retrieved UTC': stock['retrieved_utc'],
            'Data errors': 'Direct chain unavailable; using timestamped published feed. ' + direct_status.get('Data errors', '')})
    except Exception as exc:
        direct_status['Data errors'] += '; fallback: ' + str(exc)[:200]
        return [], direct_status


def scan_symbol(symbol, direction, fees, window, snapshot_sink=None, allow_fallback=True):
    now = datetime.now(timezone.utc)
    status = {'Ticker': symbol, 'Price history': 'Unavailable', 'Chains loaded': 0,
              'Expirations requested': 0, 'Pairs checked': 0, 'Over budget / unprofitable': 0,
              'Candidates': 0, 'Data errors': '', 'Retrieved UTC': now.isoformat()}
    rows, errors = [], []
    ticker = yf.Ticker(symbol)
    try:
        history = ticker.history(period='3mo', auto_adjust=True, timeout=15)
        if history.empty or len(history) < 21:
            raise ValueError('Insufficient daily price history')
        close = history['Close'].dropna()
        spot = float(close.iloc[-1])
        age = (now.date() - close.index[-1].date()).days
        if age > 4:
            raise ValueError(f'Price history stale ({age} calendar days)')
        trend = 'Bullish' if spot >= float(close.tail(20).mean()) else 'Bearish'
        status.update({'Price history': 'Available', 'Spot reference $': spot,
                       'Price date': str(close.index[-1]), '20-day trend': trend})
        if snapshot_sink is not None:
            snapshot_sink[symbol] = {'spot': spot, 'trend': trend, 'price_date': str(close.index[-1]), 'retrieved_utc': now.isoformat(), 'chains': {}}
        expirations = []
        for exp in ticker.options:
            dte = (pd.Timestamp(exp).date() - now.date()).days
            if (7 <= dte <= 14 if window == '7–14 DTE' else 30 <= dte <= 45 if window == '30–45 DTE' else 7 <= dte <= 45):
                expirations.append(exp)
        # Explicit request bound: nearest six qualifying expirations per stock.
        expirations = sorted(expirations)[:6]
        status['Expirations requested'] = len(expirations)
        if not expirations:
            errors.append('No option expirations available in selected window')
        for exp in expirations:
            try:
                chain = ticker.option_chain(exp)
                if chain is None or (chain.calls.empty and chain.puts.empty):
                    raise ValueError('Empty option chain')
                status['Chains loaded'] += 1
                if snapshot_sink is not None:
                    snapshot_sink[symbol]['chains'][exp] = {'puts': chain.puts.to_dict('records'), 'calls': chain.calls.to_dict('records')}
                found, counts = build_candidates(symbol, spot, exp, chain.puts.to_dict('records'),
                    chain.calls.to_dict('records'), direction, fees, now.date())
                for row in found:
                    row['Trend alignment'] = 'Aligned' if (row['Direction'] == 'Bull put') == (trend == 'Bullish') else 'Countertrend'
                    row['Retrieved UTC'] = now.isoformat()
                    row['Data source'] = 'Yahoo Finance / yfinance (delayed, quote time unavailable)'
                rows.extend(found)
                status['Pairs checked'] += counts['pairs_checked']
                status['Over budget / unprofitable'] += counts['risk_rejected']
            except Exception as exc:
                errors.append(f'{exp}: {type(exc).__name__}: {str(exc)[:180]}')
    except Exception as exc:
        errors.append(f'{type(exc).__name__}: {str(exc)[:200]}')
    status['Candidates'] = len(rows)
    status['Data errors'] = '; '.join(errors)
    if allow_fallback and not status['Chains loaded']:
        return snapshot_fallback(symbol, direction, fees, window, status)
    return rows, status


def run_scan(symbols, direction, fees, window, progress=None):
    rows, statuses = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(scan_symbol, s, direction, fees, window): s for s in symbols}
        for i, future in enumerate(as_completed(jobs), 1):
            try:
                found, status = future.result()
                rows.extend(found)
                statuses.append(status)
            except Exception as exc:
                statuses.append({'Ticker': jobs[future], 'Chains loaded': 0, 'Candidates': 0, 'Data errors': str(exc)[:200]})
            if progress:
                progress(i / len(symbols))
    rows.sort(key=lambda r: (r['Trend alignment'] != 'Aligned', -r['Return on risk %'], -r['Min leg OI']))
    for i, row in enumerate(rows, 1):
        row['Rank'] = i
    return {'version': VERSION, 'completed_utc': datetime.now(timezone.utc).isoformat(),
            'rows': rows, 'stocks': sorted(statuses, key=lambda s: (-s.get('Candidates', 0), s['Ticker'])),
            'settings': {'symbols': symbols, 'direction': direction, 'fees': fees, 'window': window}}


def render():
    st.subheader('Project T · Credit Spread Scanner')
    st.caption(f'{VERSION} · yfinance {yf.__version__} · Read-only research · Bull put and bear call spreads only · No naked options or order submission')
    st.caption('Run Scan tries direct Yahoo chains, then a published Yahoo snapshot collected by GitHub Actions approximately every 15 minutes. Fallback data must be at most 20 minutes old; schedules may be delayed. Coverage and actual retrieval times appear below. Custom tickers outside the published watchlist require direct data.')
    a, b, c = st.columns(3)
    a.metric('Account capital', '$5,000')
    b.metric('Maximum planned risk / trade', '$50 including fees')
    c.metric('Position size', '1 protected spread')
    st.write('Each candidate pairs one short option with one protective long option on the same stock and expiration. Credit uses the short bid minus the long ask, not midpoint prices.')
    with st.form('credit_scan_form'):
        symbols_text = st.text_area('Credit spread watchlist (up to 20 stocks)', DEFAULT_SYMBOLS)
        col1, col2, col3 = st.columns(3)
        direction = col1.selectbox('Spread direction', ['Both', 'Bull put', 'Bear call'])
        window = col2.selectbox('Expiration window', ['7–45 DTE', '7–14 DTE', '30–45 DTE'])
        fees = col3.number_input('Estimated total opening + closing fees ($)', min_value=DEFAULT_FEES, value=DEFAULT_FEES, step=.10)
        st.caption('Default $3.00 = four contract-sides × $0.65 + $0.40 allowance. Increase for your actual broker fees. Nearest six expirations per stock; 100-share regular contracts only. Both legs must have OI ≥100, volume ≥10 and bid/ask width ≤25% of ask.')
        submitted = st.form_submit_button('Run Scan', type='primary')
    if submitted:
        symbols = parse_symbols(symbols_text)
        tokens = [x for x in re.split(r'[\s,;]+', symbols_text.strip().upper()) if x]
        invalid = [s for s in tokens if s not in symbols]
        if not symbols or len(symbols) > 20 or invalid:
            st.error('Enter 1–20 valid stock tickers separated by spaces or commas. Invalid: ' + ', '.join(invalid))
        else:
            progress = st.progress(0.0, text='Retrieving prices and option chains…')
            with st.spinner('Scanning available Yahoo Finance data; no orders are sent…'):
                st.session_state['credit_scan_report'] = run_scan(symbols, direction, fees, window, progress.progress)
            progress.empty()
    report = st.session_state.get('credit_scan_report')
    if not report:
        st.info('Choose stocks and click Run Scan. Results use available delayed data; no sample trades are substituted.')
        return
    st.caption(f"Completed UTC: {report['completed_utc']} · Settings used: {report['settings']}")
    stocks = pd.DataFrame(report['stocks'])
    coverage_first = ['Ticker', 'Candidates', 'Chains loaded', 'Data errors']
    stocks = stocks[[c for c in coverage_first if c in stocks] + [c for c in stocks if c not in coverage_first]]
    chains = sum(s.get('Chains loaded', 0) for s in report['stocks'])
    if not chains:
        st.error('Scan completed with DATA UNAVAILABLE: no option chains were returned. This is not a no-trade signal. See per-stock errors below.')
    elif not report['rows']:
        st.info('No qualifying spreads in the returned chains. The $50 limit and liquidity checks were preserved. Missing chains are listed below.')
    else:
        st.success(f"{len(report['rows'])} defined-risk research candidates from {chains} option chains. Manual review required for every candidate.")
    st.markdown('**Ranked stocks and data coverage**')
    st.dataframe(stocks, hide_index=True, use_container_width=True)
    st.download_button('Download scan coverage CSV', stocks.to_csv(index=False), 'credit_scan_coverage.csv', 'text/csv')
    if report['rows']:
        frame = pd.DataFrame(report['rows'])
        lead = ['Rank', 'Ticker', 'Direction', 'Expiration', 'Short strike', 'Long strike', 'Credit / share $', 'Max loss incl fees $', 'Liquidity', 'Approval status']
        st.markdown('**Ranked defined-risk spreads**')
        st.caption('Rank: alignment with 20-day price average, then estimated net return on risk, then open interest. This is a heuristic, not a win probability. Stock ranking counts qualifying spreads.')
        st.dataframe(frame[lead + [c for c in frame.columns if c not in lead]], hide_index=True, use_container_width=True)
        st.download_button('Download credit spread candidates CSV', frame.to_csv(index=False), 'credit_spreads.csv', 'text/csv')
    st.warning('Approval status is always REVIEW REQUIRED. Yahoo does not supply verified quote timestamps here. Recheck live broker prices, standard deliverables, fees, earnings/dividends and both legs before approving a single multi-leg limit order. Planned max loss assumes the spread stays intact; early assignment or expiration can create stock exposure. Plan to close before expiration.')

