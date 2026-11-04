"""Public research feed: market caps for all setups, all available 60–90 DTE calls."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import json
import math
import yfinance as yf
from kotegawa_options_scanner import load_stock_snapshot
from kotegawa_options_engine import select_stock_signals

def collect_company(symbol):
    now=datetime.now(timezone.utc).isoformat()
    try:
        info=yf.Ticker(symbol).get_info()
        cap=info.get('marketCap')
        return symbol, {'market_cap':cap if cap and math.isfinite(float(cap)) and cap>0 else None,
            'company':info.get('shortName',symbol),'currency':info.get('currency','Unknown'),
            'sector':info.get('sector','Unknown'),'earnings_timestamp':info.get('earningsTimestamp'),
            'ex_dividend_timestamp':info.get('exDividendDate'),'dividend_yield':info.get('dividendYield'),
            'retrieved_utc':now,'error':''}
    except Exception as exc:
        return symbol, {'market_cap':None,'retrieved_utc':now,'error':str(exc)[:180]}

def collect_calls(signal):
    symbol=signal['Ticker']
    ticker=yf.Ticker(symbol)
    now=datetime.now(timezone.utc)
    out={'retrieved_utc':now.isoformat(),'chains':{},'errors':[], 'spot':None}
    try:
        hist=ticker.history(period='3mo',auto_adjust=True,timeout=12)
        if hist.empty: raise ValueError('No current price history')
        close=hist['Close'].dropna()
        if (now.date()-close.index[-1].date()).days>4: raise ValueError('Stale underlying price')
        out['spot']=float(close.iloc[-1])
        out['price_timestamp']=str(close.index[-1])
        hv=float(close.pct_change().dropna().tail(20).std()*math.sqrt(252))
        out['historical_volatility_20d']=hv if math.isfinite(hv) else None
        expirations=[e for e in ticker.options if 60 <= (datetime.fromisoformat(e).date()-now.date()).days <= 90]
        out['expirations_requested']=len(expirations)
        for exp in expirations:
            try:
                chain=ticker.option_chain(exp)
                out['chains'][exp]=chain.calls.to_dict('records')
            except Exception as exc: out['errors'].append(exp+': '+str(exc)[:140])
        if not expirations: out['errors'].append('No listed expirations in 60–90 DTE range')
    except Exception as exc: out['errors'].append(str(exc)[:180])
    return symbol,out

def main():
    stock_snapshot=load_stock_snapshot()
    selection=select_stock_signals(stock_snapshot['rows'],stock_snapshot.get('generated_at_utc'))
    companies={}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for symbol, info in pool.map(collect_company,[s['Ticker'] for s in selection['signals']]): companies[symbol]=info
    signals=sorted(selection['signals'],key=lambda s:(-(companies[s['Ticker']].get('market_cap') or 0),s['Ticker']))
    bullish=[s for s in signals if s['Direction']=='Bull put']
    calls={}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for symbol, data in pool.map(collect_calls,bullish): calls[symbol]=data
    data={'version':'long-calls-v3.0','collected_utc':datetime.now(timezone.utc).isoformat(),
          'stock_snapshot':stock_snapshot,'companies':companies,'calls':calls,
          'bullish_stocks_requested':len(bullish),'stock_setups':len(signals)}
    def clean(x):
        if isinstance(x,dict): return {k:clean(v) for k,v in x.items()}
        if isinstance(x,list): return [clean(v) for v in x]
        return None if isinstance(x,float) and not math.isfinite(x) else x
    with gzip.open('long_call_market_snapshot.json.gz','wt',encoding='utf-8') as f: json.dump(clean(data),f,default=str,allow_nan=False)
    print('LONG_CALL_FEED='+json.dumps({'setups':len(signals),'bullish_requested':len(bullish),
        'market_caps_available':sum(bool(c.get('market_cap')) for c in companies.values()),
        'stocks_with_chains':sum(bool(c['chains']) for c in calls.values()),
        'chains':sum(len(c['chains']) for c in calls.values()),
        'contracts':sum(len(rows) for c in calls.values() for rows in c['chains'].values())}))

if __name__=='__main__': main()
