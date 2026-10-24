# Weekly paper strategy review

Generated 2026-10-24T17:49:58.029712+00:00
Paper only. No strategy changes are applied automatically.

| Period | Costs | Return % | Max DD % | Exit fills | SPY % |
|---|---|---:|---:|---:|---:|
| development | base | -1.606 | -3.009 | 8 | 15.545 |
| development | stress | -2.352 | -3.372 | 8 | 15.084 |
| holdout | base | -2.487 | -2.487 | 5 | 12.106 |
| holdout | stress | -2.956 | -2.956 | 5 | 11.658 |

Options: insufficient_data; 8200 archived observations; 0 filled exits.
At least 25 exits across distinct trades and adequate regime coverage are needed before drawing conclusions.

## Limitations
- Fixed current ETF universe, not full-market or crypto validation; universe selection still subjective.
- IEX coverage and split-adjusted daily bars; dividends excluded from strategy and SPY benchmark.
- Close-based stops; next-open fills include gaps, fractional shares and shared cash; no shorting or leverage.
- No fitted parameters or automatic promotion. Historical holdout has not been blind to the developer.
- Options use archived OPRA quotes only, 100x multiplier, bid/ask and $0.65 per contract per side; no synthetic premiums.
- Sparse quote archive cannot establish continuous execution; missing exits charged full premium.
- Crypto backtest not implemented; crypto remains paper-only and unvalidated.

## Candidate comparison (research only)
| Rule | Dev return % | Dev exits | Holdout return % | Stress return % |
|---|---:|---:|---:|---:|
| volume_1.2 | -2.267 | 12 | -3.727 | -4.932 |
| deployed_volume_1.5 | -1.606 | 8 | -2.487 | -2.956 |
| volume_2.0 | -1.138 | 3 | 0.0 | 0.0 |

No candidate qualifies; no evidence-based promotion or larger allocation.
Development net return subject to minimum samples, profit factor >=1.2 and drawdown <=10%; no guarantee of maximum profit.
Same ETF subset and repeated historical holdout views; no blind prospective validation. Approval-gated paper fills differ from automatic historical replay.
