"""Create or refresh hero and hero-position reference tables from match data.

The refresh is non-destructive and includes heroes found in BP actions and
battle player rows. Position eligibility comes from observed player picks, so
flex heroes can have more than one row in ``hero_positions``.
"""

from __future__ import annotations

from pathlib import Path

from common import DB_PATH, connect, has_table

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

CREATE_HEROES_SQL = """
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
    with connect(db_path) as conn:
        mysql = getattr(conn, "dialect", None) == "mysql"
        # The backend normally owns this schema. These definitions keep the
        # standalone maintenance command usable with the SQLite fallback.
        if not has_table(conn, "heroes"):
            conn.execute(CREATE_HEROES_SQL)
        if not has_table(conn, "hero_positions"):
            conn.execute(CREATE_HERO_POSITIONS_SQL)
        has_battle_players = has_table(conn, "battle_players")
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
            FROM ({sources}) AS hero_sources
            GROUP BY hero_id
            """
        ).fetchall()
        if mysql:
            hero_upsert = """
                INSERT INTO heroes (hero_id, hero_name, hero_icon) VALUES (?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    hero_name = IF(VALUES(hero_name) != '', VALUES(hero_name), hero_name),
                    hero_icon = IF(VALUES(hero_icon) != '', VALUES(hero_icon), hero_icon)
            """
        else:
            hero_upsert = """
                INSERT INTO heroes (hero_id, hero_name, hero_icon) VALUES (?, ?, ?)
                ON CONFLICT(hero_id) DO UPDATE SET
                    hero_name = CASE WHEN excluded.hero_name != '' THEN excluded.hero_name ELSE heroes.hero_name END,
                    hero_icon = CASE WHEN excluded.hero_icon != '' THEN excluded.hero_icon ELSE heroes.hero_icon END
            """
        conn.executemany(hero_upsert, rows)
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
            if mysql:
                position_upsert = """
                    INSERT INTO hero_positions (hero_id, position, position_desc, observed_pick_count)
                    VALUES (?, ?, ?, ?)
                    ON DUPLICATE KEY UPDATE
                        position_desc = IF(VALUES(position_desc) != '', VALUES(position_desc), position_desc),
                        observed_pick_count = VALUES(observed_pick_count)
                """
            else:
                position_upsert = """
                    INSERT INTO hero_positions (hero_id, position, position_desc, observed_pick_count)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(hero_id, position) DO UPDATE SET
                        position_desc = CASE WHEN excluded.position_desc != '' THEN excluded.position_desc ELSE hero_positions.position_desc END,
                        observed_pick_count = excluded.observed_pick_count
                """
            conn.executemany(position_upsert, position_rows)
        if mysql:
            overrides_upsert = """
                INSERT INTO hero_positions (hero_id, position, position_desc, observed_pick_count)
                VALUES (?, ?, ?, 0)
                ON DUPLICATE KEY UPDATE
                    position_desc = IF(position_desc = '', VALUES(position_desc), position_desc)
            """
        else:
            overrides_upsert = """
                INSERT INTO hero_positions (hero_id, position, position_desc, observed_pick_count)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(hero_id, position) DO UPDATE SET
                    position_desc = CASE WHEN hero_positions.position_desc = '' THEN excluded.position_desc ELSE hero_positions.position_desc END
            """
        conn.executemany(overrides_upsert, POSITION_OVERRIDES)
        conn.commit()
        count = conn.execute("SELECT COUNT(*) AS count FROM heroes").fetchone()["count"]
    return int(count)


if __name__ == "__main__":
    n = init_heroes()
    print(f"hero catalog ready: {n} heroes (using DATABASE_URL or {DB_PATH})")
