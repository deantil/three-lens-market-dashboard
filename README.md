# Three Lens Market Dashboard

This repository powers [your Streamlit dashboard](https://three-lens-market-dashboard.streamlit.app/). It is separate from the older Breakout Dashboard.

## What it does today

- Scans US-listed stocks using the Nasdaq Trader listing files, SPY holdings, or your own watchlist.
- Uses daily price history from Yahoo Finance for breakout, momentum, trend, and volume screens.
- Looks up one stock by ticker.
- Includes an options-income shortlist for cash-secured puts and covered calls, based on a limited set of liquid stocks that pass the stock screen.

The app is for research and paper planning. It does not connect to a brokerage or place trades. Yahoo Finance data can be delayed, incomplete, unavailable, or rate-limited. The options probability is a simplified estimate, not a measured win rate or promise.

## Deployment

Streamlit Community Cloud is connected to this repository. Changes pushed to the `main` branch trigger a new build of the app. The main file is `streamlit_app.py`.

## Current operating limits

The current scanner is for US-listed stocks and daily price bars. It can be opened at any time, but it does not provide guaranteed real-time quotes or run stock-market trades around the clock. Streamlit Community Cloud may hibernate an app after 12 hours without traffic.

A future 24/7 asset class such as crypto needs its own market-data provider, symbol list, trading calendar, and data-freshness checks. The current stock and option rules should not be copied over as if they were validated for that asset class.

## Risk notes

A standard equity option contract generally represents 100 shares. A cash-secured put may require enough cash to buy 100 shares; a covered call requires owning 100 shares. Option writing can lose substantially more than the premium received. Review the [OCC options disclosure](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document) before considering options.

The $1,000-to-$10,000 goal tracker shows arithmetic only. It is not a forecast or a promise of returns.
