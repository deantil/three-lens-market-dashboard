"""Conservative daily-bar stock replay and point-in-time option quote replay.

No broker orders. Frozen ETF universe avoids ranking constituents using today's
liquidity. It is a subset benchmark, not a test of the full deployed universe.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import csv
import json
import math
import statistics

UNIVERSE = ("SPY", "QQQ", "IWM", "DIA")


def metrics(curve, trades):
    peak = curve[0] if curve else 100
    drawdown = 0
    for value in curve:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    profits = [t["pnl"] for t in trades]
    wins, losses = sum(max(p, 0) for p in profits), -sum(min(p, 0) for p in profits)
    returns = [b / a - 1 for a, b in zip(curve, curve[1:]) if a > 0]
    return {"return_pct": round((curve[-1] / 100 - 1) * 100, 3) if curve else 0,
            "max_drawdown_pct": round(drawdown * 100, 3), "exit_fills": len(trades),
            "profit_factor": round(wins / losses, 3) if losses else None,
            "win_rate_pct": round(sum(p > 0 for p in profits) / len(profits) * 100, 1) if profits else None,
            "daily_volatility_pct": round(statistics.stdev(returns) * 100, 3) if len(returns) > 1 else None}


def stock_replay(data, signal_fn, start, end, slippage=0.001, fee=0.0):
    """Completed-day signal -> next available session open, cash shared across symbols.

    Reference stops are checked at the close like the deployed agent, so gaps are
    filled at the following open rather than magically at the stop price.
    """
    dates = sorted({r["t"][:10] for rows in data.values() for r in rows if start <= r["t"][:10] <= end})
    indexed = {s: {r["t"][:10]: r for r in rows} for s, rows in data.items()}
    cash, positions, pending, curve, trades = 100.0, {}, [], [], []
    for day in dates:
        deferred = []
        for order in pending:
            symbol, side = order["symbol"], order["side"]
            bar = indexed[symbol].get(day)
            if bar is None:
                deferred.append(order)
                continue
            opening = float(bar["o"])
            if opening <= 0 or float(bar.get("v", 0)) <= 0:
                continue
            if side == "buy" and symbol not in positions:
                budget = min(order["budget"], cash)
                if budget < 1 or budget <= fee:
                    continue
                price = opening * (1 + slippage)
                qty = (budget - fee) / price
                cash -= budget
                positions[symbol] = {"qty": qty, "entry": price, "cost": budget, "mark": price,
                                     "stop": price * (1 - order["stop_fraction"]), "entry_date": day}
            elif side == "sell" and symbol in positions:
                position = positions.pop(symbol)
                price = opening * (1 - slippage)
                proceeds = max(0, position["qty"] * price - fee)
                cash += proceeds
                trades.append({"symbol": symbol, "entry_date": position["entry_date"], "exit_date": day,
                               "entry": position["entry"], "exit": price, "pnl": proceeds - position["cost"]})
        pending = deferred
        eligible = []
        for symbol, all_bars in data.items():
            history = [r for r in all_bars if r["t"][:10] <= day]
            current = indexed[symbol].get(day)
            if current is None:
                continue
            signal = signal_fn(symbol, history, day)
            if symbol in positions:
                position = positions[symbol]
                position["mark"] = float(current["c"])
                if signal:
                    position["stop"] = max(position["stop"], signal["price"] - 2 * signal["atr20"])
                if position["mark"] <= position["stop"] and not any(o["symbol"] == symbol for o in pending):
                    pending.append({"symbol": symbol, "side": "sell"})
            elif signal and signal["signal"]:
                eligible.append(signal)
        equity = cash + sum(p["qty"] * p["mark"] for p in positions.values())
        curve.append(equity)
        reserved = sum(o.get("budget", 0) for o in pending if o["side"] == "buy")
        owned = set(positions) | {o["symbol"] for o in pending if o["side"] == "buy"}
        for row in sorted(eligible, key=lambda r: r["score"], reverse=True):
            if len(owned) >= 3 or row["symbol"] in owned:
                continue
            budget = min(equity * 0.20, equity * 0.005 / row["stop_fraction"], cash - reserved)
            if budget >= 1:
                pending.append({"symbol": row["symbol"], "side": "buy", "budget": budget, "stop_fraction": row["stop_fraction"]})
                reserved += budget
                owned.add(row["symbol"])
    # Final liquidation is conservative: mark at close less sell slippage and fee.
    final = cash + sum(max(0, p["qty"] * p["mark"] * (1 - slippage) - fee) for p in positions.values())
    if curve:
        curve[-1] = final
    return {"metrics": metrics(curve, trades), "trades": trades, "curve": curve,
            "dates": dates, "open_positions_at_end": len(positions), "pending_at_end": len(pending)}


def option_replay(rows, fee=0.65, min_delay=60):
    """Replay archived real OPRA snapshots; missing exits are charged full premium.

    Entry requires another quote after the decision time: no same-snapshot fill.
    Available quotes determine fills, not modeled option prices or stock returns.
    Independent option sleeve cap: 5% of $100; does not pretend to replay combined
    stock/crypto allocations. Quotes collected today do not reconstruct old chains.
    """
    from options_research import quote_check
    cash, position, pending, pending_exit, curve, trades, skipped = 100.0, None, None, None, [100.0], 0, 0
    exit_records = []
    processed = set()
    for row in sorted(rows, key=lambda r: r["observed_at"]):
        observed = datetime.fromisoformat(row["observed_at"])
        if row.get("feed") != "opra":
            skipped += 1
            continue
        valid, bid, ask = quote_check({"bp": row["bid"], "ap": row["ask"], "t": row["quote_time"],
                                     "bs": row.get("bid_size", 0), "as": row.get("ask_size", 0)}, observed)
        if not valid:
            skipped += 1
            continue
        if pending and (observed - pending["time"]).total_seconds() >= 86400:
            pending = None
        if pending and row["symbol"] == pending["symbol"] and (observed - pending["time"]).total_seconds() >= min_delay:
            if ask <= pending["limit"] and ask * 100 + fee <= min(cash, 5):
                cash -= ask * 100 + fee
                position = {"symbol": row["symbol"], "premium": ask * 100, "entry": observed,
                            "expiry": datetime.fromisoformat(row["expiration_date"]).date(), "cost": ask * 100 + fee}
            pending = None
        if position and row["symbol"] == position["symbol"] and observed > position["entry"]:
            if pending_exit and (observed - pending_exit["time"]).total_seconds() >= min_delay and bid >= pending_exit["limit"]:
                proceeds = max(0, bid * 100 - fee)
                cash += proceeds
                exit_records.append({"pnl": proceeds - position["cost"]})
                trades += 1
                position = None
                pending_exit = None
            elif bid * 100 <= position["premium"] * .50 or (position["expiry"] - observed.date()).days <= 5:
                pending_exit = {"time": observed, "limit": bid}
        if not position and not pending and row.get("eligible") and row.get("market_open"):
            key = (row.get("underlying", row["symbol"]), row.get("bar_date", observed.date().isoformat()))
            if key not in processed and ask * 100 + fee <= min(cash, 5):
                pending = {"symbol": row["symbol"], "limit": ask, "time": observed}
                processed.add(key)
        curve.append(cash + (position["premium"] if position else 0))
    missing_exit = bool(position)
    if position:
        exit_records.append({"pnl": -position["cost"]})
        curve.append(cash)  # No optimistic mark for a contract lacking an exit quote.
    return {"metrics": metrics(curve, exit_records), "filled_exits": trades, "skipped_quotes": skipped,
            "missing_exit_charged_full_premium": missing_exit, "observations": len(rows),
            "status": "insufficient_data" if trades < 25 else "research_only"}


def download_stocks(api, as_of):
    params = {"symbols": ",".join(UNIVERSE), "timeframe": "1Day", "feed": "iex", "adjustment": "split",
              "start": (as_of - timedelta(days=1100)).isoformat(), "end": as_of.isoformat(), "limit": 10000, "sort": "asc"}
    output = {}
    for _ in range(100):
        page = api.data("/v2/stocks/bars", params)
        for symbol, rows in page.get("bars", {}).items():
            output.setdefault(symbol, []).extend(rows)
        if not page.get("next_page_token"):
            return output
        params["page_token"] = page["next_page_token"]
    raise RuntimeError("Historical pagination limit exceeded")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="paper_challenge_state.json")
    parser.add_argument("--output", default="research")
    args = parser.parse_args()
    import paper_agent as agent
    agent.STATE_PATH = Path(args.state)
    key, secret = agent.credentials()
    if not key or not secret:
        raise RuntimeError("Dedicated ChatGPT paper credentials are required for historical data")
    as_of = datetime.now(timezone.utc)
    api = agent.Alpaca(key, secret)
    data = download_stocks(api, as_of)
    if set(data) != set(UNIVERSE) or any(len(bars) < 500 for bars in data.values()):
        raise RuntimeError("Incomplete frozen-universe history; no backtest report will be published")
    # Anchor signal evaluation at the next UTC midnight so today's test bar is
    # complete, while still passing only the historical prefix into the rule.
    def signal(symbol, bars, day):
        prior_now = agent.now
        agent.now = lambda: datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=1)
        try:
            return agent.make_candidate(symbol, "stock", bars)
        finally:
            agent.now = prior_now
    end = (as_of.date() - timedelta(days=1)).isoformat()
    start = (as_of.date() - timedelta(days=730)).isoformat()
    midpoint = (as_of.date() - timedelta(days=365)).isoformat()
    reports = {}
    development_end = (as_of.date() - timedelta(days=366)).isoformat()
    for label, first, last in (("development", start, development_end), ("holdout", midpoint, end)):
        reports[label] = {}
        for label_cost, slip, fee in (("base", .001, 0), ("stress", .003, .01)):
            result = stock_replay(data, signal, first, last, slip, fee)
            spy = [r for r in data["SPY"] if first <= r["t"][:10] <= last]
            result["spy_buy_hold_pct"] = round((float(spy[-1]["c"]) / (float(spy[0]["o"]) * (1 + slip)) * (1 - slip) - 1) * 100, 3)
            reports[label][label_cost] = result
    option_rows = []
    for path in Path(args.output).glob("quotes-*.jsonl"):
        option_rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    option_report = option_replay(option_rows)
    state = json.loads(Path(args.state).read_text()) if Path(args.state).exists() else {}
    result = {"as_of": as_of.isoformat(), "strategy_version": agent.STRATEGY_VERSION, "universe": list(UNIVERSE),
              "stocks": reports, "options": option_report, "paper_status": state.get("broker_status"),
              "limitations": ["Fixed current ETF universe, not full-market or crypto validation; universe selection still subjective.",
                              "IEX coverage and split-adjusted daily bars; dividends excluded from strategy and SPY benchmark.",
                              "Close-based stops; next-open fills include gaps, fractional shares and shared cash; no shorting or leverage.",
                              "No fitted parameters or automatic promotion. Historical holdout has not been blind to the developer.",
                              "Options use archived OPRA quotes only, 100x multiplier, bid/ask and $0.65 per contract per side; no synthetic premiums.",
                              "Sparse quote archive cannot establish continuous execution; missing exits charged full premium.",
                              "Crypto backtest not implemented; crypto remains paper-only and unvalidated."]}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "review.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    lines = ["# Weekly paper strategy review", "", f"Generated {as_of.isoformat()}",
             "Paper only. No strategy changes are applied automatically.", "", "| Period | Costs | Return % | Max DD % | Exit fills | SPY % |",
             "|---|---|---:|---:|---:|---:|"]
    for period, costs in reports.items():
        for cost, run in costs.items():
            m = run["metrics"]
            lines.append(f"| {period} | {cost} | {m['return_pct']} | {m['max_drawdown_pct']} | {m['exit_fills']} | {run['spy_buy_hold_pct']} |")
    lines += ["", f"Options: {option_report['status']}; {len(option_rows)} archived observations; {option_report['filled_exits']} filled exits.",
              "At least 25 exits across distinct trades and adequate regime coverage are needed before drawing conclusions.",
              "", "## Limitations", *["- " + line for line in result["limitations"]]]
    (output / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
