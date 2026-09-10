"""
poller.py — main entrypoint for the NIFTY 500 market data poller.

Usage:
    python poller.py --once                    # single poll cycle (quotes + fundamentals), then exit
    python poller.py --once --quotes-only        # single quotes-only cycle
    python poller.py                              # continuous loop (default cadence below)
    python poller.py --interval 300 --fundamentals-interval 86400

Publishes to:
    market.quotes         (every --interval seconds)
    market.fundamentals    (every --fundamentals-interval seconds)

See docs/project-spec.md §7 for design rationale.
"""

import argparse
import logging
import sys
import time

from kafka_producer import MarketDataProducer
from market_data import fetch_quotes_batch, fetch_fundamentals_batch, to_dict
from universe.universe import get_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("poller")

DEFAULT_QUOTE_INTERVAL_SECONDS = 5 * 60         # 5 minutes
DEFAULT_FUNDAMENTALS_INTERVAL_SECONDS = 24 * 3600  # daily

QUOTES_TOPIC = "market.quotes"
FUNDAMENTALS_TOPIC = "market.fundamentals"


def run_quotes_cycle(producer: MarketDataProducer, universe, batch_size: int) -> int:
    snapshots = fetch_quotes_batch(universe, batch_size=batch_size)
    records = [to_dict(s) for s in snapshots]
    producer.publish_many(QUOTES_TOPIC, records)
    producer.flush()
    return len(records)


def run_fundamentals_cycle(producer: MarketDataProducer, universe, batch_size: int) -> int:
    snapshots = fetch_fundamentals_batch(universe, batch_size=batch_size)
    records = [to_dict(s) for s in snapshots]
    producer.publish_many(FUNDAMENTALS_TOPIC, records)
    producer.flush()
    return len(records)


def main():
    parser = argparse.ArgumentParser(description="NIFTY 500 market data poller")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    parser.add_argument("--quotes-only", action="store_true", help="Skip fundamentals in --once mode")
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_QUOTE_INTERVAL_SECONDS,
        help=f"Quote polling interval in seconds (default: {DEFAULT_QUOTE_INTERVAL_SECONDS})",
    )
    parser.add_argument(
        "--fundamentals-interval", type=int, default=DEFAULT_FUNDAMENTALS_INTERVAL_SECONDS,
        help=f"Fundamentals polling interval in seconds (default: {DEFAULT_FUNDAMENTALS_INTERVAL_SECONDS})",
    )
    parser.add_argument("--batch-size", type=int, default=50, help="Symbols per yfinance batch request")
    parser.add_argument("--bootstrap-servers", default="localhost:9092", help="Kafka bootstrap servers")
    parser.add_argument(
        "--universe-limit", type=int, default=None,
        help="Limit universe to first N symbols (for testing without hitting the full 500)",
    )
    args = parser.parse_args()

    logger.info("Loading NIFTY 500 universe...")
    universe = get_universe()
    if args.universe_limit:
        universe = universe[: args.universe_limit]
        logger.info("Universe limited to %d symbols for this run.", len(universe))

    producer = MarketDataProducer(bootstrap_servers=args.bootstrap_servers)

    if args.once:
        logger.info("Running single poll cycle (--once)...")
        n_quotes = run_quotes_cycle(producer, universe, args.batch_size)
        logger.info("Published %d quote records.", n_quotes)
        if not args.quotes_only:
            n_fund = run_fundamentals_cycle(producer, universe, args.batch_size)
            logger.info("Published %d fundamentals records.", n_fund)
        logger.info("Single cycle complete. Exiting.")
        return

    logger.info(
        "Starting continuous poll loop: quotes every %ds, fundamentals every %ds.",
        args.interval,
        args.fundamentals_interval,
    )
    last_fundamentals_run = 0.0

    try:
        while True:
            cycle_start = time.time()

            n_quotes = run_quotes_cycle(producer, universe, args.batch_size)
            logger.info("Published %d quote records.", n_quotes)

            if cycle_start - last_fundamentals_run >= args.fundamentals_interval:
                n_fund = run_fundamentals_cycle(producer, universe, args.batch_size)
                logger.info("Published %d fundamentals records.", n_fund)
                last_fundamentals_run = cycle_start

            elapsed = time.time() - cycle_start
            sleep_for = max(0, args.interval - elapsed)
            logger.info("Cycle took %.1fs. Sleeping %.1fs until next quote poll.", elapsed, sleep_for)
            time.sleep(sleep_for)

    except KeyboardInterrupt:
        logger.info("Interrupted. Shutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()