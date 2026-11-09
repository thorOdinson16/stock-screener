"""Retail paper allocation and a finite-capital, daily portfolio simulator.

All policies are explicit scenarios, not calibrated return forecasts. Existing
adjusted-price data supports a diagnostic simulation, never raw fill claims.
"""

from dataclasses import dataclass, asdict
from datetime import date
import math

try:
    from .corporate_actions import load_events
except ImportError:
    from corporate_actions import load_events


@dataclass
class Policy:
    capital: float = 100000
    horizon: int = 21
    max_positions: int = 10
    risk_pct: float = .5
    max_position_pct: float = 15
    max_sector_pct: float = 30
    stop_pct: float = 8
    fee_bps: float = 10
    slippage_bps: float = 10
    flat_fee: float = 0
    min_turnover: float = 10000000
    max_volatility: float = .06
    max_age_days: int = 5
    max_participation: float = .001
    top_rank: int = 20

    def validate(self):
        for key, value in asdict(self).items():
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
        if self.capital <= 0 or self.max_positions < 1 or self.horizon < 1 or self.max_age_days < 0:
            raise ValueError("Capital, positions and horizon must be positive; age cannot be negative")
        if any(int(getattr(self, key)) != getattr(self, key) for key in ("horizon", "max_positions", "max_age_days", "top_rank")):
            raise ValueError("Horizon, positions, age and rank must be integers")
        if not 0 < self.risk_pct <= 5 or not 0 < self.stop_pct < 100:
            raise ValueError("Risk must be in (0, 5] percent and stop in (0, 100) percent")
        if not 0 < self.max_position_pct <= 100 or not 0 < self.max_sector_pct <= 100:
            raise ValueError("Exposure caps must be in (0, 100] percent")
        if not 0 <= self.fee_bps < 1000 or not 0 <= self.slippage_bps < 1000 or self.flat_fee < 0:
            raise ValueError("Fee/slippage assumptions are out of range")
        if self.min_turnover < 0 or not 0 < self.max_volatility <= 1 or not 0 < self.max_participation <= 1 or self.top_rank < 1:
            raise ValueError("Invalid liquidity, volatility or rank limits")
        return self


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def eligibility(row, policy, asof, events):
    reasons = []
    price, rank = number(row.get("close")), number(row.get("rank"))
    if price is None or price <= 0:
        reasons.append("Missing positive reference price")
    if rank is None or not 0 < rank <= policy.top_rank:
        reasons.append("Outside the selected rank cutoff")
    try:
        age = (date.fromisoformat(asof) - date.fromisoformat(str(row["trade_date"])[:10])).days
        if age < 0 or age > policy.max_age_days:
            reasons.append("Price history is stale or future-dated")
    except (KeyError, ValueError, TypeError):
        reasons.append("Price date is unavailable")
    vol = number(row.get("volatility_20d"))
    avg_volume = number(row.get("volume_avg_20d"))
    if vol is None or vol < 0 or vol > policy.max_volatility:
        reasons.append("Volatility is unavailable or above the limit")
    if avg_volume is None or avg_volume <= 0 or price is None or price * avg_volume < policy.min_turnover:
        reasons.append("Insufficient verified average traded value")
    for event in events:
        # Restrict the actual strategy using only information available as of the
        # decision. Historical evaluation never filters on a future announcement.
        if row.get("symbol") in event["symbols"] and event["known_at"] <= asof:
            age = (date.fromisoformat(asof) - date.fromisoformat(event["impact_end"])).days
            if age <= 365:
                reasons.append("Corporate restructuring needs reviewed price history and fresh indicators")
    return reasons


def plan(rows, policy, asof, holdings=None, events=None, available_cash=None):
    policy.validate()
    events = load_events() if events is None else events
    holdings = holdings or []
    selected, skipped = [], []
    used, sector_values, symbols = 0., {}, set()
    for holding in holdings:
        quantity, price = number(holding.get("quantity")), number(holding.get("price"))
        if quantity is None or quantity <= 0 or quantity != int(quantity) or price is None or price <= 0 or holding["symbol"] in symbols:
            raise ValueError("Holdings require unique symbols, positive whole quantities and positive prices")
        value = quantity * price
        used += value
        industry = holding.get("industry") or "Unknown"
        sector_values[industry] = sector_values.get(industry, 0.) + value
        symbols.add(holding["symbol"])
    if used > policy.capital + 1e-6:
        raise ValueError("Existing holdings exceed the supplied total capital")
    cash = policy.capital - used if available_cash is None else available_cash
    if not math.isfinite(cash) or cash < 0 or cash > policy.capital + 1e-6:
        raise ValueError("Available cash must be between zero and total capital")
    starting_cash = cash
    fee, slip = policy.fee_bps / 10000, policy.slippage_bps / 10000
    seen = set()
    for row in sorted(rows, key=lambda r: (number(r.get("rank")) or math.inf, r["symbol"])):
        symbol = row["symbol"]
        if symbol in seen:
            continue
        seen.add(symbol)
        reasons = eligibility(row, policy, asof, events)
        if symbol in symbols:
            reasons.append("Already held")
        if len(symbols) >= policy.max_positions:
            reasons.append("Maximum position count reached")
        industry = row.get("industry") or "Unknown"
        if reasons:
            skipped.append({"symbol": symbol, "reasons": reasons})
            continue
        reference = float(row["close"])
        entry = reference * (1 + slip)
        stop = reference * (1 - policy.stop_pct / 100)
        risk_per_share = entry * (1 + fee) - stop * (1 - slip) * (1 - fee)
        budget = min(cash, policy.capital * policy.max_position_pct / 100,
                     max(0., policy.capital * policy.max_sector_pct / 100 - sector_values.get(industry, 0.)))
        quantity = min(math.floor(max(0., budget - policy.flat_fee) / (entry * (1 + fee))),
                       math.floor(max(0., policy.capital * policy.risk_pct / 100 - 2 * policy.flat_fee) / risk_per_share),
                       math.floor(float(row["volume_avg_20d"]) * policy.max_participation))
        if quantity <= 0:
            skipped.append({"symbol": symbol, "reasons": ["Cash, risk, sector or participation limits leave no whole-share allocation"]})
            continue
        outlay = quantity * entry * (1 + fee) + policy.flat_fee
        cash -= outlay
        sector_values[industry] = sector_values.get(industry, 0.) + quantity * entry
        symbols.add(symbol)
        context = []
        if number(row.get("price_momentum_1m")) is not None:
            context.append(f"One-month momentum {float(row['price_momentum_1m']) * 100:.1f}%")
        if number(row.get("rsi_14")) is not None:
            context.append(f"RSI {float(row['rsi_14']):.1f}")
        selected.append({"symbol": symbol, "company_name": row.get("company_name"), "industry": industry,
                         "rank": row["rank"], "score": row.get("score"), "reference_price": reference,
                         "assumed_entry": entry, "quantity": quantity, "cash_required": outlay,
                         "stop_reference": stop, "planned_risk": quantity * risk_per_share + 2 * policy.flat_fee,
                         "weight_pct": quantity * entry / policy.capital * 100, "context": context})
    return {"asof": asof, "policy": asdict(policy), "candidates": selected, "skipped": skipped,
            "cash_before": starting_cash, "cash_remaining": cash,
            "status": "setups_available" if selected else "no_suitable_setup",
            "probability": None, "expected_return": None,
            "method": "Reference-price paper allocation; re-size at actual entry. Stops may fill beyond their reference on gaps. Scores are not calibrated probabilities."}


def simulate(signals, bars, sessions, policy, events=None, start=None, end=None):
    """Daily capital ledger, integer shares, overlapping positions and cash.

    Signals at date t enter at the next calendar-session close. Stops use next
    day's open/low (gap-through fills at open); time exits at the horizon close.
    An unresolved corporate action in a held position stops the experiment with
    an explicit incomplete result; it never sells the asset at a fictitious loss.
    """
    policy.validate()
    events = load_events() if events is None else events
    dates = sorted(set(sessions))
    cash, positions, trades, curve, skips = policy.capital, {}, [], [], {}
    blocked = None
    fee, slip = policy.fee_bps / 10000, policy.slippage_bps / 10000
    for index, day in enumerate(dates):
        if start and day < start or end and day > end:
            continue
        # Missing current prices are not silently filled forward as if tradable.
        for symbol, position in list(positions.items()):
            relevant = [e for e in events if symbol in e["symbols"] and position["entry_date"] < e["impact_start"] <= day]
            if relevant:
                blocked = {"date": day, "symbol": symbol, "reason": "Unvalued corporate-action entitlement", "events": [e["id"] for e in relevant]}
                break
            bar = bars.get((symbol, day))
            if not bar or not number(bar.get("close")):
                blocked = {"date": day, "symbol": symbol, "reason": "Missing held-stock price"}
                break
            close = float(bar["close"])
            reason, exit_price = None, close
            low, opening = number(bar.get("low")), number(bar.get("open"))
            if day > position["entry_date"] and (low is None or opening is None or low <= 0 or opening <= 0):
                blocked = {"date": day, "symbol": symbol, "reason": "Opening/low price is missing; stop execution cannot be evaluated"}
                break
            if day > position["entry_date"] and low is not None and low <= position["stop"]:
                if opening is None:
                    blocked = {"date": day, "symbol": symbol, "reason": "Stop touched but opening price is missing"}
                    break
                exit_price, reason = min(opening, position["stop"]), "stop"
            elif index >= position["exit_index"]:
                reason = "time"
            if reason:
                fill = exit_price * (1 - slip)
                proceeds = position["quantity"] * fill * (1 - fee) - policy.flat_fee
                cash += proceeds
                trades.append({**position, "symbol": symbol, "exit_date": day, "exit_price": fill,
                               "exit_reason": reason, "proceeds": proceeds, "pnl": proceeds - position["cost"]})
                positions.pop(symbol)
        if blocked:
            break
        mark = cash + sum(p["quantity"] * float(bars[(s, day)]["close"]) for s, p in positions.items())
        previous = dates[index - 1] if index else None
        rows = signals.get(previous, []) if previous else []
        known_rows = []
        for row in rows:
            if row["symbol"] in positions:
                continue
            # Eligibility and liquidity use signal-day information; actual entry
            # prices may change sizing, but never which rank was known yesterday.
            reasons = eligibility(row, policy, previous, events)
            if reasons:
                for reason in reasons:
                    skips[reason] = skips.get(reason, 0) + 1
                continue
            bar = bars.get((row["symbol"], day))
            if not bar or not number(bar.get("close")):
                skips["Missing entry price"] = skips.get("Missing entry price", 0) + 1
                continue
            known_rows.append({**row, "close": float(bar["close"]), "trade_date": day})
        # New exposures are allocated using current prices with previously known
        # ranks. Future action announcements do not change historical eligibility.
        current_holdings = [{"symbol": s, "quantity": p["quantity"], "price": float(bars[(s, day)]["close"]), "industry": p["industry"]} for s, p in positions.items()]
        day_policy = Policy(**{**asdict(policy), "capital": mark})
        allocation = plan(known_rows, day_policy, day, current_holdings, events=[], available_cash=cash)
        for order in allocation["candidates"]:
            # If the holding cannot mature before the test cutoff, keep cash.
            if index + policy.horizon >= len(dates) or end and dates[index + policy.horizon] > end:
                continue
            symbol = order["symbol"]
            cash -= order["cash_required"]
            positions[symbol] = {"entry_date": day, "entry_price": order["assumed_entry"], "quantity": order["quantity"],
                                 "cost": order["cash_required"], "stop": order["stop_reference"], "industry": order["industry"],
                                 "exit_index": index + policy.horizon}
        equity = cash + sum(p["quantity"] * float(bars[(s, day)]["close"]) for s, p in positions.items())
        curve.append({"date": day, "equity": equity, "cash": cash, "positions": len(positions)})
        if cash < -1e-6:
            raise AssertionError("Simulator overspent cash")
    peak, max_dd = policy.capital, 0.
    for point in curve:
        peak = max(peak, point["equity"])
        max_dd = min(max_dd, point["equity"] / peak - 1)
    return {"status": "incomplete" if blocked else "complete", "blocked": blocked,
            "policy": asdict(policy), "trades": trades, "curve": curve, "open_positions": positions,
            "net_return": None if blocked or not curve else curve[-1]["equity"] / policy.capital - 1,
            "max_drawdown": None if blocked else max_dd,
            "trade_win_rate": sum(t["pnl"] > 0 for t in trades) / len(trades) if trades else None,
            "skipped": skips, "assumptions": "Adjusted-price diagnostic; next-session-close fills, configurable costs/slippage, no leverage, estimated stop fills; exchange limit/auction fills and investor taxes are not modeled."}
