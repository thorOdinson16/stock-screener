"""As-filed XBRL features with sourced publication times and strict contexts."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import math
import xml.etree.ElementTree as ET

NS = {"x": "http://www.xbrl.org/2003/instance", "d": "http://xbrl.org/2006/xbrldi"}
FEATURES = ["verified_profit_margin", "verified_revenue_yoy", "verified_profit_yoy", "verified_filing_age"]


def parse_filing(record, path, asof):
    raw = Path(path).read_bytes()
    root = ET.fromstring(raw)
    times = [datetime.strptime(t, "%d-%b-%Y %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=5, minutes=30))) for t in record["published_times"]]
    published = max(times)
    if published.date() > date.fromisoformat(asof):
        raise ValueError("Publication is in the future")
    facts = {}
    for element in root:
        context = element.get("contextRef")
        if context:
            facts.setdefault(context, {})[element.tag.split("}")[-1]] = element
    def text(elements, name):
        e = elements.get(name)
        return e.text.strip() if e is not None and e.text else None
    expected = record["symbol"].removesuffix(".NS")
    primary_pair = next(((key, f) for key, f in facts.items() if text(f, "Symbol") == expected and text(f, "DateOfEndOfReportingPeriod")), None)
    primary_context, primary = primary_pair if primary_pair else (None, None)
    if primary is None or text(primary, "NatureOfReportStandaloneConsolidated") != "Consolidated":
        raise ValueError("Security identity or consolidated scope is unverified")
    end = date.fromisoformat(text(primary, "DateOfEndOfReportingPeriod"))
    if end >= published.date() or end != datetime.strptime(record["period_end"], "%d-%b-%Y").date():
        raise ValueError("Reporting period is inconsistent with publication evidence")
    contexts = {}
    for context in root.findall("x:context", NS):
        if context.findall(".//d:explicitMember", NS) or context.findall(".//d:typedMember", NS):
            continue  # Never mistake a segment's revenue for consolidated revenue.
        start_e, end_e = context.find("x:period/x:startDate", NS), context.find("x:period/x:endDate", NS)
        if start_e is None or end_e is None:
            continue
        start, finish = date.fromisoformat(start_e.text), date.fromisoformat(end_e.text)
        if 75 <= (finish - start).days <= 105:
            contexts.setdefault(finish, []).append(context.get("id"))
    units = {u.get("id"): [e.text for e in u.findall(".//x:measure", NS)] for u in root.findall("x:unit", NS)}
    def values(day, preferred=None):
        found = []
        for key in contexts.get(day, []):
            if preferred is not None and key != preferred:
                continue
            fs = facts.get(key, {})
            revenue = fs.get("RevenueFromOperations")
            profit = next((fs[n] for n in ("ProfitLossForPeriod", "ProfitLossForPeriodFromContinuingOperations") if n in fs), None)
            if revenue is None or profit is None:
                continue
            if any(units.get(e.get("unitRef")) != ["iso4217:INR"] for e in (revenue, profit)):
                continue
            r, p = float(revenue.text), float(profit.text)
            if math.isfinite(r) and math.isfinite(p) and r > 0 and abs(p / r) < 5:
                found.append((r, p, key))
        if len(found) != 1:
            raise ValueError("A unique whole-company quarterly INR context is required")
        return found[0]
    # The reporting-period metadata selects the primary quarter. Some filings
    # repeat its dates on a separate cumulative context; never blend the two.
    revenue, profit, context = values(end, primary_context)
    try:
        old_revenue, old_profit, _ = values(end.replace(year=end.year - 1))
    except ValueError:
        old_revenue = old_profit = None
    return {"symbol": record["symbol"], "period_end": end.isoformat(), "published_at": published.isoformat(),
            # Conservative full-day convention avoids assuming intraday availability.
            "available_date": (published.date() + timedelta(days=1)).isoformat(),
            "verified_profit_margin": profit / revenue,
            "verified_revenue_yoy": revenue / old_revenue - 1 if old_revenue else None,
            "verified_profit_yoy": (profit - old_profit) / abs(old_profit) if old_profit else None,
            "source": record["source"], "publication_source": record["index_source"],
            "document_sha256": hashlib.sha256(raw).hexdigest(), "context": context,
            "scope": "Consolidated", "currency": "INR", "age_limit_days": 365}


def audit(directory, asof):
    directory = Path(directory)
    source = directory / "downloads.json"
    records = json.loads(source.read_text())["receipts"] if source.exists() else []
    accepted, rejected = [], []
    for record in records:
        try:
            if record["status"] != "downloaded":
                raise ValueError("Original document unavailable")
            accepted.append(parse_filing(record, directory / "xml" / record["file"], asof))
        except (ValueError, KeyError, ET.ParseError, OSError) as error:
            rejected.append({"symbol": record["symbol"], "source": record["source"], "reason": str(error)})
    return {"status": "verified_subset" if accepted else "unavailable", "accepted": accepted, "rejected": rejected,
            "symbols": len({r["symbol"] for r in accepted}), "asof": asof,
            "limits": ["Only verified downloaded filings are used; corpus completeness is not assumed.",
                       "Unmapped taxonomies, ambiguous contexts and future/inconsistent dates are rejected.",
                       "Limited filing history and current industry/universe membership constrain conclusions."]}


def attach(frame, rows):
    import numpy as np
    import pandas as pd
    result = frame.copy()
    for column in FEATURES:
        result[column] = np.nan
    result["verified_source"] = None
    for symbol, filings in pd.DataFrame(rows).groupby("symbol") if rows else []:
        indices = result.index[result.symbol == symbol]
        if not len(indices):
            continue
        filings = filings.sort_values(["available_date", "published_at"]).drop_duplicates("available_date", keep="last")
        observations = result.loc[indices, ["trade_date"]].reset_index(names="_index").sort_values("trade_date")
        observations["_day"] = pd.to_datetime(observations.trade_date)
        filings["_available"] = pd.to_datetime(filings.available_date)
        joined = pd.merge_asof(observations, filings, left_on="_day", right_on="_available", direction="backward")
        age = (joined._day - pd.to_datetime(joined.period_end)).dt.days
        # A filing older than a year cannot stand in for a current statement.
        valid = joined.source.notna() & age.between(0, 365)
        chosen = joined.loc[valid]
        for column in FEATURES[:-1]:
            result.loc[chosen._index, column] = chosen[column].to_numpy()
        result.loc[chosen._index, "verified_filing_age"] = age[valid].to_numpy()
        result.loc[chosen._index, "verified_source"] = chosen.source.to_numpy()
    return result
