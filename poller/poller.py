"""
poller.py — NIFTY 500 market data poller (single on-demand cycle).

The platform is on-demand, so the poller always runs exactly one cycle and
exits; there is no continuous loop. The pipeline (`scripts/poll.sh`) invokes it
once per run.

Usage:
    python poller.py --once                 # quotes + fundamentals
    python poller.py --once --quotes-only   # quotes only
    python poller.py --universe-limit 50    # single cycle, capped universe

Publishes to:
    market.quotes          (quotes for the run)
    market.fundamentals    (fundamentals, unless --quotes-only)

See docs/project-spec.md for design rationale.
"""

import argparse
import logging

from kafka_producer import MarketDataProducer
from market_data import fetch_quotes_batch, fetch_fundamentals_batch, to_dict
from universe.universe import get_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("poller")

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
    parser = argparse.ArgumentParser(description="NIFTY 500 market data poller (one cycle)")
    parser.add_argument(
        "--once", action="store_true",
        help="Deprecated no-op: the poller always runs a single cycle and exits",
    )
    parser.add_argument(
        "--quotes-only", action="store_true", help="Skip fundamentals"
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

    n_quotes = run_quotes_cycle(producer, universe, args.batch_size)
    logger.info("Published %d quote records.", n_quotes)
    if not args.quotes_only:
        n_fund = run_fundamentals_cycle(producer, universe, args.batch_size)
        logger.info("Published %d fundamentals records.", n_fund)
    logger.info("Poll cycle complete. Exiting.")


if __name__ == "__main__":
    main()
