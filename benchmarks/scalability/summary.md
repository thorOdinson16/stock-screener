# Scalability

| spark master | partitions | total s | stages |
|---|---|---|---|
| local[2] | None | 320.417 | preflight=0.016, poll=50.809, ingest=4.727, indicators=21.517, score=32.955, serving=19.456, wait_druid=190.933 |
| local[4] | None | 317.463 | preflight=0.02, poll=58.906, ingest=5.751, indicators=16.015, score=25.194, serving=20.661, wait_druid=190.913 |
| local[8] | None | 315.729 | preflight=0.023, poll=57.348, ingest=5.929, indicators=18.239, score=27.766, serving=15.356, wait_druid=191.065 |
