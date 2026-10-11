import json

import pytest

from rewriter import ACTIONS
from service.policy import Policy, key_to_state, state_to_key


def _write_qtable(path, q_table):
    path.write_text(json.dumps({"meta": {"actions": list(ACTIONS)}, "q_table": q_table}))
    return path


Q_TABLE = {
    "1,0,2": [0, 5, 0, 0],   # SUBQUERY pattern -> SUBQUERY_TO_JOIN
    "1,1,0": [0, 5, 0, 0],   # SUBQUERY, selective, LOW -> SUBQUERY_TO_JOIN
    "2,1,2": [0, 0, 5, 0],   # NOT_IN pattern -> NOT_IN_TO_ANTI_JOIN
    "3,0,2": [0, 0, 0, 5],   # OR pattern -> OR_TO_UNION
}


def test_state_key_round_trip():
    assert state_to_key((1, 0, 2)) == "1,0,2"
    assert key_to_state("2,1,0") == (2, 1, 0)


def test_load_and_select_action(tmp_path):
    policy = Policy.load(_write_qtable(tmp_path / "q.json", Q_TABLE))

    assert policy.states == 4
    assert policy.select_action((1, 0, 2)) == "SUBQUERY_TO_JOIN"
    assert policy.select_action((2, 1, 2)) == "NOT_IN_TO_ANTI_JOIN"
    assert policy.select_action((3, 0, 2)) == "OR_TO_UNION"


def test_unseen_state_defaults_to_do_nothing(tmp_path):
    policy = Policy.load(_write_qtable(tmp_path / "q.json", Q_TABLE))
    assert policy.select_action((0, 0, 0)) == "DO_NOTHING"


def test_predict_uses_static_state_and_reports_change(tmp_path):
    policy = Policy.load(_write_qtable(tmp_path / "q.json", Q_TABLE))
    sql = (
        "SELECT c.c_name, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) "
        "FROM customer c"
    )
    decision = policy.predict(sql)

    assert decision["state"] == (1, 0, 2)
    assert decision["action"] == "SUBQUERY_TO_JOIN"
    assert decision["changed"] is True
    assert "LEFT JOIN" in decision["rewritten_sql"].upper()


def test_predict_do_nothing_leaves_sql_unchanged(tmp_path):
    policy = Policy.load(_write_qtable(tmp_path / "q.json", Q_TABLE))
    sql = "SELECT * FROM customer"
    decision = policy.predict(sql)

    assert decision["action"] == "DO_NOTHING"
    assert decision["changed"] is False
    assert decision["rewritten_sql"] == sql


def test_predict_reports_state_known_and_q_values(tmp_path):
    policy = Policy.load(_write_qtable(tmp_path / "q.json", Q_TABLE))

    known = policy.predict(
        "SELECT c.c_name, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) "
        "FROM customer c"
    )
    assert known["state"] == (1, 0, 2)
    assert known["state_known"] is True
    assert known["q_values"] == [0.0, 5.0, 0.0, 0.0]

    unknown = policy.predict("SELECT * FROM customer")
    assert unknown["state_known"] is False
    assert unknown["q_values"] == [0.0, 0.0, 0.0, 0.0]


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Policy.load(tmp_path / "does_not_exist.json")


def test_load_action_mismatch_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"meta": {"actions": ["DO_NOTHING"]}, "q_table": {"0,0,0": [0]}}))
    with pytest.raises(ValueError):
        Policy.load(path)


def test_predict_estimate_source(tmp_path):
    policy = Policy.load(_write_qtable(tmp_path / "q.json", Q_TABLE))
    sql = (
        "SELECT c.c_name, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) "
        "FROM customer c"
    )

    provided = policy.predict(sql, estimated_rows=500)
    assert provided["estimate_source"] == "provided"
    assert provided["estimated_rows"] == 500.0

    planner = policy.predict(sql, planner=lambda s: 12345.0)
    assert planner["estimate_source"] == "planner"
    assert planner["estimated_rows"] == 12345.0

    fallback = policy.predict(sql, planner=lambda s: None)
    assert fallback["estimate_source"] == "static"
    assert fallback["estimated_rows"] is None

    static = policy.predict(sql)
    assert static["estimate_source"] == "static"


def test_non_positive_estimate_is_ignored(tmp_path):
    q_table = {
        "1,1,0": [9, 0, 0, 0],  # LOW bucket (point lookup) -> DO_NOTHING
        "1,1,1": [0, 9, 0, 0],  # MEDIUM bucket -> SUBQUERY_TO_JOIN
    }
    policy = Policy.load(_write_qtable(tmp_path / "q.json", q_table))
    sql = (
        "SELECT c.c_custkey, c.c_name, "
        "(SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt "
        "FROM customer c WHERE c.c_nationkey = 3"
    )

    low = policy.predict(sql, estimated_rows=50)
    assert low["state"] == (1, 1, 0)
    assert low["action"] == "DO_NOTHING"

    # 0 is not a valid planner estimate, so it is ignored -> static MEDIUM estimate
    zero = policy.predict(sql, estimated_rows=0)
    assert zero["state"] == (1, 1, 1)
    assert zero["action"] == "SUBQUERY_TO_JOIN"
