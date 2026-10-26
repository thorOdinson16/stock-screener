"""
financials.py — annual financial statements for the NIFTY 500 universe.

The live fundamentals poll (`poller.py`) captures only today's `.info` snapshot,
so it cannot give the model a fundamentals *history*. yfinance does expose each
company's last ~4-5 annual income statements and balance sheets; this script
fetches them and publishes one record per (symbol, fiscal year end) to the Kafka
topic `market.financials`, which SeaTunnel ingests into
`bronze.financial_statements`. The model turns them into point-in-time features
(ml/feature_engineering/asof.py): a statement only becomes usable a fixed lag
after its fiscal year end, so training never sees results before they were
published.

Re-runnable: silver keeps the latest ingested copy of each (symbol, period_end).

Usage:
    python financials.py --once                     # full universe
    python financials.py --once --universe-limit 20 # quick test
"""

import argparse
import logging
import math
import time
from datetime import datetime, timezone

import yfinance as yf

from kafka_producer import MarketDataProducer
from universe.universe import Stock, get_universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("financials")

FINANCIALS_TOPIC = "market.financials"

# yfinance row label -> record field. The first label present wins.
INCOME_ROWS = {
    "total_revenue": ("Total Revenue", "Operating Revenue"),
    "net_income": ("Net Income Common Stockholders", "Net Income"),
    "diluted_eps": ("Diluted EPS", "Basic EPS"),
}
BALANCE_ROWS = {
    "stockholders_equity": ("Stockholders Equity", "Common Stock Equity"),
    "total_debt": ("Total Debt",),
    "shares_outstanding": ("Ordinary Shares Number", "Share Issued"),
}


def _value(frame, labels, column):
    for label in labels:
        if label in frame.index:
            value = frame.at[label, column]
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isnan(value):
                return value
    return None


def fetch_statements(stock: Stock, retries: int = 3) -> list[dict]:
    """One record per annual period present in the income statement."""
    for attempt in range(1, retries + 1):
        try:
            ticker = yf.Ticker(stock.yf_symbol)
            income = ticker.income_stmt
            balance = ticker.balance_sheet
            break
        except Exception as e:  # noqa: BLE001 — network / parsing errors from yfinance
            logger.warning("%s: attempt %d failed: %s", stock.yf_symbol, attempt, e)
            time.sleep(2 * attempt)
    else:
        return []

    if income is None or income.empty:
        return []
    fetched_at = datetime.now(timezone.utc).isoformat()
    records = []
    for column in income.columns:
        record = {
            "symbol": stock.yf_symbol,
            "period_end": column.date().isoformat(),
            "fetched_at": fetched_at,
        }
        for field, labels in INCOME_ROWS.items():
            record[field] = _value(income, labels, column)
        for field, labels in BALANCE_ROWS.items():
            record[field] = (
                _value(balance, labels, column)
                if balance is not None and column in balance.columns
                else None
            )
        records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser(description="Annual financial statements -> Kafka")
    parser.add_argument("--once", action="store_true", help="Run once and exit (default)")
    parser.add_argument("--batch-size", type=int, default=50, help="Symbols per Kafka flush")
    parser.add_argument("--delay", type=float, default=0.3, help="Seconds between symbols")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--universe-limit", type=int, default=None)
    parser.add_argument("--topic", default=FINANCIALS_TOPIC)
    args = parser.parse_args()

    universe = get_universe()
    if args.universe_limit:
        universe = universe[: args.universe_limit]
    logger.info("Fetching annual statements for %d symbols", len(universe))

    producer = MarketDataProducer(bootstrap_servers=args.bootstrap_servers)
    pending: list[dict] = []
    total, succeeded = 0, 0
    started = time.time()
    for i, stock in enumerate(universe, start=1):
        records = fetch_statements(stock)
        if records:
            pending.extend(records)
            succeeded += 1
        else:
            logger.warning("Skipping %s — no statements.", stock.yf_symbol)
        if i % args.batch_size == 0 or i == len(universe):
            if pending:
                producer.publish_many(args.topic, pending)
                producer.flush()
                total += len(pending)
                pending.clear()
            logger.info("Progress: %d/%d symbols, %d statements (%.0fs)",
                        i, len(universe), total, time.time() - started)
        time.sleep(args.delay)

    logger.info("Done: %d/%d symbols, %d statements published to %s.",
                succeeded, len(universe), total, args.topic)


if __name__ == "__main__":
    main()
