"""Market-cap-first Kotegawa stock discovery and purchased-call research."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import gzip
import json
import math
import pandas as pd
import requests
import streamlit as st
from kotegawa_options_engine import select_stock_signals
from long_call_engine import analyze_calls, black_scholes_call
from schwab_market_data import render_connection, SchwabDataError

VERSION='long-calls-v4.1-schwab'

@st.cache_data(ttl=60,show_spinner=False)
def load_call_feed():
    r=requests.get('https://raw.githubusercontent.com/deantil/three-lens-market-dashboard/credit-scan-state/long_call_market_snapshot.json.gz',params={'refresh':int(datetime.now(timezone.utc).timestamp())//60},headers={'Cache-Control':'no-cache'},timeout=35)
    r.raise_for_status()
    return json.loads(gzip.decompress(r.content))

def load_schwab_feed(client, base, progress=None):
    """Keep stock discovery/market caps separate; replace every option chain.
    Failure never reuses Yahoo option rows while Schwab is selected."""
    now=datetime.now(timezone.utc)
    snapshot=base['stock_snapshot']
    selection=select_stock_signals(snapshot['rows'],snapshot.get('generated_at_utc'),today=now.astimezone(ZoneInfo('America/New_York')).date())
    signals=[s for s in selection['signals'] if s['Direction']=='Bull put']
    feed={**base,'calls':{},'collected_utc':now.isoformat()}
    for i,signal in enumerate(signals):
        symbol=signal['Ticker']
        try:feed['calls'][symbol]=client.calls(symbol)
        except SchwabDataError as exc:
            if exc.status in (400,401,403,429):raise
            feed['calls'][symbol]={'retrieved_utc':datetime.now(timezone.utc).isoformat(),'chains':{},'errors':['Schwab data unavailable for this symbol; no fallback used'],'source':'Schwab Market Data API'}
        if progress:progress((i+1)/max(1,len(signals)),f'Schwab call chains: {i+1}/{len(signals)}')
    return feed

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
                        'Option retrieved UTC':data['retrieved_utc'],'Quote freshness':('Schwab quote timestamps checked within 20 minutes; delayed entitlement='+str(data.get('is_delayed')) if data.get('source') else 'Unverified / delayed; retrieval is not quote time'),
                        'Historical volatility 20d %':(data.get('historical_volatility_20d') or 0)*100 or None,
                        'Earnings timestamp (unverified)':company.get('earnings_timestamp'),
                        'Ex dividend timestamp (unverified)':company.get('ex_dividend_timestamp'),
                        'Event review':'Earnings/dividends/catalyst require confirmation',
                        'Data source':data.get('source','Yahoo public background feed; Schwab not connected')})
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

def shortlist_ideas(rows, rate=.04, limit=50):
    """Research shortlist, one idea/underlying; never pad with failed candidates.

    Call spreads use actual same-expiry legs, buy ask/sell bid, standard 100
    multiplier. POP is a lognormal expiration estimate, not a broker statistic.
    """
    candidates=[]
    groups={}
    def pop(spot, breakeven, dte, iv):
        z=(math.log(spot/breakeven)+(rate-.5*iv*iv)*dte/365)/(iv*math.sqrt(dte/365))
        return 50*(1+math.erf(z/math.sqrt(2)))
    for original in rows:
        r=dict(original)
        if (r.get('Liquidity concerns',True) or r.get('Current reference deviation %',0)>-20
            or not r.get('IV %') or r.get('IV %',0)<=0
            or r.get('Target scenario IV -25% profit $') is None): continue
        groups.setdefault((r['Ticker'],r['Expiration']),[]).append(r)
        r['Long strike']=r['Strike'];r['Short strike']=None
        r['Entry debit / share $']=r['Ask entry / share $']
        r['Estimated expiration POP %']=pop(r['Underlying reference $'],r['Expiration breakeven $'],r['DTE'],r['IV %']/100)
        r['Max profit $']=None
        candidates.append(r)
    for group in groups.values():
        group=sorted(group,key=lambda r:r['Strike'])
        for i,long in enumerate(group):
            for short in group[i+1:]:
                width=short['Strike']-long['Strike']
                if width<=0 or short['Call contract']==long['Call contract']: continue
                debit=long['Ask entry / share $']-short['Bid / share $']
                fees=long['Round-trip fees $']+short['Round-trip fees $']
                risk=debit*100+fees
                if debit<=0 or risk>=width*100: continue
                r=dict(long)
                r.update({'Direction':'Bull call debit spread','Long strike':long['Strike'],
                    'Short strike':short['Strike'],'Short call contract':short['Call contract'],
                    'Entry debit / share $':debit,'Max loss incl fees $':risk,
                    'Round-trip fees $':fees,'Max profit $':width*100-risk,
                    'Expiration breakeven $':long['Strike']+risk/100,
                    'Open interest':min(long['Open interest'],short['Open interest']),
                    'Volume':min(long['Volume'],short['Volume']),
                    'Bid/ask width % of ask':max(long['Bid/ask width % of ask'],short['Bid/ask width % of ask']),
                    'Liquidity':'Both legs pass OI, volume and bid/ask checks',
                    'Research assessment':'REVIEW — defined-risk call debit spread; live quotes/events unconfirmed'})
                # Use one common IV for the vertical so modeled value stays within
                # its strike width; individual-leg IV marks can create fake arbitrage.
                iv=(long['IV %']+short['IV %'])/200
                remaining=(r['DTE']-r['Scenario horizon days'])/365
                for label,spot in [('Target',r['SMA25 scenario target $']),('Flat',r['Underlying reference $']),('Down 10%',r['Underlying reference $']*.9)]:
                    for mult,suffix in [(1.,''),(.75,' IV -25%')]:
                        value=black_scholes_call(spot,long['Strike'],remaining,iv*mult,rate)['price']-black_scholes_call(spot,short['Strike'],remaining,iv*mult,rate)['price']
                        r[label+' scenario'+suffix+' profit $']=round(value*100-risk,2)
                r['IV %']=iv*100
                r['Expiration target profit $']=min(max(r['SMA25 scenario target $']-long['Strike'],0),width)*100-risk
                r['Breakeven rise from reference %']=(r['Expiration breakeven $']/r['Underlying reference $']-1)*100
                r['Estimated model value / share $']=black_scholes_call(r['Underlying reference $'],long['Strike'],r['DTE']/365,iv,rate)['price']-black_scholes_call(r['Underlying reference $'],short['Strike'],r['DTE']/365,iv,rate)['price']
                r['IV / historical volatility ratio']=(iv*100/r['Historical volatility 20d %'] if r.get('Historical volatility 20d %') else None)
                r['Estimated expiration POP %']=pop(r['Underlying reference $'],r['Expiration breakeven $'],r['DTE'],iv)
                r['Model assumptions']='Vertical: common mean leg IV, European, no dividends; not broker POP'
                # Long-call-only fields do not describe the net vertical.
                for key in ['Estimated delta','Estimated gamma','Estimated theta $/day','Estimated vega $/IV point','Estimated rho $/rate point','Ask entry / share $','Premium $','Call comparison rank','Bid / share $','Moneyness','Moneyness spot/strike %','Intrinsic value / share $','Ask extrinsic value / share $']:
                    r.pop(key,None)
                for label in ['Target','Flat','Down 10%']:
                    for suffix in ['', ' IV -25%']:
                        r.pop(label+' scenario'+suffix+' value / share $',None)
                candidates.append(r)
    eligible=[]
    for r in candidates:
        risk=r['Max loss incl fees $'];profit=r['Target scenario IV -25% profit $']
        if profit<=0 or risk<=0: continue
        r['Stressed recovery return %']=100*profit/risk
        r['Downside scenario return %']=100*r['Down 10% scenario IV -25% profit $']/risk
        eligible.append(r)
    if not eligible: return []
    frame=pd.DataFrame(eligible)
    frame['Research score / 100']=(35*frame['Stressed recovery return %'].rank(pct=True)
        +25*frame['Downside scenario return %'].rank(pct=True)
        +20*frame['Bid/ask width % of ask'].rank(pct=True,ascending=False)
        +20*frame['Open interest'].rank(pct=True))
    frame=frame.sort_values(['Research score / 100','Market cap USD','Ticker','Expiration','Long strike'],ascending=[False,False,True,True,True],kind='stable')
    frame=frame.drop_duplicates('Ticker').head(min(50,max(0,limit))).copy()
    frame.insert(0,'Idea rank',range(1,len(frame)+1))
    frame['Approval status']='REVIEW REQUIRED — delayed quotes; not approved to trade'
    return frame.to_dict('records')


def render():
    st.header('Kotegawa stocks by market cap → 60–90-day calls')
    st.caption(VERSION+' · Purchased calls for bullish setups · Research exercise · No account-size, premium or per-trade risk limits · No orders')
    st.write('Discover stock setups automatically, sort by market capitalization from largest to smallest, then analyze every available standard call in the 60–90-day expiration window for every bullish setup. Bearish overextensions remain visible but do not produce call-buy candidates.')
    st.caption('Larger market capitalization does not mean better value or a better trade. Missing market caps sort last. Stock rules remain the transparent Kotegawa-inspired SMA25, price, dollar-volume and reference reward/risk rules; they are not a verified trading edge.')
    render_connection(st)
    source=st.selectbox('Option-chain source',['Schwab market data','Yahoo delayed research'],index=0 if st.session_state.get('schwab_verified') else 1)
    st.caption('Stock discovery and market-cap metadata still use the background stock feed. Selecting Schwab replaces option-chain data only; it does not connect TOS watchlists or alerts.')
    with st.form('long_call_form'):
        a,b=st.columns(2)
        fees=a.number_input('Estimated opening + closing fees per call ($)',min_value=0.,value=1.3,step=.1)
        rate=b.number_input('Annual interest-rate assumption (%)',min_value=0.,max_value=30.,value=4.,step=.25)
        st.caption('Analyze all available calls, then show at most 50 ranked ideas, one per stock, comparing long calls with defined-risk bull call debit spreads. No account-size or premium caps. Fees apply per leg.')
        run=st.form_submit_button('Run market-cap stock → call analysis',type='primary')
    if run:
        try:
            load_call_feed.clear()
            with st.spinner('Loading full stock discovery and available 60–90-day call chains…'):
                feed=load_call_feed()
                if source=='Schwab market data':
                    if not st.session_state.get('schwab_verified'):raise ValueError('Complete Connect Schwab market data first')
                    bar=st.progress(0.,text='Loading Schwab option chains')
                    feed=load_schwab_feed(st.session_state['schwab_client'],feed,lambda p,m:bar.progress(p,text=m))
                    bar.empty()
                st.session_state['long_call_report']=run_call_research(feed,fees,rate/100)
                st.session_state['long_call_report']['option_source']=source
        except Exception as exc:
            st.session_state.pop('long_call_report',None)
            st.error('DATA UNAVAILABLE — call feed could not load: '+str(exc)[:240])
    report=st.session_state.get('long_call_report')
    if not report:
        st.info('Click Run market-cap stock → call analysis. No ticker entry is required. Background collection may be incomplete when a provider fails; coverage is reported explicitly.')
        return
    st.caption('Option source for this report: '+report.get('option_source','Yahoo delayed research'))
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
        ideas=pd.DataFrame(shortlist_ideas(report['rows'],report['rate_assumption']))
        st.subheader('Top 50 trade ideas — long calls and bull call debit spreads')
        st.caption('Maximum 50, one per stock; fewer if fewer qualify. Requires the current reference to remain at least 20% below SMA25, both legs to pass existing liquidity checks, and positive modeled recovery profit after a 25% IV decline. Score uses percentile ranks: 35% stressed recovery return, 25% downside scenario return, 20% tighter bid/ask, 20% open interest. Market cap breaks ties. This is a research ranking, not expected return or a buy recommendation. Spread entry buys the lower strike at ask and sells the higher strike at bid, same expiration; fees include both legs. POP is an expiration lognormal estimate with zero dividends and constant IV, not TOS POP. No naked short options.')
        if ideas.empty:
            st.info('No ideas pass the shortlist checks in this feed. Failed or unavailable data is not filled with weaker ideas.')
        else:
            cols=['Idea rank','Ticker','Direction','Research score / 100','Expiration','DTE','Long strike','Short strike','Entry debit / share $','Max loss incl fees $','Max profit $','Estimated expiration POP %','Stressed recovery return %','Downside scenario return %','Open interest','Volume','Liquidity','Approval status']
            st.dataframe(ideas[cols],hide_index=True,use_container_width=True)
            st.download_button('Download top 50 trade ideas',ideas.to_csv(index=False),'project_t_top50_ideas.csv','text/csv')
            st.download_button('Download shortlist stocks for TOS',ideas[['Ticker']].to_csv(index=False,header=False),'project_t_top50_tos.csv','text/csv')
        st.subheader('Call comparisons — research, not buy approvals')
        st.caption('Stock order: market cap descending. Within each stock: liquidity concerns last, then modeled profit at the SMA25 target after an IV decline, across all expirations/strikes. No premium/risk/strike/delta filters. Entry uses ask. Scenarios are halfway to expiration; target = snapshot SMA25, not a forecast. IV stress = 25% relative decrease. Black–Scholes assumes European exercise, constant rates/IV and zero dividends; American calls/events can differ. Greeks are estimates per share; multiply theta/vega by 100 for one contract.')
        lead=['Market cap rank','Ticker','Call comparison rank','Expiration','DTE','Strike','Ask entry / share $','Max loss incl fees $','Expiration breakeven $','IV %','Estimated delta','Estimated theta $/day','Estimated vega $/IV point','Target scenario profit $','Target scenario IV -25% profit $','Flat scenario profit $','Down 10% scenario profit $','Liquidity','Research assessment','Approval status']
        with st.expander('Underlying call analysis — supporting data'):
            st.dataframe(frame[[c for c in lead if c in frame]+[c for c in frame if c not in lead]],hide_index=True,use_container_width=True)
        st.download_button('Download all call comparisons',frame.to_csv(index=False),'kotegawa_long_calls.csv','text/csv')
    else: st.info('No usable calls were returned for analysis. Review each stock’s coverage; unavailable data is not a no-opportunity conclusion.')
    st.warning('A fallen stock can keep falling. A call can lose money even if the stock rises because of premium paid, time decay or falling IV. Earnings, dividends, catalyst, price recovery timing and live broker quotes require confirmation. Check each row’s data source and quote timestamps. Schwab connection does not synchronize TOS watchlists or alerts. Nothing is automatically approved to buy.')

