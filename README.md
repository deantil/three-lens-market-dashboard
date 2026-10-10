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

The paper report records the strategy version, universe/data counts, open positions, trade history, and an SPY buy-and-hold benchmark starting with $100 on the first run of this version. Results are too small and too recent to establish that the rules work. Changes should be compared with the benchmark and risk statistics before promoting a new rule version. Options are not part of this bot: with $100, one indivisible contract can consume the whole strategy budget and lose its full premium.

## Connect the Alpaca paper account

1. Create or open an Alpaca **paper** account and create paper API credentials.
2. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Add `CHATGPT_APCA_API_KEY_ID` and `CHATGPT_APCA_API_SECRET_KEY`. Paste the paper key and secret into GitHub's secret fields; never commit them or paste them into chat.
4. Open **Actions → $100 Alpaca paper challenge → Run workflow**. A green run and `Broker: connected_paper` in the dashboard confirm the connection.

The bot pauses when it sees unrecognized open orders or holdings in the same Alpaca account. This prevents it from changing another bot's trades. Use a separate Alpaca paper account from Claude's bot. This workflow reads only the `CHATGPT_...` GitHub secrets, so its credentials do not replace Claude's. The bot still cannot scan assets that Alpaca does not list as tradable, assets with insufficient history, or options contracts.

## Deployment and free-host limits

Streamlit Community Cloud is connected to this repository. Updates pushed to `main` trigger a rebuild. The main app file is `streamlit_app.py`.

Community Cloud may hibernate the app after 12 hours without traffic. The paper-state JSON is published on the public `paper-state` branch and displayed by the app. GitHub Actions schedules one scan each day, including weekends for crypto; scheduled runs can be delayed or skipped. This is periodic automation, not continuous monitoring or guaranteed 24/7 uptime.

The scanner is currently for US-listed stocks and daily price bars. To support a 24/7 asset class later, add a separate data provider, symbol list, trading calendar, and data-freshness checks. This app does not promise always-on uptime or real-time quotes.

## Risk notes

A standard equity option contract generally represents 100 shares. The $100 challenge does not trade options. Review the [OCC options disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document) before using options.

The $1,000 target is a challenge goal for paper tracking, not a forecast. Past or simulated results do not guarantee future outcomes.
