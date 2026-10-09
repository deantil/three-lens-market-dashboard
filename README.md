# Three Lens Market Dashboard

This repository powers the [Three Lens Market Dashboard](https://three-lens-market-dashboard.streamlit.app/). It is separate from the older Breakout Dashboard.

## What it does

- Scans US-listed stocks using the Nasdaq Trader listings, SPY holdings, or a personal watchlist.
- Shows daily trend, breakout, momentum, and volume checks, plus a one-ticker lookup.
- Includes an educational options-income shortlist for selected cash-secured puts and covered calls.
- Includes an **Alpaca paper-trading challenge** that starts with $100. GitHub Actions checks a small liquid stock and crypto list about every 15 minutes, can submit paper orders, and tracks up to three positions sized to at most 15% of strategy equity each.

The dashboard and bot are for research and paper trading only. The paper agent connects only to Alpaca's paper trading host; it has no live-trading endpoint. It starts with a separate $100 strategy ledger even though Alpaca paper accounts can display a larger virtual balance. It does not spend beyond available strategy cash; realized gains can be reinvested. The challenge is a simple rule-based experiment, not an AI that learns, a validated profitable strategy, or a promise to turn $100 into $1,000. It is not a self-editing software agent.

## Connect the Alpaca paper account

1. Create or open an Alpaca **paper** account and create paper API credentials.
2. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Add `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. Paste the paper key and secret into GitHub's secret fields; never commit them or paste them into chat.
4. Open **Actions → $100 Alpaca paper challenge → Run workflow**. A green run and `Broker: connected_paper` in the dashboard confirm the connection.

The bot pauses when it sees unrecognized open orders or holdings in the same Alpaca account. This prevents it from changing another bot's trades. If Claude's bot is using that same paper account, pause one bot before starting this one. The bot trades only a small configured list of US shares and crypto pairs; it does not scan every stock, options chain, or every crypto asset.

## Deployment and free-host limits

Streamlit Community Cloud is connected to this repository. Updates pushed to `main` trigger a rebuild. The main app file is `streamlit_app.py`.

Community Cloud may hibernate the app after 12 hours without traffic. The paper-state JSON is published on the public `paper-state` branch and displayed by the app. GitHub Actions schedules the paper agent every 15 minutes, including weekends for crypto; scheduled runs can be delayed or skipped. This is periodic automation, not continuous monitoring or guaranteed 24/7 uptime.

The scanner is currently for US-listed stocks and daily price bars. To support a 24/7 asset class later, add a separate data provider, symbol list, trading calendar, and data-freshness checks. This app does not promise always-on uptime or real-time quotes.

## Risk notes

A standard equity option contract generally represents 100 shares. The $100 challenge does not trade options. Review the [OCC options disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document) before using options.

The $1,000 target is a challenge goal for paper tracking, not a forecast. Past or simulated results do not guarantee future outcomes.
