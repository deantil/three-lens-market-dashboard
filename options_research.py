"""Long-option research; no broker client or live endpoint is defined here."""
from datetime import date, datetime, timedelta, timezone
import math

UNDERLYINGS = ("SPY", "QQQ", "IWM", "AAPL", "MSFT", "AMD", "PLTR")
MAX_PREMIUM_FRACTION = 0.05  # Entire premium is at risk: $5 initially.
EXIT_DAYS = 5


def multiplier(row):
    return float(row.get("multiplier", 100 if row.get("asset_class") == "option" else 1))


def quote_check(quote, as_of, max_age=60):
    try:
        bid, ask = float(quote["bp"]), float(quote["ap"])
        stamp = datetime.fromisoformat(quote["t"].replace("Z", "+00:00"))
        age = (as_of - stamp).total_seconds()
        valid = (all(math.isfinite(x) for x in (bid, ask)) and 0 < bid <= ask
                 and 0 <= age <= max_age and float(quote.get("bs", 0)) >= 1
                 and float(quote.get("as", 0)) >= 1 and (ask - bid) / ask <= 0.15)
        return valid, bid, ask
    except (KeyError, ValueError, TypeError):
        return False, 0, 0


def scan(api, rows, as_of, equity, cash):
    """Paginated chain and contract metadata. Indicative data is research only."""
    output, counts = [], {"feed": "opra", "contracts": 0, "eligible": 0}
    symbols = list(UNDERLYINGS) + [s for s, r in rows.items() if r.get("signal") and r.get("asset_class") == "stock"][:5]
    for underlying in dict.fromkeys(symbols):
        params = {"feed": counts["feed"], "limit": 1000,
                  "expiration_date_gte": (as_of.date() + timedelta(days=14)).isoformat(),
                  "expiration_date_lte": (as_of.date() + timedelta(days=45)).isoformat()}
        snapshots = {}
        try:
            for _ in range(20):
                try:
                    page = api.data(f"/v1beta1/options/snapshots/{underlying}", params)
                except Exception as error:
                    if counts["feed"] == "opra" and getattr(getattr(error, "response", None), "status_code", None) == 403:
                        counts["feed"] = params["feed"] = "indicative"
                        page = api.data(f"/v1beta1/options/snapshots/{underlying}", params)
                    else:
                        raise
                snapshots.update(page.get("snapshots", {}))
                token = page.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
            else:
                raise RuntimeError("Option-chain page limit exceeded")
            metadata = {}
            contract_params = {"underlying_symbols": underlying, "limit": 1000, "status": "active",
                               "expiration_date_gte": params["expiration_date_gte"],
                               "expiration_date_lte": params["expiration_date_lte"]}
            for _ in range(20):
                page = api.trade("GET", "/options/contracts", params=contract_params)
                metadata.update({r["symbol"]: r for r in page.get("option_contracts", [])})
                token = page.get("next_page_token")
                if not token:
                    break
                contract_params["page_token"] = token
            else:
                raise RuntimeError("Option-contract page limit exceeded")
        except Exception as error:
            counts.setdefault("errors", []).append(f"{underlying}: {type(error).__name__}")
            continue
        for symbol, snapshot in snapshots.items():
            counts["contracts"] += 1
            contract = metadata.get(symbol, {})
            if not contract.get("tradable") or str(contract.get("size")) != "100":
                continue
            quote = snapshot.get("latestQuote", {})
            valid, bid, ask = quote_check(quote, as_of)
            delta = float((snapshot.get("greeks") or {}).get("delta") or 0)
            oi_date = contract.get("open_interest_date")
            oi_fresh = bool(oi_date) and 0 <= (as_of.date() - date.fromisoformat(oi_date)).days <= 4
            liquid = float(contract.get("open_interest") or 0) >= 100 and oi_fresh
            # Calls require the same completed-bar breakout as the stock strategy.
            # Puts are researched, but are not silently introduced as a second rule set.
            signal = bool(rows.get(underlying, {}).get("signal")) and contract.get("type") == "call"
            budget = min(max(0, cash), max(0, equity) * MAX_PREMIUM_FRACTION)
            affordable = 100 * ask <= budget and ask > 0
            eligible = valid and liquid and 0.25 <= delta <= 0.65 and signal and affordable and counts["feed"] == "opra"
            output.append({"symbol": symbol, "underlying": underlying, "asset_class": "option", "multiplier": 100,
                           "type": contract.get("type"), "expiration_date": contract.get("expiration_date"),
                           "price": ask, "bid": bid, "ask": ask, "quote_time": quote.get("t"),
                           "bid_size": quote.get("bs", 0), "ask_size": quote.get("as", 0),
                           "delta": delta, "premium_usd": round(ask * 100, 2), "budget_usd": round(budget, 2),
                           "feed": counts["feed"], "signal": signal, "eligible": eligible,
                           "reason": "eligible" if eligible else "requires fresh OPRA quote, liquidity, call breakout, delta 0.25–0.65 and affordable full premium",
                           "bar_date": rows.get(underlying, {}).get("bar_date"), "stop_fraction": 0.50})
    output.sort(key=lambda r: (not r["eligible"], not r["signal"], abs(abs(r["delta"]) - 0.4), r["premium_usd"]))
    counts["eligible"] = sum(r["eligible"] for r in output)
    return output, counts


def position_quote(api, symbol, as_of):
    page = api.data("/v1beta1/options/quotes/latest", {"symbols": symbol, "feed": "opra"})
    quote = page.get("quotes", {}).get(symbol, {})
    valid, bid, ask = quote_check(quote, as_of)
    return {"valid": valid, "bid": bid, "ask": ask, "quote_time": quote.get("t")}
