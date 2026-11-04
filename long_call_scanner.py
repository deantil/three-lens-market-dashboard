"""Market-cap-first Kotegawa stock discovery and purchased-call research."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import gzip
import json
import pandas as pd
import requests
import streamlit as st
from kotegawa_options_engine import select_stock_signals
from long_call_engine import analyze_calls

VERSION='long-calls-v3.0'

@st.cache_data(ttl=60,show_spinner=False)
def load_call_feed():
    r=requests.get('https://raw.githubusercontent.com/deantil/three-lens-market-dashboard/credit-scan-state/long_call_market_snapshot.json.gz',timeout=35)
    r.raise_for_status()
    return json.loads(gzip.decompress(r.content))

def run_call_research(feed,fees=1.3,rate=.04,now=None):
    now=now or datetime.now(timezone.utc)
    market_day=now.astimezone(ZoneInfo('America/New_York')).date()
    snapshot=feed['stock_snapshot']
    selection=select_stock_signals(snapshot['rows'],snapshot.get('generated_at_utc'),today=market_day)
    signals=sorted(selection['signals'],key=lambda s:(-(feed.get('companies',{}).get(s['Ticker'],{}).get('market_cap') or 0),s['Ticker']))
    stocks,contracts=[],[]
    for rank,signal in enumerate(signals,1):
        symbol=signal['Ticker']; company=feed.get('companies',{}).get(symbol,{})
        row=dict(signal,**{'Market cap rank':rank,'Market cap USD':company.get('market_cap') if company.get('currency')=='USD' else None,
            'Company':company.get('company',symbol),'Sector':company.get('sector','Unknown'),
            'Market cap retrieved UTC':company.get('retrieved_utc'),'Market cap status':'Available' if company.get('market_cap') else 'UNAVAILABLE — sorted last',
            'Stock outcome':'BEARISH SETUP — no call purchase analysis','Call contracts analyzed':0})
        # Provider values must be USD to compare US equity market capitalizations.
        if company.get('currency')!='USD': row['Market cap status']='UNAVAILABLE / currency unverified — sorted last'
        if signal['Direction']!='Bull put': stocks.append(row); continue
        data=feed.get('calls',{}).get(symbol,{})
        errors=list(data.get('errors',[]))
        try:
            age=(now-datetime.fromisoformat(data['retrieved_utc'])).total_seconds()/60
            if not 0<=age<=20: raise ValueError('Option retrieval older than 20 minutes or future-dated')
            if not data.get('spot') or not data.get('chains'): raise ValueError('No option chains or underlying price')
            found=[]
            for exp,chain in data['chains'].items():
                result=analyze_calls(symbol,data['spot'],signal['SMA25 $'],exp,chain,today=market_day,rate=rate,fees=fees)
                for option in result:
                    option.update({'Market cap rank':rank,'Market cap USD':row['Market cap USD'],
                        'Stock signal deviation %':signal['Deviation %'],'Stock snapshot price $':signal['Price $'],
                        'Underlying reference $':data['spot'],'Current reference deviation %':round((data['spot']/signal['SMA25 $']-1)*100,2),
                        'SMA25 scenario target $':signal['SMA25 $'],'Stock price timestamp':data.get('price_timestamp'),
                        'Option retrieved UTC':data['retrieved_utc'],'Quote freshness':'Unverified / delayed; retrieval is not quote time',
                        'Historical volatility 20d %':(data.get('historical_volatility_20d') or 0)*100 or None,
                        'Earnings timestamp (unverified)':company.get('earnings_timestamp'),
                        'Ex dividend timestamp (unverified)':company.get('ex_dividend_timestamp'),
                        'Event review':'Earnings/dividends/catalyst require confirmation',
                        'Data source':'Yahoo public background feed; Schwab not connected'})
                    stressed=option.get('Target scenario IV -25% profit $')
                    hv=data.get('historical_volatility_20d')
                    option['IV / historical volatility ratio']=(option['IV %']/100/hv if hv and hv>0 and option.get('IV %') else None)
                    option['Breakeven rise from reference %']=round((option['Expiration breakeven $']/data['spot']-1)*100,2)
                    for source,label in [('earnings_timestamp','Earnings date UTC (provider, unverified)'),('ex_dividend_timestamp','Ex dividend date UTC (provider, unverified)')]:
                        try: option[label]=datetime.fromtimestamp(company[source],timezone.utc).date().isoformat()
                        except (KeyError,ValueError,TypeError,OverflowError): option[label]='Unavailable'
                    option['Research assessment']=('REVIEW — liquidity concerns' if option.get('Liquidity concerns') else
                        'REVIEW — model unavailable' if stressed is None else
                        'REVIEW — recovery scenario survives IV decline; thesis/events unconfirmed' if stressed>0 else
                        'UNFAVORABLE under stressed recovery scenario')
                    if option['Current reference deviation %'] > -20:
                        option['Research assessment']='STOCK SIGNAL NEEDS RECHECK — current reference no longer 20% below snapshot SMA25'
                    found.append(option)
            found.sort(key=lambda r:(r.get('Liquidity concerns',True),r.get('Target scenario IV -25% profit $') is None,
                -(r.get('Target scenario IV -25% profit $') or 0),r['Expiration'],r['Strike']))
            for comparison_rank,option in enumerate(found,1): option['Call comparison rank']=comparison_rank
            row['Call contracts analyzed']=len(found)
            row['Option chains loaded']=len(data['chains'])
            row['Stock outcome']='CALLS ANALYZED — REVIEW REQUIRED' if found else 'NO USABLE 60–90 DTE CALLS'
            contracts.extend(found)
        except Exception as exc:
            row['Stock outcome']='DATA UNAVAILABLE — CALLS'; errors.append(str(exc))
        row['Data notes']='; '.join(errors)
        stocks.append(row)
    # Unknown/non-USD market caps are never invented or compared as USD.
    stocks.sort(key=lambda r:(-(r['Market cap USD'] or 0),r['Ticker']))
    ranks={r['Ticker']:i for i,r in enumerate(stocks,1)}
    for row in stocks: row['Market cap rank']=ranks[row['Ticker']]
    for row in contracts: row['Market cap rank']=ranks[row['Ticker']]
    contracts.sort(key=lambda r:(r['Market cap rank'],r.get('Call comparison rank',999999)))
    return {'version':VERSION,'stocks':stocks,'rows':contracts,'errors':selection['errors'],
        'stock_histories_screened':selection['coverage']['stocks_evaluated'],
        'stock_snapshot_utc':snapshot.get('generated_at_utc'),'feed_collected_utc':feed.get('collected_utc'),
        'completed_utc':now.isoformat(),'fees':fees,'rate_assumption':rate,
        'bullish_stocks':sum(r['Direction']=='Bull put' for r in stocks),
        'options_data_unavailable':sum(r['Stock outcome']=='DATA UNAVAILABLE — CALLS' for r in stocks)}

def render():
    st.header('Kotegawa stocks by market cap → 60–90-day calls')
    st.caption(VERSION+' · Purchased calls for bullish setups · Research exercise · No account-size, premium or per-trade risk limits · No orders')
    st.write('Discover stock setups automatically, sort by market capitalization from largest to smallest, then analyze every available standard call in the 60–90-day expiration window for every bullish setup. Bearish overextensions remain visible but do not produce call-buy candidates.')
    st.caption('Larger market capitalization does not mean better value or a better trade. Missing market caps sort last. Stock rules remain the transparent Kotegawa-inspired SMA25, price, dollar-volume and reference reward/risk rules; they are not a verified trading edge.')
    with st.form('long_call_form'):
        a,b=st.columns(2)
        fees=a.number_input('Estimated opening + closing fees per call ($)',min_value=0.,value=1.3,step=.1)
        rate=b.number_input('Annual interest-rate assumption (%)',min_value=0.,max_value=30.,value=4.,step=.25)
        st.caption('One purchased call represents 100 shares. Full premium plus entered fees can be lost. Fees are an assumption, not a risk limit. No stock or contract count cap is applied.')
        run=st.form_submit_button('Run market-cap stock → call analysis',type='primary')
    if run:
        try:
            load_call_feed.clear()
            with st.spinner('Loading full stock discovery and available 60–90-day call chains…'):
                st.session_state['long_call_report']=run_call_research(load_call_feed(),fees,rate/100)
        except Exception as exc:
            st.session_state.pop('long_call_report',None)
            st.error('DATA UNAVAILABLE — call feed could not load: '+str(exc)[:240])
    report=st.session_state.get('long_call_report')
    if not report:
        st.info('Click Run market-cap stock → call analysis. No ticker entry is required. Background collection may be incomplete when a provider fails; coverage is reported explicitly.')
        return
    st.caption(f"Completed UTC: {report['completed_utc']} · Stock snapshot: {report['stock_snapshot_utc']} · Option feed collected: {report['feed_collected_utc']}")
    a,b,c,d=st.columns(4)
    a.metric('Stock histories screened',report['stock_histories_screened']);b.metric('Stock setups',len(report['stocks']))
    c.metric('Calls analyzed',len(report['rows']));d.metric('Bullish stocks missing calls',report['options_data_unavailable'])
    for error in report['errors']: st.error(error)
    stocks=pd.DataFrame(report['stocks'])
    st.subheader('Stock setups — largest market cap first')
    if not stocks.empty:
        cols=['Market cap rank','Ticker','Company','Market cap USD','Market cap status','Signal direction','Price $','SMA25 $','Deviation %','Stock outcome','Call contracts analyzed','Data notes','Market cap retrieved UTC']
        st.dataframe(stocks[[c for c in cols if c in stocks]],hide_index=True,use_container_width=True,
            column_config={'Market cap USD':st.column_config.NumberColumn(format='$%.0f')})
        st.download_button('Download market-cap ranked stock list',stocks.to_csv(index=False),'kotegawa_market_cap_stocks.csv','text/csv')
    if report['rows']:
        frame=pd.DataFrame(report['rows'])
        st.subheader('Call comparisons — research, not buy approvals')
        st.caption('Stock order: market cap descending. Within each stock: liquidity concerns last, then modeled profit at the SMA25 target after an IV decline, across all expirations/strikes. No premium/risk/strike/delta filters. Entry uses ask. Scenarios are halfway to expiration; target = snapshot SMA25, not a forecast. IV stress = 25% relative decrease. Black–Scholes assumes European exercise, constant rates/IV and zero dividends; American calls/events can differ. Greeks are estimates per share; multiply theta/vega by 100 for one contract.')
        lead=['Market cap rank','Ticker','Call comparison rank','Expiration','DTE','Strike','Ask entry / share $','Max loss incl fees $','Expiration breakeven $','IV %','Estimated delta','Estimated theta $/day','Estimated vega $/IV point','Target scenario profit $','Target scenario IV -25% profit $','Flat scenario profit $','Down 10% scenario profit $','Liquidity','Research assessment','Approval status']
        st.dataframe(frame[[c for c in lead if c in frame]+[c for c in frame if c not in lead]],hide_index=True,use_container_width=True)
        st.download_button('Download all call comparisons',frame.to_csv(index=False),'kotegawa_long_calls.csv','text/csv')
    else: st.info('No usable calls were returned for analysis. Review each stock’s coverage; unavailable data is not a no-opportunity conclusion.')
    st.warning('A fallen stock can keep falling. A call can lose money even if the stock rises because of premium paid, time decay or falling IV. Earnings, dividends, catalyst, price recovery timing and live broker quotes require confirmation. Data is delayed Yahoo research; Schwab/thinkorswim is not connected. Nothing is automatically approved to buy.')
