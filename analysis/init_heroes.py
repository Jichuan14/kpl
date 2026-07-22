"""Create or refresh the heroes reference table from downloaded match data.

The refresh is non-destructive and includes heroes found in BP actions and
battle player rows.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS heroes (
    hero_id INTEGER PRIMARY KEY,
    hero_name VARCHAR(100) NOT NULL DEFAULT '',
    hero_icon VARCHAR(500) NOT NULL DEFAULT ''
);
"""

def init_heroes(db_path: Path = DB_PATH) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.execute(CREATE_SQL)
        has_battle_players = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'battle_players'
            """
        ).fetchone()
        sources = """
            SELECT hero_id, hero_name, hero_icon
            FROM battle_bps
            WHERE hero_id > 0
        """
        if has_battle_players:
            sources += """
                UNION ALL
                SELECT hero_id, hero_name, ''
                FROM battle_players
                WHERE hero_id > 0
            """
        rows = conn.execute(
            f"""
            SELECT hero_id, MAX(hero_name), MAX(hero_icon)
            FROM ({sources})
            GROUP BY hero_id
            """
        ).fetchall()
        conn.executemany(
            """
            INSERT INTO heroes (hero_id, hero_name, hero_icon)
            VALUES (?, ?, ?)
            ON CONFLICT(hero_id) DO UPDATE SET
                hero_name = CASE
                    WHEN excluded.hero_name != '' THEN excluded.hero_name
                    ELSE heroes.hero_name
                END,
                hero_icon = CASE
                    WHEN excluded.hero_icon != '' THEN excluded.hero_icon
                    ELSE heroes.hero_icon
                END
            """,
            rows,
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM heroes").fetchone()[0]
    return int(count)


if __name__ == "__main__":
    n = init_heroes()
    print(f"heroes table ready: {n} heroes in {DB_PATH}")
