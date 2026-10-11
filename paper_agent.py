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
import options_research as opt
import manual_replication
import trade_alerts


STATE_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("paper_challenge_state.json")
STARTING_CASH = 100.0
TARGET_BALANCE = 1000.0
STRATEGY_VERSION = "daily-swing-options-v2"
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
        "multiplier": 100 if re.search(r"\d{6}[CP]\d{8}$", symbol) else 1,
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
    if (now().date() - datetime.fromisoformat(bar_date).date()).days > 4:
        return None
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
        if normalized_symbol(position["symbol"]) in pending_symbols:
            continue  # Partial fills are reconciled below before sizing.
        expected = float(position["qty"])
        if abs(broker_qty - expected) > max(1e-6, expected * 0.02):
            raise RuntimeError(f"Quantity mismatch for {position['symbol']}; trading paused without sending orders.")


def filled_order(api: Alpaca, pending: dict) -> dict | None:
    if pending.get("order_id"):
        return api.trade("GET", f"/orders/{pending['order_id']}")
    try:
        order = api.trade("GET", "/orders:by_client_order_id", params={"client_order_id": pending["client_order_id"]})
    except requests.HTTPError as error:
        if error.response is None or error.response.status_code != 404:
            raise
        order = api.trade("POST", "/orders", json=pending["request"])
    pending["order_id"] = order["id"]
    return order


def apply_filled_order(state: dict, pending: dict, order: dict) -> None:
    symbol, side = pending["symbol"], pending["side"]
    qty = float(order.get("filled_qty") or 0)
    price = float(order.get("filled_avg_price") or pending.get("price") or 0)
    previous_qty = float(pending.get("accounted_qty", 0))
    cumulative_value = qty * price
    delta_qty = qty - previous_qty
    delta_value = cumulative_value - float(pending.get("accounted_value", 0))
    if delta_qty <= 0:
        return
    price = delta_value / delta_qty
    qty = delta_qty
    mult = opt.multiplier(pending)
    if side == "buy" and qty > 0 and price > 0:
        cost = delta_value * mult
        if cost > float(state["cash"]) + 0.02:
            raise RuntimeError("Paper fill exceeded the $100 strategy ledger; trading paused.")
        state["cash"] = max(0.0, float(state["cash"]) - cost)
        stop_fraction = float(pending.get("stop_fraction", 0.08))
        position = next((p for p in state["positions"] if p["symbol"] == symbol), None)
        if position:
            total_qty = float(position["qty"]) + qty
            position["entry_price"] = (float(position["entry_price"]) * float(position["qty"]) + delta_value) / total_qty
            position["qty"] = total_qty
        else:
            state["positions"].append({
            "symbol": symbol, "asset_class": pending["asset_class"], "qty": qty,
            "entry_price": price, "last_price": price,
            "stop": price * (1 - stop_fraction), "stop_fraction": stop_fraction,
            "entry_order_id": order["id"], "entry_date": now().date().isoformat(),
            "strategy_version": STRATEGY_VERSION,
            "multiplier": mult, "expiration_date": pending.get("expiration_date"),
            "underlying": pending.get("underlying"),
            })
        append_trade(state, "BUY", symbol, qty, price, "Filled by Alpaca paper trading")
    elif side == "sell" and qty > 0 and price > 0:
        state["cash"] = float(state["cash"]) + delta_value * mult
        for position in state["positions"]:
            if position["symbol"] == symbol:
                position["qty"] = max(0, float(position["qty"]) - qty)
        state["positions"] = [p for p in state["positions"] if float(p["qty"]) > 1e-9]
        append_trade(state, "SELL", symbol, qty, price, "Exit filled by Alpaca paper trading")
    pending["accounted_qty"] = float(order.get("filled_qty") or 0)
    pending["accounted_value"] = cumulative_value


def reconcile_pending(api, state):
    remaining = []
    for pending in state["pending"]:
        order = filled_order(api, pending)
        apply_filled_order(state, pending, order)
        if order.get("status") not in {"filled", "canceled", "expired", "rejected"}:
            remaining.append(pending)
    state["pending"] = remaining
    save_state(state)


def reserved_cash(state):
    return sum(max(0, float(p.get("notional", 0)) - float(p.get("accounted_value", 0)) * opt.multiplier(p))
               for p in state["pending"] if p["side"] == "buy")


def invested_value(state):
    return sum(float(p["qty"]) * float(p["last_price"]) * opt.multiplier(p) for p in state["positions"])


def send_order(api: Alpaca, state: dict, row: dict, side: str,
               notional: float = 0.0, qty: float | None = None) -> None:
    asset_class = row["asset_class"]
    symbol = row["symbol"]
    client_id = BOT_PREFIX + uuid.uuid4().hex[:24]
    body = {
        "symbol": symbol, "side": side, "type": "market",
        "time_in_force": "gtc" if asset_class == "crypto" else "day",
        "client_order_id": client_id,
    }
    if asset_class == "option":
        if qty is None or qty < 1 or qty != int(qty):
            raise ValueError("Options require positive whole-contract quantities")
        body.update(type="limit", qty=str(int(qty)), limit_price=f"{row['price']:.2f}",
                    position_intent="buy_to_open" if side == "buy" else "sell_to_close")
        if side == "buy":
            notional = float(body["limit_price"]) * int(qty) * 100
            if notional > min(float(state["cash"]) - reserved_cash(state),
                              (float(state["cash"]) + invested_value(state)) * opt.MAX_PREMIUM_FRACTION) + 1e-8:
                raise ValueError("Option limit price exceeds the strategy premium budget")
    elif qty is None:
        body["notional"] = f"{notional:.2f}"
    else:
        body["qty"] = f"{qty:.9f}".rstrip("0").rstrip(".")
    allowed, proposal_id = trade_alerts.gate(state, body, row, save_state, now())
    if not allowed:
        return False
    pending = {
        "order_id": None, "client_order_id": client_id, "symbol": symbol,
        "asset_class": asset_class, "side": side, "notional": notional,
        "qty": qty, "price": row.get("price", 0),
        "stop_fraction": row.get("stop_fraction", 0),
        "signal_bar_date": row.get("bar_date"), "score": row.get("score"),
        "submitted_utc": now().isoformat(),
        "request": body, "multiplier": opt.multiplier(row),
        "expiration_date": row.get("expiration_date"), "underlying": row.get("underlying"),
        "alert_proposal_id": proposal_id,
    }
    state['trade_proposals'][proposal_id]['consumed'] = True
    state['trade_proposals'][proposal_id]['status'] = 'order_intent_recorded'
    state["pending"].append(pending)
    save_state(state)  # Persist intent before the broker call; retry by unique client ID.
    order = api.trade("POST", "/orders", json=body)
    pending["order_id"] = order["id"]
    save_state(state)
    return True


def update_positions(api: Alpaca, state: dict, rows: dict[str, dict]) -> None:
    for position in list(state["positions"]):
        if position["asset_class"] == "option":
            quote = opt.position_quote(api, position["symbol"], now())
            if not quote["valid"]:
                state.setdefault("options_warnings", []).append(f"No executable quote for {position['symbol']}; exit may be delayed")
                continue
            position["last_price"] = quote["bid"]
            expiry = datetime.fromisoformat(position["expiration_date"]).date()
            exit_due = (expiry - now().date()).days <= opt.EXIT_DAYS or quote["bid"] <= float(position["entry_price"]) * 0.50
            if exit_due and not any(p["symbol"] == position["symbol"] for p in state["pending"]):
                send_order(api, state, {**position, "price": quote["bid"]}, "sell", qty=float(position["qty"]))
            continue
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
        if not all(os.getenv(k, '').strip() for k in ('EMAIL_TO', 'SMTP_USER', 'SMTP_PASS')):
            state['email_alert_status'] = 'not_configured: EMAIL_TO, SMTP_USER and SMTP_PASS required'
        if account.get("status") != "ACTIVE" or account.get("trading_blocked"):
            raise RuntimeError("Alpaca paper account is not active for trading.")
        reconcile_foreign_activity(api, state)
        # Cancel only this ledger's old open orders, which predate email approval.
        # Broker-confirmed partial fills remain accounted for below.
        for pending in state['pending']:
            if not pending.get('alert_proposal_id'):
                if not pending.get('order_id'):
                    raise RuntimeError('Unalerted uncertain order intent; manual broker reconciliation required')
                order = api.trade('GET', f"/orders/{pending['order_id']}")
                if order.get('status') not in {'filled', 'canceled', 'expired', 'rejected', 'pending_cancel'}:
                    api.trade('DELETE', f"/orders/{pending['order_id']}")
                    state.setdefault('alert_migration', []).append({'symbol': pending['symbol'], 'action': 'cancel_requested', 'time': now().isoformat()})
        reconcile_pending(api, state)
        reconcile_foreign_activity(api, state)
        approval_id = os.getenv('APPROVE_PROPOSAL_ID', '').strip()
        if approval_id:
            trade_alerts.approve(state, approval_id, now())
            save_state(state)

        # Reconcile broker-confirmed fills before sizing any new paper orders.
        options_only = "--options-only" in sys.argv
        if options_only:
            bars = fetch_bars(api, "us_equity", [{"symbol": s} for s in opt.UNDERLYINGS])
            rows = {s: r for s, b in bars.items() if (r := make_candidate(s, "stock", b))}
            candidates, summary = [], state.get("scan_summary", {})
        else:
            candidates, summary, rows = bar_candidates(api)
        state["scan_summary"] = summary
        summary["options_enabled"] = True
        state["last_scan_utc"] = now().isoformat()
        state["last_bar_date"] = summary.get("latest_completed_bar_date")
        clock = api.trade("GET", "/clock")
        state["options_warnings"] = []
        if not options_only or clock.get("is_open"):
            update_positions(api, state, rows)

        owned = {p["symbol"] for p in state["positions"]}
        owned.update(p["symbol"] for p in state["pending"] if p["side"] == "buy")
        reserved = reserved_cash(state)
        slots = MAX_POSITIONS - len(owned)
        invested = invested_value(state)
        equity = float(state["cash"]) + invested
        processed = state.setdefault("processed_signal_dates", {})
        option_rows, option_summary = opt.scan(api, rows, now(), equity, max(0, float(state["cash"]) - reserved))
        state["options_research"] = {"as_of": now().isoformat(), "summary": option_summary, "candidates": option_rows[:40]}
        research_dir = Path("research")
        research_dir.mkdir(exist_ok=True)
        observed = now().isoformat()
        with (research_dir / f"quotes-{now().date().isoformat()}.jsonl").open("a", encoding="utf-8") as archive:
            for row in option_rows[:200]:
                archive.write(json.dumps({**row, "observed_at": observed, "market_open": bool(clock.get("is_open"))}) + "\n")
        close_time = datetime.fromisoformat(clock["next_close"]) if clock.get("next_close") else now()
        halted = Path("STOP").exists() or bool(state.get("halt_new_entries"))
        if (options_only or approval_id) and not halted and int(account.get("options_trading_level") or 0) >= 2 and clock.get("is_open") and (close_time - now()).total_seconds() > 3600 and slots > 0:
            for row in option_rows:
                signal_key = "option:" + row["underlying"]
                if not row["eligible"] or row["symbol"] in owned or row["bar_date"] <= processed.get(signal_key, ""):
                    continue
                if any(p.get("asset_class") == "option" for p in state["positions"] + state["pending"]):
                    break  # At most one option position or entry order.
                quote = opt.position_quote(api, row["symbol"], now())
                if not quote["valid"] or quote["ask"] * 100 > min(equity * opt.MAX_PREMIUM_FRACTION, float(state["cash"]) - reserved):
                    continue
                row = {**row, "price": quote["ask"], "premium_usd": quote["ask"] * 100}
                if not send_order(api, state, row, "buy", notional=row["premium_usd"], qty=1):
                    break
                processed[signal_key] = row["bar_date"]
                reserved += row["premium_usd"]
                slots -= 1
                break
        for row in candidates:
            if halted:
                break
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
            if not send_order(api, state, row, "buy", notional=budget):
                continue
            processed[symbol] = row["bar_date"]
            owned.add(symbol)
            reserved += budget
            slots -= 1

        # Give paper market orders a brief chance to fill; queued stock orders
        # remain pending until the next eligible US session.
        time.sleep(2)
        reconcile_pending(api, state)

        broker_positions = api.trade("GET", "/positions")
        owned_symbols = {normalized_symbol(p["symbol"]) for p in state["positions"]}
        owned_symbols.update(normalized_symbol(p["symbol"]) for p in state["pending"])
        if any(normalized_symbol(p["symbol"]) not in owned_symbols for p in broker_positions):
            raise RuntimeError("Unexpected paper position detected after order processing; trading paused.")

        invested = invested_value(state)
        virtual_equity = float(state["cash"]) + invested
        spy = rows.get("SPY")
        if spy and float(spy["price"]) > 0:
            if not state.get("benchmark_shares"):
                state["benchmark_start_price"] = spy["price"]
                state["benchmark_shares"] = STARTING_CASH / spy["price"]
            benchmark = float(state["benchmark_shares"]) * float(spy["price"])
        else:
            benchmark = float((state.get("equity_history") or [{}])[-1].get("benchmark", STARTING_CASH))

        state["broker_status"] = "connected_paper"
        state["broker_equity"] = float(account.get("equity", STARTING_CASH))
        state["last_run_utc"] = now().isoformat()
        state["strategy_version"] = STRATEGY_VERSION
        state["manual_replication"] = {
            "as_of": now().isoformat(), "destination": "thinkorswim paperMoney; manual only",
            "tickets": manual_replication.tickets(state), "holdings": manual_replication.holdings(state),
            "limitation": "All asset classes are tracked. Exact spot-crypto replication is unavailable; no automatic ETF/futures substitution. Broker eligibility and identical fills cannot be guaranteed.",
        }
        state["message"] = (
            "Paper only. Daily swing rules scan Alpaca-tradable US equities and USD crypto. "
            "Options research and small long-call paper orders share the same ledger. No live endpoint or profit guarantee."
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
            f"{summary.get('eligible_stocks', 0)} eligible stocks, {summary.get('eligible_crypto', 0)} crypto assets, "
            f"{summary.get('qualifying_signals', 0)} daily swing signals."
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


