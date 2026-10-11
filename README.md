# Q-Select for PostgreSQL

A practical system using tabular Q-learning to detect candidate query patterns and learn when and how to select semantics-preserving rewrites that improve PostgreSQL query execution performance.

## Targeted SQL Rewrite Patterns

1. **Correlated Scalar Subqueries (`SUBQUERY_TO_JOIN`)**:
   PostgreSQL's optimizer does not decorrelate scalar aggregate subqueries in the `SELECT` projection list, and may execute an inner `SubPlan` once per outer row. Rewriting to a grouped `LEFT JOIN` pre-aggregates the derived table and joins it to the outer relation.
2. **`NOT IN` Anti-Joins on Non-Nullable Keys (`NOT_IN_TO_ANTI_JOIN`)**:
   Due to SQL 3-valued NULL logic, `NOT IN` generally prevents the planner from applying straightforward anti-join transformations. When both outer and inner expressions are non-nullable, rewriting to `NOT EXISTS` exposes the query to anti-join planning, including Hash Anti Join.
3. **Cross-Table `OR` Disjunctions (`OR_TO_UNION`)**:
   Cross-table `OR` disjunctions can make selective index-based plans less attractive or harder for the planner to exploit. Rewriting to disjoint `UNION ALL` branches allows each branch to be optimized independently, potentially enabling more selective index-based access paths.

## Why Q-Learning?
Rewriting is **not** universally beneficial. If a query has a selective index filter on the outer table (e.g., fetching a small number of rows), PostgreSQL's default plan is often faster than computing full table joins or aggregations.

The tabular Q-learning agent learns this exact trade-off: **when to rewrite vs. when to retain PostgreSQL's default plan**.

## Deployment

The system runs as three containers (see [`docker-compose.yml`](docker-compose.yml)):

| Service | Container | Host port | Purpose |
| :--- | :--- | :--- | :--- |
| `postgres` | `qselect-postgres` | `5433` | TPC-H SF 1.0 database |
| `qselect-api` | `qselect-api` | `8000` | FastAPI rewrite service |
| `qselect-ui` | `qselect-ui` | `8501` | Streamlit dashboard |

`qselect-api` and `qselect-ui` share one image built from the `Dockerfile`.

### Prerequisites
- Docker + Docker Compose
- [`uv`](https://docs.astral.sh/uv/) (for the one-time database load and training)
- A trained model artifact, `artifacts/q_table.json` (produced by `training.ipynb`)

### One-time setup
```bash
# 1. Start PostgreSQL
docker compose up -d postgres

# 2. Load the TPC-H SF 1.0 dataset (~30-60s)
uv run --with duckdb python initdb/populate_tpch.py

# 3. Refresh planner statistics
docker exec -it qselect-postgres psql -U postgres -d tpch_sf1 -c "VACUUM ANALYZE;"

# 4. Train the Q-learning agent and export the Q-table to artifacts/q_table.json
uv run jupyter lab            # run training.ipynb end-to-end
```

### Run everything with Docker Compose
```bash
docker compose up -d --build
```
This builds the shared image (installs the API + UI dependencies) and starts all three
containers. The API waits for PostgreSQL to report healthy.

### Access the UI and API
| What | URL |
| :--- | :--- |
| **Dashboard (Streamlit)** | http://localhost:8501 |
| **API base URL** | http://localhost:8000 |
| **API interactive docs (Swagger UI)** | http://localhost:8000/docs |
| **API health check** | http://localhost:8000/health |
| **Learned policy dump** | http://localhost:8000/policy |

On the dashboard: paste a query into the tester (or open **Example Queries** and click
*Test this query*) and press **Run** to see the three-way comparison — **① Original
PostgreSQL**, **② Always-Rewrite**, **③ Q-learning agent** — with timings, speedups, and a
regression-avoidance callout.

### Managing the stack
```bash
docker compose ps                       # container status
docker compose logs -f qselect-api      # follow API logs (also: qselect-ui)
docker compose restart qselect-api      # restart just the API
docker compose down                     # stop everything (keeps the DB volume)
docker compose down -v                  # stop and delete the database volume
```

### Ports
Defaults are `5433` (Postgres), `8000` (API), and `8501` (UI). Override them in a `.env`
file at the repo root (the repo ships with `POSTGRES_PORT=5433`):
```dotenv
POSTGRES_PORT=5433
API_PORT=8000
UI_PORT=8501
```

### Rebuilding after retraining
`artifacts/q_table.json` is baked into the image, so after re-running `training.ipynb` rebuild
the API and recreate the UI so both pick up the new policy:
```bash
docker compose up -d --build qselect-api && docker compose up -d qselect-ui
```

### Run locally (without Docker for the API/UI)
```bash
# API (Postgres must be running)
uv run uvicorn service.app:app --host 0.0.0.0 --port 8000

# UI (in another terminal)
QS_API_URL=http://localhost:8000 uv run streamlit run frontend/app.py
```

### Endpoints
```bash
# liveness + loaded policy summary
curl -s localhost:8000/health

# inspect the learned policy (states, Q-values, and chosen action per state)
curl -s localhost:8000/policy

# choose an action and return the rewritten SQL (no DB required)
curl -s -X POST localhost:8000/rewrite -H 'content-type: application/json' \
  -d '{"sql":"SELECT c.c_name, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) FROM customer c"}'

# run the original, the always-rewrite heuristic, and the agent's choice; verify
# result equivalence and time all three
curl -s -X POST localhost:8000/execute -H 'content-type: application/json' \
  -d '{"sql":"SELECT c.c_name, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) FROM customer c"}'
```

`/execute` returns all three variants — `original` (Baseline 1), `heuristic` (Baseline 2,
always-rewrite, with its `heuristic_action`), and `rewritten` (the agent) — plus `results_match`
and `speedup`. `/rewrite` and `/execute` also return `state_known` and `q_values` for the detected
state, so you can see whether the agent has a learned value (an unseen state falls back to
`DO_NOTHING`).

### Configuration (environment variables)
| Variable | Default | Description |
| :--- | :--- | :--- |
| `QS_TABLE_PATH` | `artifacts/q_table.json` | Path to the exported Q-table |
| `QS_PLANNER_ESTIMATE` | `true` | Use `EXPLAIN (FORMAT JSON)` for cardinality (falls back to the static AST estimate) |
| `QS_DSN` | `PG*` vars | PostgreSQL DSN; overrides the `PGHOST`/`PGPORT`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` defaults |
| `QS_QUERY_TIMEOUT_SEC` | `15` | Per-query statement timeout for `/execute` |
| `QS_QUERY_WARMUPS` | `2` | Warm-up runs per query for `/execute` |
| `QS_QUERY_REPEATS` | `5` | Measured runs per query for `/execute` |
| `QS_API_URL` | `http://localhost:8000` | Backend API base URL used by the frontend |

### Tests
```bash
uv run pytest
```
