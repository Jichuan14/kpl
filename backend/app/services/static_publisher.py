"""Publish browser-ready aggregate analysis files for Nginx to serve."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import League
from app.services.analysis_pipeline import ANALYSIS_DIR, EXPORT_ROOT, OUTPUT_ROOT
from app.services.draft_simulator import metadata

PUBLISHED_ROOT = ANALYSIS_DIR / "published"
DATA_ROOT = PUBLISHED_ROOT / "data"
ROLE_ORDER = {6: 0, 2: 1, 5: 2, 7: 3, 4: 4}


def _write_json(path: Path, value: object) -> None:
    """Atomically replace a published file so visitors never read a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(value, temporary, ensure_ascii=False, separators=(",", ":"))
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)
    # Nginx runs as an unprivileged user and serves this bind-mounted file.
    path.chmod(0o644)


def _publish_seasons(db: Session) -> None:
    leagues = db.scalars(
        select(League).order_by(League.year.desc(), League.season.desc(), League.id.desc())
    ).all()
    rows = []
    for league in leagues:
        directory = DATA_ROOT / league.league_id
        if not (directory / "overview.json").is_file():
            continue
        rows.append(
            {
                "league_id": league.league_id,
                "league_name": league.league_name,
                "year": league.year,
                "season": league.season,
                "status": league.status,
                "team_synergy_ready": (directory / "team-synergies.json").is_file(),
                "rankings_ready": (directory / "rankings.json").is_file(),
            }
        )
    _write_json(DATA_ROOT / "seasons.json", rows)


def _publish_meta_history() -> None:
    """Combine the tiny meta sections without making the browser read patterns."""
    entries = []
    for manifest_path in DATA_ROOT.glob("*/overview.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("league"):
            entries.append({"season": manifest["league"], "meta_heroes": manifest.get("meta_heroes", [])})
    entries.sort(key=lambda item: (int(item["season"].get("year") or 0), int(item["season"].get("season") or 0)))
    _write_json(DATA_ROOT / "meta-history.json", entries)


def _strip_unused_icons(row: dict[str, object]) -> dict[str, object]:
    """Icons are resolved from the bundled hero assets, never remote row URLs."""
    return {key: value for key, value in row.items() if key not in {"source_hero_icon", "target_hero_icon"}}


def _hero_response_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Publish only the three response cards Feature Space can render per hero."""
    groups: dict[tuple[int, str], list[dict[str, object]]] = {}
    for row in rows:
        if (
            row["context_level"] == "overall"
            and not row["is_peak_battle"]
            and row["relation"] in {"pick_synergy", "counter_pick", "counter_ban"}
        ):
            groups.setdefault((int(row["source_hero_id"]), str(row["relation"])), []).append(row)

    selected: list[dict[str, object]] = []
    for group in groups.values():
        supported = [row for row in group if int(row.get("selections") or 0) >= 3]
        candidates = supported or group
        by_target: dict[int, dict[str, object]] = {}
        for row in candidates:
            target_id = int(row["target_hero_id"])
            current = by_target.get(target_id)
            if current is None or (
                float(row.get("smoothed_lift") or 0),
                float(row.get("smoothed_probability") or 0),
                int(row.get("selections") or 0),
            ) > (
                float(current.get("smoothed_lift") or 0),
                float(current.get("smoothed_probability") or 0),
                int(current.get("selections") or 0),
            ):
                by_target[target_id] = row
        selected.extend(
            sorted(
                by_target.values(),
                key=lambda row: (
                    float(row.get("smoothed_lift") or 0),
                    float(row.get("smoothed_probability") or 0),
                    int(row.get("selections") or 0),
                ),
                reverse=True,
            )[:3]
        )
    return selected


def _battle_lineup(
    players: list[dict[str, object]],
    actions: list[dict[str, object]],
    camp: int,
) -> list[dict[str, object]]:
    """Prefer final player roles, with BP picks as a historical fallback."""
    player_rows = [
        {
            "hero_id": int(row.get("hero_id") or 0),
            "hero_name": str(row.get("hero_name") or ""),
            "position": int(row.get("position") or 0),
        }
        for row in players
        if int(row.get("camp") or 0) == camp
        and int(row.get("hero_id") or 0) > 0
    ]
    if len(player_rows) == 5 and len({row["hero_id"] for row in player_rows}) == 5:
        return sorted(
            player_rows,
            key=lambda row: ROLE_ORDER.get(int(row["position"]), 99),
        )

    picks = [
        {
            "hero_id": int(row.get("hero_id") or 0),
            "hero_name": str(row.get("hero_name") or ""),
            "position": int(row.get("position") or 0),
        }
        for row in actions
        if row.get("action") == "pick"
        and int(row.get("camp") or 0) == camp
        and int(row.get("hero_id") or 0) > 0
    ]
    return (
        picks
        if len(picks) == 5 and len({row["hero_id"] for row in picks}) == 5
        else []
    )


def _historical_battle_lineups(matches_path: Path) -> dict[str, object]:
    battles: list[dict[str, object]] = []
    if not matches_path.is_file():
        return {"battles": battles}
    with matches_path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            match = json.loads(line)
            for battle in match.get("battles") or []:
                players = battle.get("players") or []
                actions = battle.get("bp_actions") or []
                blue = _battle_lineup(players, actions, 1)
                red = _battle_lineup(players, actions, 2)
                if len(blue) != 5 or len(red) != 5:
                    continue
                camp_teams = battle.get("camp_teams") or {}
                blue_team = camp_teams.get("1") or camp_teams.get(1) or {}
                red_team = camp_teams.get("2") or camp_teams.get(2) or {}
                battles.append(
                    {
                        "key": (
                            f'{battle.get("battle_id") or match.get("match_id")}-'
                            f'{int(battle.get("battle_seq") or 0)}'
                        ),
                        "match_id": str(match.get("match_id") or ""),
                        "battle_id": str(battle.get("battle_id") or ""),
                        "battle_seq": int(battle.get("battle_seq") or 0),
                        "start_time": str(match.get("start_time") or ""),
                        "match_stage": str(match.get("match_stage") or ""),
                        "blue_team_id": str(blue_team.get("team_id") or ""),
                        "blue_team_name": str(blue_team.get("team_name") or "Blue"),
                        "red_team_id": str(red_team.get("team_id") or ""),
                        "red_team_name": str(red_team.get("team_name") or "Red"),
                        "winner_camp": int(battle.get("win_camp") or 0),
                        "blue": blue,
                        "red": red,
                    }
                )
    battles.sort(
        key=lambda row: (
            str(row["start_time"]),
            str(row["match_id"]),
            int(row["battle_seq"]),
        ),
        reverse=True,
    )
    return {
        "generated_at": datetime.fromtimestamp(
            matches_path.stat().st_mtime
        ).astimezone().isoformat(),
        "battles": battles,
    }


def publish_league(db: Session, league_id: str) -> dict[str, object]:
    """Create all currently available static public files for one season."""
    # Import here to keep API modules independent during application startup.
    from app.api.visualization import statistics_ready, team_synergies, visualization_patterns

    league = db.scalar(select(League).where(League.league_id == league_id))
    if league is None:
        raise ValueError("League not found")

    published: list[str] = []
    directory = DATA_ROOT / league_id

    matches_path = EXPORT_ROOT / league_id / "matches.jsonl"
    if matches_path.is_file():
        historical_lineups = _historical_battle_lineups(matches_path)
        historical_lineups["league_id"] = league_id
        _write_json(directory / "battle-lineups.json", historical_lineups)
        published.append("battle-lineups.json")

    if statistics_ready(league_id):
        patterns = visualization_patterns(league_id=league_id, min_selections=2, db=db).data
        rows = [_strip_unused_icons(row) for row in patterns["rows"]]
        manifest = {
            "league": patterns["league"],
            "meta_heroes": [
                _strip_unused_icons(hero) for hero in patterns["meta_heroes"]
            ],
            "source_counts": patterns["source_counts"],
            "generated_at": patterns["generated_at"],
        }
        _write_json(directory / "overview.json", manifest)
        published.append("overview.json")
        for relation in {row["relation"] for row in rows}:
            for context in {row["context_level"] for row in rows if row["relation"] == relation}:
                _write_json(
                    directory / "patterns" / relation / f"{context}.json",
                    {"rows": [row for row in rows if row["relation"] == relation and row["context_level"] == context]},
                )
        # Feature Space only needs a few overall responses per selected hero.
        response_rows = _hero_response_rows(rows)
        _write_json(directory / "hero-responses.json", {"rows": response_rows})
        published.append("hero-responses.json")
        # Replaced by relation/context shards. Do not retain a second full copy.
        legacy_patterns = directory / "patterns.json"
        if legacy_patterns.is_file():
            legacy_patterns.unlink()

    if (OUTPUT_ROOT / league_id / "team_synergy_stats.jsonl").is_file():
        teams = team_synergies(league_id=league_id, min_selections=2, db=db).data
        _write_json(directory / "team-synergies.json", teams)
        published.append("team-synergies.json")

    rankings_path = OUTPUT_ROOT / league_id / "power_rankings.json"
    if rankings_path.is_file():
        try:
            rankings = json.loads(rankings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Power rankings artifact is invalid") from exc
        rankings["league"] = {
            "league_id": league.league_id,
            "league_name": league.league_name,
            "year": league.year,
            "season": league.season,
        }
        _write_json(directory / "rankings.json", rankings)
        published.append("rankings.json")

    if (OUTPUT_ROOT / league_id / "draft_model.json").is_file():
        model = metadata(league_id)
        _write_json(directory / "draft-model.json", model)
        published.append("draft-model.json")

    _publish_seasons(db)
    _publish_meta_history()
    return {"files": published, "directory": str(directory)}
