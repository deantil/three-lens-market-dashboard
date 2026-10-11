# Three Lens Market Dashboard

This repository powers the [Three Lens Market Dashboard](https://three-lens-market-dashboard.streamlit.app/). It is separate from the older Breakout Dashboard.

## What it does

- Scans US-listed stocks using the Nasdaq Trader listings, SPY holdings, or a personal watchlist.
- Shows daily trend, breakout, momentum, and volume checks, plus a one-ticker lookup.
- Includes an educational options-income shortlist for selected cash-secured puts and covered calls.
- Includes an **Alpaca paper-trading challenge** that starts with a separate $100 strategy ledger. GitHub Actions scans Alpaca-tradable US equities outside OTC and supported USD crypto pairs once per day, using completed daily bars.

The dashboard and bot are for research and paper trading only. The paper agent connects only to Alpaca's paper trading host; it has no live-trading endpoint. It starts with a separate $100 strategy ledger even though Alpaca paper accounts can display a larger virtual balance. It does not spend beyond available strategy cash; realized gains can be reinvested. This rule-based experiment is unvalidated and cannot promise profit or turn $100 into $1,000. It does not rewrite its own rules.

## Paper swing strategy (daily-swing-v1)

The bot checks every eligible asset returned by Alpaca's active/tradable asset list, excluding OTC equities, warrants, rights, units, preferred shares, and leveraged/inverse funds. It downloads paginated daily bars in batches; stock prices and volume use Alpaca's IEX feed, so coverage and volume can differ from the full-market consolidated feed. It requires at least 200 completed daily bars.

A candidate must meet **all** of these rules:

1. Close above its 50-day average, with the 50-day average above the 200-day average.
2. Close above the highest high of the prior 20 completed sessions.
3. Latest completed daily volume at least 1.5 times the prior 20-session average.
4. Positive 63-session momentum.
5. Average 20-session dollar volume of at least $5 million for stocks or $2 million for crypto. Stocks must also be at least $5 per share.
6. A valid two-ATR reference stop no more than 15% below the signal close.

Passing candidates are ranked by 63-session momentum plus a capped relative-volume bonus. That ranking is not a probability of winning. The bot can hold at most three positions, limits each position to 20% of its strategy equity, and sizes it so planned loss to the reference stop is at most 0.5% of current strategy equity. Stops are checked after completed daily bars; gaps can make losses larger. Stock market orders submitted after the close queue for the next eligible session. Crypto orders can execute any time.

The report records strategy version, universe/data counts, positions, orders and an SPY benchmark. Upgrades preserve existing cash, holdings, pending orders and benchmark. Options now share this ledger under the safeguards below; crypto is retained. Small samples cannot establish that a strategy works.

## Connect the Alpaca paper account

1. Create or open an Alpaca **paper** account and create paper API credentials.
2. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Add `CHATGPT_APCA_API_KEY_ID` and `CHATGPT_APCA_API_SECRET_KEY`. Paste the paper key and secret into GitHub's secret fields; never commit them or paste them into chat.
4. Open **Actions → $100 Alpaca paper challenge → Run workflow**. A green run and `Broker: connected_paper` in the dashboard confirm the connection.

The bot pauses on unrecognized broker orders/holdings. It uses only `CHATGPT_...` GitHub secrets and a separate paper account from Claude. Existing credentials remain configured. Order intents are saved before submission, recovered by unique client IDs, and cumulative partial fills applied once. Failure to restore the ledger aborts the workflow rather than resetting $100.

## Options research and paper trading

Every new buy **and sell** now requires an emailed proposal and explicit approval. See the email policy below.

## Email-before-order approval

Every new order must have an SMTP-accepted proposal and explicit approval. Missing sender credentials or failed email blocks submission. Proposals expire after 24 hours. Approve through **Actions → $100 Alpaca paper challenge → Run workflow → mode `approve`**, entering the emailed proposal ID. Email replies do not approve trades. Current signal, cash, quote, contract and risk checks still apply; a changed amount or limit needs a new proposal. Approval is consumed when a durable intent is saved, preventing duplicate orders after uncertain responses. Approval delays can change fills and prevent timely exits; everything remains paper-only.

On first deployment, tracked open orders predating this alert policy are requested canceled. Completed/partial fills are reconciled and preserved. No foreign orders are canceled and the ledger is not reset. Cancellation can race with a fill; broker confirmation must be checked.

Configure `EMAIL_TO` (recipient), `SMTP_USER` (sender), `SMTP_PASS` (SMTP/app password) as repository Actions secrets, optionally `SMTP_HOST` (default Gmail SSL, port 465). No values belong in public code/state or chat. For Gmail use an eligible Google App Password with 2-Step Verification: https://support.google.com/accounts/answer/185833. Run workflow mode `test-email` and verify inbox receipt. SMTP acceptance does not prove delivery. Email is not active until a sender is configured and the setup test succeeds.

## Options implementation

`daily-swing-options-v2` inspects Alpaca chains and contract metadata for SPY, QQQ, IWM, AAPL, MSFT, AMD, PLTR and up to five qualified stock signals. Calls and puts expiring in 14–45 calendar days are researched. Only **long calls on qualifying daily stock breakouts** may trade; puts stay research-only. No short options, covered calls, cash-secured puts or spreads are introduced.

One standard whole contract at a time shares the three-position limit and existing cash with stocks/crypto. The entire premium must fit **5% of current strategy equity** ($5 initially), a separate experimental risk cap from the stock/crypto 0.5% planned stop risk. A contract can lose its entire premium. No affordable contract is a likely, valid result; the budget is never increased to force a trade. No stock is sold solely to create an options slot.

Entry requires broker options level 2, an open market with at least an hour before close, actual OPRA quotes at most 60 seconds old, positive uncrossed bid/ask, size at least one, spread at most 15% of ask, recent open interest at least 100, and call delta 0.25–0.65. Indicative quotes are research-only. Restricted data is reported; the bot does not buy a subscription. Whole-contract day limit orders specify buy-to-open or sell-to-close.

Exits are attempted at a 50% premium loss or five calendar days before expiry. Unfilled limits, gaps, unavailable quotes and skipped GitHub runs can delay exits. The bot never requests exercise; broker automatic exercise can still occur at expiry if an exit fails. Unexpected exercise/assignment holdings pause the agent. Monitor warnings and pending exits. Add `STOP` on main or set `halt_new_entries: true` in the paper-state ledger to stop new entries while permitting exits.

Full stock/crypto scans run daily at 00:15 UTC. Options checks run weekdays at 14:45, 16:45, 18:45 and 20:00 UTC; broker clock checks handle holidays, early closes and DST. This is periodic automation, not continuous protection.

## Manual replication in thinkorswim paperMoney

Alpaca remains the paper engine. The dashboard lists **all** pending stock, option and crypto tickets and offers a CSV download. Stock tickets retain the dollar allocation, MARKET/DAY order, symbol and daily reference exit rule. Option tickets include underlying, expiration, strike, call/put, contracts, limit and open/close intent. Confirm the matching symbol/contract and order availability in your own paperMoney account, check remaining rather than already-filled quantities, and do not duplicate a ticket you already copied. These tickets neither connect to Schwab nor send orders there.

Thinkorswim documents dollar-based fractional orders on eligible equities/ETFs; fractional notional orders require MARKET and actual quantities/fills can differ. See [official notional-order instructions](https://toslc.thinkorswim.com/center/howToTos/thinkManual/Trade/Order-Entry-Tools/Order-Types/Notional). PaperMoney support must be checked in the user's account; platform documentation is not a per-account availability guarantee.

Direct spot crypto is marked **not an exact thinkorswim match**. Keep those trades on Alpaca paper; no automatic futures/ETF proxy is introduced. [Schwab's crypto page](https://www.schwab.com/cryptocurrency) currently describes direct crypto as coming soon and lists ETPs/futures exposure. Thus exact replication of every Alpaca crypto trade cannot be guaranteed. Trading everything on Alpaca and maintaining a manual comparison report is supported; identical executions across platforms are not.

## Realistic backtests and weekly reviews

Weekly reviews also compare three predefined relative-volume thresholds (1.2, deployed 1.5, and 2.0) using identical sizing and execution assumptions. Candidate selection uses development net return only, requires 25 development exits, positive return, profit factor at least 1.2 and drawdown at most 10%. Holdout and higher-cost results are reported separately. If none qualifies, the report says so. No rule changes are automatic. Repeated historical holdout reviews are not blind prospective validation; no best strategy or maximum profit is guaranteed. Approval-gated paper results will differ from historical automatic-fill simulations.

`Weekly paper strategy review` runs Saturdays at 15:00 UTC, or manually. It publishes `research/review.md` and `research/review.json` on `paper-state`, workflow summaries and 90-day artifacts. Trading/review workflows share one concurrency group. No rules change automatically.

- Stocks: a frozen SPY/QQQ/IWM/DIA ETF universe, two years divided into development and holdout periods, at least 200 preceding daily bars for warmup. The deployed signal sees only each historical prefix; entries and close-triggered exits fill at the next open, including gaps. Fractional sizing shares $100 cash and three slots. Base costs use 0.1% slippage per side; stress costs use 0.3% and $0.01 per order. Final open holdings are conservatively liquidated for equity reporting; open counts remain visible. SPY uses matching periods and slippage.
- Options: up to 200 inspected contracts per run are archived with quote time, sizes, bid/ask, feed and eligibility. Replay uses actual OPRA snapshots, another observation for entry/exit limit fills, the 100x multiplier and a $0.65 per-contract per-side cost stress assumption. It tests a separate $5 sleeve, not the combined stock/crypto ledger. Missing exits cost full premium. No same-snapshot fill, indicative quote or synthetic premium can produce claimed performance. Early reports show insufficient data.
- Limits: the ETF subset does not validate the full market or crypto. IEX covers part of the market; dividends are excluded from both strategy and benchmark. Current ETF selection is subjective and the historical holdout is not a blind prospective test. Sparse options snapshots cannot establish continuous execution or remove contract-selection bias. Coverage and exit counts are reported. Crypto backtesting remains unimplemented and its paper strategy unvalidated.

Run `python -m unittest discover -s tests -v` for offline safeguard tests. Run `python research_backtest.py --state paper_challenge_state.json` for a credential-backed historical review; it sends no orders. See [Alpaca options trading](https://docs.alpaca.markets/us/docs/options-trading) and [historical data/feed limitations](https://docs.alpaca.markets/us/docs/historical-option-data); historical options data starts in February 2024.

## Deployment and free-host limits

Streamlit Community Cloud is connected to this repository. Updates pushed to `main` trigger a rebuild. The main app file is `streamlit_app.py`.

Community Cloud may hibernate the app after 12 hours without traffic. The paper-state JSON is published on the public `paper-state` branch and displayed by the app. GitHub Actions schedules one scan each day, including weekends for crypto; scheduled runs can be delayed or skipped. This is periodic automation, not continuous monitoring or guaranteed 24/7 uptime.

The scanner is currently for US-listed stocks and daily price bars. To support a 24/7 asset class later, add a separate data provider, symbol list, trading calendar, and data-freshness checks. This app does not promise always-on uptime or real-time quotes.

## Risk notes

A standard equity option contract generally represents 100 shares. The challenge allows only the limited long-call paper entries above. Review the [OCC options disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document) before using options.

The $1,000 target is a challenge goal for paper tracking, not a forecast. Past or simulated results do not guarantee future outcomes.

