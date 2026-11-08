"""Corporate-action accounting and quality gates, shared by research and serving.

Existing Yahoo OHLC is provider-adjusted: never apply dividends/splits to it twice.
Complex unresolved actions produce unknown returns, never estimated losses.
The unadjusted-price engine supports sourced entitlement terms separately.
"""

from datetime import date
import hashlib
import json
import math
from pathlib import Path

DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / "config" / "corporate_actions.json"


def load_events(path=DEFAULT_LEDGER):
    ledger = json.loads(Path(path).read_text())
    if ledger.get("version") != 1:
        raise ValueError("Unsupported corporate-action ledger version")
    events = ledger["events"]
    seen = set()
    for event in events:
        if event["id"] in seen or not event.get("source") or not event.get("symbols"):
            raise ValueError("Corporate actions require unique ids, symbols and a source")
        seen.add(event["id"])
        for key in ("impact_start", "impact_end", "known_at"):
            date.fromisoformat(event[key])
        if event["impact_end"] < event["impact_start"]:
            raise ValueError("Corporate-action dates are reversed")
    return events


def fingerprint(events):
    return hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()


def crossing_events(symbol, entry, exit_date, events):
    return [e for e in events if symbol in e["symbols"] and
            entry <= e["impact_end"] and exit_date >= e["impact_start"]]


def adjusted_return(symbol, entry, exit_date, prices, events):
    # The ledger never asserts an adjustment merely because a notice was read.
    # All complex-action windows remain unvalued in the provider-adjusted series.
    blocked = crossing_events(symbol, entry, exit_date, events)
    if blocked:
        return None, [e["id"] for e in blocked]
    first, last = prices.get((symbol, entry)), prices.get((symbol, exit_date))
    if any(v is None or not math.isfinite(v) or v <= 0 for v in (first, last)):
        return None, ["missing_price"]
    return last / first - 1, []


def unadjusted_wealth(symbol, entry, exit_date, prices, actions):
    """Value an actual holding with splits, cash dividends and distributed shares.

    `prices` MUST be contemporaneous, unadjusted traded prices. Actions require
    verified source/terms; absent child prices leave wealth unknown. This engine
    does not pretend Yahoo's adjusted close is a raw traded price.
    """
    first = prices.get((symbol, entry))
    if first is None or not math.isfinite(first) or first <= 0:
        return {"return": None, "reason": "missing_entry_price"}
    holdings, cash = {symbol: 1.}, 0.
    for event in sorted(actions, key=lambda e: (e["date"], e.get("id", ""))):
        if not entry < event["date"] <= exit_date or event["symbol"] not in holdings:
            continue
        if not event.get("verified") or not event.get("source"):
            return {"return": None, "reason": "unverified_terms", "event": event.get("id")}
        shares = holdings[event["symbol"]]
        if event["kind"] == "split":
            ratio = event["ratio"]
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError("Split ratio must be positive")
            holdings[event["symbol"]] *= ratio
        elif event["kind"] == "dividend":
            amount = event["cash_per_share"]
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("Dividend must be non-negative")
            cash += shares * amount
        elif event["kind"] in ("demerger", "merger"):
            if not event.get("entitlements"):
                return {"return": None, "reason": "missing_entitlement_terms"}
            for child in event["entitlements"]:
                ratio = child["ratio"]
                if not math.isfinite(ratio) or ratio <= 0:
                    raise ValueError("Entitlement ratio must be positive")
                holdings[child["symbol"]] = holdings.get(child["symbol"], 0.) + shares * ratio
            if event["kind"] == "merger":
                holdings.pop(event["symbol"])
        else:
            return {"return": None, "reason": "unsupported_action"}
    missing = [s for s in holdings if prices.get((s, exit_date)) is None or
               not math.isfinite(prices[(s, exit_date)]) or prices[(s, exit_date)] < 0]
    if missing:
        return {"return": None, "reason": "unpriced_entitlements", "symbols": missing}
    wealth = cash + sum(shares * prices[(s, exit_date)] for s, shares in holdings.items())
    return {"return": wealth / first - 1, "cash": cash, "holdings": holdings, "exit_wealth": wealth}


def reset_features(group, events, compute):
    """Start a new indicator history after structural actions; keep every raw bar."""
    import numpy as np
    import pandas as pd
    frame = group.sort_values("trade_date").copy()
    symbol = str(frame["symbol"].iloc[0])
    dates = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    segments = pd.Series(0, index=frame.index)
    impacted = pd.Series(False, index=frame.index)
    for event in events:
        if symbol in event["symbols"]:
            segments += (dates >= event["impact_start"]).astype(int) + (dates > event["impact_end"]).astype(int)
            impacted |= dates.between(event["impact_start"], event["impact_end"])
    output = []
    frame["_segment"] = segments
    frame["_impacted"] = impacted
    for _, part in frame.groupby("_segment", sort=True):
        result = compute(part.drop(columns=["_segment", "_impacted"]))
        if part["_impacted"].any():
            for col in result.columns.difference(["symbol", "trade_date", "close", "volume"]):
                result[col] = np.nan
        output.append(result)
    return pd.concat(output, ignore_index=True)


def mask_labels(group, events, horizons, compute):
    """Never teach the model that an unresolved entitlement is a realized loss."""
    import pandas as pd
    frame = compute(group)
    dates = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    symbol = str(frame["symbol"].iloc[0])
    for horizon in horizons:
        exits = dates.shift(-horizon)
        for event in events:
            if symbol in event["symbols"]:
                crosses = (dates <= event["impact_end"]) & (exits >= event["impact_start"])
                frame.loc[crosses, f"fwd_ret_{horizon}d"] = float("nan")
    return frame
