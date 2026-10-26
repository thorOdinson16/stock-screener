# Fault recovery (Experiment 4)

Each component is killed mid-run; the run is retried after the component is
restarted. Druid counts are rows per datasource before the faulty run and after
the recovered run (one successful run of `--limit` symbols is expected to add
one snapshot).

| component | killed during | run failed | failed stage | recovered | recovery s | Kafka lag before -> after | screener delta | stock_scores delta | market_quotes delta |
|---|---|---|---|---|---|---|---|---|---|
| druid-middlemanager | wait_druid | False | None | True | 327.1 | 0 -> 0 | 998 | 1960 | 99 |
| kafka | ingest | True | serving | True | 358.0 | 5333 -> 5333 | 499 | 980 | 647 |
| seatunnel | ingest | True | ingest | True | 361.5 | 5333 -> 0 | 499 | 980 | 100 |
