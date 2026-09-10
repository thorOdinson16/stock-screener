"""
kafka_producer.py — thin wrapper around confluent_kafka Producer for publishing
quote/fundamentals snapshots, keyed by symbol per the topic design (see
kafka/topics/TOPIC_DESIGN.md).
"""

import json
import logging

from confluent_kafka import Producer

logger = logging.getLogger(__name__)


class MarketDataProducer:
    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        self._producer = Producer({"bootstrap.servers": bootstrap_servers})
        self._delivered = 0
        self._failed = 0

    def _delivery_callback(self, err, msg):
        if err is not None:
            self._failed += 1
            logger.error("Delivery failed for key=%s: %s", msg.key(), err)
        else:
            self._delivered += 1

    def publish(self, topic: str, key: str, value: dict) -> None:
        self._producer.produce(
            topic=topic,
            key=key.encode("utf-8"),
            value=json.dumps(value).encode("utf-8"),
            callback=self._delivery_callback,
        )
        # poll(0) drives delivery callbacks without blocking — needed periodically
        # so the internal librdkafka queue doesn't fill up during a large batch.
        self._producer.poll(0)

    def publish_many(self, topic: str, records: list[dict], key_field: str = "symbol") -> None:
        for record in records:
            self.publish(topic, key=record[key_field], value=record)

    def flush(self, timeout: float = 30.0) -> int:
        """Blocks until all queued messages are delivered or timeout is reached.
        Returns the number of messages still undelivered (0 = full success)."""
        remaining = self._producer.flush(timeout)
        logger.info(
            "Flush complete: %d delivered, %d failed, %d still queued.",
            self._delivered,
            self._failed,
            remaining,
        )
        return remaining