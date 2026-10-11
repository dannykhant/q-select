"""Public API of the query rewriter module."""

from .constants import (
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
)
from .rewriters import (
    rewrite_subquery_to_join,
    rewrite_not_in_to_anti_join,
    rewrite_or_to_union,
    apply_action,
)
from .patterns import get_state, cardinality_to_bucket
from .nullability import is_non_nullable, register_non_nullable

__all__ = [
    "ACTIONS",
    "ACTION_TO_IDX",
    "PATTERN_NONE",
    "PATTERN_SUBQUERY",
    "PATTERN_NOT_IN",
    "PATTERN_OR",
    "CARDINALITY_LOW",
    "CARDINALITY_MEDIUM",
    "CARDINALITY_HIGH",
    "CARDINALITY_BUCKETS",
    "rewrite_subquery_to_join",
    "rewrite_not_in_to_anti_join",
    "rewrite_or_to_union",
    "apply_action",
    "get_state",
    "cardinality_to_bucket",
    "is_non_nullable",
    "register_non_nullable",
]
