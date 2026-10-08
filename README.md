# Three Lens Market Dashboard (separate app)

This is a separate project from your existing **Breakout Dashboard** at `deantil-breakout-dashboard-breakout-dashboard-bva1o5.streamlit.app`. Pick all listed US exchanges, daily SPY holdings (S&P 500), or a custom watchlist. It retrieves symbols from Nasdaq Trader and State Street and daily price history from Yahoo Finance. Data coverage and service availability are not guaranteed.

## Deploy

1. Create a **new GitHub repository**, for example `deantil/three-lens-market-dashboard`. Do not use or change the repository connected to your existing Breakout Dashboard.
2. Put these three files in the new repository.
3. In Streamlit Community Cloud, choose **Create app**, select the new repository and `main` branch, and set the main file to `streamlit_app.py`.
4. Give the new Streamlit app a distinct name such as `three-lens-market-dashboard` (or `breakout-dashboard-lab`). Leave the existing Streamlit app and its URL alone.
5. Deploy. No API key is required by this version.

The first scan of thousands of symbols can take several minutes and may be constrained by public data-provider rate limits. Universe count means symbols returned by the listing endpoint; it does not mean every symbol will have usable price history.

## Strategy notes

- **Trader breakout:** transparent approximation based on the pasted notes: trend, recent breakout, positive momentum, and confirming volume.
- **Nirvana Omni inspired:** public Power Move concept approximation (price, momentum, and volume agreement). This does not reproduce proprietary Nirvana signals or licensed software.
- **Birbia inspired:** public-feature approximation with transparent long/short-term screens and a downloadable journal. It does not reproduce Birbia's private AI or score rules, and it does not analyze option chains.
- **Stock lookup:** enter a ticker to view available chart history and the dashboard's calculated technical checks.
- **Options income scan:** automatically checks option chains for a shortlist of the most liquid, account-affordable stocks, then compares covered calls and cash-secured puts by breakeven, estimated profit probability, premium, and max gain/loss. Free market-data endpoints can fail or return delayed quotes; the scan does not cover every listed contract.

The dashboard is for research and risk planning, not individualized investment advice. Option probability is a simplified implied-volatility model, not a guaranteed chance or measured win rate. Option writers can lose substantial amounts; read the OCC options disclosure. The $1,000-to-$10,000 target tracker is arithmetic only, not a return forecast or promise.

