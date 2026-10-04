# Iceberg maintenance

Runs per table (`spark/jobs/maintain_iceberg.py`):

| Step | Mechanism | What it does |
|------|-----------|--------------|
| `rewrite_data_files` | Spark procedure | compacts many small files into larger ones |
| `rewrite_manifests`  | Spark procedure | consolidates manifest files |
| `expire_snapshots`   | driver-side Iceberg API | drops snapshots older than N days and their unreferenced files |

Tables: `bronze.{market_quotes,quotes_daily,market_fundamentals}`,
`silver.{quotes_enriched,fundamentals_clean}`, `gold.{stock_scores,top_picks}`,
`ml.training_dataset`.

**Orphan-file cleanup is intentionally not included** — the bundled
`iceberg-spark-runtime` does not ship `org.apache.iceberg.actions.RemoveOrphanFiles`,
and the Spark `remove_orphan_files` procedure hangs in this Spark 3.3.4 + Iceberg
1.8.1 runtime.

## Run

```bash
scripts/maintenance.sh                 # expire snapshots older than 7 days
SNAPSHOT_RETENTION_DAYS=30 scripts/maintenance.sh
DRY_RUN=1 scripts/maintenance.sh       # stats only, no changes

# single table
spark-submit --packages org.apache.iceberg:iceberg-spark-runtime-3.3_2.12:1.8.1 \
  spark/jobs/maintain_iceberg.py --tables iceberg.gold.stock_scores
```

Or from the dashboard (**Maintenance** button) / Airflow DAG `screening_maintenance`.

## Useful queries

```sql
-- snapshots (history + time travel)
SELECT snapshot_id, committed_at, operation FROM iceberg.gold.stock_scores.snapshots;

-- files per table (watch compaction reduce this)
SELECT COUNT(*) AS files, SUM(file_size_in_bytes)/1e6 AS mb, AVG(file_size_in_bytes) AS avg_bytes
FROM iceberg.silver.quotes_enriched.files;

-- time travel: query a table as of a past snapshot
SELECT COUNT(*) FROM iceberg.silver.quotes_enriched
  VERSION AS OF <snapshot_id>;

SELECT COUNT(*) FROM iceberg.silver.quotes_enriched
  TIMESTAMP AS OF '2026-09-15 00:00:00';

-- schema evolution (add a column without rewriting)
ALTER TABLE iceberg.silver.quotes_enriched ADD COLUMN earnings_yield DOUBLE;
SELECT * FROM iceberg.silver.quotes_enriched.snapshots
  WHERE operation = 'replace' ORDER BY committed_at DESC LIMIT 5;
```
