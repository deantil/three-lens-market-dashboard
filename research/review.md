# Weekly paper strategy review

Generated 2026-11-04T14:34:05.982088+00:00
Paper only. No strategy changes are applied automatically.

| Period | Costs | Return % | Max DD % | Exit fills | SPY % |
|---|---|---:|---:|---:|---:|
| development | base | -1.636 | -3.009 | 8 | 19.405 |
| development | stress | -2.47 | -3.372 | 8 | 18.928 |
| holdout | base | -1.803 | -1.803 | 4 | 11.359 |
| holdout | stress | -2.189 | -2.189 | 4 | 10.914 |

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
| volume_1.2 | -2.708 | 12 | -2.623 | -3.817 |
| deployed_volume_1.5 | -1.636 | 8 | -1.803 | -2.189 |
| volume_2.0 | -1.138 | 3 | 0.0 | 0.0 |

No candidate qualifies; no evidence-based promotion or larger allocation.
Development net return subject to minimum samples, profit factor >=1.2 and drawdown <=10%; no guarantee of maximum profit.
Same ETF subset and repeated historical holdout views; no blind prospective validation. Approval-gated paper fills differ from automatic historical replay.
