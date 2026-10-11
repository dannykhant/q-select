"""SQL rewrite functions and dispatcher."""

from sqlglot import exp, parse_one
from .nullability import is_non_nullable


def _get_table_alias_map(tree: exp.Expression) -> dict[str, str]:
    """Build a mapping of table aliases to actual table names in the query."""
    mapping = {}
    for table_node in tree.find_all(exp.Table):
        tbl_name = getattr(table_node, "name", "")
        tbl_alias = getattr(table_node, "alias_or_name", tbl_name)
        if tbl_name:
            mapping[tbl_name.lower()] = tbl_name.lower()
        if tbl_alias:
            mapping[tbl_alias.lower()] = tbl_name.lower()
    return mapping


def _flatten_ands(node: exp.Expression) -> list[exp.Expression]:
    """Flatten an AST expression tree composed of AND nodes into a list of conjuncts."""
    if isinstance(node, exp.And):
        return _flatten_ands(node.left) + _flatten_ands(node.right)
    return [node]


def rewrite_subquery_to_join(sql: str) -> str:
    """
    Decorrelate a scalar aggregate subquery in the SELECT list to a LEFT JOIN.
    Preserves all non-correlation inner subquery predicates in the derived table's WHERE clause.
    COUNT aggregates are wrapped with COALESCE(..., 0) to preserve empty-group semantics;
    other aggregates (SUM, AVG, MIN, MAX) remain NULL.

    Transforms:
        SELECT c.c_name, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey AND o.o_orderstatus = 'F') AS cnt
        FROM customer c
    Into:
        SELECT c.c_name, COALESCE(subq.cnt, 0) AS cnt
        FROM customer c
        LEFT JOIN (
            SELECT o_custkey, COUNT(*) AS cnt
            FROM orders o
            WHERE o.o_orderstatus = 'F'
            GROUP BY o_custkey
        ) subq ON c.c_custkey = subq.o_custkey
    """
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return sql

    for i, item in enumerate(tree.expressions):
        subq_node = item.find(exp.Select)
        if subq_node is None:
            continue

        from_clause = subq_node.find(exp.From)
        where_clause = subq_node.find(exp.Where)
        agg_func = subq_node.find(exp.AggFunc)
        if not (from_clause and where_clause and agg_func):
            continue

        from_table_node = from_clause.this
        from_table_name = getattr(from_table_node, "name", str(from_table_node))
        from_table_alias = getattr(from_table_node, "alias_or_name", from_table_name)

        # Split WHERE conjuncts into the correlation equality vs. inner subquery filters
        conjuncts = _flatten_ands(where_clause.this)
        corr_eq = None
        inner_filters = []

        for conj in conjuncts:
            if isinstance(conj, exp.EQ) and corr_eq is None:
                left_table = getattr(conj.this, "table", "")
                right_table = getattr(conj.expression, "table", "")
                is_left_inner = left_table in (from_table_name, from_table_alias)
                is_right_inner = right_table in (from_table_name, from_table_alias)

                if is_left_inner and not is_right_inner:
                    corr_eq = conj
                    inner_col = getattr(conj.this, "name", str(conj.this))
                    outer_ref = conj.expression.sql(dialect="postgres")
                    continue
                elif is_right_inner and not is_left_inner:
                    corr_eq = conj
                    inner_col = getattr(conj.expression, "name", str(corr_eq.expression))
                    outer_ref = conj.this.sql(dialect="postgres")
                    continue

            inner_filters.append(conj)

        if corr_eq is None:
            continue

        alias = getattr(item, "alias", None) or "cnt"
        from_source_sql = from_clause.sql(dialect="postgres").replace("FROM ", "", 1).strip()
        agg_sql = agg_func.sql(dialect="postgres")

        # Build inner WHERE clause if there are non-correlation filters
        if inner_filters:
            inner_where_sql = " WHERE " + " AND ".join(f.sql(dialect="postgres") for f in inner_filters)
        else:
            inner_where_sql = ""

        derived_sql = (
            f"(SELECT {inner_col}, {agg_sql} AS {alias} "
            f"FROM {from_source_sql}{inner_where_sql} GROUP BY {inner_col}) AS subq"
        )

        # COUNT(*) over an empty group returns 0, but the LEFT JOIN yields NULL for unmatched
        # rows, so COALESCE to 0 preserves semantics. All other aggregates (SUM, AVG, MIN, MAX)
        # return NULL over an empty set, so they must NOT be coalesced.
        agg_name = agg_func.key.upper() if hasattr(agg_func, "key") else agg_func.__class__.__name__.upper()
        if agg_name == "COUNT":
            projection_expr = f"COALESCE(subq.{alias}, 0) AS {alias}"
        else:
            projection_expr = f"subq.{alias} AS {alias}"

        tree_copy = tree.copy()
        tree_copy.expressions[i] = parse_one(projection_expr, read="postgres")
        tree_copy = tree_copy.join(
            derived_sql, on=f"{outer_ref} = subq.{inner_col}", join_type="LEFT"
        )
        return tree_copy.sql(dialect="postgres")

    return sql


def _has_is_not_null_guard(where_node: exp.Expression | None, col_name: str) -> bool:
    """Return True if where_node explicitly contains `col IS NOT NULL`."""
    if where_node is None:
        return False
    target = col_name.lower()
    for is_node in where_node.find_all(exp.Is):
        if is_node.args.get("negate") and isinstance(is_node.expression, exp.Null):
            col = is_node.this
            if getattr(col, "name", "").lower() == target:
                return True
    return False


def rewrite_not_in_to_anti_join(sql: str) -> str:
    """
    Rewrite NOT IN (SELECT ...) to NOT EXISTS when BOTH sides are provably non-nullable
    via schema registry or explicit IS NOT NULL guards.
    If nullability cannot be proven, the rewrite is skipped and the original SQL is returned.

    Transforms:
        SELECT c.c_custkey, c.c_name
        FROM customer c
        WHERE c.c_custkey NOT IN (SELECT o.o_custkey FROM orders o)
    Into:
        SELECT c.c_custkey, c.c_name
        FROM customer c
        WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.o_custkey = c.c_custkey)
    """
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return sql

    table_alias_map = _get_table_alias_map(tree)

    for node in tree.find_all(exp.In):
        parent = node.parent
        is_not = bool(node.args.get("is_not")) or isinstance(parent, exp.Not)
        if not is_not:
            continue

        subq = node.args.get("query")
        select_node = subq.find(exp.Select) if isinstance(subq, exp.Subquery) else (subq if isinstance(subq, exp.Select) else None)
        if select_node is None:
            continue

        outer_col_node = node.this
        if not select_node.expressions:
            continue
        inner_col_node = select_node.expressions[0]
        inner_from = select_node.find(exp.From)
        if inner_from is None:
            continue

        outer_table_ref = getattr(outer_col_node, "table", "")
        outer_col = getattr(outer_col_node, "name", "")
        outer_real_table = table_alias_map.get(outer_table_ref.lower(), outer_table_ref)

        inner_table_node = inner_from.this
        inner_table_name = getattr(inner_table_node, "name", str(inner_table_node))
        inner_table_alias = getattr(inner_table_node, "alias_or_name", inner_table_name)
        inner_col = getattr(inner_col_node, "name", "")

        # Nullability guard: both sides must be verified non-nullable
        # (either in the non-null schema registry or explicitly guarded with IS NOT NULL)
        outer_is_non_null = (
            is_non_nullable(outer_table_ref, outer_col)
            or is_non_nullable(outer_real_table, outer_col)
            or _has_is_not_null_guard(tree.args.get("where"), outer_col)
        )
        inner_is_non_null = (
            is_non_nullable(inner_table_name, inner_col)
            or is_non_nullable(inner_table_alias, inner_col)
            or _has_is_not_null_guard(select_node.find(exp.Where), inner_col)
        )

        if not (outer_is_non_null and inner_is_non_null):
            return sql

        from_source_sql = inner_from.sql(dialect="postgres").replace("FROM ", "", 1).strip()
        outer_ref = outer_col_node.sql(dialect="postgres")
        inner_ref = inner_col_node.sql(dialect="postgres")

        inner_where = select_node.find(exp.Where)
        corr_cond = f"{inner_ref} = {outer_ref}"
        if inner_where:
            where_sql = f"{inner_where.this.sql(dialect='postgres')} AND {corr_cond}"
        else:
            where_sql = corr_cond

        not_exists_clause = parse_one(
            f"NOT EXISTS (SELECT 1 FROM {from_source_sql} WHERE {where_sql})",
            read="postgres"
        )

        # Replace target node in tree
        target_to_replace = parent if isinstance(parent, exp.Not) else node
        target_to_replace.replace(not_exists_clause)
        return tree.sql(dialect="postgres")

    return sql


def rewrite_or_to_union(sql: str) -> str:
    """
    Split WHERE ... (P1 OR P2) ... into (SELECT ... WHERE ... P1 ...) UNION ALL (SELECT ... WHERE ... ((P2) AND NOT (P1)) ...).
    Preserves all outer AND conjuncts in both branches.
    """
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return sql

    where = tree.args.get("where")
    if where is None:
        return sql

    or_node = where.find(exp.Or)
    if or_node is None:
        return sql

    p1 = or_node.left
    p2 = or_node.right

    branch1 = tree.copy()
    or_in_b1 = branch1.args["where"].find(exp.Or)
    if or_in_b1:
        or_in_b1.replace(p1.copy())

    branch2 = tree.copy()
    or_in_b2 = branch2.args["where"].find(exp.Or)
    branch2_pred = parse_one(
        f"({p2.sql(dialect='postgres')}) AND NOT ({p1.sql(dialect='postgres')})",
        read="postgres"
    )
    if or_in_b2:
        or_in_b2.replace(branch2_pred)

    union_query = exp.union(branch1, branch2, distinct=False)
    return union_query.sql(dialect="postgres")


_DISPATCH = {
    "SUBQUERY_TO_JOIN": rewrite_subquery_to_join,
    "NOT_IN_TO_ANTI_JOIN": rewrite_not_in_to_anti_join,
    "OR_TO_UNION": rewrite_or_to_union,
}


def apply_action(sql: str, action: str) -> str:
    """Apply the specified action to the query. Returns original SQL if action is DO_NOTHING or unknown."""
    fn = _DISPATCH.get(action)
    return fn(sql) if fn else sql
