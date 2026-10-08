# Three Lens Market Dashboard (separate app)

This is a separate project from your existing **Breakout Dashboard** at `deantil-breakout-dashboard-breakout-dashboard-bva1o5.streamlit.app`. It retrieves Nasdaq, NYSE, and NYSE American listings from Nasdaq's public screener and daily price history from Yahoo Finance. Data coverage and service availability are not guaranteed.

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
- **Birbia:** intentionally inactive until its source or rules are supplied.

The dashboard is for research and risk planning, not individualized investment advice. The $1,000-to-$10,000 target tracker is arithmetic only, not a return forecast or promise.
