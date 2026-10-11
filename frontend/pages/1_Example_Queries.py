"""Example queries page: browse representative queries with their detected state."""

from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from frontend.examples import EXAMPLE_QUERIES  # noqa: E402
from rewriter import get_state  # noqa: E402

st.set_page_config(page_title="Example Queries · Q-Select", page_icon="📚", layout="wide")

st.title("Example Queries")
st.caption(
    "Each example is chosen to make the three columns visible in the tester: "
    "**① Original PostgreSQL**, **② Always-Rewrite (heuristic)**, **③ Q-learning agent**. "
    "Click **Test this query** to load it into the tester and run it."
)

for example in EXAMPLE_QUERIES:
    pattern, has_filter, card_bucket = get_state(example["sql"])

    with st.container(border=True):
        st.subheader(example["title"])
        st.write(example["description"])
        st.info(f"What to look for — {example['highlight']}")

        info, action_col = st.columns([3, 1])
        with info:
            st.caption(
                f"type = `{example['type']}`  ·  "
                f"static state = (pattern={pattern}, has_filter={has_filter}, "
                f"cardinality_bucket={card_bucket})"
            )
        with action_col:
            if st.button("Test this query", key=f"test-{example['type']}", use_container_width=True):
                st.session_state["sql_input"] = example["sql"]
                st.session_state["auto_run"] = True
                st.switch_page("app.py")

        st.code(example["sql"], language="sql")
