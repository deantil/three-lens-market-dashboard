"""Automatic official-source observations, kept separate from private paper fills."""
from datetime import datetime, timezone
import time

STATE_URL = 'https://raw.githubusercontent.com/deantil/three-lens-market-dashboard/trader-state/trader_source_state.json'

def render_monitor():
    import requests
    import streamlit as st
    import pandas as pd
    from trader_agent import collect

    @st.cache_data(ttl=60, show_spinner=False)
    def published_state():
        response=requests.get(STATE_URL,params={'report_minute':int(time.time())//60},timeout=12)
        response.raise_for_status()
        data=response.json()
        if data.get('schema_version')!=1:
            raise ValueError('Unknown source-state version')
        return data

    @st.cache_data(ttl=300, show_spinner=False)
    def check_sources_now():
        # Shared 5-minute cache prevents a public dashboard from hammering publisher sites.
        return collect()

    @st.fragment(run_every='60s')
    def panel():
        st.markdown('### Automatic official-source agent')
        st.caption('Scheduled public-source checks every 15 minutes, including while this dashboard sleeps. This panel reloads the published report every 60 seconds while open. GitHub schedules may be delayed.')
        st.info('Publications are collected automatically. Real-time personal trade alerts are not yet connected. A company article or old video is never treated as a new trade.')
        left,right=st.columns(2)
        reload=left.button('Refresh published agent report',key='agent_report_refresh')
        check=right.button('Check official sources now',key='agent_sources_now')
        if reload:
            published_state.clear()
        data=None
        try:
            data=published_state()
        except (requests.RequestException,ValueError):
            st.warning('Published report unavailable. Use Check official sources now for a direct public-source check; this does not access paid alerts.')
        if check:
            with st.spinner('Checking official publishers…'):
                st.session_state['trader_direct_observation']=check_sources_now()
        direct=st.session_state.get('trader_direct_observation')
        if direct and (not data or direct['last_run_utc']>data['last_run_utc']):
            data=direct
            st.caption('Showing a direct check (cached up to five minutes). Scheduled reports remain the durable history.')
        if not data:
            return
        previous_report=st.session_state.get('trader_agent_report',{})
        st.session_state['trader_agent_report']=data
        if previous_report.get('last_run_utc')!=data['last_run_utc']:
            # Refresh the per-trader sections as well as this timed panel.
            st.rerun()
        observed=datetime.fromisoformat(data['last_run_utc'])
        age=(datetime.now(timezone.utc)-observed).total_seconds()/60
        st.caption(f"Last actual collection: {data['last_run_utc']} · {age:.0f} minutes ago · {len(data.get('items',[]))} observed publication links · {data.get('new_items_this_run',0)} first seen this run")
        if age>45:
            st.warning('Collector report is over 45 minutes old. Do not assume monitoring is current; check the workflow.')
        st.link_button('View scheduled agent runs','https://github.com/deantil/three-lens-market-dashboard/actions/workflows/trader-monitor.yml')
        st.dataframe(pd.DataFrame([{'Trader':s['trader'],'Source':s['source_id'],'Access':s['status'],
            'Checked UTC':s['checked_utc'],'Items':s['items_returned'],'Coverage':s['note'],
            'Issue':s.get('error','')} for s in data['sources']]),hide_index=True,use_container_width=True)
        choice=st.selectbox('Automatically collected updates',['All','Alfredo Holguin','John Carter','Tony Saliba','Tom Sosnoff','Bruce Marshall','CNBC Investing Club'],key='agent_trader_filter')
        rows=[r for r in data.get('items',[]) if choice=='All' or r['trader']==choice]
        if rows:
            st.dataframe(pd.DataFrame([{'Trader context':r['trader'],'Publisher title':r['title'],
                'Author':r.get('author') or 'Not established','Published UTC':r.get('published_utc') or 'Not supplied',
                'First observed UTC':r['first_seen_utc'],'Baseline item':r.get('baseline',False),
                'Classification':r['classification'],'Source':r['source_url']} for r in rows]),
                hide_index=True,use_container_width=True,
                column_config={'Source':st.column_config.LinkColumn('Official source')})
        else:
            st.info('No publication links observed for this selection. Availability-only sources do not supply trade alerts.')
        st.caption('First observed is when this agent found an item, not its publication time. Initial items are marked baseline; no historical item becomes a fresh alert. Only publication titles, dates and links are published, never subscriber messages, account details or paper fills.')
        with st.expander('Connect actual trade alerts — legitimate routes'):
            st.markdown('''- **Official X API:** an adapter is implemented for HolguinTrades and johnfcarter, both linked by their official sites. It remains disabled until you explicitly approve API access and a spending cap. Public posts may still omit trades or exits. No X scraping or unofficial mirrors.
- **Carter:** the official room has an Alerts pane searchable by “JC” and a Save export. A subscription does not automatically grant permission for bot access or redistribution; confirm an approved integration/export route first.
- **Holguin:** his site offers SMS enrollment, with trial/subscription wording varying between pages. Confirm current terms before enrolling. His documented alerts omit premiums and quantities; fresh quotes and your sizing are required. The agent will never guess these values or automatically accept assignment instructions.
- **Saliba/Sosnoff:** no complete current personal signal feed has been verified. Their connected public material is research only.''')
            st.link_button('Carter official alert/export instructions','https://www.simplertrading.com/trading-education/tutorials/where-can-i-see-johns-trades')
            st.link_button('Holguin official alert format','https://holguintrades.com/trainings/understanding_text_syntax')
    panel()

def render_trader_sections(name):
    import streamlit as st
    import pandas as pd
    data=st.session_state.get('trader_agent_report',{})
    st.markdown('### 1 · Current disclosed trades / holdings')
    coverage=data.get('holdings_coverage',{}).get(name,{})
    positions=[r for r in data.get('current_positions',[]) if r.get('trader')==name]
    if positions:
        st.dataframe(pd.DataFrame(positions),hide_index=True,use_container_width=True)
    else:
        st.warning('Current holdings are not publicly verified. No connected complete position source is available; this is not a statement that the trader has no positions.')
    st.caption('Holdings as of: '+str(coverage.get('as_of_utc') or 'Unknown')+' · Complete portfolio: '+('Yes' if coverage.get('complete') else 'Not established'))
    st.markdown('### 2 · New trade alerts')
    alerts=[r for r in data.get('trade_alerts',[]) if r.get('trader')==name]
    if alerts:
        st.warning('These are detected trade-related source updates, not confirmed executions. Baseline items are historical discoveries, not newly taken trades. Follow the source to verify details; no holdings or orders are inferred.')
        st.dataframe(pd.DataFrame([{'Update':r['title'],'Published UTC':r['published_utc'],
            'Observed UTC':r['observed_utc'],'Status':r['alert_state'],
            'Verification':r['classification'],'Source':r['source_url']} for r in alerts]),
            hide_index=True,use_container_width=True,column_config={'Source':st.column_config.LinkColumn('Official source')})
    else:
        st.info('No verified new entry, exit or adjustment has been captured. Personal real-time trade feeds are not connected. Publication monitoring does not prove that no trade occurred.')
    st.caption('An alert needs trader identity, explicit trade intent, source publication time, observation time and contract details. Missing fields stay missing. A detected entry alone cannot establish current holdings without subsequent exits and adjustments.')
    with st.expander('Public research updates and source coverage'):
        sources=[s for s in data.get('sources',[]) if s['trader']==name]
        for source in sources:
            st.write(source['source_id']+' · '+source['status']+' · '+source['checked_utc'])
            st.caption(source['note'])
            st.link_button('Open '+source['source_id'],source['url'])
        rows=[r for r in data.get('items',[]) if r['trader']==name]
        if rows:
            st.dataframe(pd.DataFrame([{'Title':r['title'],'Published UTC':r.get('published_utc') or 'Not supplied',
                'Observed UTC':r['first_seen_utc'],'Type':r['classification'],'Source':r['source_url']} for r in rows]),
                hide_index=True,use_container_width=True,column_config={'Source':st.column_config.LinkColumn('Official source')})
        else:
            st.caption('No public items collected for this trader context yet.')
