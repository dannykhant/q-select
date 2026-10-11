"""Shared constants for actions and detected query patterns."""

ACTIONS = ["DO_NOTHING", "SUBQUERY_TO_JOIN", "NOT_IN_TO_ANTI_JOIN", "OR_TO_UNION"]
ACTION_TO_IDX = {a: i for i, a in enumerate(ACTIONS)}

# Detected pattern IDs (used in state tuple)
PATTERN_NONE = 0
PATTERN_SUBQUERY = 1
PATTERN_NOT_IN = 2
PATTERN_OR = 3

# Cardinality bucket IDs (used in state tuple)
CARDINALITY_LOW = 0     # point lookup / small input (e.g. PK equality, LIMIT 1, <= 100 rows)
CARDINALITY_MEDIUM = 1  # filtered subset (e.g. range/category filter, 101 - 10,000 rows)
CARDINALITY_HIGH = 2    # unfiltered scan / large input (> 10,000 rows)
CARDINALITY_BUCKETS = [CARDINALITY_LOW, CARDINALITY_MEDIUM, CARDINALITY_HIGH]

