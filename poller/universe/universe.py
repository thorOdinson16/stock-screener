"""
universe.py — fetches and caches the NIFTY 500 constituent list from NSE India.

NSE serves this as a public CSV, but blocks requests without a browser-like User-Agent
(returns 403 otherwise).

The cache is only (re)fetched when it is MISSING or when you ask explicitly:

    python poller/universe/universe.py --refresh

It is deliberately not refreshed by age. Frozen research manifests record the sha256
of universe_cache.json, so a silent weekly rewrite marked every research panel stale
and made the frozen paper-trial stages fail ("Feature source changed"). The index only
rebalances in March and September; refresh by hand then, and expect the research
evidence tied to the old file to read as stale (see docs/known-limitations.md).
"""

import csv
import io
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

NIFTY500_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"

# NSE blocks requests without a browser-like User-Agent (returns 403 otherwise).
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
}

CACHE_PATH = Path(__file__).parent / "universe_cache.json"
# Warn (never refetch) when the cache is older than this: the index rebalances twice a year.
STALE_WARNING_SECONDS = 200 * 24 * 3600


@dataclass(frozen=True)
class Stock:
    company_name: str
    industry: str
    symbol: str          # raw NSE symbol, e.g. "RELIANCE"
    yf_symbol: str        # yfinance-compatible symbol, e.g. "RELIANCE.NS"
    isin: str


def _to_yf_symbol(nse_symbol: str) -> str:
    """NSE equities map to yfinance/Yahoo Finance tickers via the .NS suffix."""
    return f"{nse_symbol.strip()}.NS"


def _fetch_from_nse(timeout: int = 15) -> list[Stock]:
    logger.info("Fetching NIFTY 500 constituent list from NSE...")
    resp = requests.get(NIFTY500_CSV_URL, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()

    reader = csv.DictReader(io.StringIO(resp.text))
    stocks = []
    for row in reader:
        symbol = row["Symbol"].strip()
        stocks.append(
            Stock(
                company_name=row["Company Name"].strip(),
                industry=row["Industry"].strip(),
                symbol=symbol,
                yf_symbol=_to_yf_symbol(symbol),
                isin=row["ISIN Code"].strip(),
            )
        )

    if len(stocks) < 400:
        # NSE occasionally serves a truncated/error page with HTTP 200; guard against
        # silently proceeding with a broken universe.
        raise ValueError(
            f"Expected ~500 NIFTY 500 constituents, parsed only {len(stocks)}. "
            "NSE response may be malformed or blocked — inspect manually."
        )

    logger.info("Fetched %d constituents from NSE.", len(stocks))
    return stocks


def _write_cache(stocks: list[Stock]) -> None:
    payload = {
        "fetched_at": time.time(),
        "stocks": [s.__dict__ for s in stocks],
    }
    CACHE_PATH.write_text(json.dumps(payload, indent=2))


def _read_cache() -> list[Stock] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    age = time.time() - payload.get("fetched_at", 0)
    if age > STALE_WARNING_SECONDS:
        logger.warning(
            "Universe cache is %.0f days old; new index constituents are not being "
            "picked up. Refresh deliberately with: python poller/universe/universe.py --refresh",
            age / 86400,
        )

    return [Stock(**s) for s in payload["stocks"]]


def get_universe(force_refresh: bool = False) -> list[Stock]:
    """
    Returns the NIFTY 500 constituent list, using a local cache when fresh.

    Falls back to a stale cache (if one exists) rather than failing outright when NSE
    is unreachable — better to run against slightly stale constituents than not run at all.
    """
    if not force_refresh:
        cached = _read_cache()
        if cached is not None:
            logger.info("Using cached universe (%d stocks).", len(cached))
            return cached

    try:
        stocks = _fetch_from_nse()
        _write_cache(stocks)
        return stocks
    except (requests.RequestException, ValueError) as e:
        logger.warning("Failed to fetch fresh universe from NSE: %s", e)
        if CACHE_PATH.exists():
            logger.warning("Falling back to stale cache.")
            payload = json.loads(CACHE_PATH.read_text())
            return [Stock(**s) for s in payload["stocks"]]
        raise RuntimeError(
            "Could not fetch NIFTY 500 universe from NSE and no cache exists. "
            "Check network connectivity to nsearchives.nseindia.com."
        ) from e


def refresh() -> int:
    """Fetch from NSE, overwrite the cache and report what changed."""
    before = {s.yf_symbol for s in _read_cache() or []}
    after = {s.yf_symbol for s in get_universe(force_refresh=True)}
    print(f"Universe refreshed: {len(after)} stocks "
          f"(+{len(after - before)} added, -{len(before - after)} removed).")
    for label, symbols in (("added", after - before), ("removed", before - after)):
        if symbols:
            print(f"  {label}: {', '.join(sorted(symbols))}")
    print(
        "\nNOTE: universe_cache.json is hash-pinned by frozen research manifests. Run\n"
        "  python scripts/pinned_sources.py --verify\n"
        "to see which evidence now reads as stale; the frozen paper-trial stages will\n"
        "refuse to run until a new versioned trial is frozen on the new universe."
    )
    return 0


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Show or deliberately refresh the NIFTY 500 universe cache")
    parser.add_argument("--refresh", action="store_true", help="re-download from NSE and overwrite the cache")
    if parser.parse_args().refresh:
        sys.exit(refresh())
    universe = get_universe()
    print(f"Universe size: {len(universe)}")
    print("First 5:", [s.yf_symbol for s in universe[:5]])