"""Non-nullability registry and guard checks."""

# Default: TPC-H primary keys and foreign keys used in the 3 rewrite patterns.
# Keys are lowercase (table_or_alias_name, column_name).
_NON_NULLABLE: set[tuple[str, str]] = {
    # Full table names
    ("customer", "c_custkey"),
    ("customer", "c_nationkey"),
    ("orders", "o_custkey"),
    ("orders", "o_orderkey"),
    ("lineitem", "l_orderkey"),
    ("lineitem", "l_partkey"),
    ("lineitem", "l_suppkey"),
    ("part", "p_partkey"),
    ("supplier", "s_suppkey"),
    ("nation", "n_nationkey"),
    ("region", "r_regionkey"),
    # Standard TPC-H 1-letter aliases
    ("c", "c_custkey"),
    ("c", "c_nationkey"),
    ("o", "o_custkey"),
    ("o", "o_orderkey"),
    ("l", "l_orderkey"),
    ("l", "l_partkey"),
    ("l", "l_suppkey"),
    ("p", "p_partkey"),
    ("s", "s_suppkey"),
    ("n", "n_nationkey"),
    ("r", "r_regionkey"),
}


def is_non_nullable(table_or_alias: str, column: str) -> bool:
    """Return True when (table_or_alias, column) is provably non-nullable."""
    return (table_or_alias.lower(), column.lower()) in _NON_NULLABLE


def register_non_nullable(table_or_alias: str, column: str) -> None:
    """Allow callers to extend the registry for custom schemas or aliases."""
    _NON_NULLABLE.add((table_or_alias.lower(), column.lower()))
