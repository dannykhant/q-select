import json

import httpx
import pytest

from frontend.client import FrontendError, analyze, rewrite

_ANALYZE_PAYLOAD = {
    "state": {"pattern": 1, "has_selective_filter": 0, "cardinality_bucket": 2},
    "action": "SUBQUERY_TO_JOIN",
    "rewritten_sql": "SELECT ...",
    "changed": True,
    "original": {"median_ms": 10.0, "row_count": 2, "success": True},
    "rewritten": {"median_ms": 5.0, "row_count": 2, "success": True},
    "results_match": True,
    "speedup": 2.0,
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_analyze_returns_payload():
    def handler(request):
        assert request.url.path == "/execute"
        return httpx.Response(200, json=_ANALYZE_PAYLOAD)

    result = analyze("http://api", "SELECT 1", client=_client(handler))
    assert result["action"] == "SUBQUERY_TO_JOIN"
    assert result["speedup"] == 2.0


def test_analyze_forwards_estimated_rows():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_ANALYZE_PAYLOAD)

    analyze("http://api", "SELECT 1", estimated_rows=150000, client=_client(handler))
    assert captured["estimated_rows"] == 150000


def test_analyze_omits_estimated_rows_when_unset():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_ANALYZE_PAYLOAD)

    analyze("http://api", "SELECT 1", client=_client(handler))
    assert "estimated_rows" not in captured


def test_rewrite_returns_payload():
    def handler(request):
        assert request.url.path == "/rewrite"
        return httpx.Response(
            200,
            json={
                "state": {"pattern": 0, "has_selective_filter": 0, "cardinality_bucket": 2},
                "action": "DO_NOTHING",
                "rewritten_sql": "SELECT 1",
                "changed": False,
            },
        )

    result = rewrite("http://api", "SELECT 1", client=_client(handler))
    assert result["action"] == "DO_NOTHING"


def test_error_detail_is_surfaced():
    def handler(request):
        return httpx.Response(422, json={"detail": "Only a single SELECT/UNION query is supported."})

    with pytest.raises(FrontendError, match="SELECT/UNION"):
        analyze("http://api", "DELETE FROM customer", client=_client(handler))


def test_connection_error_is_wrapped():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(FrontendError, match="Could not reach"):
        analyze("http://api", "SELECT 1", client=_client(handler))
