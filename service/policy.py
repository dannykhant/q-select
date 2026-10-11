"""Load a trained tabular Q-table and select semantics-preserving rewrite actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import numpy as np

from rewriter import ACTIONS, apply_action, get_state


def state_to_key(state: tuple) -> str:
    """Serialize a state tuple to the Q-table's comma-joined string key."""
    return ",".join(str(int(x)) for x in state)


def key_to_state(key: str) -> tuple:
    """Parse a comma-joined string key back into an int state tuple."""
    return tuple(int(x) for x in key.split(","))


class Policy:
    """Greedy tabular Q-learning policy loaded from an exported Q-table."""

    def __init__(self, q_table: dict, meta: dict | None = None):
        self.q_table = {
            key_to_state(key): np.asarray(values, dtype=np.float32)
            for key, values in q_table.items()
        }
        self.meta = meta or {}

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"Q-table not found at {path}. Run the training notebook to produce "
                "artifacts/q_table.json before starting the service."
            )

        payload = json.loads(path.read_text())
        meta = payload.get("meta", {})
        actions = list(meta.get("actions", ACTIONS))
        if actions != list(ACTIONS):
            raise ValueError(f"Q-table actions {actions} do not match ACTIONS {list(ACTIONS)}.")

        q_table = payload.get("q_table", {})
        if not q_table:
            raise ValueError(f"Q-table at {path} is empty.")
        return cls(q_table, meta)

    @property
    def states(self) -> int:
        return len(self.q_table)

    def get_q(self, state: tuple) -> np.ndarray:
        """Q-values for a state; unseen states default to zeros (safe DO_NOTHING)."""
        values = self.q_table.get(state)
        if values is None:
            return np.zeros(len(ACTIONS), dtype=np.float32)
        return values

    def select_action(self, state: tuple) -> str:
        return ACTIONS[int(np.argmax(self.get_q(state)))]

    def predict(
        self,
        sql: str,
        estimated_rows: float | None = None,
        planner: Callable[[str], float | None] | None = None,
    ) -> dict:
        """
        Choose a rewrite action for `sql` and return the decision.

        Cardinality is resolved automatically, mirroring training: use PostgreSQL's planner
        estimate (`EXPLAIN (FORMAT JSON)`) when available, otherwise fall back to the static AST
        estimate. An explicit positive `estimated_rows` still overrides this if supplied
        programmatically; non-positive values are ignored (PostgreSQL never estimates < 1 row).
        """
        if estimated_rows is not None and estimated_rows > 0:
            cardinality = float(estimated_rows)
            estimate_source = "provided"
        elif planner is not None:
            cardinality = planner(sql)
            estimate_source = "planner" if cardinality is not None else "static"
        else:
            cardinality = None
            estimate_source = "static"

        state = get_state(sql, cardinality=cardinality)
        q_values = self.get_q(state)
        state_known = state in self.q_table
        action = ACTIONS[int(np.argmax(q_values))]
        rewritten_sql = apply_action(sql, action)
        return {
            "state": state,
            "state_known": state_known,
            "q_values": [float(v) for v in q_values],
            "estimated_rows": float(cardinality) if cardinality is not None else None,
            "estimate_source": estimate_source,
            "action": action,
            "rewritten_sql": rewritten_sql,
            "changed": rewritten_sql != sql,
        }
