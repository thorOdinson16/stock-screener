"""
backfill.py — one-off historical daily OHLCV backfill for the NIFTY 500 universe.

Fetches ~2 years of daily bars per symbol via yfinance and publishes them to the
Kafka topic `market.quotes.daily`, which SeaTunnel ingests into
`bronze.quotes_daily`. This is what makes the technical indicators
(sma_200, momentum_3m/6m, 52-week high/low) meaningful, since the live poller
only ever captures the current snapshot.

Usage:
    python backfill.py --once                     # full universe, 2y of daily bars
    python backfill.py --once --universe-limit 20 # quick test
    python backfill.py --once --period 1y

See docs/project-spec.md §3.2 (historical backfills) and §6.3 (derived features).
"""

import argparse
import logging
import time
from datetime import date, datetime, timezone

import yfinance as yf

from kafka_producer import MarketDataProducer
from universe.universe import Stock, get_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("backfill")

DAILY_TOPIC = "market.quotes.daily"


def _num(value, cast=float):
    """yfinance returns float('nan') for missing cells; emit None instead so the
    value reaches SeaTunnel as JSON null rather than an invalid number."""
    try:
        if value is None:
            return None
        f = float(value)
        if f != f:  # NaN
            return None
        return cast(f)
    except (TypeError, ValueError):
        return None


def fetch_history(stock: Stock, period: str, interval: str, retries: int = 3):
    """Returns a list of daily-bar dicts for one symbol (possibly empty)."""
    for attempt in range(1, retries + 1):
        try:
            frame = yf.Ticker(stock.yf_symbol).history(
                period=period, interval=interval, auto_adjust=True
            )
            if frame is None or frame.empty:
                logger.warning("No history for %s (period=%s).", stock.yf_symbol, period)
                return []

            records = []
            for idx, row in frame.iterrows():
                bar_date = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                records.append(
                    {
                        "symbol": stock.yf_symbol,
                        "trade_date": bar_date.isoformat(),
                        "open": _num(row.get("Open")),
                        "high": _num(row.get("High")),
                        "low": _num(row.get("Low")),
                        "close": _num(row.get("Close")),
                        "volume": _num(row.get("Volume"), cast=int),
                    }
                )
            return records
        except Exception as e:  # noqa: BLE001 — one bad symbol must not abort the backfill
            if attempt == retries:
                logger.error("Failed to fetch history for %s: %s", stock.yf_symbol, e)
                return []
            backoff = 2**attempt
            logger.warning(
                "History fetch failed for %s (attempt %d/%d): %s — retrying in %ds",
                stock.yf_symbol,
                attempt,
                retries,
                e,
                backoff,
            )
            time.sleep(backoff)
    return []


def main():
    parser = argparse.ArgumentParser(description="Historical daily OHLCV backfill")
    parser.add_argument("--once", action="store_true", help="Run the backfill and exit")
    parser.add_argument("--period", default="2y", help="yfinance period (default: 2y)")
    parser.add_argument("--interval", default="1d", help="yfinance interval (default: 1d)")
    parser.add_argument("--batch-size", type=int, default=50, help="Symbols per Kafka flush")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between symbols")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument(
        "--universe-limit", type=int, default=None,
        help="Limit universe to first N symbols (for testing)",
    )
    parser.add_argument("--topic", default=DAILY_TOPIC)
    args = parser.parse_args()

    logger.info("Loading NIFTY 500 universe...")
    universe = get_universe()
    if args.universe_limit:
        universe = universe[: args.universe_limit]
    logger.info("Backfilling %d symbols: period=%s interval=%s", len(universe), args.period, args.interval)

    producer = MarketDataProducer(bootstrap_servers=args.bootstrap_servers)

    total_records = 0
    succeeded = 0
    pending: list[dict] = []
    started = time.time()

    for i, stock in enumerate(universe, start=1):
        records = fetch_history(stock, args.period, args.interval)
        if records:
            pending.extend(records)
            succeeded += 1
        else:
            logger.warning("Skipping %s — no bars.", stock.yf_symbol)

        if i % args.batch_size == 0 or i == len(universe):
            if pending:
                producer.publish_many(args.topic, pending)
                producer.flush()
                total_records += len(pending)
                pending.clear()
            logger.info(
                "Progress: %d/%d symbols, %d bars published so far (%.0fs elapsed)",
                i,
                len(universe),
                total_records,
                time.time() - started,
            )

        time.sleep(args.delay)

    elapsed = time.time() - started
    logger.info(
        "Backfill complete: %d/%d symbols, %d bars published to %s in %.0fs.",
        succeeded,
        len(universe),
        total_records,
        args.topic,
        elapsed,
    )


if __name__ == "__main__":
    main()
