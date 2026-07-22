"""Shared helpers for analysis scripts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def resolve_league(
    conn: sqlite3.Connection,
    *,
    league_id: str | None = None,
    year: int | None = None,
    name_contains: str | None = None,
) -> sqlite3.Row:
    """Resolve a single league by id, or by year + name substring."""
    if league_id:
        row = conn.execute(
            "SELECT * FROM leagues WHERE league_id = ?",
            (league_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"No league with league_id={league_id!r}")
        return row

    if year is None and not name_contains:
        raise ValueError("Provide league_id, or year and/or name_contains")

    clauses: list[str] = []
    params: list[object] = []
    if year is not None:
        clauses.append("year = ?")
        params.append(year)
    if name_contains:
        clauses.append("league_name LIKE ?")
        params.append(f"%{name_contains}%")

    rows = conn.execute(
        f"SELECT * FROM leagues WHERE {' AND '.join(clauses)} ORDER BY season DESC, league_id DESC",
        params,
    ).fetchall()

    if not rows:
        raise ValueError(
            f"No league matched year={year!r} name_contains={name_contains!r}"
        )
    if len(rows) > 1:
        options = ", ".join(f"{r['league_id']} ({r['league_name']})" for r in rows)
        raise ValueError(f"Multiple leagues matched; pass --league-id. Candidates: {options}")
    return rows[0]
