"""Session-local Schwab OAuth and market-data-only client. No order/account API.

Credentials/tokens never enter URLs, exports, logs, caches or public snapshots.
OAuth app key is public client identification; its secret stays in this session.
"""
from datetime import datetime, timezone, timedelta
import math
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit, parse_qs
import requests

class SchwabDataError(Exception):
    def __init__(self,message,status=None):
        super().__init__(message);self.status=status

def normalize_chain(symbol, payload, now=None):
    now=now or datetime.now(timezone.utc)
    spot=payload.get('underlyingPrice')
    try:
        spot=float(spot)
        if not math.isfinite(spot) or spot<=0: raise ValueError()
    except (ValueError,TypeError): raise SchwabDataError('Schwab returned no usable underlying price')
    chains={}; stale=0; rejected=0
    for expkey,strikes in payload.get('callExpDateMap',{}).items():
        exp=expkey.split(':')[0]
        try:dte=(datetime.fromisoformat(exp).date()-now.date()).days
        except ValueError:continue
        if not 60<=dte<=90:continue
        for contracts in strikes.values():
            for c in contracts:
                code=str(c.get('symbol','')).replace(' ','')
                if (c.get('putCall')!='CALL' or c.get('nonStandard',True)
                    or c.get('multiplier')!=100 or c.get('optionDeliverablesList')
                    or not re.fullmatch(re.escape(symbol.replace('-','').upper())+r'\d{6}C\d{8}',code)):
                    rejected+=1;continue
                try:
                    stamp=datetime.fromtimestamp(float(c['quoteTimeInLong'])/1000,timezone.utc)
                    age=(now-stamp).total_seconds()
                    if not 0<=age<=1200:raise ValueError()
                except (ValueError,TypeError,KeyError,OverflowError):
                    stale+=1;continue
                iv=c.get('volatility')
                try:iv=float(iv)/100
                except (ValueError,TypeError):iv=None
                chains.setdefault(exp,[]).append({'contractSymbol':code,'strike':c.get('strikePrice'),
                    'bid':c.get('bid'),'ask':c.get('ask'),'contractSize':'REGULAR',
                    'openInterest':c.get('openInterest'),'volume':c.get('totalVolume'),
                    'impliedVolatility':iv,'lastTradeDate':c.get('tradeTimeInLong'),
                    'brokerQuoteTime':stamp.isoformat()})
    return {'spot':spot,'chains':chains,'retrieved_utc':now.isoformat(),
        'price_timestamp':'Schwab chain underlying reference; quote time may differ',
        'source':'Schwab Market Data API','is_delayed':payload.get('isDelayed'),
        'errors':[f'{stale} stale/missing quote timestamps excluded; {rejected} nonstandard/unverified deliverables excluded']}

class SchwabMarketData:
    def __init__(self,client_id,client_secret,redirect_uri,session=None):
        u=urlsplit(redirect_uri)
        if u.scheme!='https' or not u.netloc or u.query or u.fragment:
            raise SchwabDataError('Use the exact HTTPS callback URL registered with Schwab, without query or fragment')
        if not client_id.strip() or not client_secret.strip():raise SchwabDataError('App key and secret are required')
        self._id=client_id.strip();self._secret=client_secret.strip();self._redirect=redirect_uri
        self._session=session or requests.Session();self._token={};self._expires=0;self._next=0
        self._state=secrets.token_urlsafe(32)

    def authorization_url(self):
        return 'https://api.schwabapi.com/v1/oauth/authorize?'+urlencode({'response_type':'code',
            'client_id':self._id,'redirect_uri':self._redirect,'state':self._state})

    def _exchange(self,data):
        try:r=self._session.post('https://api.schwabapi.com/v1/oauth/token',data=data,
            auth=(self._id,self._secret),timeout=25)
        except requests.RequestException:raise SchwabDataError('Schwab authorization service could not be reached') from None
        if r.status_code!=200:raise SchwabDataError(f'Schwab authorization failed (HTTP {r.status_code}); sign in again or check app credentials/callback',r.status_code)
        try:
            token=r.json()
            if not token.get('access_token'):raise ValueError()
            self._token={**self._token,**token};self._expires=time.monotonic()+max(0,float(token.get('expires_in',0))-60)
        except (ValueError,TypeError):raise SchwabDataError('Schwab returned an invalid authorization response') from None

    def complete_authorization(self,callback):
        u=urlsplit(callback);expected=urlsplit(self._redirect);q=parse_qs(u.query)
        if (u.scheme,u.netloc,u.path)!=(expected.scheme,expected.netloc,expected.path):
            raise SchwabDataError('Returned URL does not match the registered callback')
        if not secrets.compare_digest(q.get('state',[''])[0],self._state):raise SchwabDataError('Authorization state mismatch; start Connect Schwab again')
        code=q.get('code',[''])[0]
        if not code:raise SchwabDataError('No authorization code in the returned URL')
        self._exchange({'grant_type':'authorization_code','code':code,'redirect_uri':self._redirect})
        self._state=secrets.token_urlsafe(32)

    def get(self,path,params):
        if path not in ('/quotes','/chains'):raise SchwabDataError('Only market-data quotes and option chains are permitted')
        if self._expires<=time.monotonic():
            if not self._token.get('refresh_token'):raise SchwabDataError('Schwab sign-in is required')
            self._exchange({'grant_type':'refresh_token','refresh_token':self._token['refresh_token']})
        # Bound this session below 120 requests/minute. Never retry into a throttle.
        time.sleep(max(0,self._next-time.monotonic()));self._next=time.monotonic()+.6
        try:r=self._session.get('https://api.schwabapi.com/marketdata/v1'+path,params=params,
            headers={'Authorization':'Bearer '+self._token['access_token']},timeout=25)
        except requests.RequestException:raise SchwabDataError('Schwab market data could not be reached') from None
        if r.status_code!=200:raise SchwabDataError(f'Schwab market-data request failed (HTTP {r.status_code}); no Yahoo substitution was used',r.status_code)
        try:return r.json()
        except ValueError:raise SchwabDataError('Schwab returned invalid market-data JSON') from None

    def verify(self):
        data=self.get('/quotes',{'symbols':'SPY','fields':'quote'})
        quote=data.get('SPY',{}).get('quote',{})
        if not any(isinstance(quote.get(k),(int,float)) and math.isfinite(quote[k]) and quote[k]>0 for k in ('lastPrice','bidPrice','askPrice')):
            raise SchwabDataError('Schwab quote response did not contain a usable price for the requested symbol')
        return True

    def calls(self,symbol):
        today=datetime.now(timezone.utc).date()
        return normalize_chain(symbol,self.get('/chains',{'symbol':symbol,'contractType':'CALL',
            'strategy':'SINGLE','includeUnderlyingQuote':'true','fromDate':(today+timedelta(days=60)).isoformat(),
            'toDate':(today+timedelta(days=90)).isoformat()}))

def render_connection(st):
    with st.expander('Connect Schwab market data',expanded=not bool(st.session_state.get('schwab_verified'))):
        st.write('Developer approval enables your app. This step authorizes this browser session to retrieve quotes and call chains. Your app secret and tokens stay in this server session; they are not saved to GitHub or downloads. Closing/restarting the session requires reconnecting. No orders or account endpoints are implemented.')
        if st.session_state.get('schwab_verified'):
            st.success('Schwab market data verified with a SPY quote request for this session.')
            st.caption('This verifies access, not real-time entitlements or a trade recommendation. Each contract quote timestamp is checked during scanning.')
            return
        with st.form('schwab_prepare',clear_on_submit=True):
            key=st.text_input('Schwab app key',type='password')
            secret=st.text_input('Schwab app secret',type='password')
            callback=st.text_input('Exact registered callback URL',placeholder='Copy from your approved Schwab app')
            prepare=st.form_submit_button('Prepare Schwab sign-in')
        if prepare:
            try:st.session_state['schwab_pending']=SchwabMarketData(key,secret,callback)
            except SchwabDataError as exc:st.error(str(exc))
        client=st.session_state.get('schwab_pending')
        if client:
            st.link_button('Sign in and authorize at Schwab',client.authorization_url())
            st.write('Sign in yourself and review Schwab’s consent. After redirect, copy the complete returned callback URL, even if that page cannot load, and paste it below promptly. The code is short-lived. Never paste credentials or the returned URL into chat.')
            with st.form('schwab_finish',clear_on_submit=True):
                returned=st.text_input('Returned callback URL',type='password')
                finish=st.form_submit_button('Verify Schwab connection')
            if finish:
                try:
                    client.complete_authorization(returned);client.verify()
                    st.session_state['schwab_client']=client;st.session_state['schwab_verified']=True
                    st.session_state.pop('schwab_pending',None);st.rerun()
                except SchwabDataError as exc:st.error(str(exc))

