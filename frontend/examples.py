"""Curated example queries for the dashboard.

Each example is chosen to make the three columns visible: Original PostgreSQL,
Always-Rewrite (heuristic), and the Q-learning agent.
"""

from __future__ import annotations

EXAMPLE_QUERIES: list[dict] = [
    {
        "type": "not_in_full",
        "title": "NOT IN anti-join — both rewrites win",
        "description": (
            "`NOT IN` is evaluated with a hashed subplan; both Always-Rewrite and the agent "
            "convert it to an anti-join (`NOT EXISTS`)."
        ),
        "highlight": "① Original is slowest; ② and ③ both rewrite and are much faster.",
        "sql": (
            "SELECT c.c_custkey, c.c_name FROM customer c "
            "WHERE c.c_custkey NOT IN "
            "(SELECT o.o_custkey FROM orders o WHERE o.o_orderstatus = 'F') "
            "AND c.c_nationkey = 17"
        ),
    },
    {
        "type": "subquery_full_scan",
        "title": "Correlated subquery, selective filter — agent avoids a regression",
        "description": (
            "The rewrite decorrelates to a `LEFT JOIN` that aggregates all of `orders`. With a "
            "selective outer filter (one nation) that is much slower than the per-row subplan."
        ),
        "highlight": "② Always-Rewrite regresses badly; ③ agent keeps ① (DO_NOTHING).",
        "sql": (
            "SELECT c.c_custkey, c.c_name, "
            "(SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt "
            "FROM customer c WHERE c.c_nationkey = 3"
        ),
    },
    {
        "type": "subquery_unfiltered",
        "title": "Correlated subquery over a full scan — both rewrites win",
        "description": (
            "With a large outer scan, aggregating `orders` once and hash-joining beats running "
            "the subquery per outer row."
        ),
        "highlight": "② and ③ both decorrelate to a `LEFT JOIN` and beat ①.",
        "sql": (
            "SELECT c.c_custkey, c.c_name, "
            "(SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt "
            "FROM customer c"
        ),
    },
    {
        "type": "subquery_filtered",
        "title": "Point lookup — rewriting adds overhead",
        "description": (
            "A primary-key lookup returns one row, so the correlated subplan does a single index "
            "probe; building the joined aggregate costs more."
        ),
        "highlight": "③ agent keeps ① (DO_NOTHING); any rewrite is unnecessary here.",
        "sql": (
            "SELECT c.c_custkey, c.c_name, "
            "(SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt "
            "FROM customer c WHERE c.c_custkey = 1601"
        ),
    },
    {
        "type": "or_cross_table",
        "title": "Cross-table OR — rewriting is neutral",
        "description": (
            "Splitting the disjunction into `UNION ALL` branches is roughly a wash here, so there "
            "is nothing to gain."
        ),
        "highlight": "② and ③ are both ~neutral vs ①; the agent keeps the default plan.",
        "sql": (
            "SELECT l.l_orderkey, p.p_name "
            "FROM lineitem l JOIN part p ON l.l_partkey = p.p_partkey "
            "WHERE (p.p_brand = 'Brand#11' AND l.l_quantity < 10) "
            "OR (p.p_brand = 'Brand#12' AND l.l_quantity > 40)"
        ),
    },
    {
        "type": "not_in_filtered",
        "title": "NOT IN point lookup — both rewrites win",
        "description": (
            "Even for a single-row outer lookup, replacing `NOT IN` with `NOT EXISTS` lets the "
            "planner use an anti-join."
        ),
        "highlight": "② and ③ both rewrite and are far faster than ①.",
        "sql": (
            "SELECT c.c_custkey, c.c_name FROM customer c "
            "WHERE c.c_custkey NOT IN (SELECT o.o_custkey FROM orders o) "
            "AND c.c_custkey = 501"
        ),
    },
]
