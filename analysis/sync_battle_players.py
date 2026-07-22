"""Legacy backfill for teams, players, and per-battle player rows.

The backend's unified league sync now writes these rows from the same battle
detail response as BP actions. Use this script only to repair databases created
before that pipeline existed; running it after a unified sync repeats API calls.

Usage (from repo root):

  python3 analysis/sync_battle_players.py --year 2026 --name 挑战者杯
  python3 analysis/sync_battle_players.py --league-id 20260002 --battle-limit 5
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import DB_PATH, connect, resolve_league

COMP_BASE_URL = "https://prod.comp.smoba.qq.com"
DEFAULT_DELAY = 0.2

CREATE_TEAMS_SQL = """
CREATE TABLE IF NOT EXISTS teams (
    team_id VARCHAR(32) PRIMARY KEY,
    team_name VARCHAR(64) NOT NULL DEFAULT '',
    team_icon VARCHAR(500) NOT NULL DEFAULT ''
);
"""

CREATE_PLAYERS_SQL = """
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name VARCHAR(64) NOT NULL,
    team_id VARCHAR(32) NOT NULL DEFAULT '',
    team_name VARCHAR(64) NOT NULL DEFAULT '',
    player_icon VARCHAR(500) NOT NULL DEFAULT '',
    UNIQUE (player_name, team_id)
);
"""

CREATE_BATTLE_PLAYERS_SQL = """
CREATE TABLE IF NOT EXISTS battle_players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    battle_id VARCHAR(64) NOT NULL,
    match_id VARCHAR(32) NOT NULL,
    league_id VARCHAR(32) NOT NULL,
    team_id VARCHAR(32) NOT NULL DEFAULT '',
    team_name VARCHAR(64) NOT NULL DEFAULT '',
    player_name VARCHAR(64) NOT NULL DEFAULT '',
    player_icon VARCHAR(500) NOT NULL DEFAULT '',
    hero_id INTEGER NOT NULL DEFAULT 0,
    hero_name VARCHAR(100) NOT NULL DEFAULT '',
    camp INTEGER NOT NULL DEFAULT 0,
    match_camp INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    position_desc VARCHAR(32) NOT NULL DEFAULT '',
    UNIQUE (battle_id, player_name, hero_id, camp)
);
CREATE INDEX IF NOT EXISTS ix_battle_players_battle_id ON battle_players (battle_id);
CREATE INDEX IF NOT EXISTS ix_battle_players_match_id ON battle_players (match_id);
CREATE INDEX IF NOT EXISTS ix_battle_players_league_id ON battle_players (league_id);
CREATE INDEX IF NOT EXISTS ix_battle_players_team_id ON battle_players (team_id);
"""


@dataclass
class SyncStats:
    battles_seen: int = 0
    battles_synced: int = 0
    battles_skipped: int = 0
    battle_player_rows: int = 0
    teams_upserted: int = 0
    players_upserted: int = 0
    camp_flips: int = 0
    errors: int = 0


def ensure_tables(conn) -> None:
    conn.executescript(
        CREATE_TEAMS_SQL + CREATE_PLAYERS_SQL + CREATE_BATTLE_PLAYERS_SQL
    )
    conn.commit()


def seed_teams_from_matches(conn, league_id: str) -> int:
    """Upsert teams from match camp1/camp2 without hitting the API."""
    rows = conn.execute(
        """
        SELECT camp1_team_id AS team_id, camp1_team_name AS team_name
        FROM matches WHERE league_id = ? AND camp1_team_id != ''
        UNION
        SELECT camp2_team_id, camp2_team_name
        FROM matches WHERE league_id = ? AND camp2_team_id != ''
        """,
        (league_id, league_id),
    ).fetchall()
    n = 0
    for row in rows:
        n += upsert_team(conn, row["team_id"], row["team_name"] or "", "")
    conn.commit()
    return n


def upsert_team(conn, team_id: str, team_name: str, team_icon: str = "") -> int:
    if not team_id:
        return 0
    conn.execute(
        """
        INSERT INTO teams (team_id, team_name, team_icon)
        VALUES (?, ?, ?)
        ON CONFLICT(team_id) DO UPDATE SET
            team_name = CASE
                WHEN excluded.team_name != '' THEN excluded.team_name
                ELSE teams.team_name
            END,
            team_icon = CASE
                WHEN excluded.team_icon != '' THEN excluded.team_icon
                ELSE teams.team_icon
            END
        """,
        (team_id, team_name or "", team_icon or ""),
    )
    return 1


def upsert_player(
    conn,
    player_name: str,
    team_id: str,
    team_name: str = "",
    player_icon: str = "",
) -> int:
    if not player_name:
        return 0
    conn.execute(
        """
        INSERT INTO players (player_name, team_id, team_name, player_icon)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(player_name, team_id) DO UPDATE SET
            team_name = CASE
                WHEN excluded.team_name != '' THEN excluded.team_name
                ELSE players.team_name
            END,
            player_icon = CASE
                WHEN excluded.player_icon != '' THEN excluded.player_icon
                ELSE players.player_icon
            END
        """,
        (player_name, team_id or "", team_name or "", player_icon or ""),
    )
    return 1


def fetch_battle_detail(
    battle_id: str,
    *,
    base_url: str = COMP_BASE_URL,
    timeout: float = 20.0,
) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}/leaguesite/battle/open"
    params = {"battle_id": battle_id}

    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
    except ImportError:
        query = urllib.parse.urlencode(params)
        full_url = f"{url}?{query}"
        try:
            with urllib.request.urlopen(full_url, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  API error for {battle_id}: {exc}")
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"  API error for {battle_id}: {exc}")
        return None

    if not isinstance(payload, dict) or payload.get("code") != 200:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def api_camp_for_team(data: dict[str, Any], team_id: str) -> int | None:
    """Which API camp (1/2) a team sits on in this battle detail payload."""
    if not team_id:
        return None
    for camp, key in ((1, "camp1"), (2, "camp2")):
        node = data.get(key) or {}
        if str(node.get("team_id") or "") == team_id:
            return camp
    for player in data.get("battle_player_list") or []:
        if str(player.get("team_id") or "") == team_id:
            camp = int(player.get("camp") or 0)
            return camp if camp in (1, 2) else None
    return None


def compute_camp_flip(data: dict[str, Any], match_camp1_team_id: str) -> int:
    """Return 1 if API camps are flipped vs match camp1/camp2, else 0."""
    api_camp = api_camp_for_team(data, match_camp1_team_id)
    if api_camp is None:
        return 0
    return 0 if api_camp == 1 else 1


def flip_camp(api_camp: int, camp_flip: int) -> int:
    if api_camp not in (1, 2):
        return api_camp
    if camp_flip != 1:
        return api_camp
    return 2 if api_camp == 1 else 1


def player_display_name(node: dict[str, Any]) -> str:
    name = (node.get("actual_player_name") or node.get("player_name") or "").strip()
    return name


def sync_one_battle(
    conn,
    *,
    battle_id: str,
    match_id: str,
    league_id: str,
    match_camp1_team_id: str,
    base_url: str,
) -> tuple[int, int, int, bool]:
    """Returns (player_rows, teams_n, players_n, did_flip)."""
    data = fetch_battle_detail(battle_id, base_url=base_url)
    if not data:
        return 0, 0, 0, False

    players = data.get("battle_player_list") or []
    if not isinstance(players, list) or not players:
        return 0, 0, 0, False

    camp_flip = compute_camp_flip(data, match_camp1_team_id)
    teams_n = 0
    players_n = 0

    for key in ("camp1", "camp2"):
        node = data.get(key) or {}
        teams_n += upsert_team(
            conn,
            str(node.get("team_id") or ""),
            node.get("team_name") or "",
            node.get("team_icon") or "",
        )

    conn.execute("DELETE FROM battle_players WHERE battle_id = ?", (battle_id,))

    rows = 0
    seen: set[tuple[str, int, int]] = set()
    for node in players:
        if not isinstance(node, dict):
            continue
        team_id = str(node.get("team_id") or "")
        team_name = node.get("team_name") or ""
        team_icon = node.get("team_icon") or ""
        player_name = player_display_name(node)
        player_icon = node.get("player_icon") or ""
        hero_id = int(node.get("hero_id") or 0)
        api_camp = int(node.get("camp") or 0)
        match_camp = flip_camp(api_camp, camp_flip)
        key = (player_name, hero_id, api_camp)
        if player_name and key in seen:
            continue
        if player_name:
            seen.add(key)

        teams_n += upsert_team(conn, team_id, team_name, team_icon)
        players_n += upsert_player(conn, player_name, team_id, team_name, player_icon)

        conn.execute(
            """
            INSERT INTO battle_players (
                battle_id, match_id, league_id,
                team_id, team_name, player_name, player_icon,
                hero_id, hero_name, camp, match_camp,
                position, position_desc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                battle_id,
                match_id,
                league_id,
                team_id,
                team_name,
                player_name,
                player_icon,
                hero_id,
                node.get("hero_name") or "",
                api_camp,
                match_camp,
                int(node.get("position") or 0),
                node.get("position_desc") or "",
            ),
        )
        rows += 1

    conn.commit()
    return rows, teams_n, players_n, camp_flip == 1


def list_battles(
    conn,
    league_id: str,
    *,
    battle_limit: int | None = None,
    only_missing: bool = False,
) -> list[Any]:
    sql = """
        SELECT
            b.battle_id,
            b.match_id,
            b.league_id,
            b.battle_seq,
            m.camp1_team_id,
            m.camp1_team_name,
            m.camp2_team_id,
            m.camp2_team_name
        FROM battles b
        JOIN matches m ON m.match_id = b.match_id
        WHERE b.league_id = ?
    """
    if only_missing:
        sql += """
          AND NOT EXISTS (
            SELECT 1 FROM battle_players bp WHERE bp.battle_id = b.battle_id
          )
        """
    sql += " ORDER BY b.match_id, b.battle_seq"
    if battle_limit is not None:
        sql += f" LIMIT {int(battle_limit)}"
    return conn.execute(sql, (league_id,)).fetchall()


def sync_league_players(
    conn,
    league_id: str,
    *,
    battle_limit: int | None = None,
    only_missing: bool = False,
    delay: float = DEFAULT_DELAY,
    base_url: str = COMP_BASE_URL,
) -> SyncStats:
    ensure_tables(conn)
    stats = SyncStats()
    stats.teams_upserted += seed_teams_from_matches(conn, league_id)

    battles = list_battles(
        conn, league_id, battle_limit=battle_limit, only_missing=only_missing
    )
    for row in battles:
        stats.battles_seen += 1
        battle_id = row["battle_id"]
        print(
            f"[{stats.battles_seen}/{len(battles)}] "
            f"{row['match_id']} G{row['battle_seq']} {battle_id}"
        )
        try:
            player_rows, teams_n, players_n, flipped = sync_one_battle(
                conn,
                battle_id=battle_id,
                match_id=row["match_id"],
                league_id=league_id,
                match_camp1_team_id=row["camp1_team_id"] or "",
                base_url=base_url,
            )
        except Exception as exc:  # noqa: BLE001 - keep syncing remaining battles
            stats.errors += 1
            print(f"  failed: {exc}")
            continue

        if player_rows == 0:
            stats.battles_skipped += 1
            print("  skipped (no player detail)")
        else:
            stats.battles_synced += 1
            stats.battle_player_rows += player_rows
            stats.teams_upserted += teams_n
            stats.players_upserted += players_n
            if flipped:
                stats.camp_flips += 1
            print(
                f"  players={player_rows} flip={flipped} "
                f"({row['camp1_team_name']} vs {row['camp2_team_name']})"
            )

        if delay > 0:
            time.sleep(delay)

    return stats


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync teams/players/battle_players for one league"
    )
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--league-id", default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--name", dest="name_contains", default=None)
    parser.add_argument(
        "--battle-limit",
        type=int,
        default=None,
        help="Only process the first N battles (smoke test)",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Skip battles that already have battle_players rows",
    )
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--base-url", default=COMP_BASE_URL)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.league_id and args.year is None and not args.name_contains:
        args.year = 2026
        args.name_contains = "挑战者杯"

    with connect(args.db) as conn:
        league = resolve_league(
            conn,
            league_id=args.league_id,
            year=args.year,
            name_contains=args.name_contains,
        )
        print(
            f"Syncing players/teams for {league['league_name']} ({league['league_id']})"
        )
        stats = sync_league_players(
            conn,
            league["league_id"],
            battle_limit=args.battle_limit,
            only_missing=args.only_missing,
            delay=args.delay,
            base_url=args.base_url,
        )

    print("\nDone")
    print(f"  battles seen:     {stats.battles_seen}")
    print(f"  battles synced:   {stats.battles_synced}")
    print(f"  battles skipped:  {stats.battles_skipped}")
    print(f"  battle_players:   {stats.battle_player_rows}")
    print(f"  teams upserts:    {stats.teams_upserted}")
    print(f"  players upserts:  {stats.players_upserted}")
    print(f"  camp flips:       {stats.camp_flips}")
    print(f"  errors:           {stats.errors}")
    return 0 if stats.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
