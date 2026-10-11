"""Pattern detection and state extraction for Q-learning agent."""

from sqlglot import exp, parse_one
from .constants import (
    PATTERN_NONE,
    PATTERN_SUBQUERY,
    PATTERN_NOT_IN,
    PATTERN_OR,
    CARDINALITY_LOW,
    CARDINALITY_MEDIUM,
    CARDINALITY_HIGH,
)

FILTER_PREDICATES = (
    exp.EQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.NEQ,
    exp.Between,
    exp.In,
    exp.Like,
    exp.ILike,
)

# Known primary key / single-row unique identifier column names in TPC-H and standard schemas
POINT_LOOKUP_COLUMNS = {
    "c_custkey",
    "o_orderkey",
    "p_partkey",
    "s_suppkey",
}


def cardinality_to_bucket(rows: float | int) -> int:
    """
    Map an estimated or actual row count to a coarse cardinality bucket:
      0: CARDINALITY_LOW    (<= 100 rows)
      1: CARDINALITY_MEDIUM (101 to 10,000 rows)
      2: CARDINALITY_HIGH   (> 10,000 rows)
    """
    if rows <= 100:
        return CARDINALITY_LOW
    if rows <= 10_000:
        return CARDINALITY_MEDIUM
    return CARDINALITY_HIGH


def _estimate_static_cardinality_bucket(
    tree: exp.Expression | None,
    pattern: int,
    has_filter: int,
) -> int:
    """
    Infer cardinality bucket statically from query AST when planner estimates are unavailable.
    - Full scans (no filter) -> CARDINALITY_HIGH (2)
    - Point lookups on PK/unique keys or LIMIT <= 100 -> CARDINALITY_LOW (0)
    - Filtered subsets (range, category, secondary filters) -> CARDINALITY_MEDIUM (1)
    """
    if tree is None:
        return CARDINALITY_HIGH

    # Check for small explicit LIMIT
    limit_node = tree.find(exp.Limit)
    if limit_node and limit_node.expression:
        try:
            limit_val = int(limit_node.expression.this)
            if limit_val <= 100:
                return CARDINALITY_LOW
        except (ValueError, TypeError, AttributeError):
            pass

    if has_filter == 0:
        return CARDINALITY_HIGH

    # Check for equality filter on primary key or unique ID
    outer_where = tree.args.get("where")
    if outer_where:
        for eq in outer_where.find_all(exp.EQ):
            ancestor_select = eq.find_ancestor(exp.Select)
            if ancestor_select is not None and ancestor_select != tree:
                continue
            if pattern == PATTERN_OR and eq.find_ancestor(exp.Or):
                continue
            for col in eq.find_all(exp.Column):
                col_name = getattr(col, "name", "").lower()
                if (
                    col_name in POINT_LOOKUP_COLUMNS
                    or col_name == "id"
                    or col_name.endswith("_id")
                ):
                    return CARDINALITY_LOW

    return CARDINALITY_MEDIUM


def get_state(
    sql: str,
    cardinality: float | int | None = None,
    cardinality_bucket: int | None = None,
) -> tuple[int, int, int]:
    """
    Extract a discrete state tuple from SQL:
    (detected_pattern, has_selective_outer_filter, cardinality_bucket)

    detected_pattern:
      0: None (PATTERN_NONE)
      1: Correlated subquery in SELECT projection (PATTERN_SUBQUERY)
      2: NOT IN subquery in WHERE (PATTERN_NOT_IN)
      3: Single top-level OR in WHERE (PATTERN_OR)

    has_selective_outer_filter:
      1: Outer query has a selective filter expected to reduce outer rows (often favoring DO_NOTHING)
      0: Outer query has no outer filter or full scan

    cardinality_bucket:
      0: CARDINALITY_LOW    (point lookup, selective PK filter, or <= 100 rows)
      1: CARDINALITY_MEDIUM (range/category filter, 101 - 10,000 rows)
      2: CARDINALITY_HIGH   (unfiltered full scan, > 10,000 rows)
    """
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return (PATTERN_NONE, 0, CARDINALITY_HIGH)

    # 1. Detect Pattern (Priority Order)
    pattern = PATTERN_NONE
    for item in tree.expressions:
        if item.find(exp.Select):
            pattern = PATTERN_SUBQUERY
            break

    if pattern == PATTERN_NONE:
        for node in tree.find_all(exp.In):
            parent = node.parent
            is_not = bool(node.args.get("is_not")) or isinstance(parent, exp.Not)
            if is_not and node.args.get("query"):
                pattern = PATTERN_NOT_IN
                break

    if pattern == PATTERN_NONE:
        outer_where = tree.args.get("where")
        if outer_where and outer_where.find(exp.Or):
            pattern = PATTERN_OR

    # 2. Detect Outer Filter
    has_filter = 0
    outer_where = tree.args.get("where")
    if outer_where:
        if pattern == PATTERN_SUBQUERY:
            # For correlated subqueries in SELECT projection, any outer WHERE predicate is an outer filter
            has_filter = 1
        elif pattern == PATTERN_NOT_IN:
            # For NOT IN, check if there are outer predicates other than the NOT IN clause itself
            for node in outer_where.find_all(FILTER_PREDICATES):
                ancestor_select = node.find_ancestor(exp.Select)
                if ancestor_select is None or ancestor_select == tree:
                    if not (
                        isinstance(node, exp.In)
                        and (node.args.get("is_not") or isinstance(node.parent, exp.Not))
                    ):
                        has_filter = 1
                        break
        elif pattern == PATTERN_OR:
            # For OR disjunctions, check if there are outer conjuncts outside the OR clause
            or_node = outer_where.find(exp.Or)
            for node in outer_where.find_all(FILTER_PREDICATES):
                ancestor_select = node.find_ancestor(exp.Select)
                if ancestor_select is None or ancestor_select == tree:
                    if or_node and not node.find_ancestor(exp.Or):
                        has_filter = 1
                        break
        else:
            has_filter = 1

    # 3. Determine Cardinality Bucket
    if cardinality_bucket is not None:
        card_bucket = int(cardinality_bucket)
    elif cardinality is not None:
        card_bucket = cardinality_to_bucket(cardinality)
    else:
        card_bucket = _estimate_static_cardinality_bucket(tree, pattern, has_filter)

    return (pattern, has_filter, card_bucket)

