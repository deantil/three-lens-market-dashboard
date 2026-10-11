# Weekly paper strategy review

Generated 2026-10-11T17:42:01.325444+00:00
Paper only. No strategy changes are applied automatically.

| Period | Costs | Return % | Max DD % | Exit fills | SPY % |
|---|---|---:|---:|---:|---:|
| development | base | -2.45 | -3.009 | 6 | 13.198 |
| development | stress | -3.284 | -3.545 | 6 | 12.746 |
| holdout | base | -1.624 | -1.993 | 4 | 17.597 |
| holdout | stress | -2.1 | -2.418 | 4 | 17.127 |

Options: insufficient_data; 0 archived observations; 0 filled exits.
At least 25 exits across distinct trades and adequate regime coverage are needed before drawing conclusions.

## Limitations
- Fixed current ETF universe, not full-market or crypto validation; universe selection still subjective.
- IEX coverage and split-adjusted daily bars; dividends excluded from strategy and SPY benchmark.
- Close-based stops; next-open fills include gaps, fractional shares and shared cash; no shorting or leverage.
- No fitted parameters or automatic promotion. Historical holdout has not been blind to the developer.
- Options use archived OPRA quotes only, 100x multiplier, bid/ask and $0.65 per contract per side; no synthetic premiums.
- Sparse quote archive cannot establish continuous execution; missing exits charged full premium.
- Crypto backtest not implemented; crypto remains paper-only and unvalidated.
