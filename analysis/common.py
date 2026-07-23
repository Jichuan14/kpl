"""Shared helpers for analysis scripts."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"
ENV_PATH = REPO_ROOT / "backend" / ".env"


def database_url() -> str | None:
    """Read the backend database URL without requiring a web-server import."""
    if value := os.environ.get("DATABASE_URL"):
        return value
    if not ENV_PATH.is_file():
        return None
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "DATABASE_URL":
            return value.strip().strip('"').strip("'")
    return None


class SqlAlchemyConnection:
    """Small DB-API-style adapter for analysis scripts that use qmark SQL."""

    def __init__(self, url: str):
        self.engine = create_engine(url)
        self.connection = self.engine.connect()
        self.dialect = self.engine.dialect.name

    def execute(self, sql: str, params: Any = ()):
        values = tuple(params or ())
        if self.dialect == "mysql":
            sql = sql.replace("?", "%s")
        # SQLite rows support both column-name and positional access. Return
        # SQLAlchemy mappings so the analysis scripts retain their named-column
        # access when DATABASE_URL points to MySQL.
        return self.connection.exec_driver_sql(sql, values).mappings()

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        if self.dialect == "mysql":
            sql = sql.replace("?", "%s")
        parameter_rows = [
            tuple(row.values()) if hasattr(row, "values") else tuple(row)
            for row in rows
        ]
        if parameter_rows:
            self.connection.exec_driver_sql(sql, parameter_rows)

    def commit(self) -> None:
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
        self.engine.dispose()

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection | SqlAlchemyConnection:
    """Connect to MySQL when DATABASE_URL targets it; otherwise use SQLite."""
    url = database_url()
    if url and not url.startswith("sqlite") and db_path == DB_PATH:
        return SqlAlchemyConnection(url)
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def has_table(conn: sqlite3.Connection | SqlAlchemyConnection, name: str) -> bool:
    if isinstance(conn, SqlAlchemyConnection):
        return inspect(conn.engine).has_table(name)
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


def resolve_league(
    conn: sqlite3.Connection | SqlAlchemyConnection,
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
