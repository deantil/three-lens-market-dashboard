# Three Lens Market Dashboard

This repository powers the [Three Lens Market Dashboard](https://three-lens-market-dashboard.streamlit.app/). It is separate from the older Breakout Dashboard.

## What it does

### Project T ORB alerts in Tab 11

**Tab 11 · Project T ORB alerts** adds dashboard-only research monitoring and a manual thinkorswim paperMoney ticket calculator for a reference $25,000 account at 0.25% planned risk per trade ($62.50 initially). Click **Start ORB monitoring** after 09:35 Eastern. A session fragment refreshes every 60 seconds while the session remains active; this does not run while the app sleeps or send email, SMS or background push notifications. Session toast alerts fire on transitions to a recent breakout; the table, timestamps and session history show the underlying state. Clicking refresh re-requests data subject to a shared 60-second cache. No Schwab API, broker credentials, orders or auto-trading are connected.

The default universe is an explicit 30-stock liquid watchlist, editable up to 40 stocks. This is **not a full-market Stocks-in-Play scanner**. Opening-volume rank is computed within the submitted watchlist; no market-wide top-20 membership is asserted. Import your broker scanner's current shortlist. Baseline: first five-minute range 09:30–09:35 ET; trade in the opening candle's direction; price > $5; prior 14-session ADV > 1M; prior 14-session simple average true range > $0.50; opening volume / mean of the prior 14 opening volumes >= 1; top 20 within the watchlist. Trigger is one cent beyond the range; stop distance is 10% of daily ATR rounded up to a cent. No fixed profit target; stop or five minutes before exchange close. Completed five-minute bars only; daily metrics exclude today. NYSE calendar handles holidays, DST and early closes. Missing opening/history data, invalid OHLCV, stale bars and failed/missed breakouts produce wait/skip/data-unavailable states. Yahoo data availability and completeness are not guaranteed.

Project T design adaptations: no new entries after 11:00 ET; no chasing beyond 0.25 stop distances; spread <= min($0.05, 10% of stop distance); one trade per symbol per day; max 25% equity per position, 3 positions, 0.75% total planned open risk, 100% gross exposure and 1% daily loss gate. These are risk choices, not claims of validated profitability. Sizing reserves spread plus max($0.01, 5 bps) adverse allowance on each side; fees/gaps may exceed the estimate. At 11:00 or later the correct instruction is no new ORB entry, even if a historical breakout looks attractive.

Select a candidate, enter a freshly checked paperMoney bid/ask, and confirm catalyst, broker scanner eligibility, account totals and manual paper-only approval. Short tickets require an additional eligibility/borrow check. Entered account totals are manual session inputs, not connected balances or an enforceable durable portfolio ledger. Plans expire after 60 seconds or a candidate-state change. Recheck before manually placing the paperMoney order. Protect the actual fill immediately with the specified stop distance; manage stop/exits in the broker platform, since this dashboard does not monitor personal positions or close them. No private account/ticket information is published to GitHub. Session information can disappear on disconnect. The $100 Alpaca challenge and its orders are separate and unchanged.

Research reference: Zarattini, Barbon and Aziz, *A Profitable Day Trading Strategy for the U.S. Equity Market*, https://ssrn.com/abstract=4729284 (historical study, not proof of forward profitability). Platform references: https://www.schwab.com/trading/thinkorswim/paper-trading ; https://toslc.thinkorswim.com/center/reference/thinkScript/tutorials/Basic/Chapter-7---Creating-Strategies ; refresh behavior https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment ; intraday data parameters https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html .

`orb_strategy.py` contains the deterministic rules and sizing; `orb_dashboard.py` renders the panel. `tests/test_orb.py` covers daily-data leakage, incomplete/future bars, missing history/timezones, cutoff/staleness, failed/missed breakouts, short checks, cost-aware sizing, portfolio limits, watchlist scope and exchange calendar. Install `requirements.txt` then run `python -m unittest discover -s tests -v`. The dedicated **ORB strategy checks** workflow installs the full dependencies; unrelated minimal-dependency bot workflows skip ORB tests when its optional data libraries are absent.

### Automatic trader-source agent

The six trader tabs now separate **1. Current disclosed trades/holdings** and **2. New trade alerts**, including Bruce Marshall and CNBC Investing Club. Current complete holdings have not been verified for any of these accounts through an authorized connected position feed; this is shown as unknown, not zero holdings. No source currently connected supplies a complete live personal trade alert stream. Public publications are shown separately under research/coverage, never converted to positions. Bruce's official source documents a member portfolio, trade adjustments and alerts; current product access must be checked. CNBC's help center identifies Trust open/closed lots as a subscriber feature. Its public RSS access was intermittent in verification (HTTP 403 then success); no bypass is attempted. First-person trade/portfolio-related headlines from CNBC, or explicitly authored trader RSS items, are triaged as **potential updates with unverified details**, never confirmed executions. Initial items are baseline, not new notifications. Missing contracts, quantities and prices stay missing. No external push notification is sent; alerts appear in the dashboard. References: https://www.simplertrading.com/traders/bruce-marshall and https://cnbc.zendesk.com/hc/en-us/articles/4415961706011-How-can-I-see-the-Charitable-Trust-Portfolio .

`trader_agent.py` and **Project T official source monitor** run every 15 minutes via GitHub Actions. Public metadata is persisted on the separate `trader-state` branch; Tab 8 reloads this report every 60 seconds while open. This runs independently of Streamlit hibernation. GitHub schedules can be delayed/skipped, and public sources are not a low-latency trade feed. The report marks stale runs, errors, empty feeds, initial baseline items, observation timestamps and missing publication dates. Refresh published agent report retrieves the latest durable collection. Check official sources now performs a direct check, shared/cached up to five minutes to limit publisher load.

Connected public routes: Simpler Trading RSS and article links (team publications, not Carter personal trades); Liquid Mercury official news RSS (company news, not Saliba trades); MoneyShow's Sosnoff appearance links (not current trade executions). Holguin's history page is checked for availability only: its terms restrict copying/storing materials without consent, so no history rows are archived or published. All publication items are research-only and cannot submit orders. Only titles, source links and metadata are retained; no subscriber content or private paper records is published. Links alone do not establish that a trader made a trade. Empty feeds mean empty, never fabricated signals.

The collector uses deterministic extraction rather than a paid LLM: no OpenAI API calls, API key or AI charges. This automation solves collection; an LLM cannot create missing trader alerts or prove a trader's performance. Live trade collection requires an authorized official X API or publisher-approved subscriber integration. No passwords, new subscriptions or paid API access were requested/created.

An official X recent-search adapter is implemented **but disabled**. It allowlists only HolguinTrades and johnfcarter, verified through their official website links. Saliba's company account is not substituted for his personal trades. After separate explicit access/budget approval, configure privately `PROJECT_T_X_APPROVED=yes`, `PROJECT_T_X_BEARER_TOKEN`, and `PROJECT_T_X_TOTAL_BUDGET_USD`, then run `python trader_agent.py --x-private --approved --state /private/non-synced/path/trades-private.json`. Do not paste secrets into chat. Never point this adapter at the public source-state file or run it with a public publication workflow. It makes one read-only API search per invocation, no auto-loop or order submission. It does not run unless explicitly invoked. X pricing checked October 25, 2026 is $0.005/post; a maximum 10-post response reserves $0.05 before each request, including uncertain failures. This deliberately overcounts sparse responses. Set the real X console spending limit; the local counter is not an account-wide guarantee and cannot survive deletion/reset of its file. A response with more pages stops without advancing the cursor, requiring gap reconciliation. Full catch-up, streaming and edit/deletion reconciliation remain future work; this adapter is for read-only evaluation, not unattended execution.

The alert parser never supplies a missing premium or quantity, always requires review, and never places orders. Holguin documents omitting both premium and quantity, so his alerts require current broker quotes and independent sizing. His guide also includes rolls and assignment instructions: these are outside the long-option simulator. Carter's official room documents filtering by JC and exporting alert history; obtain a publisher-authorized automation route before accessing or redistributing member content. Official references:

- https://www.simplertrading.com/trading-education/tutorials/where-can-i-see-johns-trades
- https://holguintrades.com/trainings/understanding_text_syntax
- https://holguintrades.com/legal/terms-of-service
- https://docs.x.com/x-api/getting-started/pricing

Local source collection: `python trader_agent.py --state trader_source_state.json`. No external subscription/API credential needed. A malformed existing state file aborts rather than silently discarding history. The public source agent is separate from both the Alpaca bot and private paper ledger.

- **Tab 8: Options trader tracker**, with individual tabs for Alfredo Holguin, John Carter, Tony Saliba and Tom Sosnoff. Add or update authorized trade records, refresh the session ledger, and export/import CSV. No external trader feed, X API or Schwab broker connection is active. Source links and per-trader copying instructions distinguish verified access routes from unverified ones.
- Tracker records live only in the current Streamlit session. Download after editing and import next visit. They are never committed to this public repository. Durable authenticated storage and authorized feed integrations remain future work. Refresh does not collect fresh external signals. Never upload proprietary alerts without the right to process them on this host.
- Tracker P&L uses user-entered full-round-trip long-option fills, standard 100 multiplier, quantity and total fees; shorts, multi-leg trades and partial exits are excluded from that calculation. Status changes submit no orders. Existing paper bot rules are separate.

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

Email is disabled at the user's request. Trade alerts are published in the dashboard and paper-state research/manual_alerts.json. No SMTP credentials or Google app password are needed.

## Dashboard alerts and manual replication

The daily scan publishes up to the available three entry slots, with symbol, side, dollar allocation or contracts, order type, signal date, reference stop, expiry and approval ID. Download current replication alerts from the paper challenge tab. Refresh latest published paper report reloads the state; Run workflow in daily mode triggers a new scan. Expired/consumed alerts and unrefreshed legacy email failures are excluded.

Alerts are proposals, not submitted orders. To approve an exact paper order, open Actions → paper challenge → Run workflow → approve and enter its dashboard ID. Signal, cash, quote and risk checks run again. A changed order needs a new approval; proposals expire after 24 hours. No order is submitted just because an alert exists. Approval can delay entries and exits.

Stocks and listed options have manual thinkorswim paperMoney instructions, subject to account/symbol eligibility and different fills. Spot crypto is explicitly marked as having no exact thinkorswim match; no ETF/futures replacement is invented. Reference stops are daily checks, not guaranteed resting broker orders. Paper only.

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



## Real Estate Investments (Tab 9)

`real_estate.py` screens `real_estate_data.json` separately from all trading systems. Fixed financing is 25% borrowed HELOC down payment at 9% interest-only and 75% mortgage at 7.5%/30 years. Both debts count in cash flow. The tab shows Strictly Qualified (currently zero) and five research candidates sortable by scenario CoC or purchase price. These are not verified investment recommendations.

Monthly operating assumptions: 5% vacancy, 8% management, 5% repairs, 5% capital replacements and 3% leasing/turnover, plus property-specific tax/HOA/insurance/utilities/other contingencies. Closing/loan costs are estimated at 4%; initial repairs at $2,500. Savings allocation is at least $30,000, retaining at least six months of carrying costs; >$40,000 fails. Actual costs and lender terms may be higher. HOA <=$200 is a preference, not a hard limit. NOI DSCR 1.20 is a disclosed screening floor plus a separate lender-document gate, not a claim of approval.

All required evidence must be verified with a source URL and actual observation timestamp, no older than seven days. Missing, conflicting, reported, stale or future-dated evidence fails. Current rental rules, objective safety review, rent comps, county tax/parcel records, HOA documents and lender acceptance have not been obtained; no candidate qualifies. The tax/HOA conflicts and pending Kildaire status are shown explicitly. Reported sale listings do not establish investment eligibility. Unfurnished rent scenarios are assumptions; the $2,795 furnished/utilities-included advertisement is not substituted for a long-term lease comp.

Manual update: review primary documents and live listing status, edit the JSON evidence and numeric scenarios, preserve source links and timestamps, run `python -m unittest discover -s tests -p test_real_estate.py -v`, then commit/push to main. The existing Streamlit deployment loads the committed JSON; page reloads do not collect fresh listing evidence. No automated property collector or broker/HOA outreach is active. Evidence JSON and scenario CSV can be downloaded in the tab. Reconcile parcel identity and buyer tax reassessment/uncapping before marking tax verified.
