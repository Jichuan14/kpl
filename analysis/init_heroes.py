"""Create or refresh hero and hero-position reference tables from match data.

The refresh is non-destructive and includes heroes found in BP actions and
battle player rows. Position eligibility comes from observed player picks, so
flex heroes can have more than one row in ``hero_positions``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"

# These heroes appear in the BP catalog but have no player-role observation in
# the locally downloaded matches. Keep the role model complete while marking
# them with zero observed picks.
POSITION_OVERRIDES = (
    (116, 5, "打野"),  # 阿轲
    (117, 5, "打野"),  # 钟无艳
    (153, 5, "打野"),  # 兰陵王
    (170, 5, "打野"),  # 刘备
    (195, 5, "打野"),  # 百里玄策
    (529, 5, "打野"),  # 盘古
)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS heroes (
    hero_id INTEGER PRIMARY KEY,
    hero_name VARCHAR(100) NOT NULL DEFAULT '',
    hero_icon VARCHAR(500) NOT NULL DEFAULT ''
);
"""

CREATE_HERO_POSITIONS_SQL = """
CREATE TABLE IF NOT EXISTS hero_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hero_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    position_desc VARCHAR(32) NOT NULL DEFAULT '',
    observed_pick_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (hero_id, position),
    FOREIGN KEY (hero_id) REFERENCES heroes(hero_id)
);
"""

def init_heroes(db_path: Path = DB_PATH) -> int:
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.execute(CREATE_SQL)
        conn.execute(CREATE_HERO_POSITIONS_SQL)
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
        if has_battle_players:
            position_rows = conn.execute(
                """
                SELECT
                    hero_id,
                    position,
                    MAX(position_desc) AS position_desc,
                    COUNT(*) AS observed_pick_count
                FROM battle_players
                WHERE hero_id > 0 AND position > 0
                GROUP BY hero_id, position
                """
            ).fetchall()
            conn.executemany(
                """
                INSERT INTO hero_positions (
                    hero_id, position, position_desc, observed_pick_count
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(hero_id, position) DO UPDATE SET
                    position_desc = CASE
                        WHEN excluded.position_desc != '' THEN excluded.position_desc
                        ELSE hero_positions.position_desc
                    END,
                    observed_pick_count = excluded.observed_pick_count
                """,
                position_rows,
            )
        conn.executemany(
            """
            INSERT INTO hero_positions (
                hero_id, position, position_desc, observed_pick_count
            )
            VALUES (?, ?, ?, 0)
            ON CONFLICT(hero_id, position) DO UPDATE SET
                position_desc = CASE
                    WHEN hero_positions.position_desc = '' THEN excluded.position_desc
                    ELSE hero_positions.position_desc
                END
            """,
            POSITION_OVERRIDES,
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM heroes").fetchone()[0]
    return int(count)


if __name__ == "__main__":
    n = init_heroes()
    print(f"hero catalog ready: {n} heroes in {DB_PATH}")
