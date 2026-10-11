from frontend.examples import EXAMPLE_QUERIES
from rewriter import PATTERN_NOT_IN, PATTERN_OR, PATTERN_SUBQUERY, get_state

EXPECTED_STATES = {
    "subquery_full_scan": (PATTERN_SUBQUERY, 1, 1),
    "subquery_filtered": (PATTERN_SUBQUERY, 1, 0),
    "subquery_unfiltered": (PATTERN_SUBQUERY, 0, 2),
    "not_in_full": (PATTERN_NOT_IN, 1, 1),
    "not_in_filtered": (PATTERN_NOT_IN, 1, 0),
    "or_cross_table": (PATTERN_OR, 0, 2),
}


def test_examples_cover_all_types():
    assert {e["type"] for e in EXAMPLE_QUERIES} == set(EXPECTED_STATES)


def test_examples_have_required_fields():
    for example in EXAMPLE_QUERIES:
        assert example["title"].strip()
        assert example["description"].strip()
        assert example["highlight"].strip()
        assert example["sql"].strip()


def test_examples_detected_states_match_expected():
    for example in EXAMPLE_QUERIES:
        assert get_state(example["sql"]) == EXPECTED_STATES[example["type"]]
