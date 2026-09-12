"""
market_data.py — fetches quote and fundamentals snapshots via yfinance, in batches.

yfinance soft-throttles aggressive request patterns. We batch symbols per request
(yf.Tickers accepts a space-separated string) and add a small delay between batches
to stay well under any informal rate limit, rather than hitting 500 individual
single-symbol requests.
"""

import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import yfinance as yf

from universe.universe import Stock

logger = logging.getLogger(__name__)

IST = timezone.utc  # placeholder; see note in poller.py re: timezone handling


@dataclass
class QuoteSnapshot:
    symbol: str
    timestamp: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    previous_close: float | None
    change_percent: float | None
    market_cap: float | None
    sector: str | None
    industry: str | None
    exchange: str | None


@dataclass
class FundamentalsSnapshot:
    symbol: str
    timestamp: str
    pe_ratio: float | None
    pb_ratio: float | None
    eps: float | None
    dividend_yield: float | None
    market_cap: float | None
    beta: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    return_on_equity: float | None
    debt_to_equity: float | None
    revenue_growth: float | None
    earnings_growth: float | None


def _safe_get(info: dict, key: str):
    """yfinance's .info dict has inconsistent key presence across symbols — missing
    fields come back as None rather than raising, so downstream validation (SeaTunnel)
    can flag/route them rather than the poller crashing on one bad symbol."""
    return info.get(key)


def fetch_quotes_batch(stocks: list[Stock], batch_size: int = 50, delay_seconds: float = 1.0) -> list[QuoteSnapshot]:
    """
    Fetches current quote data for a list of stocks, in batches.

    A failure on one batch does not abort the whole run — it's logged and skipped,
    so a transient issue with 50 symbols doesn't lose the other 450.
    """
    snapshots: list[QuoteSnapshot] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    for i in range(0, len(stocks), batch_size):
        batch = stocks[i : i + batch_size]
        symbols_str = " ".join(s.yf_symbol for s in batch)
        logger.info("Fetching quotes batch %d-%d of %d...", i, i + len(batch), len(stocks))

        try:
            tickers = yf.Tickers(symbols_str)
            for stock in batch:
                try:
                    t = tickers.tickers[stock.yf_symbol]
                    info = t.fast_info  # fast_info is cheaper than .info for price data
                    snapshots.append(
                        QuoteSnapshot(
                            symbol=stock.yf_symbol,
                            timestamp=now,
                            open=getattr(info, "open", None),
                            high=getattr(info, "day_high", None),
                            low=getattr(info, "day_low", None),
                            close=getattr(info, "last_price", None),
                            volume=getattr(info, "last_volume", None),
                            previous_close=getattr(info, "previous_close", None),
                            change_percent=_compute_change_pct(info),
                            market_cap=getattr(info, "market_cap", None),
                            sector=None,       # not in fast_info — comes from fundamentals poll
                            industry=stock.industry,
                            exchange=getattr(info, "exchange", None),
                        )
                    )
                except Exception as e:
                    logger.warning("Failed to fetch quote for %s: %s", stock.yf_symbol, e)
                    continue
        except Exception as e:
            logger.error("Batch fetch failed for symbols %d-%d: %s", i, i + len(batch), e)
            continue

        if i + batch_size < len(stocks):
            time.sleep(delay_seconds)

    logger.info("Fetched %d/%d quote snapshots.", len(snapshots), len(stocks))
    return snapshots


def _compute_change_pct(fast_info) -> float | None:
    try:
        last = getattr(fast_info, "last_price", None)
        prev = getattr(fast_info, "previous_close", None)
        if last is None or prev in (None, 0):
            return None
        return round((last - prev) / prev * 100, 4)
    except (TypeError, ZeroDivisionError):
        return None


def fetch_fundamentals_batch(
    stocks: list[Stock], batch_size: int = 50, delay_seconds: float = 2.0
) -> list[FundamentalsSnapshot]:
    """
    Fetches fundamentals data for a list of stocks, in batches.

    Uses the slower .info property (fundamentals aren't in fast_info) — hence the
    longer inter-batch delay and the daily-only cadence this is meant to run at.
    """
    snapshots: list[FundamentalsSnapshot] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    for i in range(0, len(stocks), batch_size):
        batch = stocks[i : i + batch_size]
        logger.info("Fetching fundamentals batch %d-%d of %d...", i, i + len(batch), len(stocks))

        for stock in batch:
            try:
                t = yf.Ticker(stock.yf_symbol)
                info = t.info
                snapshots.append(
                    FundamentalsSnapshot(
                        symbol=stock.yf_symbol,
                        timestamp=now,
                        pe_ratio=_safe_get(info, "trailingPE"),
                        pb_ratio=_safe_get(info, "priceToBook"),
                        eps=_safe_get(info, "trailingEps"),
                        dividend_yield=_safe_get(info, "dividendYield"),
                        market_cap=_safe_get(info, "marketCap"),
                        beta=_safe_get(info, "beta"),
                        fifty_two_week_high=_safe_get(info, "fiftyTwoWeekHigh"),
                        fifty_two_week_low=_safe_get(info, "fiftyTwoWeekLow"),
                        return_on_equity=_safe_get(info, "returnOnEquity"),
                        debt_to_equity=_safe_get(info, "debtToEquity"),
                        revenue_growth=_safe_get(info, "revenueGrowth"),
                        earnings_growth=_safe_get(info, "earningsGrowth"),
                    )
                )
            except Exception as e:
                logger.warning("Failed to fetch fundamentals for %s: %s", stock.yf_symbol, e)
                continue

        if i + batch_size < len(stocks):
            time.sleep(delay_seconds)

    logger.info("Fetched %d/%d fundamentals snapshots.", len(snapshots), len(stocks))
    return snapshots


def to_dict(snapshot) -> dict:
    return asdict(snapshot)