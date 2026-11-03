import gzip
import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import pandas as pd
import requests
from dual_signal_scan import fetch_batch

def universe():
    symbols=[]
    for filename in ("nasdaqlisted.txt","otherlisted.txt"):
        r=requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/"+filename,timeout=30)
        r.raise_for_status()
        df=pd.read_csv(StringIO(r.text),sep="|",dtype=str)
        col="Symbol" if "Symbol" in df else "ACT Symbol"
        df=df[df[col].notna() & ~df[col].str.startswith("File Creation Time",na=False)]
        if "Test Issue" in df: df=df[df["Test Issue"].eq("N")]
        if "ETF" in df: df=df[df["ETF"].ne("Y")]
        if "Security Name" in df: df=df[~df["Security Name"].str.contains("Warrant|Right|Unit|Preferred",case=False,na=False)]
        symbols.extend(df[col].tolist())
    return sorted(set(x.replace(".","-").replace("$","-") for x in symbols if isinstance(x,str) and x.isascii()))

def main():
    symbols=universe()
    rows={}
    failures=[]
    for i in range(0,len(symbols),50):
        chunk=tuple(symbols[i:i+50])
        try:
            rows.update(fetch_batch(chunk,False))
        except Exception:
            failures.extend(chunk)
        print(i,len(symbols),len(rows),flush=True)
    if not rows: raise RuntimeError("No results")
    payload=dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),universe_size=len(symbols),usable_count=len(rows),failed_symbols=failures,rows=list(rows.values()))
    Path("data").mkdir(exist_ok=True)
    with gzip.open("data/full_universe_daily.json.gz","wt",encoding="utf-8") as f:
        json.dump(payload,f)

if __name__=="__main__":
    main()
