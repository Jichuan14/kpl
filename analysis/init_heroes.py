"""Create the heroes reference table and seed it from existing BP rows.

Minimal schema for now (hero_id, hero_name, hero_icon). Add more columns later as needed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"

CREATE_SQL = """
CREATE TABLE heroes (
    hero_id INTEGER PRIMARY KEY,
    hero_name VARCHAR(100) NOT NULL DEFAULT '',
    hero_icon VARCHAR(500) NOT NULL DEFAULT ''
);
"""

SEED_SQL = """
INSERT INTO heroes (hero_id, hero_name, hero_icon)
SELECT hero_id, MAX(hero_name), MAX(hero_icon)
FROM battle_bps
WHERE hero_id > 0
GROUP BY hero_id;
"""


def init_heroes(db_path: Path = DB_PATH) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE IF EXISTS heroes")
        conn.execute(CREATE_SQL)
        conn.execute(SEED_SQL)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM heroes").fetchone()[0]
    return int(count)


if __name__ == "__main__":
    n = init_heroes()
    print(f"heroes table ready: {n} heroes in {DB_PATH}")
