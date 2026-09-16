# Latency

repeat=2 limit=50 full=False

| run | total s | handoff s | stages |
|---|---|---|---|
| 2026-09-16T08:42:18.814Z | 145.253 | 46.747 | preflight=0.0, poll=46.4, ingest=6.0, indicators=26.7, score=39.8, serving=26.0, wait_druid=0.1 |
| 2026-09-16T08:42:18.814Z | 148.298 | 47.44 | preflight=0.0, poll=47.2, ingest=7.2, indicators=25.7, score=42.1, serving=25.8, wait_druid=0.1 |

p50 end-to-end **146.8s**, p95 end-to-end **148.1s**; p50 handoff **47.1s**, p95 handoff **47.4s**.
