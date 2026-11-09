"""Ten-position prospective cohort ledger, including still-open positions."""

from dataclasses import asdict
from .retail import Policy, plan, number
from .corporate_actions import crossing_events


def evaluate_book(record, rows, bars, sessions, events, entry_receipt=None):
    policy = Policy(**record["policy"])
    future = [i for i, d in enumerate(sessions) if d > record["publication_date"]]
    if not future:
        return {"status": "pending_entry", "net_return": None, "positions": {}, "curve": []}
    entry = future[0]
    day = sessions[entry]
    if entry_receipt is None:
        priced = []
        for rank, row in enumerate(rows, 1):
            close = number(bars.get((row["symbol"], day), {}).get("close"))
            if close is None or close <= 0:
                return {"status": "incomplete", "net_return": None, "reason": "Missing selected entry price", "symbol": row["symbol"]}
            priced.append({**row, "rank": rank, "close": close, "trade_date": day})
        # Eligibility was frozen at publication. Resize once using first
        # observed entry prices, then preserve quantities/stops/cash forever.
        allocation = plan(priced, policy, day, events=[])
        positions = {r["symbol"]: {"quantity": r["quantity"], "stop": r["stop_reference"], "cost": r["cash_required"], "entry_price": r["assumed_entry"]} for r in allocation["candidates"]}
        entry_receipt = {"entry_date": day, "publication_date": record["publication_date"], "policy": record["policy"],
                         "allocation": allocation, "positions": {s: dict(p) for s, p in positions.items()},
                         "reference_prices": {s: bars[(s, day)]["close"] for s in positions}}
    else:
        if entry_receipt["entry_date"] != day or entry_receipt["publication_date"] != record["publication_date"] or entry_receipt["policy"] != record["policy"]:
            raise ValueError("Frozen entry receipt does not match the cohort")
        for symbol, original in entry_receipt["reference_prices"].items():
            current = number(bars.get((symbol, day), {}).get("close"))
            if current is None or abs(current / original - 1) > 1e-8:
                return {"status": "incomplete", "net_return": None, "reason": "Entry-price basis revised; frozen fills require sourced reconciliation", "symbol": symbol}
        allocation = entry_receipt["allocation"]
        positions = {s: dict(p) for s, p in entry_receipt["positions"].items()}
    cash = allocation["cash_remaining"]
    trades, curve, blocked = [], [], None
    for i in range(entry, min(entry + policy.horizon + 1, len(sessions))):
        date = sessions[i]
        for symbol, position in list(positions.items()):
            if crossing_events(symbol, day, date, events):
                blocked = {"date": date, "symbol": symbol, "reason": "Unvalued corporate-action entitlement"}
                break
            bar = bars.get((symbol, date), {})
            close, opening, low = [number(bar.get(k)) for k in ("close", "open", "low")]
            if close is None or close <= 0 or i > entry and (opening is None or low is None or min(opening, low) <= 0):
                blocked = {"date": date, "symbol": symbol, "reason": "Missing held-stock OHLC"}
                break
            price, reason = None, None
            if i > entry and low <= position["stop"]:
                price, reason = min(opening, position["stop"]), "stop"
            elif i == entry + policy.horizon:
                price, reason = close, "time"
            if price is not None:
                fill = price * (1 - policy.slippage_bps / 10000)
                proceeds = position["quantity"] * fill * (1 - policy.fee_bps / 10000) - policy.flat_fee
                cash += proceeds
                trades.append({"symbol": symbol, **position, "exit_date": date, "exit_price": fill, "proceeds": proceeds, "pnl": proceeds - position["cost"], "reason": reason})
                del positions[symbol]
        if blocked:
            break
        equity = cash + sum(p["quantity"] * bars[(s, date)]["close"] for s, p in positions.items())
        curve.append({"date": date, "cash": cash, "equity": equity, "positions": len(positions)})
    mature = entry + policy.horizon < len(sessions)
    return {"status": "incomplete" if blocked else "complete" if mature else "open", "blocked": blocked,
            "entry_date": day, "net_return": cash / policy.capital - 1 if mature and not blocked else None,
            "mark_to_market_return": curve[-1]["equity"] / policy.capital - 1 if curve and not blocked else None,
            "positions": positions, "trades": trades, "curve": curve, "allocation": allocation,
            "entry_receipt": entry_receipt,
            "method": "Independent ten-position cohort ledger; open marks are not completed holding-period returns; cohorts are not compounded"}
