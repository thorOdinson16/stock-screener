# Iceberg

## Schema evolution
- columns before: ['trade_date', 'label', 'rank', 'symbol', 'score', 'model_name', 'scored_at']
- columns after: ['trade_date', 'label', 'rank', 'symbol', 'score', 'model_name', 'scored_at', 'bench_note']
- old data readable: True

## Time travel
- snapshots: 1
- rows VERSION AS OF first: 80
- rows TIMESTAMP AS OF first: 80

## Compaction
- files 1 -> 1

## Partition evolution
- partitions before: ['Row(trade_date_month=672)', 'Row(trade_date_month=673)']
- partitions after: ['Row(trade_date_month=672)', 'Row(trade_date_month=673)', 'Row(trade_date_month=673)']

