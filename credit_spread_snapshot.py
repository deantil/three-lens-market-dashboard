"""Collect public market data only; never reads broker credentials or sends orders."""
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from credit_spread_scanner import scan_symbol, DEFAULT_SYMBOLS

def main():
    stocks = {}
    symbols = DEFAULT_SYMBOLS.split()
    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = list(pool.map(lambda s: scan_symbol(s, 'Both', 3., '7–45 DTE', stocks, False), symbols))
    # A failed collection cannot silently publish an apparently healthy empty feed.
    loaded = sum(status.get('Chains loaded', 0) for _, status in reports)
    if not loaded:
        raise RuntimeError('No chains retrieved; existing snapshot remains untouched')
    data = {'collected_utc': datetime.now(timezone.utc).isoformat(), 'stocks': stocks,
            'coverage': [status for _, status in reports]}
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return None if isinstance(value, float) and not math.isfinite(value) else value
    with open('credit_market_snapshot.json', 'w') as f:
        json.dump(clean(data), f, default=str, allow_nan=False)
    print(json.dumps({'chains_loaded': loaded, 'coverage': data['coverage']}, default=str))

if __name__ == '__main__':
    main()

