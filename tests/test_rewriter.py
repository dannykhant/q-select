import pytest
from rewriter import (
    ACTIONS,
    ACTION_TO_IDX,
    PATTERN_NONE,
    PATTERN_SUBQUERY,
    PATTERN_NOT_IN,
    PATTERN_OR,
    CARDINALITY_LOW,
    CARDINALITY_MEDIUM,
    CARDINALITY_HIGH,
    CARDINALITY_BUCKETS,
    apply_action,
    get_state,
    cardinality_to_bucket,
    rewrite_subquery_to_join,
    rewrite_not_in_to_anti_join,
    rewrite_or_to_union,
    register_non_nullable,
)


def normalize_sql(sql: str) -> str:
    """Helper to remove extra spaces/newlines for comparison assertions."""
    return " ".join(sql.split()).upper()


# ── Pattern 1: SUBQUERY_TO_JOIN ────────────────────────────────────────────────


def test_subquery_to_join_transforms():
    sql = (
        "SELECT c.c_custkey, c.c_name, "
        "(SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt "
        "FROM customer c"
    )
    rewritten = rewrite_subquery_to_join(sql)
    norm = normalize_sql(rewritten)

    assert "LEFT JOIN" in norm
    assert "GROUP BY O_CUSTKEY" in norm
    assert "COALESCE(SUBQ.CNT, 0) AS CNT" in norm
    assert "SELECT COUNT(*) FROM ORDERS" not in norm


def test_subquery_to_join_preserves_inner_filters():
    sql = (
        "SELECT c.c_custkey, "
        "(SELECT AVG(o.o_totalprice) FROM orders o WHERE o.o_custkey = c.c_custkey AND o.o_orderstatus = 'F') AS avg_val "
        "FROM customer c"
    )
    rewritten = rewrite_subquery_to_join(sql)
    norm = normalize_sql(rewritten)

    assert "O.O_ORDERSTATUS = 'F'" in norm
    assert "SUBQ.AVG_VAL AS AVG_VAL" in norm


def test_subquery_noop_when_no_subquery():
    sql = "SELECT c.c_custkey, c.c_name FROM customer c WHERE c.c_nationkey = 1"
    assert rewrite_subquery_to_join(sql) == sql


# ── Pattern 2: NOT_IN_TO_ANTI_JOIN ────────────────────────────────────────────


def test_not_in_to_not_exists_non_nullable():
    sql = (
        "SELECT c.c_custkey, c.c_name "
        "FROM customer c "
        "WHERE c.c_custkey NOT IN (SELECT o.o_custkey FROM orders o WHERE o.o_orderstatus = 'F')"
    )
    rewritten = rewrite_not_in_to_anti_join(sql)
    norm = normalize_sql(rewritten)

    assert "NOT EXISTS" in norm
    assert "FROM ORDERS AS O" in norm
    assert "O.O_CUSTKEY = C.C_CUSTKEY" in norm
    assert "NOT IN" not in norm


def test_not_in_skipped_when_nullable():
    sql = (
        "SELECT c.c_custkey, c.c_comment "
        "FROM customer c "
        "WHERE c.c_comment NOT IN (SELECT o.o_comment FROM orders o)"
    )
    assert rewrite_not_in_to_anti_join(sql) == sql


def test_not_in_custom_registered_non_nullable():
    register_non_nullable("my_tbl", "col_a")
    register_non_nullable("other_tbl", "col_b")
    sql = (
        "SELECT t.col_a FROM my_tbl t "
        "WHERE t.col_a NOT IN (SELECT o.col_b FROM other_tbl o)"
    )
    rewritten = rewrite_not_in_to_anti_join(sql)
    assert "NOT EXISTS" in normalize_sql(rewritten)


def test_not_in_guarded_with_is_not_null():
    # c_comment and o_comment are nullable, but explicitly guarded with IS NOT NULL
    sql = (
        "SELECT c.c_custkey, c.c_comment "
        "FROM customer c "
        "WHERE c.c_comment IS NOT NULL "
        "AND c.c_comment NOT IN (SELECT o.o_comment FROM orders o WHERE o.o_comment IS NOT NULL)"
    )
    rewritten = rewrite_not_in_to_anti_join(sql)
    assert "NOT EXISTS" in normalize_sql(rewritten)



# ── Pattern 3: OR_TO_UNION ────────────────────────────────────────────────────


def test_or_to_union_splits():
    sql = (
        "SELECT l.l_orderkey, p.p_name "
        "FROM lineitem l JOIN part p ON l.l_partkey = p.p_partkey "
        "WHERE (p.p_brand = 'Brand#11' AND l.l_quantity < 10) "
        "OR (p.p_brand = 'Brand#12' AND l.l_quantity > 40)"
    )
    rewritten = rewrite_or_to_union(sql)
    norm = normalize_sql(rewritten)

    assert "UNION ALL" in norm
    assert "AND NOT" in norm
    assert "P.P_BRAND = 'BRAND#11'" in norm
    assert "P.P_BRAND = 'BRAND#12'" in norm


def test_or_to_union_preserves_outer_and_conjuncts():
    sql = (
        "SELECT l.l_orderkey, p.p_name "
        "FROM lineitem l JOIN part p ON l.l_partkey = p.p_partkey "
        "WHERE p.p_type = 'PROMO' "
        "AND (l.l_quantity < 10 OR l.l_discount > 0.08)"
    )
    rewritten = rewrite_or_to_union(sql)
    norm = normalize_sql(rewritten)

    assert "UNION ALL" in norm
    assert norm.count("P.P_TYPE = 'PROMO'") == 2


def test_or_noop_when_no_or():
    sql = "SELECT l.l_orderkey FROM lineitem l WHERE l.l_quantity > 10"
    assert rewrite_or_to_union(sql) == sql


# ── Dispatcher: apply_action ──────────────────────────────────────────────────


def test_apply_action_do_nothing():
    sql = "SELECT * FROM customer"
    assert apply_action(sql, "DO_NOTHING") == sql
    assert apply_action(sql, "UNKNOWN_ACTION") == sql


def test_apply_action_dispatch_subquery():
    sql = (
        "SELECT c.c_custkey, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt "
        "FROM customer c"
    )
    res = apply_action(sql, "SUBQUERY_TO_JOIN")
    assert "LEFT JOIN" in normalize_sql(res)


def test_apply_action_dispatch_not_in():
    sql = "SELECT c.c_custkey FROM customer c WHERE c.c_custkey NOT IN (SELECT o.o_custkey FROM orders o)"
    res = apply_action(sql, "NOT_IN_TO_ANTI_JOIN")
    assert "NOT EXISTS" in normalize_sql(res)


def test_apply_action_dispatch_or():
    sql = "SELECT * FROM customer c WHERE c.c_nationkey = 1 OR c.c_nationkey = 2"
    res = apply_action(sql, "OR_TO_UNION")
    assert "UNION ALL" in normalize_sql(res)


# ── Pattern Detection & State: get_state ──────────────────────────────────────


def test_get_state_subquery_without_filter():
    sql = "SELECT c.c_name, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) FROM customer c"
    pattern, has_filter, card_bucket = get_state(sql)
    assert pattern == PATTERN_SUBQUERY
    assert has_filter == 0
    assert card_bucket == CARDINALITY_HIGH


def test_get_state_subquery_with_outer_filter_1e():
    sql = (
        "SELECT c.c_custkey, c.c_name, "
        "(SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey AND o.o_totalprice > 100000) AS expensive_orders "
        "FROM customer c "
        "WHERE c.c_acctbal > 0"
    )
    pattern, has_filter, card_bucket = get_state(sql)
    assert pattern == PATTERN_SUBQUERY
    assert has_filter == 1
    assert card_bucket == CARDINALITY_MEDIUM


def test_get_state_not_in_without_filter():
    sql = "SELECT c.c_name FROM customer c WHERE c.c_custkey NOT IN (SELECT o.o_custkey FROM orders o)"
    pattern, has_filter, card_bucket = get_state(sql)
    assert pattern == PATTERN_NOT_IN
    assert has_filter == 0
    assert card_bucket == CARDINALITY_HIGH


def test_get_state_not_in_with_outer_filter_2d():
    sql = (
        "SELECT c.c_custkey, c.c_name "
        "FROM customer c "
        "WHERE c.c_acctbal > 0 AND c.c_custkey NOT IN (SELECT o.o_custkey FROM orders o WHERE o.o_orderstatus = 'F')"
    )
    pattern, has_filter, card_bucket = get_state(sql)
    assert pattern == PATTERN_NOT_IN
    assert has_filter == 1
    assert card_bucket == CARDINALITY_MEDIUM


def test_get_state_or_without_filter():
    sql = "SELECT * FROM part p WHERE p.p_brand = 'A' OR p.p_brand = 'B'"
    pattern, has_filter, card_bucket = get_state(sql)
    assert pattern == PATTERN_OR
    assert has_filter == 0
    assert card_bucket == CARDINALITY_HIGH


def test_get_state_or_with_outer_filter_3c():
    sql = (
        "SELECT l.l_orderkey, p.p_name "
        "FROM lineitem l JOIN part p ON l.l_partkey = p.p_partkey "
        "WHERE p.p_type = 'PROMO' AND (l.l_quantity < 10 OR l.l_discount > 0.08)"
    )
    pattern, has_filter, card_bucket = get_state(sql)
    assert pattern == PATTERN_OR
    assert has_filter == 1
    assert card_bucket == CARDINALITY_MEDIUM


def test_get_state_plain_filter_flag_on():
    sql = "SELECT c.c_name FROM customer c WHERE c.c_custkey = 42"
    pattern, has_filter, card_bucket = get_state(sql)
    assert pattern == PATTERN_NONE
    assert has_filter == 1
    assert card_bucket == CARDINALITY_LOW


def test_get_state_plain_filter_flag_off():
    sql = "SELECT c.c_name FROM customer c"
    pattern, has_filter, card_bucket = get_state(sql)
    assert pattern == PATTERN_NONE
    assert has_filter == 0
    assert card_bucket == CARDINALITY_HIGH


# ── Cardinality Bucket Unit Tests ─────────────────────────────────────────────


def test_cardinality_to_bucket_thresholds():
    assert cardinality_to_bucket(0) == CARDINALITY_LOW
    assert cardinality_to_bucket(1) == CARDINALITY_LOW
    assert cardinality_to_bucket(100) == CARDINALITY_LOW
    assert cardinality_to_bucket(101) == CARDINALITY_MEDIUM
    assert cardinality_to_bucket(5000) == CARDINALITY_MEDIUM
    assert cardinality_to_bucket(10000) == CARDINALITY_MEDIUM
    assert cardinality_to_bucket(10001) == CARDINALITY_HIGH
    assert cardinality_to_bucket(150000) == CARDINALITY_HIGH


def test_get_state_explicit_cardinality():
    sql = "SELECT c.c_name FROM customer c"
    # Unfiltered SQL would default to HIGH, but explicit cardinality overrides
    assert get_state(sql, cardinality=50)[2] == CARDINALITY_LOW
    assert get_state(sql, cardinality=5000)[2] == CARDINALITY_MEDIUM
    assert get_state(sql, cardinality=50000)[2] == CARDINALITY_HIGH


def test_get_state_explicit_cardinality_bucket():
    sql = "SELECT c.c_name FROM customer c"
    assert get_state(sql, cardinality_bucket=CARDINALITY_LOW)[2] == CARDINALITY_LOW
    assert get_state(sql, cardinality_bucket=CARDINALITY_MEDIUM)[2] == CARDINALITY_MEDIUM
    assert get_state(sql, cardinality_bucket=CARDINALITY_HIGH)[2] == CARDINALITY_HIGH


def test_get_state_pk_point_lookup_low():
    sql = "SELECT * FROM orders o WHERE o.o_orderkey = 100"
    pattern, has_filter, card_bucket = get_state(sql)
    assert card_bucket == CARDINALITY_LOW
    assert has_filter == 1


def test_get_state_small_limit_low():
    sql = "SELECT * FROM customer c LIMIT 10"
    pattern, has_filter, card_bucket = get_state(sql)
    assert card_bucket == CARDINALITY_LOW

