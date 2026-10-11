"""Planner cardinality estimates via PostgreSQL ``EXPLAIN (FORMAT JSON)``."""

from __future__ import annotations

import psycopg


def planner_cardinality(dsn: str, sql: str) -> float | None:
    """Estimated plan row count from the PostgreSQL planner, or None if unavailable."""
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
                plan = cur.fetchone()[0][0]["Plan"]
                return float(plan.get("Plan Rows", 1.0))
    except Exception:
        return None
