"""Environment-driven configuration for the Q-Select API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_dsn() -> str:
    return (
        f"host={os.environ.get('PGHOST', 'localhost')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"dbname={os.environ.get('PGDATABASE', 'tpch_sf1')} "
        f"user={os.environ.get('PGUSER', 'postgres')} "
        f"password={os.environ.get('PGPASSWORD', 'postgres')}"
    )


@dataclass(frozen=True)
class Settings:
    table_path: Path
    planner_estimate: bool
    dsn: str
    default_timeout_sec: float
    default_warmups: int
    default_repeats: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            table_path=Path(os.environ.get("QS_TABLE_PATH", "artifacts/q_table.json")),
            planner_estimate=_env_bool("QS_PLANNER_ESTIMATE", True),
            dsn=os.environ.get("QS_DSN") or _default_dsn(),
            default_timeout_sec=float(os.environ.get("QS_QUERY_TIMEOUT_SEC", "15")),
            default_warmups=int(os.environ.get("QS_QUERY_WARMUPS", "2")),
            default_repeats=int(os.environ.get("QS_QUERY_REPEATS", "5")),
        )
