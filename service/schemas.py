"""Pydantic request/response models for the Q-Select API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RewriteRequest(BaseModel):
    sql: str = Field(..., min_length=1, description="SQL query to rewrite")
    estimated_rows: float | None = Field(
        None, ge=0, description="Optional planner row estimate (skips EXPLAIN)"
    )


class ExecuteRequest(RewriteRequest):
    timeout_sec: float | None = Field(None, gt=0)
    warmups: int | None = Field(None, ge=0)
    repeats: int | None = Field(None, ge=1)


class StateModel(BaseModel):
    pattern: int
    has_selective_filter: int
    cardinality_bucket: int


class RewriteResponse(BaseModel):
    state: StateModel
    state_known: bool
    q_values: list[float]
    estimated_rows: float | None
    estimate_source: str
    action: str
    rewritten_sql: str
    changed: bool


class ExecutionStats(BaseModel):
    median_ms: float | None
    row_count: int | None
    success: bool


class ExecuteResponse(RewriteResponse):
    original: ExecutionStats
    heuristic: ExecutionStats
    heuristic_action: str
    rewritten: ExecutionStats
    results_match: bool
    speedup: float | None


class PolicyEntry(BaseModel):
    state: str
    q_values: list[float]
    action: str


class PolicyResponse(BaseModel):
    actions: list[str]
    meta: dict
    states: list[PolicyEntry]


class HealthResponse(BaseModel):
    status: str
    states: int
    actions: list[str]
    table_path: str
    planner_estimate: bool
