import json

import pytest
from fastapi.testclient import TestClient

from rewriter import ACTIONS
from service.app import app


def _write_qtable(path):
    q_table = {
        "1,0,2": [0, 5, 0, 0],  # SUBQUERY -> SUBQUERY_TO_JOIN
        "1,1,0": [5, 0, 0, 0],  # SUBQUERY, selective, LOW -> DO_NOTHING
        "1,1,1": [0, 5, 0, 0],  # SUBQUERY, selective, MEDIUM -> SUBQUERY_TO_JOIN
        "2,1,2": [0, 0, 5, 0],  # NOT_IN -> NOT_IN_TO_ANTI_JOIN
        "3,0,2": [0, 0, 0, 5],  # OR -> OR_TO_UNION
    }
    path.write_text(json.dumps({"meta": {"actions": list(ACTIONS)}, "q_table": q_table}))
    return path


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("QS_TABLE_PATH", str(_write_qtable(tmp_path / "q.json")))
    monkeypatch.setenv("QS_PLANNER_ESTIMATE", "false")
    with TestClient(app) as test_client:
        yield test_client


SUBQUERY_SQL = (
    "SELECT c.c_name, (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) "
    "FROM customer c"
)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["states"] == 5
    assert body["actions"] == list(ACTIONS)
    assert body["planner_estimate"] is False


def test_rewrite_subquery(client):
    resp = client.post("/rewrite", json={"sql": SUBQUERY_SQL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "SUBQUERY_TO_JOIN"
    assert body["changed"] is True
    assert body["state"]["pattern"] == 1
    assert body["state_known"] is True
    assert len(body["q_values"]) == 4
    assert "LEFT JOIN" in body["rewritten_sql"].upper()


def test_rewrite_reports_unknown_state(client):
    # SELECT * FROM customer -> state (0,0,2): not in the fixture Q-table
    body = client.post("/rewrite", json={"sql": "SELECT * FROM customer"}).json()
    assert body["state_known"] is False
    assert body["q_values"] == [0.0, 0.0, 0.0, 0.0]
    assert body["action"] == "DO_NOTHING"


def test_rewrite_reports_estimate_source(client):
    # planner disabled in the fixture -> static estimate
    static = client.post("/rewrite", json={"sql": SUBQUERY_SQL}).json()
    assert static["estimate_source"] == "static"
    assert static["estimated_rows"] is None

    provided = client.post("/rewrite", json={"sql": SUBQUERY_SQL, "estimated_rows": 500}).json()
    assert provided["estimate_source"] == "provided"
    assert provided["estimated_rows"] == 500.0


def test_policy_endpoint_lists_learned_states(client):
    resp = client.get("/policy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["actions"] == list(ACTIONS)
    states = {entry["state"]: entry for entry in body["states"]}
    assert len(states) == 5
    assert states["1,1,1"]["action"] == "SUBQUERY_TO_JOIN"


def test_rewrite_do_nothing(client):
    resp = client.post("/rewrite", json={"sql": "SELECT * FROM customer"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "DO_NOTHING"
    assert body["changed"] is False


def test_rewrite_rejects_non_select(client):
    resp = client.post("/rewrite", json={"sql": "DELETE FROM customer"})
    assert resp.status_code == 422


def test_rewrite_rejects_malformed_sql(client):
    resp = client.post("/rewrite", json={"sql": "SELECT FROM WHERE"})
    assert resp.status_code == 422


def test_rewrite_requires_sql(client):
    resp = client.post("/rewrite", json={})
    assert resp.status_code == 422


def test_rewrite_ignores_non_positive_estimate(client):
    sql = (
        "SELECT c.c_custkey, c.c_name, "
        "(SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt "
        "FROM customer c WHERE c.c_nationkey = 3"
    )

    low = client.post("/rewrite", json={"sql": sql, "estimated_rows": 50}).json()
    assert low["state"]["cardinality_bucket"] == 0
    assert low["action"] == "DO_NOTHING"

    # estimated_rows=0 is invalid and ignored -> static MEDIUM estimate
    zero = client.post("/rewrite", json={"sql": sql, "estimated_rows": 0}).json()
    assert zero["state"]["cardinality_bucket"] == 1
    assert zero["action"] == "SUBQUERY_TO_JOIN"


def test_execute_reports_speedup(client, monkeypatch):
    def fake_execute(dsn, sql, timeout_sec=15.0, warmups=2, repeats=5):
        fast = any(k in sql.upper() for k in ("LEFT JOIN", "NOT EXISTS", "UNION ALL"))
        return (5.0 if fast else 20.0), [(1,), (2,)], True

    monkeypatch.setattr("service.app.execute_query", fake_execute)

    resp = client.post("/execute", json={"sql": SUBQUERY_SQL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "SUBQUERY_TO_JOIN"
    assert body["original"]["median_ms"] == 20.0
    assert body["rewritten"]["median_ms"] == 5.0
    assert body["heuristic"]["median_ms"] == 5.0
    assert body["heuristic_action"] == "SUBQUERY_TO_JOIN"
    assert body["results_match"] is True
    assert body["speedup"] == 4.0


def test_execute_heuristic_vs_agent_regression_avoidance(client, monkeypatch):
    def fake_execute(dsn, sql, timeout_sec=15.0, warmups=2, repeats=5):
        fast = any(k in sql.upper() for k in ("LEFT JOIN", "NOT EXISTS", "UNION ALL"))
        return (5.0 if fast else 20.0), [(1,), (2,)], True

    monkeypatch.setattr("service.app.execute_query", fake_execute)

    # PK lookup -> state (1,1,0) -> agent DO_NOTHING; heuristic still rewrites (faster here).
    sql = (
        "SELECT c.c_custkey, c.c_name, "
        "(SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt "
        "FROM customer c WHERE c.c_custkey = 1601"
    )
    body = client.post("/execute", json={"sql": sql}).json()
    assert body["action"] == "DO_NOTHING"
    assert body["heuristic_action"] == "SUBQUERY_TO_JOIN"
    assert body["original"]["median_ms"] == 20.0
    assert body["rewritten"]["median_ms"] == 20.0
    assert body["heuristic"]["median_ms"] == 5.0
