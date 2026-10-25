"""Private-session options research ledger. Never connects to a broker."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import csv
import io
from urllib.parse import urlparse

PROFILES = {
    'Alfredo Holguin': {
        'url': 'https://holguintrades.com/faqs',
        'source': 'Official SMS subscription; access and automation permission must be confirmed.',
        'method': 'Obtain alerts directly from Holguin Trades. Record the exact entry, exit and each adjustment. The site describes put selling: do not convert a sold put into a bought put.',
        'compatibility': 'Short puts are outside the current long-options simulator. Research only until collateral, assignment and short-option risk are supported.',
    },
    'John Carter': {
        'url': 'https://www.simplertrading.com/traders/john-carter',
        'source': 'Official Simpler Trading room/alerts; confirm which product includes Carter personally.',
        'method': 'Use an authorized room or alert subscription. Identify the named trader on every alert, capture all legs and exits, and distinguish a setup discussion from an actual entry.',
        'compatibility': 'Only explicit single-leg long calls/puts fit the current simulator. Spreads and futures remain research only.',
    },
    'Tony Saliba': {
        'url': 'https://www.liquidmercury.com/leadership',
        'source': 'No current retail entry/exit alert feed verified.',
        'method': 'Monitor official publications for explicit, timestamped trades. Educational examples and historical trades are not current copy signals. Confirm a legitimate signal service before importing alerts.',
        'compatibility': 'Research watchlist until an authorized, complete signal source is established.',
    },
    'Tom Sosnoff': {
        'url': 'https://www.moneyshow.com/expert/e9e385ec29154496a19c8490b9fad291/',
        'source': 'Official current appearances/publications; no complete current copy feed verified.',
        'method': 'Use official current material, not old tastylive clips presented as new signals. Log only explicit contracts, entry times, quantities and exits; commentary stays research only.',
        'compatibility': 'Short-premium and multi-leg strategies are outside the current long-options simulator.',
    },
    'Bruce Marshall': {
        'url':'https://www.simplertrading.com/traders/bruce-marshall',
        'source':'Official Simpler Trading free publications; Options Income Mastery member alerts/portfolio need authorized access.',
        'method':'Monitor free official updates automatically. His official membership covers entries, adjustments and exits; a complete current portfolio is not publicly verified. Keep every spread leg and adjustment together.',
        'compatibility':'Calendars, diagonals, butterflies and condors need full multi-leg risk accounting. Research only in the current long-option simulator.',
    },
    'CNBC Investing Club': {
        'url':'https://www.cnbc.com/investingclub/',
        'source':'Official CNBC public RSS where accessible; Charitable Trust open/closed lots are a subscriber feature.',
        'method':'Track explicitly disclosed Trust transactions separately from Cramer commentary, watchlists and Bullpen ideas. The Trust is a stock portfolio, not an options-trader account. Full current holdings require authorized member access.',
        'compatibility':'Public RSS access can be intermittent. No paywall workaround or complete holdings claim. Do not convert stock mentions into options trades.',
    },
}
FIELDS = ['trade_id', 'trader', 'source_url', 'published_utc', 'received_utc', 'contract',
          'action', 'quantity', 'signal_limit', 'status', 'paper_entry', 'paper_exit', 'fees', 'notes']
STATUSES = ['Research', 'Awaiting review', 'Approved for paper review', 'Paper open', 'Paper closed', 'Skipped']

def utc(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('Timestamps need a timezone, for example 2026-10-26T14:30:00Z.')
    return d.astimezone(timezone.utc)

def number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Invalid numeric value.') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('Numbers must be finite and non-negative.')
    return result

def validate(rows):
    seen = set()
    clean = []
    for index, raw in enumerate(rows, 1):
        row = {k: str(raw.get(k, '') or '').strip() for k in FIELDS}
        if not row['trade_id']:
            raise ValueError(f'Row {index}: trade_id is required.')
        key = row['trade_id']
        if key in seen:
            raise ValueError('Duplicate trade_id. Use a distinct ID for each tracked trade.')
        seen.add(key)
        if row['trader'] not in PROFILES or row['status'] not in STATUSES:
            raise ValueError(f'Row {index}: unknown trader or status.')
        link = urlparse(row['source_url'])
        if link.scheme != 'https' or not link.netloc:
            raise ValueError('An HTTPS source link is required.')
        published, received = utc(row['published_utc']), utc(row['received_utc'])
        if received < published or published > datetime.now(timezone.utc):
            raise ValueError('Receipt must follow publication; publication cannot be in the future.')
        if not row['contract'] or row['action'] not in ['BTO', 'STC', 'STO', 'BTC', 'MULTILEG']:
            raise ValueError('Contract and explicit BTO/STC/STO/BTC/MULTILEG action required.')
        qty = number(row['quantity'])
        if qty < 1 or qty != qty.to_integral_value():
            raise ValueError('Quantity must be a positive whole number.')
        number(row['signal_limit'])
        for field in ['paper_entry', 'paper_exit', 'fees']:
            if row[field]:
                number(row[field])
        if row['status'] in ['Paper open', 'Paper closed'] and not row['paper_entry']:
            raise ValueError('Paper positions require an actual recorded paper entry fill.')
        if row['status'] == 'Paper closed' and (not row['paper_exit'] or not row['fees']):
            raise ValueError('Closed positions require exit fill and total fees (use 0 if zero).')
        clean.append(row)
    return clean

def pnl(row):
    # Only a complete single-leg long round trip supports this simple calculation.
    if row['status'] != 'Paper closed' or row['action'] != 'BTO':
        return None
    return float((number(row['paper_exit'])-number(row['paper_entry'])) * number(row['quantity']) * 100 - number(row['fees']))

def export_csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    for row in rows:
        # Prevent spreadsheet formula execution in exported untrusted text.
        writer.writerow({k: ("'"+str(row.get(k, '')) if str(row.get(k, '')).startswith(('=', '+', '-', '@', '\t', '\r')) else row.get(k, '')) for k in FIELDS})
    return out.getvalue()

def render():
    import pandas as pd
    import streamlit as st
    from trader_monitor_ui import render_monitor, render_trader_sections

    st.subheader('Options trader tracker')
    st.caption('Paper research only · Automatic official publication monitoring · No broker orders')
    render_monitor()
    st.markdown('### Your private paper-trade ledger')
    st.caption('The automatic source report above is durable. Your personal fill records below remain private to this session. Ledger refresh does not fetch subscriber alerts.')
    st.warning('Your ledger is private to this browser session and can be lost when the session expires or the app restarts. Download it after updates and import it next time. Nothing is written to the public repository.')
    st.session_state.setdefault('trader_ledger', [])
    st.session_state.setdefault('trader_revision', 0)
    if st.button('Refresh trader tracker', key='trader_refresh'):
        st.session_state['trader_refreshed'] = datetime.now(timezone.utc).isoformat()
    st.caption('Last manual refresh (UTC): ' + st.session_state.get('trader_refreshed', 'Not refreshed yet'))
    upload = st.file_uploader('Restore/import authorized tracker CSV', type=['csv'], key='trader_import')
    if st.button('Import CSV into this session', disabled=upload is None):
        try:
            if upload.size > 2_000_000:
                raise ValueError('CSV limit is 2 MB.')
            incoming = list(csv.DictReader(io.StringIO(upload.getvalue().decode('utf-8-sig'))))
            combined = {r['trade_id']: r for r in st.session_state['trader_ledger']}
            # Validate before merging, so duplicates in the import are rejected.
            for row in validate(incoming):
                combined[row['trade_id']] = row
            st.session_state['trader_ledger'] = validate(list(combined.values()))
            st.session_state['trader_revision'] += 1
            st.success('Imported. Matching trade IDs were updated; other saved trades were retained.')
        except (ValueError, UnicodeError) as exc:
            st.error(str(exc))
    st.download_button('Download saved tracker CSV', export_csv(st.session_state['trader_ledger']), 'options_trader_tracker.csv', 'text/csv')
    st.download_button('Download blank CSV template', export_csv([]), 'trader_template.csv', 'text/csv')

    tabs = st.tabs(list(PROFILES))
    for tab, (name, profile) in zip(tabs, PROFILES.items()):
        with tab:
            render_trader_sections(name)
            st.markdown(f'**Signal source:** {profile["source"]}')
            st.link_button('Open source / verify access', profile['url'])
            st.write(profile['method'])
            st.caption(profile['compatibility'])
            saved = [r for r in st.session_state['trader_ledger'] if r['trader'] == name]
            closed = [pnl(r) for r in saved if pnl(r) is not None]
            a, b, c = st.columns(3)
            a.metric('Tracked trades', len(saved))
            b.metric('Open paper positions', sum(r['status'] == 'Paper open' for r in saved))
            c.metric('Recorded long-option net P&L', f'${sum(closed):,.2f}' if closed else 'No closed long trades')
            st.caption('P&L uses operator-entered fills, total round-trip fees and a standard 100 multiplier. It excludes short options, spreads and partial exits. This is your paper ledger, not the trader’s verified return.')
            if not saved:
                st.info('No trades recorded. No sample trades or performance have been invented.')
            with st.form('ledger_'+name):
                frame = pd.DataFrame(saved, columns=FIELDS).astype(str)
                edited = st.data_editor(frame, hide_index=True, num_rows='dynamic', use_container_width=True,
                    key=f'trader_editor_{name}_{st.session_state["trader_revision"]}',
                    column_config={'trader': st.column_config.SelectboxColumn(options=[name], default=name, required=True),
                                   'status': st.column_config.SelectboxColumn(options=STATUSES, default='Research', required=True),
                                   'action': st.column_config.SelectboxColumn(options=['BTO', 'STC', 'STO', 'BTC', 'MULTILEG'], required=True)})
                if st.form_submit_button('Save '+name+' ledger'):
                    try:
                        rows = edited.fillna('').to_dict('records')
                        for row in rows:
                            row['trader'] = name
                        others = [r for r in st.session_state['trader_ledger'] if r['trader'] != name]
                        st.session_state['trader_ledger'] = validate(others + rows)
                        st.session_state['trader_revision'] += 1
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
    with st.expander('How to copy a trade into thinkorswim paperMoney', expanded=False):
        st.markdown('''1. Obtain the complete alert from an authorized source. Save its link, publication time and receipt time. Confirm it is an entry or exit, not commentary.
2. Record underlying, expiry, strike, call/put, each leg, buy/sell, open/close, quantity, limit and exit plan. Keep updates and partial exits in notes. This simple P&L ledger handles full single-leg long round trips only.
3. Open thinkorswim **paperMoney** and select the exact contract from the option chain. Check the account says paper, the multiplier is 100, market data is current and the bid/ask is tradable. Do not substitute a similar contract.
4. Review premium at risk, spread, fees and your own paper budget. Skip stale, ambiguous or unaffordable alerts. A seller’s short put is not the same trade as buying a put.
5. Review a DAY LIMIT ticket with the exact opening/closing instruction and approve it yourself in paperMoney. A tracker status does not submit, approve or fill any broker order.
6. Record your actual paper fill, not the trader’s claimed fill. Track exits and adjustments, then record exit fill and total fees. Compare your results after delay and costs before considering live copying.''')
