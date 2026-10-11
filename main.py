from rewriter import (
    ACTIONS,
    apply_action,
    get_state,
    PATTERN_SUBQUERY,
    PATTERN_NOT_IN,
    PATTERN_OR,
)


def demo():
    sample_queries = [
        # 1. Correlated Subquery -> LEFT JOIN
        (
            "1. Correlated COUNT -> LEFT JOIN",
            "SUBQUERY_TO_JOIN",
            """
            SELECT c.c_custkey,
                c.c_name,
                (SELECT COUNT(*)
                    FROM orders o
                    WHERE o.o_custkey = c.c_custkey) AS cnt
            FROM customer c
            """,
        ),
        (
            "1a. Correlated COUNT -> LEFT JOIN",
            "SUBQUERY_TO_JOIN",
            """
            SELECT c.c_custkey,
                (SELECT COUNT(*)
                    FROM orders o
                    WHERE o.o_custkey = c.c_custkey) AS order_count
            FROM customer c
            """,
        ),
        (
            "1b. Correlated SUM -> LEFT JOIN",
            "SUBQUERY_TO_JOIN",
            """
            SELECT c.c_custkey,
                (SELECT SUM(o.o_totalprice)
                    FROM orders o
                    WHERE o.o_custkey = c.c_custkey) AS total_spend
            FROM customer c
            """,
        ),
        (
            "1c. Correlated MAX -> LEFT JOIN",
            "SUBQUERY_TO_JOIN",
            """
            SELECT c.c_custkey,
                (SELECT MAX(o.o_orderdate)
                    FROM orders o
                    WHERE o.o_custkey = c.c_custkey) AS last_order
            FROM customer c
            """,
        ),
        (
            "1d. Correlated AVG with filter -> LEFT JOIN",
            "SUBQUERY_TO_JOIN",
            """
            SELECT c.c_custkey,
                (SELECT AVG(o.o_totalprice)
                    FROM orders o
                    WHERE o.o_custkey = c.c_custkey
                    AND o.o_orderstatus = 'F') AS avg_completed_order
            FROM customer c
            """,
        ),
        (
            "1e. Correlated subquery with outer filter -> LEFT JOIN",
            "SUBQUERY_TO_JOIN",
            """
            SELECT c.c_custkey,
                c.c_name,
                (SELECT COUNT(*)
                    FROM orders o
                    WHERE o.o_custkey = c.c_custkey
                    AND o.o_totalprice > 100000) AS expensive_orders
            FROM customer c
            WHERE c.c_acctbal > 0
            """,
        ),
        (
            "1f. Correlated subquery with multiple predicates -> LEFT JOIN",
            "SUBQUERY_TO_JOIN",
            """
            SELECT c.c_custkey,
                (SELECT COUNT(*)
                    FROM orders o
                    WHERE o.o_custkey = c.c_custkey
                    AND o.o_orderstatus = 'F'
                    AND o.o_totalprice > 50000) AS cnt
            FROM customer c
            """,
        ),

        # 2. NOT IN -> NOT EXISTS / Anti-Join
        (
            "2. Basic NOT IN -> NOT EXISTS",
            "NOT_IN_TO_ANTI_JOIN",
            """
            SELECT c.c_custkey, c.c_name
            FROM customer c
            WHERE c.c_custkey NOT IN (
                SELECT o.o_custkey
                FROM orders o
            )
            """,
        ),
        (
            "2a. NOT IN with inner filter -> NOT EXISTS",
            "NOT_IN_TO_ANTI_JOIN",
            """
            SELECT c.c_custkey
            FROM customer c
            WHERE c.c_custkey NOT IN (
                SELECT o.o_custkey
                FROM orders o
                WHERE o.o_orderstatus = 'F'
            )
            """,
        ),
        (
            "2b. NOT IN with price condition -> NOT EXISTS",
            "NOT_IN_TO_ANTI_JOIN",
            """
            SELECT c.c_custkey
            FROM customer c
            WHERE c.c_custkey NOT IN (
                SELECT o.o_custkey
                FROM orders o
                WHERE o.o_totalprice > 100000
            )
            """,
        ),
        (
            "2c. NOT IN with multiple inner predicates",
            "NOT_IN_TO_ANTI_JOIN",
            """
            SELECT c.c_custkey, c.c_name
            FROM customer c
            WHERE c.c_custkey NOT IN (
                SELECT o.o_custkey
                FROM orders o
                WHERE o.o_orderstatus = 'F'
                AND o.o_totalprice > 50000
            )
            """,
        ),
        (
            "2d. NOT IN with outer predicate",
            "NOT_IN_TO_ANTI_JOIN",
            """
            SELECT c.c_custkey, c.c_name
            FROM customer c
            WHERE c.c_acctbal > 0
            AND c.c_custkey NOT IN (
                SELECT o.o_custkey
                FROM orders o
                WHERE o.o_orderstatus = 'F'
            )
            """,
        ),
        (
            "2e. NOT IN with DISTINCT inner query",
            "NOT_IN_TO_ANTI_JOIN",
            """
            SELECT c.c_custkey
            FROM customer c
            WHERE c.c_custkey NOT IN (
                SELECT DISTINCT o.o_custkey
                FROM orders o
                WHERE o.o_orderstatus = 'F'
            )
            """,
        ),
        (
            "2f. NOT IN with inner NULL guard",
            "NOT_IN_TO_ANTI_JOIN",
            """
            SELECT c.c_custkey
            FROM customer c
            WHERE c.c_custkey NOT IN (
                SELECT o.o_custkey
                FROM orders o
                WHERE o.o_custkey IS NOT NULL
            )
            """,
        ),

        # 3. Multi-table OR -> UNION ALL
        (
            "3. Different brands, mutually exclusive",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, p.p_name
            FROM lineitem l
            JOIN part p ON l.l_partkey = p.p_partkey
            WHERE (p.p_brand = 'Brand#11' AND l.l_quantity < 10)
            OR (p.p_brand = 'Brand#12' AND l.l_quantity > 40)
            """,
        ),
        (
            "3a. Different price ranges",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, p.p_name
            FROM lineitem l
            JOIN part p ON l.l_partkey = p.p_partkey
            WHERE (p.p_retailprice < 1000)
            OR (p.p_retailprice > 5000)
            """,
        ),
        (
            "3b. OR on lineitem predicates",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, l.l_partkey
            FROM lineitem l
            WHERE (l.l_quantity < 10)
            OR (l.l_quantity > 40)
            """,
        ),
        (
            "3c. OR with common predicate",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, p.p_name
            FROM lineitem l
            JOIN part p ON l.l_partkey = p.p_partkey
            WHERE p.p_type = 'PROMO'
            AND (
                l.l_quantity < 10
                OR l.l_discount > 0.08
            )
            """,
        ),
        (
            "3d. Mutually exclusive status predicates",
            "OR_TO_UNION",
            """
            SELECT o.o_orderkey, o.o_custkey
            FROM orders o
            WHERE o.o_orderstatus = 'F'
            OR o.o_orderstatus = 'O'
            """,
        ),
        (
            "3e. Overlapping predicates",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, l.l_quantity
            FROM lineitem l
            WHERE l.l_quantity < 20
            OR l.l_quantity < 10
            """,
        ),
        (
            "3f. OR across joined tables",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, p.p_name
            FROM lineitem l
            JOIN part p ON l.l_partkey = p.p_partkey
            WHERE p.p_size < 10
            OR l.l_quantity > 40
            """,
        ),
        (
            "3g. OR with three branches",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, p.p_name
            FROM lineitem l
            JOIN part p ON l.l_partkey = p.p_partkey
            WHERE p.p_brand = 'Brand#11'
            OR p.p_brand = 'Brand#12'
            OR p.p_brand = 'Brand#13'
            """,
        ),
        (
            "3h. OR with AND branches",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, p.p_name
            FROM lineitem l
            JOIN part p ON l.l_partkey = p.p_partkey
            WHERE (p.p_size < 10 AND l.l_quantity < 20)
            OR (p.p_size > 30 AND l.l_quantity > 40)
            """,
        ),
        (
            "3i. OR with outer filter",
            "OR_TO_UNION",
            """
            SELECT l.l_orderkey, p.p_name
            FROM lineitem l
            JOIN part p ON l.l_partkey = p.p_partkey
            WHERE l.l_shipdate >= DATE '1995-01-01'
            AND (
                p.p_brand = 'Brand#11'
                OR p.p_brand = 'Brand#12'
            )
            """,
        ),
    ]
    print("=" * 70)
    print("Q-SELECT QUERY REWRITER DEMO")
    print("=" * 70)

    for title, action, sql in sample_queries:
        state = get_state(sql)
        rewritten = apply_action(sql, action)
        print(f"\n--- {title} ---")
        print(
            f"Detected State: (pattern={state[0]}, has_selective_outer_filter={state[1]}, cardinality_bucket={state[2]})"
        )
        print(f"Action: {action}")
        print(f"Original SQL:\n  {sql}")
        print(f"Rewritten SQL:\n  {rewritten}")

    print("\n" + "=" * 70)


def main():
    demo()


if __name__ == "__main__":
    main()
