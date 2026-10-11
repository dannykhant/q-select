"""Streamlit UI: submit a query, see the chosen action and rewrite-vs-default timing."""

from __future__ import annotations

import os
import sys

import pandas as pd
import streamlit as st

# Ensure the repo root is importable when run via `streamlit run frontend/app.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frontend.client import FrontendError, analyze, health  # noqa: E402
from rewriter import ACTIONS  # noqa: E402

DEFAULT_SQL = """SELECT c.c_custkey, c.c_name,
       (SELECT COUNT(*) FROM orders o WHERE o.o_custkey = c.c_custkey) AS cnt
FROM customer c
WHERE c.c_nationkey = 3"""

API_URL = os.environ.get("QS_API_URL", "http://localhost:8000")


def _fmt_ms(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f} ms"


def main() -> None:
    st.set_page_config(page_title="Q-Select", page_icon="🔍", layout="wide")
    st.title("Q-Select — PostgreSQL Query Rewriter")
    st.caption(
        "Tabular Q-learning agent that decides when to apply semantics-preserving SQL rewrites."
    )
    st.page_link(
        "pages/1_Example_Queries.py",
        label="Not sure what to try? Browse example queries —>",
        icon="📚",
    )

    with st.sidebar:
        st.header("Backend")
        st.code(API_URL, language=None)
        if st.button("Check API health"):
            try:
                st.success(f"OK — {health(API_URL)['states']} states loaded")
            except FrontendError as exc:
                st.error(str(exc))

    st.session_state.setdefault("sql_input", DEFAULT_SQL)
    auto_run = st.session_state.pop("auto_run", False)

    st.caption(
        "The cardinality estimate is derived automatically from PostgreSQL `EXPLAIN` "
        "(exactly as during training) — no manual input needed."
    )

    with st.form("query_form"):
        sql = st.text_area("SQL query", key="sql_input", height=180)
        submitted = st.form_submit_button("Run (or press Ctrl/Cmd+Enter)")

    if not (submitted or auto_run):
        return

    sql = sql.strip()
    if not sql:
        st.warning("Enter a SQL query first.")
        return

    with st.spinner("Running the original and rewritten query..."):
        try:
            result = analyze(API_URL, sql)
        except FrontendError as exc:
            st.error(str(exc))
            return

    state = result["state"]
    action = result["action"]
    original = result["original"]
    heuristic = result["heuristic"]
    heuristic_action = result.get("heuristic_action", "DO_NOTHING")
    rewritten = result["rewritten"]
    results_match = result["results_match"]

    def _ratio(base: float | None, value: float | None) -> float | None:
        return (base / value) if (base and value) else None

    agent_vs_orig = _ratio(original["median_ms"], rewritten["median_ms"])
    agent_vs_heur = _ratio(heuristic["median_ms"], rewritten["median_ms"])

    col1, col2, col3 = st.columns(3)
    col1.metric("① Original PostgreSQL", _fmt_ms(original["median_ms"]))
    col2.metric(f"② Always rewrite · {heuristic_action}", _fmt_ms(heuristic["median_ms"]))
    col3.metric(
        f"③ Q-learning agent · {action}",
        _fmt_ms(rewritten["median_ms"]),
        delta=(f"{agent_vs_orig:.2f}x vs original" if agent_vs_orig else None),
    )

    speed_bits = []
    if agent_vs_orig:
        speed_bits.append(f"{agent_vs_orig:.2f}x vs Original")
    if agent_vs_heur:
        speed_bits.append(f"{agent_vs_heur:.2f}x vs Always-Rewrite")
    if speed_bits:
        st.caption("Agent: " + " · ".join(speed_bits))

    bucket_label = {0: "low", 1: "medium", 2: "high"}.get(state["cardinality_bucket"], "?")
    est_rows = result.get("estimated_rows")
    est_source = result.get("estimate_source", "static")
    estimate_note = (
        f" · estimate = {est_source} ({est_rows:,.0f} rows)"
        if est_rows is not None
        else f" · estimate = {est_source}"
    )
    st.caption(
        f"state = (pattern={state['pattern']}, "
        f"has_selective_filter={state['has_selective_filter']}, "
        f"cardinality_bucket={state['cardinality_bucket']} [{bucket_label}]){estimate_note}"
    )

    heur_regressed = bool(
        original["median_ms"] and heuristic["median_ms"]
        and heuristic["median_ms"] > original["median_ms"] * 1.05
    )
    agent_regressed = bool(
        original["median_ms"] and rewritten["median_ms"]
        and rewritten["median_ms"] > original["median_ms"] * 1.05
    )
    if heuristic_action != "DO_NOTHING" and heur_regressed and not agent_regressed:
        st.success(
            "Regression avoidance: Always-Rewrite is slower than Original on this query, while the "
            "agent kept the default plan (or a faster one)."
        )
    elif heuristic_action != "DO_NOTHING" and heur_regressed and agent_regressed:
        st.warning("Both Always-Rewrite and the agent regressed on this query.")

    if result["changed"] and results_match:
        st.success("Agent rewrite is equivalent to the original (multiset match).")
    elif result["changed"] and not results_match:
        st.error("Agent rewrite produced a different result set.")
    elif not result.get("state_known", True):
        st.warning(
            "The agent has no learned value for this state, so it defaults to DO_NOTHING."
        )
    else:
        st.info("The agent kept PostgreSQL's default plan (DO_NOTHING).")

    q_values = result.get("q_values")
    if q_values:
        with st.expander("Policy values for this state"):
            st.dataframe(
                pd.DataFrame({"action": ACTIONS, "Q": q_values}).sort_values("Q", ascending=False),
                hide_index=True,
            )

    st.subheader("Rewritten SQL")
    st.code(result["rewritten_sql"], language="sql")

    st.subheader("Execution time — two baselines vs. the agent")
    labels = [
        "① Original PostgreSQL",
        f"② Always rewrite ({heuristic_action})",
        f"③ Q-learning agent ({action})",
    ]
    st.bar_chart(
        pd.DataFrame(
            {"median ms": [original["median_ms"] or 0.0, heuristic["median_ms"] or 0.0, rewritten["median_ms"] or 0.0]},
            index=labels,
        )
    )
    st.dataframe(
        pd.DataFrame(
            {
                "variant": labels,
                "median_ms": [original["median_ms"], heuristic["median_ms"], rewritten["median_ms"]],
                "rows": [original["row_count"], heuristic["row_count"], rewritten["row_count"]],
                "success": [original["success"], heuristic["success"], rewritten["success"]],
            }
        ),
        hide_index=True,
    )

    with st.expander("Original SQL"):
        st.code(sql, language="sql")


main()
