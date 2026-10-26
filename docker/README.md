# Running the platform in Docker (Windows, macOS, Linux)

The same stack as the native install, packaged with Docker Compose: HDFS, Kafka
(KRaft), SeaTunnel, Druid, Airflow, the FastAPI backend and the dashboard, at the
versions pinned in the main `README.md`. The native setup (`start-stack.sh`,
`start-ui.sh`, `setup.md`) is unchanged and does not use any of this.

## How it is put together

- **One platform image** (`docker/Dockerfile`, target `platform`) contains the
  pinned Apache releases, Java 11 + 17, Python 3.12 (poller, API) and Python 3.10
  (Spark, Airflow), and the repo at `/opt/screening`. Every backend service is a
  container of that image started with a different role (`docker/entrypoint.sh`).
- **One shared network namespace.** All containers join the `net` container's
  network (`network_mode: service:net`), so services reach each other on
  `localhost` with the same ports as the native install. No config or code
  addresses change.
- **Hive Metastore is not included.** The pipeline doesn't use it (Iceberg uses a
  Hadoop catalog on HDFS). Table creation uses `docker/create_tables.py` instead
  of the `spark-sql` CLI.
- **The dashboard** is built with Vite and served by nginx on port 5173, which
  proxies `/api` to FastAPI.
- **Data** lives in named Docker volumes, so it survives `docker compose down`.

## Requirements

- **Windows:** Windows 10/11 with [Docker Desktop](https://www.docker.com/products/docker-desktop/)
  using the WSL2 backend.
- **Memory:** about **14 GB for Docker**. The stack runs ~8 JVMs plus a Spark driver.
  On Windows, WSL2 gets half the host RAM by default, so a 32 GB machine works out
  of the box. Otherwise raise the limit in `%UserProfile%\.wslconfig`:

  ```ini
  [wsl2]
  memory=14GB
  ```

  Then run `wsl --shutdown` and restart Docker Desktop.
- **Disk:** about 10 GB (image ~5 GB plus data).
- **Network:** internet access, for Yahoo Finance at run time and for the
  downloads in the one-time image build.
- **Free ports:** 5173, 8000, 8080, 8888, 9870, 9092 and 5801. On Linux, stop the
  native stack first (`./stop-ui.sh && ./stop-stack.sh`).

## Quick start

On Windows, clone the repo inside WSL or with Git for Windows. `.gitattributes`
keeps LF line endings, so either works.

```bash
git clone https://github.com/thorOdinson16/stock-screener.git
cd stock-screener
```

Then get the images in one of two ways.

**Option A: pull the prebuilt images (recommended).** They are published to GitHub
Container Registry (`ghcr.io/thorodinson16/stock-screener-platform` and
`-frontend`, linux/amd64). The download is about 3 GB and needs no build:

```bash
docker compose pull
docker compose up -d
```

If the packages are private, log in first with a GitHub token that has the
`read:packages` scope: `docker login ghcr.io -u <github-user>`.

**Option B: build from source.**

```bash
docker compose up -d --build
```

The first build downloads about 2.3 GB of Apache releases from archive.apache.org,
which is often throttled to around 100 KB/s, plus Python and Maven packages. On a
slow connection that can take a few hours. Later builds reuse Docker's cache and
only redo the steps whose inputs changed. To speed up the first build, point
`APACHE_MIRROR` at a faster mirror that still hosts these versions:
`docker compose build --build-arg APACHE_MIRROR=https://<mirror>/apache`.

Either way, wait for the one-time setup to finish:

```bash
docker compose logs -f init      # wait for "Init complete"
```

On first start, `init` creates the Kafka topics, the Iceberg tables and the Druid
supervisors (left suspended). On later starts it only re-suspends the supervisors.

### First-time data load (once)

A fresh stack has no data and no model. Load 5 years of history and train:

```bash
docker compose exec airflow docker/bootstrap-data.sh             # full NIFTY 500 (~30-60 min)
docker compose exec airflow docker/bootstrap-data.sh --limit 20  # quick smoke test
```

Then open the dashboard and press **Run pipeline** (Full, with price history).

## Using it

| What | URL |
|---|---|
| Dashboard | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Airflow | http://localhost:8080 (`admin` / `admin`) |
| Druid console | http://localhost:8888 |
| HDFS NameNode | http://localhost:9870 |

The pipeline works the same as natively: **Run pipeline**, **Retrain model**, or
trigger the DAGs in Airflow. To run a script directly:

```bash
docker compose exec airflow scripts/run_once.sh --limit 20
docker compose exec airflow bash                 # a shell with everything on PATH
```

## Settings

Override these in a `.env` file next to `docker-compose.yml`:

| Variable | Default | Meaning |
|---|---|---|
| `SPARK_DRIVER_MEMORY` | `4g` | Spark driver heap for pipeline/retrain jobs |
| `SPARK_MASTER` | `local[4]` | Spark parallelism |
| `DRUID_MEMORY` | `6g` | Total memory Druid sizes itself to |
| `SEATUNNEL_HEAP` | `2g` | SeaTunnel Zeta heap |
| `KAFKA_HEAP` | `1g` | Kafka heap |
| `AIRFLOW_USER` / `AIRFLOW_PASSWORD` | `admin` / `admin` | Airflow login, also used by the API |
| `APACHE_MIRROR` (build arg) | archive.apache.org | Faster Apache mirror for the image build |

## Stop, restart, reset

```bash
docker compose stop          # stop, keep containers + data
docker compose up -d         # start again
docker compose down          # remove containers, keep data volumes
docker compose down -v       # remove everything, including all data and the model
```

After `docker compose down -v`, repeat the first-time data load.

## Troubleshooting

- **A service keeps restarting:** check `docker compose logs <service>`. Most often
  Docker has too little memory; see Requirements.
- **`init` failed:** fix the cause, then run `docker compose up -d init`. Steps it
  already completed are skipped.
- **Build fails with `Could not resolve host`:** some home routers drop DNS
  lookups when the build fetches several files in parallel. Retry, or build using
  the host's resolver:
  `docker build --network host -f docker/Dockerfile --target platform -t ghcr.io/thorodinson16/stock-screener-platform:latest .`
  (and `--target frontend -t ghcr.io/thorodinson16/stock-screener-frontend:latest` for the dashboard),
  then run `docker compose up -d` without `--build`.
- **Port already in use:** another program (or the native stack) holds one of the
  published ports.
