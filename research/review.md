# Weekly paper strategy review

Generated 2026-11-14T19:42:03.565129+00:00
Paper only. No strategy changes are applied automatically.

| Period | Costs | Return % | Max DD % | Exit fills | SPY % |
|---|---|---:|---:|---:|---:|
| development | base | -1.192 | -1.784 | 6 | 12.278 |
| development | stress | -1.756 | -2.01 | 6 | 11.83 |
| holdout | base | -1.803 | -1.803 | 4 | 13.136 |
| holdout | stress | -2.189 | -2.189 | 4 | 12.685 |

Options: insufficient_data; 8600 archived observations; 0 filled exits.
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
| volume_1.2 | -2.411 | 11 | -2.623 | -3.817 |
| deployed_volume_1.5 | -1.192 | 6 | -1.803 | -2.189 |
| volume_2.0 | -0.453 | 1 | 0.0 | 0.0 |

No candidate qualifies; no evidence-based promotion or larger allocation.
Development net return subject to minimum samples, profit factor >=1.2 and drawdown <=10%; no guarantee of maximum profit.
Same ETF subset and repeated historical holdout views; no blind prospective validation. Approval-gated paper fills differ from automatic historical replay.
