"""FastAPI application serving the Q-Select rewrite policy."""

from __future__ import annotations

from contextlib import asynccontextmanager

import sqlglot
from fastapi import FastAPI, HTTPException, Request
from sqlglot import exp

from rewriter import (
    ACTIONS,
    PATTERN_NOT_IN,
    PATTERN_OR,
    PATTERN_SUBQUERY,
    apply_action,
)
from .config import Settings
from .db import execute_query, results_match
from .planner import planner_cardinality
from .policy import Policy
from .schemas import (
    ExecuteRequest,
    ExecuteResponse,
    ExecutionStats,
    HealthResponse,
    PolicyEntry,
    PolicyResponse,
    RewriteRequest,
    RewriteResponse,
    StateModel,
)

# Baseline 2 ("always rewrite"): unconditionally apply the pattern's rewrite.
HEURISTIC_ACTION = {
    PATTERN_SUBQUERY: "SUBQUERY_TO_JOIN",
    PATTERN_NOT_IN: "NOT_IN_TO_ANTI_JOIN",
    PATTERN_OR: "OR_TO_UNION",
}


def _parse_select(sql: str):
    """Return the parsed query for a single SELECT/UNION statement, else None."""
    candidate = sql.strip().rstrip(";").strip()
    if not candidate:
        return None
    try:
        tree = sqlglot.parse_one(candidate, read="postgres")
    except Exception:
        return None
    if not isinstance(tree, exp.Query):
        return None
    return tree


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings
    app.state.policy = Policy.load(settings.table_path)
    app.state.planner = (
        (lambda sql: planner_cardinality(settings.dsn, sql)) if settings.planner_estimate else None
    )
    yield


app = FastAPI(title="Q-Select", version="0.1.0", lifespan=lifespan)


def _state_model(state: tuple) -> StateModel:
    pattern, has_selective_filter, cardinality_bucket = state
    return StateModel(
        pattern=pattern,
        has_selective_filter=has_selective_filter,
        cardinality_bucket=cardinality_bucket,
    )


def _decision(request: Request, sql: str, estimated_rows: float | None) -> dict:
    if _parse_select(sql) is None:
        raise HTTPException(status_code=422, detail="Only a single SELECT/UNION query is supported.")
    policy: Policy = request.app.state.policy
    planner = request.app.state.planner
    return policy.predict(sql, estimated_rows=estimated_rows, planner=planner)


@app.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    policy: Policy = request.app.state.policy
    return HealthResponse(
        status="ok",
        states=policy.states,
        actions=list(ACTIONS),
        table_path=str(settings.table_path),
        planner_estimate=settings.planner_estimate,
    )


@app.get("/policy", response_model=PolicyResponse)
def policy_table(request: Request) -> PolicyResponse:
    policy: Policy = request.app.state.policy
    entries = [
        PolicyEntry(
            state=",".join(str(int(x)) for x in state),
            q_values=[float(v) for v in q_values],
            action=ACTIONS[int(q_values.argmax())],
        )
        for state, q_values in sorted(policy.q_table.items())
    ]
    return PolicyResponse(actions=list(ACTIONS), meta=policy.meta, states=entries)


@app.post("/rewrite", response_model=RewriteResponse)
def rewrite(req: RewriteRequest, request: Request) -> RewriteResponse:
    decision = _decision(request, req.sql, req.estimated_rows)
    return RewriteResponse(
        state=_state_model(decision["state"]),
        state_known=decision["state_known"],
        q_values=decision["q_values"],
        estimated_rows=decision["estimated_rows"],
        estimate_source=decision["estimate_source"],
        action=decision["action"],
        rewritten_sql=decision["rewritten_sql"],
        changed=decision["changed"],
    )


@app.post("/execute", response_model=ExecuteResponse)
def execute(req: ExecuteRequest, request: Request) -> ExecuteResponse:
    settings = request.app.state.settings
    decision = _decision(request, req.sql, req.estimated_rows)

    timeout = req.timeout_sec or settings.default_timeout_sec
    warmups = settings.default_warmups if req.warmups is None else req.warmups
    repeats = settings.default_repeats if req.repeats is None else req.repeats

    def run(sql: str):
        elapsed, rows, ok = execute_query(settings.dsn, sql, timeout, warmups, repeats)
        stats = ExecutionStats(
            median_ms=elapsed,
            row_count=(len(rows) if rows is not None else None),
            success=ok,
        )
        return stats, rows, ok

    # Baseline 1: Original PostgreSQL (no rewrite)
    original, r_orig, ok_orig = run(req.sql)
    if not ok_orig:
        raise HTTPException(status_code=400, detail="Original query failed or timed out.")

    # Condition 3: Q-learning agent (greedy policy)
    if decision["changed"]:
        rewritten, r_rw, ok_rw = run(decision["rewritten_sql"])
        match = bool(ok_rw and results_match(r_orig, r_rw))
        speedup = (original.median_ms / rewritten.median_ms) if (ok_rw and rewritten.median_ms) else None
    else:
        rewritten = ExecutionStats(
            median_ms=original.median_ms, row_count=original.row_count, success=True
        )
        match = True
        speedup = 1.0

    # Baseline 2: Always Rewrite (heuristic)
    heuristic_action = HEURISTIC_ACTION.get(decision["state"][0], "DO_NOTHING")
    heuristic_sql = apply_action(req.sql, heuristic_action)
    if heuristic_sql != req.sql:
        heuristic, _, _ = run(heuristic_sql)
    else:
        heuristic_action = "DO_NOTHING"
        heuristic = ExecutionStats(
            median_ms=original.median_ms, row_count=original.row_count, success=True
        )

    return ExecuteResponse(
        state=_state_model(decision["state"]),
        state_known=decision["state_known"],
        q_values=decision["q_values"],
        estimated_rows=decision["estimated_rows"],
        estimate_source=decision["estimate_source"],
        action=decision["action"],
        rewritten_sql=decision["rewritten_sql"],
        changed=decision["changed"],
        original=original,
        heuristic=heuristic,
        heuristic_action=heuristic_action,
        rewritten=rewritten,
        results_match=match,
        speedup=speedup,
    )
