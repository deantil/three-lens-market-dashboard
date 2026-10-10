"""Paper-only, broad-universe daily swing trader for Alpaca.

This is a transparent rules experiment, not a profit guarantee or an AI that
rewrites its own strategy. All orders are sent only to Alpaca's paper endpoint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

import requests


STATE_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("paper_challenge_state.json")
STARTING_CASH = 100.0
TARGET_BALANCE = 1000.0
STRATEGY_VERSION = "daily-swing-v1"
MAX_POSITIONS = 3
MAX_POSITION_FRACTION = 0.20
MAX_RISK_FRACTION = 0.005
MAX_STOP_FRACTION = 0.15
MIN_STOCK_PRICE = 5.0
MIN_STOCK_DOLLAR_VOLUME = 5_000_000.0
MIN_CRYPTO_DOLLAR_VOLUME = 2_000_000.0
MIN_RELATIVE_VOLUME = 1.5
LOOKBACK_CALENDAR_DAYS = 420
SYMBOL_BATCH_SIZE = 100
BOT_PREFIX = "tl100-"
TRADE_API = "https://paper-api.alpaca.markets/v2"
DATA_API = "https://data.alpaca.markets"
EXCLUDED_NAME = re.compile(r"warrant|right|unit|preferred|2x|3x|ultra|inverse|bear daily|bull daily", re.I)


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_state() -> dict:
    return {
        "schema_version": 2,
        "engine": "Alpaca paper trading",
        "strategy_version": STRATEGY_VERSION,
        "starting_cash": STARTING_CASH,
        "target_balance": TARGET_BALANCE,
        "cash": STARTING_CASH,
        "positions": [],
        "pending": [],
        "trades": [],
        "equity_history": [],
        "last_bar_date": None,
        "last_run_utc": None,
        "last_scan_utc": None,
        "scan_summary": {},
        "processed_signal_dates": {},
        "benchmark_shares": None,
        "benchmark_start_price": None,
        "broker_status": "waiting_for_credentials",
        "broker_equity": None,
        "message": "Add the dedicated Alpaca paper API credentials to GitHub Actions secrets.",
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return new_state()
    with STATE_PATH.open("r", encoding="utf-8") as file:
        prior = json.load(file)
    if prior.get("schema_version") != 2 or prior.get("engine") != "Alpaca paper trading":
        return new_state()
    state = new_state()
    state.update(prior)
    # Bound the idempotency ledger in case the strategy runs for years.
    state["processed_signal_dates"] = dict(list(state.get("processed_signal_dates", {}).items())[-10000:])
    return state


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=STATE_PATH.parent, delete=False) as file:
        json.dump(state, file, indent=2)
        file.write("\n")
        temp_path = Path(file.name)
    temp_path.replace(STATE_PATH)


def append_trade(state: dict, action: str, symbol: str, qty: float, price: float, note: str) -> None:
    state["trades"].append({
        "date": now().isoformat(), "action": action, "symbol": symbol,
        "shares": round(qty, 9), "price": round(price, 6), "note": note,
        "strategy_version": STRATEGY_VERSION,
    })
    state["trades"] = state["trades"][-500:]


def credentials() -> tuple[str, str]:
    return (
        (os.getenv("CHATGPT_APCA_API_KEY_ID") or "").strip(),
        (os.getenv("CHATGPT_APCA_API_SECRET_KEY") or "").strip(),
    )


class Alpaca:
    def __init__(self, key: str, secret: str):
        self.headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        }

    def trade(self, method: str, path: str, **kwargs) -> dict | list:
        response = requests.request(method, TRADE_API + path, headers=self.headers, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def data(self, path: str, params: dict) -> dict:
        last_error = None
        for attempt in range(5):
            response = requests.get(DATA_API + path, headers=self.headers, params=params, timeout=60)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = requests.HTTPError(f"Market data HTTP {response.status_code}: {response.text[:300]}")
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(5 * (2 ** attempt), 30)
                except ValueError:
                    delay = min(5 * (2 ** attempt), 30)
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        raise last_error or RuntimeError("Market data request failed after retries")


def normalized_symbol(value: str) -> str:
    return str(value).replace("/", "").replace("-", "").upper()


def list_assets(api: Alpaca, asset_class: str) -> list[dict]:
    assets = api.trade("GET", "/assets", params={"status": "active", "asset_class": asset_class})
    eligible = []
    for asset in assets:
        if not asset.get("tradable") or not asset.get("symbol"):
            continue
        symbol = str(asset["symbol"])
        if asset_class == "us_equity":
            if str(asset.get("exchange") or "").upper() == "OTC" or EXCLUDED_NAME.search(str(asset.get("name", ""))):
                continue
            # Exclude malformed and test-like tickers; retain normal share classes.
            if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
                continue
        elif not symbol.endswith("/USD"):
            continue
        eligible.append(asset)
    return eligible


def fetch_bars(api: Alpaca, asset_class: str, assets: list[dict]) -> dict[str, list[dict]]:
    """Fetch daily history in bounded batches and follow every pagination token."""
    end = now()
    start = end - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    path = "/v2/stocks/bars" if asset_class == "us_equity" else "/v1beta3/crypto/us/bars"
    output: dict[str, list[dict]] = {}
    symbols = [str(asset["symbol"]) for asset in assets]
    for offset in range(0, len(symbols), SYMBOL_BATCH_SIZE):
        batch = symbols[offset:offset + SYMBOL_BATCH_SIZE]
        params = {
            "symbols": ",".join(batch), "timeframe": "1Day",
            "start": start.isoformat(), "end": end.isoformat(), "limit": 10000,
            "sort": "asc",
        }
        if asset_class == "us_equity":
            params.update({"feed": "iex", "adjustment": "split"})
        page_count = 0
        while True:
            result = api.data(path, params)
            for symbol, rows in result.get("bars", {}).items():
                output.setdefault(symbol, []).extend(rows)
            token = result.get("next_page_token")
            if not token:
                break
            page_count += 1
            if page_count > 100:
                raise RuntimeError(f"Too many market-data pages for batch starting {batch[0]}; scan stopped safely.")
            params["page_token"] = token
    return output


def completed_daily_bars(bars: list[dict], as_of: datetime) -> list[dict]:
    """Drop today's still-forming UTC candle so signals use closed daily bars."""
    completed = []
    for row in bars:
        stamp = row.get("t")
        if not stamp:
            continue
        try:
            bar_time = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if bar_time.date() < as_of.date():
            completed.append(row)
    return completed


def make_candidate(symbol: str, asset_class: str, bars: list[dict]) -> dict | None:
    bars = completed_daily_bars(bars, now())
    if len(bars) < 201:
        return None
    closes = [float(row["c"]) for row in bars]
    highs = [float(row["h"]) for row in bars]
    lows = [float(row["l"]) for row in bars]
    volumes = [float(row.get("v", 0)) for row in bars]
    last = closes[-1]
    if last <= 0 or (asset_class == "stock" and last < MIN_STOCK_PRICE):
        return None
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / 200
    prior_high = max(highs[-21:-1])
    avg_volume = sum(volumes[-21:-1]) / 20
    relative_volume = volumes[-1] / avg_volume if avg_volume > 0 else 0.0
    avg_dollar_volume = sum(c * v for c, v in zip(closes[-20:], volumes[-20:])) / 20
    momentum_63d = (last / closes[-64] - 1) * 100 if closes[-64] else 0.0

    true_ranges = []
    for index in range(len(bars) - 20, len(bars)):
        previous_close = closes[index - 1]
        true_ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - previous_close),
            abs(lows[index] - previous_close),
        ))
    atr20 = sum(true_ranges) / len(true_ranges)
    stop = last - 2 * atr20
    stop_fraction = (last - stop) / last
    min_dollar_volume = MIN_STOCK_DOLLAR_VOLUME if asset_class == "stock" else MIN_CRYPTO_DOLLAR_VOLUME
    trend_ok = last > sma50 > sma200
    breakout_ok = last > prior_high
    volume_ok = relative_volume >= MIN_RELATIVE_VOLUME
    liquidity_ok = avg_dollar_volume >= min_dollar_volume
    risk_ok = 0 < stop < last and 0 < stop_fraction <= MAX_STOP_FRACTION
    signal = trend_ok and breakout_ok and volume_ok and momentum_63d > 0 and liquidity_ok and risk_ok
    # Simple, inspectable rank. This orders already-qualified candidates; it is
    # not a probability of winning.
    score = momentum_63d + min(relative_volume, 3.0) * 2.0
    bar_date = str(bars[-1]["t"])[:10]
    return {
        "symbol": symbol, "asset_class": asset_class, "price": last,
        "stop": stop, "stop_fraction": stop_fraction, "atr20": atr20,
        "relative_volume": relative_volume, "avg_dollar_volume": avg_dollar_volume,
        "momentum_63d_pct": momentum_63d, "sma50": sma50, "sma200": sma200,
        "prior_20d_high": prior_high, "bar_date": bar_date, "score": score,
        "signal": signal,
    }


def bar_candidates(api: Alpaca) -> tuple[list[dict], dict, dict[str, dict]]:
    stocks = list_assets(api, "us_equity")
    cryptos = list_assets(api, "crypto")
    stock_bars = fetch_bars(api, "us_equity", stocks)
    crypto_bars = fetch_bars(api, "crypto", cryptos)
    candidates = []
    rows_by_symbol = {}
    valid_histories = 0
    for asset_class, data in (("stock", stock_bars), ("crypto", crypto_bars)):
        for symbol, bars in data.items():
            row = make_candidate(symbol, asset_class, bars)
            if row:
                valid_histories += 1
                rows_by_symbol[symbol] = row
                if row["signal"]:
                    candidates.append(row)
    if not valid_histories:
        raise RuntimeError("No eligible symbols returned enough completed daily price history; no orders were sent.")
    candidates.sort(key=lambda row: (row["score"], row["avg_dollar_volume"]), reverse=True)
    dates = [row["bar_date"] for row in rows_by_symbol.values()]
    summary = {
        "strategy_version": STRATEGY_VERSION,
        "eligible_stocks": len(stocks), "eligible_crypto": len(cryptos),
        "symbols_with_200d_history": valid_histories,
        "qualifying_signals": len(candidates),
        "stocks_with_bars": len(stock_bars), "crypto_with_bars": len(crypto_bars),
        "latest_completed_bar_date": max(dates) if dates else None,
        "stock_data_feed": "Alpaca IEX daily bars",
        "crypto_data_feed": "Alpaca US daily bars",
        "options_traded": False,
    }
    return candidates, summary, rows_by_symbol


def reconcile_foreign_activity(api: Alpaca, state: dict) -> None:
    broker_positions = api.trade("GET", "/positions")
    open_orders = api.trade("GET", "/orders", params={"status": "open", "limit": 500})
    tracked = {normalized_symbol(position["symbol"]) for position in state["positions"]}
    pending_symbols = {normalized_symbol(order["symbol"]) for order in state["pending"]}
    foreign_positions = [p["symbol"] for p in broker_positions if normalized_symbol(p["symbol"]) not in tracked | pending_symbols]
    foreign_orders = [o.get("id") for o in open_orders if not str(o.get("client_order_id", "")).startswith(BOT_PREFIX)]
    if foreign_positions or foreign_orders:
        raise RuntimeError(
            "This paper account has activity from another bot or manual trades. No order was sent. "
            f"Unmanaged positions: {foreign_positions}; unmanaged open orders: {len(foreign_orders)}."
        )
    actual = {normalized_symbol(p["symbol"]): float(p["qty"]) for p in broker_positions}
    for position in state["positions"]:
        broker_qty = actual.get(normalized_symbol(position["symbol"]), 0.0)
        expected = float(position["qty"])
        if abs(broker_qty - expected) > max(1e-6, expected * 0.02):
            raise RuntimeError(f"Quantity mismatch for {position['symbol']}; trading paused without sending orders.")


def filled_order(api: Alpaca, pending: dict) -> dict | None:
    order = api.trade("GET", f"/orders/{pending['order_id']}")
    if order.get("status") == "filled":
        return order
    if order.get("status") in {"rejected", "canceled", "expired"}:
        return {**order, "terminal_unfilled": True}
    return None


def apply_filled_order(state: dict, pending: dict, order: dict) -> None:
    symbol, side = pending["symbol"], pending["side"]
    qty = float(order.get("filled_qty") or 0)
    price = float(order.get("filled_avg_price") or pending.get("price") or 0)
    if order.get("terminal_unfilled"):
        append_trade(state, "CANCELLED", symbol, 0, price, f"Paper order {order.get('status')}; no fill")
        return
    if side == "buy" and qty > 0 and price > 0:
        cost = qty * price
        if cost > float(state["cash"]) + 0.02:
            raise RuntimeError("Paper fill exceeded the $100 strategy ledger; trading paused.")
        state["cash"] = max(0.0, float(state["cash"]) - cost)
        stop_fraction = float(pending.get("stop_fraction", 0.08))
        state["positions"].append({
            "symbol": symbol, "asset_class": pending["asset_class"], "qty": qty,
            "entry_price": price, "last_price": price,
            "stop": price * (1 - stop_fraction), "stop_fraction": stop_fraction,
            "entry_order_id": order["id"], "entry_date": now().date().isoformat(),
            "strategy_version": STRATEGY_VERSION,
        })
        append_trade(state, "BUY", symbol, qty, price, "Filled by Alpaca paper trading")
    elif side == "sell" and qty > 0 and price > 0:
        state["cash"] = float(state["cash"]) + qty * price
        state["positions"] = [p for p in state["positions"] if p["symbol"] != symbol]
        append_trade(state, "SELL", symbol, qty, price, "Exit filled by Alpaca paper trading")


def send_order(api: Alpaca, state: dict, row: dict, side: str,
               notional: float = 0.0, qty: float | None = None) -> None:
    asset_class = row["asset_class"]
    symbol = row["symbol"]
    client_id = BOT_PREFIX + uuid.uuid4().hex[:24]
    body = {
        "symbol": symbol, "side": side, "type": "market",
        "time_in_force": "day" if asset_class == "stock" else "gtc",
        "client_order_id": client_id,
    }
    if qty is None:
        body["notional"] = f"{notional:.2f}"
    else:
        body["qty"] = f"{qty:.9f}".rstrip("0").rstrip(".")
    order = api.trade("POST", "/orders", json=body)
    state["pending"].append({
        "order_id": order["id"], "client_order_id": client_id, "symbol": symbol,
        "asset_class": asset_class, "side": side, "notional": notional,
        "qty": qty, "price": row.get("price", 0),
        "stop_fraction": row.get("stop_fraction", 0),
        "signal_bar_date": row.get("bar_date"), "score": row.get("score"),
        "submitted_utc": now().isoformat(),
    })


def update_positions(api: Alpaca, state: dict, rows: dict[str, dict]) -> None:
    for position in list(state["positions"]):
        row = rows.get(position["symbol"])
        if not row:
            continue
        position["last_price"] = row["price"]
        # ATR-derived trailing reference stop. Orders are submitted only after a
        # completed daily bar; a gap can produce a worse fill than this level.
        trailing_stop = row["price"] - 2 * float(row.get("atr20", 0))
        position["stop"] = max(float(position["stop"]), trailing_stop)
        if row["price"] <= float(position["stop"]):
            already_exiting = any(
                p["symbol"] == position["symbol"] and p["side"] == "sell" for p in state["pending"]
            )
            if not already_exiting:
                send_order(api, state, {**row, "asset_class": position["asset_class"]}, "sell",
                           qty=float(position["qty"]))


def run() -> None:
    state = load_state()
    key, secret = credentials()
    if not key or not secret:
        state["broker_status"] = "waiting_for_credentials"
        state["message"] = "Add CHATGPT_APCA_API_KEY_ID and CHATGPT_APCA_API_SECRET_KEY to GitHub Actions secrets."
        save_state(state)
        print(state["message"])
        return

    api = Alpaca(key, secret)
    try:
        account = api.trade("GET", "/account")
        if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
            raise RuntimeError("Alpaca paper account is not active for trading.")
        reconcile_foreign_activity(api, state)

        # Reconcile broker-confirmed fills before sizing any new paper orders.
        remaining = []
        for pending in state["pending"]:
            result = filled_order(api, pending)
            if result is None:
                remaining.append(pending)
            else:
                apply_filled_order(state, pending, result)
        state["pending"] = remaining

        candidates, summary, rows = bar_candidates(api)
        state["scan_summary"] = summary
        state["last_scan_utc"] = now().isoformat()
        state["last_bar_date"] = summary["latest_completed_bar_date"]
        update_positions(api, state, rows)

        owned = {p["symbol"] for p in state["positions"]}
        owned.update(p["symbol"] for p in state["pending"] if p["side"] == "buy")
        reserved = sum(float(p.get("notional", 0)) for p in state["pending"] if p["side"] == "buy")
        slots = MAX_POSITIONS - len(state["positions"]) - sum(
            1 for p in state["pending"] if p["side"] == "buy"
        )
        invested = sum(float(p["qty"]) * float(p["last_price"]) for p in state["positions"])
        equity = float(state["cash"]) + invested
        processed = state.setdefault("processed_signal_dates", {})
        for row in candidates:
            if slots <= 0:
                break
            symbol = row["symbol"]
            if symbol in owned or row["bar_date"] <= processed.get(symbol, ""):
                continue
            risk_fraction = float(row["stop_fraction"])
            risk_budget = equity * MAX_RISK_FRACTION
            risk_based_notional = risk_budget / risk_fraction if risk_fraction else 0.0
            allocation_cap = equity * MAX_POSITION_FRACTION
            budget = min(risk_based_notional, allocation_cap, max(0.0, float(state["cash"]) - reserved))
            if budget < 1.0:
                continue
            send_order(api, state, row, "buy", notional=budget)
            processed[symbol] = row["bar_date"]
            owned.add(symbol)
            reserved += budget
            slots -= 1

        # Give paper market orders a brief chance to fill; queued stock orders
        # remain pending until the next eligible US session.
        time.sleep(2)
        remaining = []
        for pending in state["pending"]:
            result = filled_order(api, pending)
            if result is None:
                remaining.append(pending)
            else:
                apply_filled_order(state, pending, result)
        state["pending"] = remaining

        broker_positions = api.trade("GET", "/positions")
        owned_symbols = {normalized_symbol(p["symbol"]) for p in state["positions"]}
        if any(normalized_symbol(p["symbol"]) not in owned_symbols for p in broker_positions):
            raise RuntimeError("Unexpected paper position detected after order processing; trading paused.")

        invested = sum(float(p["qty"]) * float(p["last_price"]) for p in state["positions"])
        virtual_equity = float(state["cash"]) + invested
        spy = rows.get("SPY")
        if spy and float(spy["price"]) > 0:
            if not state.get("benchmark_shares"):
                state["benchmark_start_price"] = spy["price"]
                state["benchmark_shares"] = STARTING_CASH / spy["price"]
            benchmark = float(state["benchmark_shares"]) * float(spy["price"])
        else:
            benchmark = float(state.get("equity_history", [{}])[-1].get("benchmark", STARTING_CASH))

        state["broker_status"] = "connected_paper"
        state["broker_equity"] = float(account.get("equity", STARTING_CASH))
        state["last_run_utc"] = now().isoformat()
        state["strategy_version"] = STRATEGY_VERSION
        state["message"] = (
            "Paper only. Daily swing rules scan Alpaca-tradable US equities and USD crypto. "
            "No live endpoint, options orders, or profit guarantee."
        )
        state["equity_history"].append({
            "date": now().isoformat(), "equity": round(virtual_equity, 6),
            "benchmark": round(benchmark, 6), "cash": round(float(state["cash"]), 6),
            "positions": len(state["positions"]), "broker_equity": state["broker_equity"],
            "strategy_version": STRATEGY_VERSION,
        })
        state["equity_history"] = state["equity_history"][-2000:]
        save_state(state)
        print(
            f"Paper run complete: ${virtual_equity:.2f}; {len(state['positions'])} positions; "
            f"{summary['eligible_stocks']} eligible stocks, {summary['eligible_crypto']} crypto assets, "
            f"{summary['qualifying_signals']} daily swing signals."
        )
    except Exception as error:
        state["broker_status"] = "paused_safety_check" if any(
            text in str(error).lower() for text in ("another bot", "quantity mismatch", "unexpected paper position")
        ) else "error"
        state["message"] = str(error)[:500]
        state["last_run_utc"] = now().isoformat()
        save_state(state)
        raise


if __name__ == "__main__":
    run()

