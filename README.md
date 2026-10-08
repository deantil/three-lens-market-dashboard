# Three Lens Market Dashboard

This repository powers the [Three Lens Market Dashboard](https://three-lens-market-dashboard.streamlit.app/). It is separate from the older Breakout Dashboard.

## What it does

- Scans US-listed stocks using the Nasdaq Trader listings, SPY holdings, or a personal watchlist.
- Shows daily trend, breakout, momentum, and volume checks, plus a one-ticker lookup.
- Includes an educational options-income shortlist for selected cash-secured puts and covered calls.
- Includes a **$100 to $1,000 paper-trading challenge**. It uses the breakout and volume rules, waits for a newer daily bar before simulating fills, holds up to three fractional-share positions, and tracks a stop-based exit rule.

The app is for research and paper trading only. It cannot connect to a brokerage or place real orders. Price and options data from Yahoo Finance can be delayed, incomplete, unavailable, or rate-limited. The paper challenge is a simple rule-based experiment, not an AI that learns, a validated profitable strategy, or a promise to turn $100 into $1,000.

## Deployment and free-host limits

Streamlit Community Cloud is connected to this repository. Updates pushed to `main` trigger a rebuild. The main app file is `streamlit_app.py`.

Community Cloud may hibernate the app after 12 hours without traffic. Paper-trading state is kept in the current browser session; download the JSON save file after updates and load it next time to continue.

The scanner is currently for US-listed stocks and daily price bars. To support a 24/7 asset class later, add a separate data provider, symbol list, trading calendar, and data-freshness checks. This app does not promise always-on uptime or real-time quotes.

## Risk notes

A standard equity option contract generally represents 100 shares. A cash-secured put may require enough cash to buy 100 shares; a covered call requires owning 100 shares. Option writers can lose substantially more than the premium received. Review the [OCC options disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document).

The $1,000 target is a challenge goal for paper tracking, not a forecast. Past or simulated results do not guarantee future outcomes.

