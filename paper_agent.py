"""Paper-only Alpaca trader with an isolated $100 strategy budget.

The trading host is deliberately hard-coded to Alpaca's paper endpoint. This
bot never connects to a live brokerage endpoint. Its market strategy is an
educational hourly breakout experiment, not a return guarantee.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

import requests


STATE_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("paper_challenge_state.json")
STARTING_CASH = 100.0
TARGET_BALANCE = 1000.0
MAX_POSITIONS = 3
MAX_POSITION_DOLLARS = 15.0
STOP_PCT = 0.05
BOT_PREFIX = "tl100-"
TRADE_API = "https://paper-api.alpaca.markets/v2"
DATA_API = "https://data.alpaca.markets"
STOCKS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMD", "AMZN", "META", "TSLA", "COIN"]
CRYPTOS = ["BTC/USD", "ETH/USD", "SOL/USD", "LTC/USD"]


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_state() -> dict:
    return {
        "schema_version": 2,
        "engine": "Alpaca paper trading",
        "starting_cash": STARTING_CASH,
        "target_balance": TARGET_BALANCE,
        "cash": STARTING_CASH,
        "positions": [],
        "pending": [],
        "trades": [],
        "equity_history": [],
        "last_bar_date": None,
        "last_run_utc": None,
        "broker_status": "waiting_for_credentials",
        "broker_equity": None,
        "message": "Add Alpaca paper API credentials as GitHub Actions secrets to start.",
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return new_state()
    with STATE_PATH.open("r", encoding="utf-8") as file:
        prior = json.load(file)
    # Start a distinct Alpaca account ledger. The previous simulator's VST
    # signal and benchmark must never become Alpaca positions.
    if prior.get("schema_version") != 2 or prior.get("engine") != "Alpaca paper trading":
        return new_state()
    base = new_state()
    base.update(prior)
    return base


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
    })
    state["trades"] = state["trades"][-500:]


def credentials() -> tuple[str, str]:
    key = os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY") or ""
    secret = os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET") or ""
    return key.strip(), secret.strip()


class Alpaca:
    def __init__(self, key: str, secret: str):
        self.headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Accept": "application/json",
        }

    def trade(self, method: str, path: str, **kwargs) -> dict | list:
        response = requests.request(method, TRADE_API + path, headers=self.headers, timeout=20, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def data(self, path: str, params: dict) -> dict:
        response = requests.get(DATA_API + path, headers=self.headers, params=params, timeout=25)
        response.raise_for_status()
        return response.json()


def normalized_symbol(value: str) -> str:
    return str(value).replace("/", "").replace("-", "").upper()


def bar_candidates(api: Alpaca) -> list[dict]:
    candidates = []
    stock_end = now()
    stock_start = stock_end - timedelta(hours=48)
    for symbol in STOCKS:
        try:
            result = api.data(f"/v2/stocks/{symbol}/bars", {
                "timeframe": "1Hour", "start": stock_start.isoformat(),
                "end": stock_end.isoformat(), "limit": 40, "feed": "iex",
            })
            bars = result.get("bars", [])
            candidate = make_candidate(symbol, "stock", bars)
            if candidate:
                candidates.append(candidate)
        except requests.RequestException as error:
            print(f"Stock data unavailable for {symbol}: {error}")

    try:
        result = api.data("/v1beta3/crypto/us/bars", {
            "symbols": ",".join(CRYPTOS), "timeframe": "1Hour",
            "start": stock_start.isoformat(), "end": stock_end.isoformat(), "limit": 40,
        })
        for symbol, bars in result.get("bars", {}).items():
            candidate = make_candidate(symbol, "crypto", bars)
            if candidate:
                candidates.append(candidate)
    except requests.RequestException as error:
        print(f"Crypto data unavailable: {error}")
    return sorted(candidates, key=lambda row: (row["signal"], row["relative_volume"], row["momentum_pct"]), reverse=True)


def make_candidate(symbol: str, asset_class: str, bars: list[dict]) -> dict | None:
    if len(bars) < 22:
        return None
    closes = [float(row["c"]) for row in bars]
    highs = [float(row["h"]) for row in bars]
    volumes = [float(row.get("v", 0)) for row in bars]
    last = closes[-1]
    prior_high = max(highs[-21:-1])
    avg_volume = sum(volumes[-21:-1]) / 20
    relative_volume = volumes[-1] / avg_volume if avg_volume > 0 else 0.0
    momentum = (last / closes[-7] - 1) * 100 if closes[-7] else 0.0
    bar_time = bars[-1].get("t")
    fresh = False
    if bar_time:
        try:
            bar_dt = datetime.fromisoformat(str(bar_time).replace("Z", "+00:00"))
            fresh = (now() - bar_dt).total_seconds() <= (2 * 60 * 60)
        except ValueError:
            pass
    return {
        "symbol": symbol, "asset_class": asset_class, "price": last,
        "stop": last * (1 - STOP_PCT), "relative_volume": relative_volume,
        "momentum_pct": momentum, "bar_time": bar_time,
        "signal": fresh and last > prior_high and momentum > 0 and relative_volume >= 1.2,
    }


def reconcile_foreign_activity(api: Alpaca, state: dict) -> None:
    broker_positions = api.trade("GET", "/positions")
    open_orders = api.trade("GET", "/orders", params={"status": "open", "limit": 500})
    tracked = {normalized_symbol(position["symbol"]) for position in state["positions"]}
    pending_symbols = {normalized_symbol(order["symbol"]) for order in state["pending"]}
    foreign_positions = [p["symbol"] for p in broker_positions if normalized_symbol(p["symbol"]) not in tracked | pending_symbols]
    foreign_orders = [o.get("id") for o in open_orders if not str(o.get("client_order_id", "")).startswith(BOT_PREFIX)]
    if foreign_positions or foreign_orders:
        raise RuntimeError(
            "This Alpaca paper account has activity from another bot or manual trades. "
            f"For safety, no order was sent. Unmanaged positions: {foreign_positions}; "
            f"unmanaged open orders: {len(foreign_orders)}. Use a separate paper account or pause the other bot."
        )
    # A position in a symbol owned by this bot must still match its recorded
    # quantity. If another bot changes that position, stop instead of selling it.
    actual = {normalized_symbol(p["symbol"]): float(p["qty"]) for p in broker_positions}
    for position in state["positions"]:
        broker_qty = actual.get(normalized_symbol(position["symbol"]), 0.0)
        expected = float(position["qty"])
        if abs(broker_qty - expected) > max(1e-6, expected * 0.02):
            raise RuntimeError(f"Quantity mismatch for {position['symbol']}; another process may be trading it. No order was sent.")


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
        if side == "buy":
            state["cash"] = min(STARTING_CASH, float(state["cash"]) + float(pending["notional"]))
        return
    if side == "buy" and qty > 0 and price > 0:
        state["cash"] = max(0.0, float(state["cash"]) - qty * price)
        state["positions"].append({
            "symbol": symbol, "asset_class": pending["asset_class"], "qty": qty,
            "entry_price": price, "last_price": price, "stop": price * (1 - STOP_PCT),
            "entry_order_id": order["id"], "entry_date": now().date().isoformat(),
        })
        append_trade(state, "BUY", symbol, qty, price, "Filled by Alpaca paper trading")
    elif side == "sell" and qty > 0 and price > 0:
        state["cash"] = min(STARTING_CASH, float(state["cash"]) + qty * price)
        state["positions"] = [p for p in state["positions"] if p["symbol"] != symbol]
        append_trade(state, "SELL", symbol, qty, price, "Exit filled by Alpaca paper trading")


def send_order(api: Alpaca, state: dict, symbol: str, asset_class: str, side: str,
               notional: float, qty: float | None = None, price: float = 0.0) -> None:
    client_id = BOT_PREFIX + uuid.uuid4().hex[:24]
    body = {"symbol": symbol, "side": side, "type": "market",
            "time_in_force": "day" if asset_class == "stock" else "gtc",
            "client_order_id": client_id}
    if qty is None:
        body["notional"] = f"{notional:.2f}"
    else:
        body["qty"] = f"{qty:.9f}".rstrip("0").rstrip(".")
    order = api.trade("POST", "/orders", json=body)
    state["pending"].append({
        "order_id": order["id"], "client_order_id": client_id, "symbol": symbol,
        "asset_class": asset_class, "side": side, "notional": notional,
        "qty": qty, "price": price, "submitted_utc": now().isoformat(),
    })


def run() -> None:
    state = load_state()
    key, secret = credentials()
    if not key or not secret:
        state["broker_status"] = "waiting_for_credentials"
        state["message"] = "Add APCA_API_KEY_ID and APCA_API_SECRET_KEY to GitHub Actions secrets."
        save_state(state)
        print(state["message"])
        return

    api = Alpaca(key, secret)
    try:
        account = api.trade("GET", "/account")
        if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
            raise RuntimeError("Alpaca paper account is not active for trading.")
        stock_market_open = bool(api.trade("GET", "/clock").get("is_open", False))
        reconcile_foreign_activity(api, state)

        # Reconcile fills from prior workflow runs before evaluating any new signal.
        still_pending = []
        for pending in state["pending"]:
            result = filled_order(api, pending)
            if result is None:
                still_pending.append(pending)
            else:
                apply_filled_order(state, pending, result)
        state["pending"] = still_pending

        candidates = bar_candidates(api)
        prices = {row["symbol"]: row for row in candidates}
        # Stop exits are monitored on every scheduled pass. The stop is checked
        # by this bot and can fill worse during a fast market or workflow delay.
        for position in list(state["positions"]):
            row = prices.get(position["symbol"])
            if row:
                position["last_price"] = row["price"]
                position["stop"] = max(float(position["stop"]), row["price"] * (1 - STOP_PCT))
                if row["price"] <= float(position["stop"]):
                    already_exiting = any(p["symbol"] == position["symbol"] and p["side"] == "sell" for p in state["pending"])
                    market_available = position["asset_class"] == "crypto" or stock_market_open
                    if not already_exiting and market_available:
                        send_order(api, state, position["symbol"], position["asset_class"], "sell",
                                   0.0, qty=float(position["qty"]), price=row["price"])

        owned = {p["symbol"] for p in state["positions"]}
        owned.update(p["symbol"] for p in state["pending"] if p["side"] == "buy")
        reserved = sum(float(p["notional"]) for p in state["pending"] if p["side"] == "buy")
        slots = MAX_POSITIONS - len(state["positions"]) - sum(1 for p in state["pending"] if p["side"] == "buy")
        for row in candidates:
            if slots <= 0:
                break
            if not row["signal"]:
                continue
            if row["asset_class"] == "stock" and not stock_market_open:
                continue
            if row["symbol"] in owned or row["symbol"] not in prices:
                continue
            budget = min(MAX_POSITION_DOLLARS, max(0.0, float(state["cash"]) - reserved))
            if budget < 1.0:
                break
            send_order(api, state, row["symbol"], row["asset_class"], "buy", budget, price=row["price"])
            owned.add(row["symbol"])
            reserved += budget
            slots -= 1

        # Give market orders a short chance to fill so the dashboard reflects
        # broker-confirmed positions, not merely submitted orders.
        time.sleep(2)
        still_pending = []
        for pending in state["pending"]:
            result = filled_order(api, pending)
            if result is None:
                still_pending.append(pending)
            else:
                apply_filled_order(state, pending, result)
        state["pending"] = still_pending

        broker_positions = api.trade("GET", "/positions")
        owned_symbols = {normalized_symbol(p["symbol"]) for p in state["positions"]}
        if any(normalized_symbol(p["symbol"]) not in owned_symbols for p in broker_positions):
            raise RuntimeError("Unexpected broker position detected after order processing; trading paused.")
        broker_equity = float(account.get("equity", STARTING_CASH))
        invested = sum(float(p["qty"]) * float(p["last_price"]) for p in state["positions"])
        virtual_equity = float(state["cash"]) + invested
        state["broker_status"] = "connected_paper"
        state["broker_equity"] = broker_equity
        state["last_run_utc"] = now().isoformat()
        state["message"] = "Paper trading only. Bot allocation is capped at $100; no live endpoint is configured."
        state["equity_history"].append({
            "date": now().isoformat(), "equity": round(virtual_equity, 6),
            "benchmark": STARTING_CASH, "cash": round(float(state["cash"]), 6),
            "positions": len(state["positions"]), "broker_equity": broker_equity,
        })
        state["equity_history"] = state["equity_history"][-2000:]
        save_state(state)
        print(f"Paper run complete: virtual equity ${virtual_equity:.2f}; positions {len(state['positions'])}.")
    except Exception as error:
        state["broker_status"] = "paused_safety_check" if "another bot" in str(error).lower() or "quantity mismatch" in str(error).lower() else "error"
        state["message"] = str(error)[:500]
        state["last_run_utc"] = now().isoformat()
        save_state(state)
        raise


if __name__ == "__main__":
    run()
