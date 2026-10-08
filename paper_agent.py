"""Free, rule-based daily paper trader for US SPY constituents. Never places orders."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile

import pandas as pd
import requests
import yfinance as yf


STATE_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("paper_challenge_state.json")
STARTING_CASH = 100.0
TARGET_BALANCE = 1000.0
MAX_POSITIONS = 3
MIN_DOLLAR_VOLUME = 10_000_000


def initial_state() -> dict:
    return {
        "schema_version": 1,
        "starting_cash": STARTING_CASH,
        "target_balance": TARGET_BALANCE,
        "cash": STARTING_CASH,
        "positions": [],
        "pending": [],
        "trades": [],
        "equity_history": [],
        "last_bar_date": None,
        "benchmark_shares": None,
        "last_run_utc": None,
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return initial_state()
    with STATE_PATH.open("r", encoding="utf-8") as file:
        state = json.load(file)
    if state.get("schema_version") != 1:
        raise ValueError("Unsupported paper challenge state format.")
    base = initial_state()
    base.update(state)
    return base


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=STATE_PATH.parent, delete=False) as file:
        json.dump(state, file, indent=2)
        file.write("\n")
        temporary_path = Path(file.name)
    temporary_path.replace(STATE_PATH)


def spy_holdings() -> list[str]:
    url = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    raw = pd.read_excel(BytesIO(response.content), header=None, dtype=str)
    header_row = symbol_column = None
    for row_number in range(min(20, len(raw))):
        values = raw.iloc[row_number].astype(str).str.strip().str.lower().tolist()
        for label in ("ticker", "symbol"):
            if label in values:
                header_row, symbol_column = row_number, values.index(label)
                break
        if header_row is not None:
            break
    if header_row is None:
        raise ValueError("Could not locate the ticker column in the SPY holdings file.")
    symbols = raw.iloc[header_row + 1 :, symbol_column].dropna().astype(str).str.strip().tolist()
    cleaned = {
        symbol.replace(".", "-")
        for symbol in symbols
        if symbol and symbol.lower() not in {"nan", "cash", "usd", "spdr s&p 500 etf trust"}
        and symbol.isascii() and any(character.isalpha() for character in symbol)
    }
    cleaned.add("SPY")
    return sorted(cleaned)


def get_price_frames(symbols: list[str]) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for offset in range(0, len(symbols), 50):
        batch = symbols[offset : offset + 50]
        try:
            downloaded = yf.download(
                batch, period="1y", interval="1d", auto_adjust=True,
                group_by="ticker", progress=False, threads=False, timeout=45,
            )
        except Exception as error:
            print(f"Price batch {offset // 50 + 1} failed: {error}")
            continue
        if downloaded.empty:
            continue
        for symbol in batch:
            try:
                if isinstance(downloaded.columns, pd.MultiIndex):
                    if symbol in downloaded.columns.get_level_values(0):
                        frame = downloaded[symbol]
                    elif symbol in downloaded.columns.get_level_values(1):
                        frame = downloaded.xs(symbol, axis=1, level=1)
                    else:
                        continue
                elif len(batch) == 1:
                    frame = downloaded
                else:
                    continue
                frame = frame.dropna(subset=["Close", "High", "Low", "Volume"], how="any")
                if not frame.empty:
                    frames[symbol] = frame
            except (KeyError, ValueError):
                continue
    return frames


def score(symbol: str, frame: pd.DataFrame) -> dict | None:
    close = frame["Close"].dropna()
    high = frame["High"].dropna()
    low = frame["Low"].dropna()
    volume = frame["Volume"].dropna()
    if len(close) < 60 or len(high) < 21 or len(volume) < 21:
        return None
    price = float(close.iloc[-1])
    average_volume = float(volume.iloc[-21:-1].mean())
    rel_volume = float(volume.iloc[-1] / average_volume) if average_volume > 0 else 0.0
    ma21 = float(close.rolling(21).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else float("nan")
    prior_high = float(high.iloc[-21:-1].max())
    momentum = float((price / float(close.iloc[-21]) - 1) * 100)
    dollar_volume = float((close.tail(20) * volume.tail(20)).mean())
    trend = price > ma21 > ma50
    breakout = price > prior_high
    volume_ok = rel_volume >= 1.5
    points = sum((trend, breakout, volume_ok, momentum > 0, pd.isna(ma200) or price > ma200))
    stop = min(float(low.iloc[-10:].min()), price * 0.94)
    return {
        "symbol": symbol,
        "price": price,
        "score": int(points),
        "rel_volume": rel_volume,
        "momentum_pct": momentum,
        "dollar_volume": dollar_volume,
        "breakout": breakout,
        "volume_ok": volume_ok,
        "stop": stop,
        "bar_date": pd.Timestamp(close.index[-1]).date().isoformat(),
    }


def append_trade(state: dict, date_text: str, action: str, symbol: str, shares: float, price: float, note: str) -> None:
    state["trades"].append({
        "date": date_text, "action": action, "symbol": symbol,
        "shares": round(shares, 8), "price": round(price, 6), "note": note,
    })


def run() -> None:
    state = load_state()
    symbols = spy_holdings()
    frames = get_price_frames(symbols)
    if "SPY" not in frames:
        raise RuntimeError("SPY benchmark price history is unavailable; no state was changed.")
    bar_date = pd.Timestamp(frames["SPY"].index[-1]).date().isoformat()
    if state["last_bar_date"] == bar_date:
        print(f"No new completed daily bar ({bar_date}); leaving the challenge unchanged.")
        return

    scores = {symbol: score(symbol, frame) for symbol, frame in frames.items()}
    scores = {symbol: result for symbol, result in scores.items() if result is not None and result["bar_date"] == bar_date}
    if not scores:
        raise RuntimeError("No price rows match SPY's latest daily bar; no state was changed.")
    spy_price = scores["SPY"]["price"]

    if state["last_bar_date"] is None:
        state["benchmark_shares"] = STARTING_CASH / spy_price
        state["pending"] = [
            {"symbol": row["symbol"], "signal_date": bar_date, "signal_stop": row["stop"]}
            for row in sorted(scores.values(), key=lambda row: (row["score"], row["rel_volume"]), reverse=True)
            if row["score"] >= 4 and row["breakout"] and row["volume_ok"] and row["dollar_volume"] >= MIN_DOLLAR_VOLUME
        ][:10]
        state["equity_history"].append({"date": bar_date, "equity": STARTING_CASH, "benchmark": STARTING_CASH, "cash": STARTING_CASH, "positions": 0})
        state["last_bar_date"] = bar_date
        state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        print(f"Initialized $100 paper challenge for {bar_date}; queued {len(state['pending'])} signals.")
        return

    cash = float(state["cash"])
    open_positions = []
    for position in state["positions"]:
        symbol = position["symbol"]
        row = scores.get(symbol)
        if row is None:
            open_positions.append(position)
            continue
        stop = max(float(position["stop"]), float(row["stop"]))
        if row["price"] <= stop:
            cash += float(position["shares"]) * row["price"]
            append_trade(state, bar_date, "SELL", symbol, float(position["shares"]), row["price"], "Daily close at or below trailing reference stop")
        else:
            open_positions.append({**position, "last_price": row["price"], "stop": stop})
    state["positions"] = open_positions

    held = {position["symbol"] for position in state["positions"]}
    for pending in state["pending"]:
        if len(state["positions"]) >= MAX_POSITIONS:
            break
        symbol = pending["symbol"]
        row = scores.get(symbol)
        if symbol in held or row is None:
            continue
        if row["price"] <= float(pending["signal_stop"]):
            append_trade(state, bar_date, "SKIP", symbol, 0, row["price"], "Price was below the signal stop at the next daily close")
            continue
        marked_equity = cash + sum(float(position["shares"]) * float(position["last_price"]) for position in state["positions"])
        allocation = min(cash, marked_equity / MAX_POSITIONS)
        if allocation <= 0:
            break
        shares = allocation / row["price"]
        position = {
            "symbol": symbol, "shares": shares, "entry_price": row["price"],
            "last_price": row["price"], "stop": max(float(pending["signal_stop"]), row["stop"]),
            "entry_date": bar_date,
        }
        state["positions"].append(position)
        cash -= allocation
        held.add(symbol)
        append_trade(state, bar_date, "BUY", symbol, shares, row["price"], "Paper fill at the next available daily close")
    state["cash"] = max(cash, 0.0)

    candidates = [
        row for row in scores.values()
        if row["score"] >= 4 and row["breakout"] and row["volume_ok"]
        and row["dollar_volume"] >= MIN_DOLLAR_VOLUME and row["symbol"] not in held
    ]
    candidates.sort(key=lambda row: (row["score"], row["rel_volume"]), reverse=True)
    state["pending"] = [
        {"symbol": row["symbol"], "signal_date": bar_date, "signal_stop": row["stop"]}
        for row in candidates[:10]
    ]
    marked_positions = sum(float(position["shares"]) * float(position["last_price"]) for position in state["positions"])
    equity = float(state["cash"]) + marked_positions
    benchmark = float(state["benchmark_shares"] or 0) * spy_price
    state["equity_history"].append({
        "date": bar_date, "equity": round(equity, 6), "benchmark": round(benchmark, 6),
        "cash": round(float(state["cash"]), 6), "positions": len(state["positions"]),
    })
    state["last_bar_date"] = bar_date
    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"Updated paper challenge for {bar_date}: equity=${equity:.2f}, positions={len(state['positions'])}, pending={len(state['pending'])}.")


if __name__ == "__main__":
    run()

