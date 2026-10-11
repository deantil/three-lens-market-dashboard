"""Readable trade tickets for manual review. Never sends orders to another broker."""
import re


def option_details(symbol):
    match = re.fullmatch(r"(.+?)(\d{2})(\d{2})(\d{2})([CP])(\d{8})", symbol)
    if not match:
        return {}
    underlying, year, month, day, side, strike = match.groups()
    return {"underlying": underlying, "expiration": f"20{year}-{month}-{day}",
            "option_type": "CALL" if side == "C" else "PUT", "strike": int(strike) / 1000,
            "contract_multiplier": 100}


def tickets(state):
    rows = []
    for order in state.get("pending", []):
        asset = order["asset_class"]
        symbol, side = order["symbol"], order["side"]
        request = order.get("request", {})
        quantity = order.get("qty")
        accounted_qty = float(order.get("accounted_qty", 0))
        if quantity is not None:
            quantity = max(0, float(quantity) - accounted_qty)
        remaining_dollars = max(0, float(order.get("notional", 0)) - float(order.get("accounted_value", 0)) * (100 if asset == "option" else 1))
        details = option_details(symbol) if asset == "option" else {}
        row = {"ticket_id": order.get("client_order_id", order.get("order_id")),
               "alpaca_status": "PENDING; confirm current broker status before copying",
               "asset_class": asset, "symbol": symbol, "action": side.upper(),
               "order_type": request.get("type", "market").upper(),
               "time_in_force": request.get("time_in_force", "gtc" if asset == "crypto" else "day").upper(),
               "dollar_amount": round(remaining_dollars, 2) if side == "buy" else None,
               "quantity": quantity, "limit_price": request.get("limit_price"),
               "submitted_utc": order.get("submitted_utc"), "signal_bar_date": order.get("signal_bar_date"),
               "already_filled_quantity": accounted_qty,
               "initial_reference_stop": round(float(order.get("price", 0)) * (1 - float(order.get("stop_fraction", 0))), 4) or None,
               **details}
        if asset == "crypto":
            row.update(thinkorswim_status="NOT AN EXACT SPOT-CRYPTO MATCH",
                       manual_instruction="Keep this spot order on Alpaca paper. A crypto ETF or futures contract is a different trade; no substitution is authorized.")
        elif asset == "option":
            row.update(thinkorswim_status="CHECK matching listed contract and paperMoney eligibility",
                       manual_instruction=f"In paperMoney, select {details.get('underlying', symbol)} option chain, {details.get('expiration', '')}, {details.get('strike', '')} {details.get('option_type', '')}. {'BUY TO OPEN' if side == 'buy' else 'SELL TO CLOSE'} {quantity} contract(s), DAY LIMIT at {request.get('limit_price', order.get('price'))}. Premium = limit x contracts x 100; check fees and available paper cash.")
        else:
            row.update(thinkorswim_status="CHECK symbol, fractional/notional support in your paperMoney account",
                       manual_instruction=(f"In paperMoney, choose {symbol}, BUY, dollar-based quantity ${remaining_dollars:.2f}, MARKET, DAY. Eligible fractional orders require MARKET."
                                           if side == 'buy' else f"In paperMoney, close your corresponding {symbol} holding with SELL MARKET DAY; use your thinkorswim held quantity, which may differ from Alpaca."))
        row["exit_rule"] = ("Attempt exit at 50% premium loss or five calendar days before expiry; periodic checks can miss a move."
                            if asset == "option" else "Daily completed-bar ATR trailing reference stop; it is not a broker resting stop.")
        row["copy_warning"] = "PaperMoney only. Check the ticket has not already been copied or filled/canceled. Manual delay, broker availability and prices mean fills and returns will differ."
        rows.append(row)
    return rows


def holdings(state):
    return [{"symbol": p["symbol"], "asset_class": p["asset_class"], "alpaca_quantity": p["qty"],
             "entry_price": p["entry_price"], "last_price": p["last_price"],
             "reference_stop": p.get("stop"), "expiration": p.get("expiration_date"),
             "thinkorswim_status": "NOT AN EXACT SPOT-CRYPTO MATCH" if p["asset_class"] == "crypto" else "CHECK matching symbol/contract; size from your own paper holding",
             **(option_details(p["symbol"]) if p["asset_class"] == "option" else {})}
            for p in state.get("positions", [])]
