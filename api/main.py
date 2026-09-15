"""
main.py — FastAPI backend for the stock screening dashboard.

Thin query/format layer over Druid SQL. Run with:
    api/.venv/bin/uvicorn api.main:app --reload --port 8000

Endpoints:
    GET /api/health
    GET /api/market/overview
    GET /api/picks?label=&k=
    GET /api/screener?...
    GET /api/stocks/{symbol}
    GET /api/stocks/{symbol}/history?range=
    GET /api/model/comparison
    GET /api/universe
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from api import druid
from api.druid import DruidError, LATEST_SNAPSHOT, query, quote
from api import universe

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_COMPARISON_PATH = os.path.join(
    _REPO_ROOT, "ml", "evaluation", "results", "comparison.json"
)

DATASOURCES = ["screener", "price_history", "stock_scores", "market_quotes"]

SCREENER_COLUMNS = [
    "symbol", "company_name", "industry", "close", "change_percent", "rsi_14",
    "volume_ratio", "distance_from_52w_high", "distance_from_52w_low",
    "price_momentum_1m", "price_momentum_3m", "price_momentum_6m",
    "pe_ratio", "pb_ratio", "dividend_yield", "market_cap", "beta",
    "sma_50", "sma_200", "score_5d", "rank_5d", "score_21d", "rank_21d",
]

LABELS = {
    "fwd_ret_5d": ("score_5d", "rank_5d"),
    "5d": ("score_5d", "rank_5d"),
    "fwd_ret_21d": ("score_21d", "rank_21d"),
    "21d": ("score_21d", "rank_21d"),
}

RANGES = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "2y": 730}

app = FastAPI(title="Stock Screening API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _qcols(columns: list[str]) -> str:
    """Quote column identifiers, since some (e.g. `close`) are SQL reserved words."""
    return ", ".join("*" if c == "*" else f'"{c}"' for c in columns)


def _latest_screener(columns: list[str], extra_where: list[str] | None = None) -> list[dict]:
    where = [f"__time = {LATEST_SNAPSHOT}"] + (extra_where or [])
    sql = f"SELECT {_qcols(columns)} FROM screener WHERE {' AND '.join(where)}"
    return query(sql)


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if "." not in symbol and not symbol.startswith("^"):
        symbol += ".NS"
    return symbol


@app.get("/api/health")
def health():
    counts = {ds: druid.datasource_count(ds) for ds in DATASOURCES}
    reachable = any(v is not None for v in counts.values())
    return {
        "status": "ok" if reachable else "degraded",
        "druid": druid.DRUID_SQL_URL,
        "datasources": counts,
        "universe_metadata": universe.size(),
    }


@app.get("/api/market/overview")
def market_overview():
    rows = _latest_screener(
        ["symbol", "company_name", "industry", "change_percent", "close",
         "price_momentum_1m", "pe_ratio"]
    )
    if not rows:
        raise HTTPException(status_code=503, detail="No screener snapshot available yet.")

    changes = [r["change_percent"] or 0.0 for r in rows]
    advancers = sum(1 for c in changes if c > 0)
    decliners = sum(1 for c in changes if c < 0)
    unchanged = len(changes) - advancers - decliners

    gainers = sorted(rows, key=lambda r: r["change_percent"] or -1e9, reverse=True)[:10]
    losers = sorted(rows, key=lambda r: r["change_percent"] or 1e9)[:10]

    by_sector = defaultdict(list)
    for r in rows:
        by_sector[r["industry"] or "Unknown"].append(r)

    sectors = []
    for name, members in by_sector.items():
        changes_s = [m["change_percent"] or 0.0 for m in members]
        pes = [m["pe_ratio"] for m in members if m["pe_ratio"] and m["pe_ratio"] > 0]
        sectors.append({
            "industry": name,
            "count": len(members),
            "avg_change_percent": round(sum(changes_s) / len(changes_s), 3),
            "advancers": sum(1 for c in changes_s if c > 0),
            "decliners": sum(1 for c in changes_s if c < 0),
            "avg_pe": round(sum(pes) / len(pes), 2) if pes else None,
        })
    sectors.sort(key=lambda s: s["avg_change_percent"], reverse=True)

    return {
        "universe_size": len(rows),
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "avg_change_percent": round(sum(changes) / len(changes), 3),
        "gainers": gainers,
        "losers": losers,
        "sectors": sectors,
    }


@app.get("/api/picks")
def picks(label: str = Query("fwd_ret_21d"), k: int = Query(20, ge=1, le=100)):
    if label not in LABELS:
        raise HTTPException(status_code=400, detail=f"label must be one of {sorted(set(LABELS))}")
    score_col, rank_col = LABELS[label]
    rows = _latest_screener(
        ["symbol", "company_name", "industry", "close", "change_percent", "rsi_14",
         "pe_ratio", "price_momentum_1m", score_col, rank_col],
        extra_where=[f"{rank_col} <= {int(k)}"],
    )
    rows.sort(key=lambda r: r[rank_col] if r[rank_col] is not None else 1e9)
    for r in rows:
        r["label"] = "fwd_ret_5d" if score_col == "score_5d" else "fwd_ret_21d"
        r["score"] = r.pop(score_col)
        r["rank"] = r.pop(rank_col)
    return {"label": rows[0]["label"] if rows else label, "k": k, "count": len(rows), "picks": rows}


@app.get("/api/screener")
def screener(
    industry: list[str] | None = Query(None),
    min_score: float | None = Query(None),
    max_score: float | None = Query(None),
    label: str = Query("fwd_ret_21d"),
    min_pe: float | None = Query(None),
    max_pe: float | None = Query(None),
    min_rsi: float | None = Query(None),
    max_rsi: float | None = Query(None),
    min_momentum: float | None = Query(None),
    max_distance_52w_high: float | None = Query(None),
    min_volume_ratio: float | None = Query(None),
    max_pe_only_positive: bool = Query(True),
    sort: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    if label not in LABELS:
        raise HTTPException(status_code=400, detail=f"label must be one of {sorted(set(LABELS))}")
    score_col, rank_col = LABELS[label]

    where = []
    if industry:
        quoted = ", ".join(quote(i) for i in industry)
        where.append(f"industry IN ({quoted})")
    if min_score is not None:
        where.append(f"{score_col} >= {float(min_score)}")
    if max_score is not None:
        where.append(f"{score_col} <= {float(max_score)}")
    if min_pe is not None:
        where.append(f"pe_ratio >= {float(min_pe)}")
    if max_pe is not None:
        where.append(f"pe_ratio <= {float(max_pe)}")
    if max_pe_only_positive:
        where.append("pe_ratio > 0")
    if min_rsi is not None:
        where.append(f"rsi_14 >= {float(min_rsi)}")
    if max_rsi is not None:
        where.append(f"rsi_14 <= {float(max_rsi)}")
    if min_momentum is not None:
        where.append(f"price_momentum_1m >= {float(min_momentum)}")
    if max_distance_52w_high is not None:
        # distance is <= 0; e.g. -0.05 keeps names within 5% of the 52w high
        where.append(f"distance_from_52w_high >= {float(max_distance_52w_high)}")
    if min_volume_ratio is not None:
        where.append(f"volume_ratio >= {float(min_volume_ratio)}")

    rows = _latest_screener(SCREENER_COLUMNS, extra_where=where)
    for r in rows:
        r["score"] = r.pop(score_col)
        r["rank"] = r.pop(rank_col)

    reverse = order.lower() != "asc"
    sort_map = {
        "score": lambda r: r["score"] if r["score"] is not None else -1e9,
        "rank": lambda r: r["rank"] if r["rank"] is not None else 1e9,
        "change_percent": lambda r: r["change_percent"] if r["change_percent"] is not None else -1e9,
        "rsi_14": lambda r: r["rsi_14"] if r["rsi_14"] is not None else -1e9,
        "momentum": lambda r: r["price_momentum_1m"] if r["price_momentum_1m"] is not None else -1e9,
        "pe_ratio": lambda r: r["pe_ratio"] if r["pe_ratio"] is not None else 1e9,
        "market_cap": lambda r: r["market_cap"] if r["market_cap"] is not None else -1e9,
        "symbol": lambda r: r["symbol"],
    }
    key = sort_map.get(sort, sort_map["score"])
    rows.sort(key=key, reverse=reverse)

    total = len(rows)
    return {"total": total, "limit": limit, "offset": offset, "rows": rows[offset: offset + limit]}


@app.get("/api/stocks/{symbol}")
def stock_detail(symbol: str):
    sym = _normalize_symbol(symbol)
    rows = _latest_screener(["*"], extra_where=[f"symbol = {quote(sym)}"])
    if not rows:
        raise HTTPException(status_code=404, detail=f"No snapshot for symbol {sym}")
    row = rows[0]
    row["symbol"] = sym
    return row


@app.get("/api/stocks/{symbol}/history")
def stock_history(symbol: str, range: str = Query("6mo")):
    days = RANGES.get(range, 180)
    sym = _normalize_symbol(symbol)
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")
    rows = query(
        'SELECT __time, "close", sma_20, sma_50, sma_200, rsi_14, macd, macd_signal, volume '
        f"FROM price_history WHERE symbol = {quote(sym)} "
        f"AND __time >= TIMESTAMP '{start}' ORDER BY __time LIMIT 2000"
    )
    return {"symbol": sym, "range": range, "count": len(rows), "history": rows}


@app.get("/api/model/comparison")
def model_comparison():
    if not os.path.exists(_COMPARISON_PATH):
        raise HTTPException(status_code=404, detail="comparison.json not found — run evaluate.py")
    with open(_COMPARISON_PATH) as fh:
        return json.load(fh)


@app.get("/api/universe")
def universe_info():
    rows = _latest_screener(["industry", "symbol"])
    by_industry = defaultdict(int)
    for r in rows:
        by_industry[r["industry"] or "Unknown"] += 1
    return {
        "size": len(rows),
        "industries": [
            {"industry": k, "count": v}
            for k, v in sorted(by_industry.items(), key=lambda kv: -kv[1])
        ],
    }
