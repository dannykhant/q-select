"""Database execution protocol mirroring the training notebook's measurement rules."""

from __future__ import annotations

import statistics
import time

import psycopg


def execute_query(dsn: str, sql: str, timeout_sec: float = 15.0, warmups: int = 2, repeats: int = 5):
    """
    Execute `sql` following the evaluation protocol: `warmups` unmeasured runs
    followed by `repeats` measured runs. Returns (median_runtime_ms, rows, success).
    """
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {int(timeout_sec * 1000)};")

                for _ in range(warmups):
                    cur.execute(sql)
                    cur.fetchall()

                runtimes = []
                rows = None
                for _ in range(repeats):
                    t0 = time.perf_counter()
                    cur.execute(sql)
                    rows = cur.fetchall()
                    t1 = time.perf_counter()
                    runtimes.append((t1 - t0) * 1000.0)

                return statistics.median(runtimes), rows, True
    except Exception:
        return None, None, False


def results_match(rows1, rows2) -> bool:
    """Verify result set semantic equivalence (multiset comparison)."""
    if rows1 is None or rows2 is None:
        return False
    if len(rows1) != len(rows2):
        return False
    return sorted(str(r) for r in rows1) == sorted(str(r) for r in rows2)
