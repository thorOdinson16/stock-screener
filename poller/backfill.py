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
    python backfill.py --once --include-index      # also backfill the benchmark index (^NSEI)
    python backfill.py --once --index-only          # backfill only the benchmark index
    python backfill.py --once --include-index --start 2026-10-01  # bars since a date

On-demand runs keep daily history current using --watermarks (scripts/poll.sh),
which supplies a separate committed date for each symbol. Pipeline runs
re-fetch a small overlap, then deduplicate bronze by (symbol, trade_date).
Explicit --start / --period backfills remain available for historical repairs.

See docs/project-spec.md §3.2 (historical backfills) and §6.3 (derived features).
"""

import argparse
import json
import logging
import math
from pathlib import Path
import time
from datetime import date, datetime, timezone

import yfinance as yf

from kafka_producer import MarketDataProducer
from universe.universe import Stock, get_universe
from incremental import history_start, adjustment_revision

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("backfill")

DAILY_TOPIC = "market.quotes.daily"

# Benchmark indices are ingested through the same daily pipeline but excluded from
# the tradable universe (training + scoring). Used as the index benchmark by the
# evaluation layer (ml/evaluation/evaluate.py).
INDEX_SYMBOLS = (
    Stock(
        company_name="NIFTY 50 Index",
        industry="Index",
        symbol="^NSEI",
        yf_symbol="^NSEI",
        isin="",
    ),
)


def _num(value, cast=float):
    """yfinance returns float('nan') for missing cells; emit None instead so the
    value reaches SeaTunnel as JSON null rather than an invalid number."""
    try:
        if value is None:
            return None
        f = float(value)
        if not math.isfinite(f):
            return None
        return cast(f)
    except (TypeError, ValueError):
        return None


def fetch_history(stock: Stock, period: str, interval: str, retries: int = 3, start: str | None = None, actions_dir=None):
    """Returns a list of daily-bar dicts for one symbol (possibly empty). `start`
    (YYYY-MM-DD) takes precedence over `period`."""
    window = {"start": start} if start else {"period": period}
    for attempt in range(1, retries + 1):
        try:
            frame = yf.Ticker(stock.yf_symbol).history(
                interval=interval, auto_adjust=True, actions=True, repair=True, **window
            )
            if frame is None or frame.empty:
                logger.warning("No history for %s (%s).", stock.yf_symbol, window)
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
            if actions_dir is not None:
                path = Path(actions_dir) / (stock.yf_symbol.replace("/", "_") + ".json")
                path.parent.mkdir(parents=True, exist_ok=True)
                previous = json.loads(path.read_text()) if path.exists() else {"events": []}
                events = {(e["date"], e["kind"]): e for e in previous["events"]}
                for idx, row in frame.iterrows():
                    for column, kind in [("Stock Splits", "split"), ("Dividends", "dividend")]:
                        value = _num(row.get(column))
                        if value:
                            event = {"date": idx.date().isoformat(), "kind": kind, "value": value,
                                     "source": "yfinance.history", "provider_adjusted": True}
                            events[(event["date"], kind)] = event
                payload = {"symbol": stock.yf_symbol, "checked_at": datetime.now(timezone.utc).isoformat(),
                           "price_basis": "provider_adjusted", "repair_requested": True,
                           "events": list(events.values()), "fetched_start": records[0]["trade_date"],
                           "fetched_end": records[-1]["trade_date"]}
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload, allow_nan=False, indent=2))
                temporary.replace(path)
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
    parser.add_argument(
        "--start", default=None,
        help="Fetch bars from this date (YYYY-MM-DD) instead of --period",
    )
    parser.add_argument("--interval", default="1d", help="yfinance interval (default: 1d)")
    parser.add_argument("--watermarks", help="JSON symbol -> latest committed daily date")
    parser.add_argument("--overlap-days", type=int, default=5)
    parser.add_argument("--references", help="Stored overlap closes and earliest dates, exported from Iceberg")
    parser.add_argument("--actions-dir", default=str(Path(__file__).resolve().parents[1] / "ml/evaluation/results/market_data/actions"))
    parser.add_argument("--batch-size", type=int, default=50, help="Symbols per Kafka flush")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between symbols")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument(
        "--universe-limit", type=int, default=None,
        help="Limit universe to first N symbols (for testing)",
    )
    parser.add_argument("--topic", default=DAILY_TOPIC)
    parser.add_argument(
        "--include-index",
        action="store_true",
        help="Also backfill the benchmark index (^NSEI) into the same daily topic",
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="Backfill only the benchmark index symbols (skip the NIFTY 500 universe)",
    )
    args = parser.parse_args()
    if args.overlap_days < 0:
        parser.error("--overlap-days must be non-negative")
    if args.watermarks and args.start:
        parser.error("--watermarks and --start are mutually exclusive")
    watermarks = None
    references = json.loads(Path(args.references).read_text()) if args.references else {}
    if args.watermarks:
        with open(args.watermarks) as fh:
            watermarks = json.load(fh)
        if not isinstance(watermarks, dict):
            parser.error("watermarks must be a JSON object")

    if args.index_only:
        universe = list(INDEX_SYMBOLS)
        logger.info("Backfilling benchmark index only: %s", [s.yf_symbol for s in universe])
    else:
        logger.info("Loading NIFTY 500 universe...")
        universe = get_universe()
        if args.universe_limit:
            universe = universe[: args.universe_limit]
        if args.include_index:
            universe = universe + list(INDEX_SYMBOLS)
            logger.info(
                "Including benchmark index symbols: %s", [s.yf_symbol for s in INDEX_SYMBOLS]
            )
    logger.info(
        "Backfilling %d symbols: %s interval=%s",
        len(universe),
        f"start={args.start}" if args.start else f"period={args.period}",
        args.interval,
    )

    producer = MarketDataProducer(bootstrap_servers=args.bootstrap_servers)

    total_records = 0
    succeeded = 0
    pending: list[dict] = []
    started = time.time()

    for i, stock in enumerate(universe, start=1):
        start = (history_start(stock.yf_symbol, watermarks, args.overlap_days)
                 if watermarks is not None else args.start)
        logger.info("Fetching %s: %s", stock.yf_symbol,
                    f"since {start}" if start else f"bootstrap {args.period}")
        records = fetch_history(stock, args.period, args.interval, start=start, actions_dir=args.actions_dir)
        reference = references.get(stock.yf_symbol, {})
        if start and adjustment_revision(records, reference):
            logger.warning("%s: historical adjustment changed; refreshing from %s", stock.yf_symbol, reference["earliest"])
            records = fetch_history(stock, args.period, args.interval, start=reference["earliest"], actions_dir=args.actions_dir)
            if not records or min(r["trade_date"] for r in records) > reference["earliest"]:
                raise RuntimeError(f"Cannot reconcile {stock.yf_symbol} history; refusing to mix adjusted price bases")
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
