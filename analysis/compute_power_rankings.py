"""Build cross-season team Elo and player-on-hero performance rankings.

The selected season defines who is eligible for the boards and the ranking
cutoff. Earlier exported seasons contribute evidence with exponential time
decay. Existing match artifacts are read without modification; this script
writes one new, versioned JSON artifact.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from common import CURRENT_LEAGUE_ID, REPO_ROOT

DEFAULT_HALF_LIFE_DAYS = 180.0
DEFAULT_ELO_K = 24.0
DEFAULT_ELO_REGRESSION_HALF_LIFE_DAYS = 365.0
DEFAULT_OUTPUT_NAME = "power_rankings.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected an object in {path}:{line_number}")
            rows.append(row)
    return rows


def parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except ValueError:
        return None


def decay_weight(
    played_at: datetime,
    as_of: datetime,
    half_life_days: float,
) -> float:
    age_days = max(0.0, (as_of - played_at).total_seconds() / 86_400.0)
    return 0.5 ** (age_days / half_life_days)


def canonical_player_name(value: Any) -> str:
    name = str(value or "").strip()
    for separator in (".", "．"):
        if separator in name:
            suffix = name.rsplit(separator, 1)[1].strip()
            if suffix:
                return suffix
    return name


def percentile(value: float, sorted_values: list[float]) -> float:
    if len(sorted_values) <= 1:
        return 0.5
    left = bisect.bisect_left(sorted_values, value)
    right = bisect.bisect_right(sorted_values, value)
    midpoint_rank = (left + right - 1) / 2
    return midpoint_rank / (len(sorted_values) - 1)


def load_history(
    exports_root: Path,
    target_league_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], datetime, list[str]]:
    target_path = exports_root / target_league_id / "matches.jsonl"
    if not target_path.is_file():
        raise ValueError(f"Missing selected-season match export: {target_path}")
    target_matches = read_jsonl(target_path)
    target_times = [
        parsed
        for row in target_matches
        if (parsed := parse_time(row.get("start_time"))) is not None
    ]
    if not target_times:
        raise ValueError("Selected season has no dated matches")
    as_of = max(target_times)

    by_match_id: dict[str, dict[str, Any]] = {}
    included_leagues: set[str] = set()
    for path in sorted(exports_root.glob("*/matches.jsonl")):
        for match in read_jsonl(path):
            played_at = parse_time(match.get("start_time"))
            match_id = str(match.get("match_id") or "")
            if not match_id or played_at is None or played_at > as_of:
                continue
            by_match_id[match_id] = match
            included_leagues.add(str(match.get("league_id") or path.parent.name))
    history = sorted(
        by_match_id.values(),
        key=lambda row: (str(row.get("start_time") or ""), str(row.get("match_id") or "")),
    )
    return target_matches, history, as_of, sorted(included_leagues)


def _battle_teams(battle: dict[str, Any]) -> list[dict[str, Any]]:
    teams = [
        team
        for team in (battle.get("camp_teams") or {}).values()
        if str(team.get("team_id") or "")
    ]
    unique: dict[str, dict[str, Any]] = {}
    for team in teams:
        unique[str(team["team_id"])] = team
    return list(unique.values())


def compute_team_rankings(
    target_matches: list[dict[str, Any]],
    history: list[dict[str, Any]],
    as_of: datetime,
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    elo_k: float = DEFAULT_ELO_K,
    regression_half_life_days: float = DEFAULT_ELO_REGRESSION_HALF_LIFE_DAYS,
) -> list[dict[str, Any]]:
    active_team_ids = {
        str(team.get("team_id") or "")
        for match in target_matches
        for team in match.get("teams") or []
        if str(team.get("team_id") or "")
    }
    ratings: defaultdict[str, float] = defaultdict(lambda: 1500.0)
    last_played: dict[str, datetime] = {}
    names: dict[str, str] = {}
    events: defaultdict[str, list[tuple[datetime, bool]]] = defaultdict(list)
    target_games: defaultdict[str, int] = defaultdict(int)
    target_league_id = str(target_matches[0].get("league_id") or "") if target_matches else ""

    def regress(team_id: str, played_at: datetime) -> None:
        previous = last_played.get(team_id)
        if previous is None:
            return
        factor = decay_weight(previous, played_at, regression_half_life_days)
        ratings[team_id] = 1500.0 + (ratings[team_id] - 1500.0) * factor

    for match in history:
        played_at = parse_time(match.get("start_time"))
        if played_at is None:
            continue
        league_id = str(match.get("league_id") or "")
        for battle in match.get("battles") or []:
            teams = _battle_teams(battle)
            winner_id = str(battle.get("winner_team_id") or "")
            if len(teams) != 2 or winner_id not in {str(team["team_id"]) for team in teams}:
                continue
            team_a, team_b = teams
            a_id, b_id = str(team_a["team_id"]), str(team_b["team_id"])
            names[a_id] = str(team_a.get("team_name") or a_id)
            names[b_id] = str(team_b.get("team_name") or b_id)
            regress(a_id, played_at)
            regress(b_id, played_at)
            expected_a = 1.0 / (1.0 + 10.0 ** ((ratings[b_id] - ratings[a_id]) / 400.0))
            actual_a = 1.0 if winner_id == a_id else 0.0
            change = elo_k * (actual_a - expected_a)
            ratings[a_id] += change
            ratings[b_id] -= change
            last_played[a_id] = played_at
            last_played[b_id] = played_at
            events[a_id].append((played_at, actual_a == 1.0))
            events[b_id].append((played_at, actual_a == 0.0))
            if league_id == target_league_id:
                target_games[a_id] += 1
                target_games[b_id] += 1

    rows: list[dict[str, Any]] = []
    for team_id in active_team_ids:
        if team_id not in last_played:
            continue
        factor = decay_weight(last_played[team_id], as_of, regression_half_life_days)
        final_elo = 1500.0 + (ratings[team_id] - 1500.0) * factor
        weighted_games = sum(
            decay_weight(played_at, as_of, half_life_days)
            for played_at, _won in events[team_id]
        )
        weighted_wins = sum(
            decay_weight(played_at, as_of, half_life_days)
            for played_at, won in events[team_id]
            if won
        )
        decayed_win_rate = (weighted_wins + 3.0) / (weighted_games + 6.0)
        elo_component = 100.0 / (1.0 + 10.0 ** ((1500.0 - final_elo) / 400.0))
        hybrid = 0.72 * elo_component + 0.28 * decayed_win_rate * 100.0
        recent = events[team_id][-10:]
        rows.append(
            {
                "team_id": team_id,
                "team_name": names.get(team_id, team_id),
                "hybrid_score": round(hybrid, 2),
                "elo": round(final_elo, 1),
                "elo_component": round(elo_component, 2),
                "decayed_win_rate": round(decayed_win_rate, 6),
                "games": len(events[team_id]),
                "effective_games": round(weighted_games, 2),
                "target_season_games": target_games[team_id],
                "recent_10_wins": sum(1 for _played_at, won in recent if won),
                "recent_10_games": len(recent),
                "last_played": last_played[team_id].isoformat(sep=" "),
            }
        )
    rows.sort(key=lambda row: (-row["hybrid_score"], -row["elo"], row["team_name"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def _player_events(
    matches: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for match in matches:
        played_at = parse_time(match.get("start_time"))
        if played_at is None:
            continue
        league_id = str(match.get("league_id") or "")
        for battle in match.get("battles") or []:
            winner_id = str(battle.get("winner_team_id") or "")
            duration_minutes = max(float(battle.get("game_duration_ms") or 0) / 60_000.0, 1.0)
            for player in battle.get("players") or []:
                if not player.get("performance_data_available"):
                    continue
                hero_id = int(player.get("hero_id") or 0)
                player_name = canonical_player_name(player.get("player_name"))
                if hero_id <= 0 or not player_name:
                    continue
                damage = player.get("damage") or {}
                events.append(
                    {
                        "league_id": league_id,
                        "played_at": played_at,
                        "player_id": player_name.casefold(),
                        "player_name": player_name,
                        "source_player_name": str(player.get("player_name") or player_name),
                        "team_id": str(player.get("team_id") or ""),
                        "team_name": str(player.get("team_name") or ""),
                        "hero_id": hero_id,
                        "hero_name": str(player.get("hero_name") or hero_id),
                        "position": int(player.get("position") or 0),
                        "position_desc": str(player.get("position_desc") or ""),
                        "won": str(player.get("team_id") or "") == winner_id,
                        "kda": float(player.get("kda") or 0.0),
                        "mvp_score": float(player.get("mvp_score") or 0.0),
                        "participation_rate": float(player.get("participation_rate") or 0.0),
                        "damage_rate": float(damage.get("to_heroes_rate") or 0.0),
                        "gold_per_minute": float(player.get("gold") or 0.0) / duration_minutes,
                    }
                )
    return events


def compute_hero_rankings(
    target_matches: list[dict[str, Any]],
    history: list[dict[str, Any]],
    as_of: datetime,
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    hero_catalog: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    target_events = _player_events(target_matches)
    eligible_pairs = {
        (event["player_id"], event["hero_id"])
        for event in target_events
    }
    target_pair_games: defaultdict[tuple[str, int], int] = defaultdict(int)
    for event in target_events:
        target_pair_games[(event["player_id"], event["hero_id"])] += 1

    events = _player_events(history)
    distributions: defaultdict[tuple[str, int, str], list[float]] = defaultdict(list)
    metric_names = ("kda", "mvp_score", "participation_rate", "damage_rate", "gold_per_minute")
    for event in events:
        group = (event["league_id"], event["position"])
        for metric in metric_names:
            distributions[(*group, metric)].append(float(event[metric]))
    for values in distributions.values():
        values.sort()

    aggregates: defaultdict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {
            "weighted_score": 0.0,
            "weighted_kda": 0.0,
            "weighted_wins": 0.0,
            "weight": 0.0,
            "games": 0,
            "team_names": set(),
            "positions": set(),
            "last_event": None,
        }
    )
    hero_names: dict[int, str] = dict(hero_catalog or {})
    for event in events:
        hero_names[event["hero_id"]] = event["hero_name"]
        key = (event["player_id"], event["hero_id"])
        if key not in eligible_pairs:
            continue
        group = (event["league_id"], event["position"])
        components = {
            metric: percentile(
                float(event[metric]),
                distributions[(*group, metric)],
            )
            for metric in metric_names
        }
        game_score = 100.0 * (
            0.40 * components["kda"]
            + 0.18 * components["mvp_score"]
            + 0.12 * components["participation_rate"]
            + 0.10 * components["damage_rate"]
            + 0.08 * components["gold_per_minute"]
            + 0.12 * float(event["won"])
        )
        weight = decay_weight(event["played_at"], as_of, half_life_days)
        aggregate = aggregates[key]
        aggregate["weighted_score"] += weight * game_score
        aggregate["weighted_kda"] += weight * event["kda"]
        aggregate["weighted_wins"] += weight * float(event["won"])
        aggregate["weight"] += weight
        aggregate["games"] += 1
        aggregate["team_names"].add(event["team_name"])
        aggregate["positions"].add(event["position_desc"] or str(event["position"]))
        if aggregate["last_event"] is None or event["played_at"] > aggregate["last_event"]["played_at"]:
            aggregate["last_event"] = event

    by_hero: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for (player_id, hero_id), aggregate in aggregates.items():
        weight = float(aggregate["weight"])
        if weight <= 0:
            continue
        # Four effective games of neutral evidence protects one-game leaders.
        hybrid_score = (aggregate["weighted_score"] + 4.0 * 50.0) / (weight + 4.0)
        decayed_win_rate = (aggregate["weighted_wins"] + 1.5) / (weight + 3.0)
        last_event = aggregate["last_event"]
        by_hero[hero_id].append(
            {
                "player_id": player_id,
                "player_name": last_event["player_name"],
                "source_player_name": last_event["source_player_name"],
                "current_team_id": last_event["team_id"],
                "current_team_name": last_event["team_name"],
                "teams": sorted(name for name in aggregate["team_names"] if name),
                "positions": sorted(aggregate["positions"]),
                "hybrid_score": round(hybrid_score, 2),
                "decayed_kda": round(aggregate["weighted_kda"] / weight, 2),
                "decayed_win_rate": round(decayed_win_rate, 6),
                "games": aggregate["games"],
                "effective_games": round(weight, 2),
                "target_season_games": target_pair_games[(player_id, hero_id)],
                "confidence": round(1.0 - math.exp(-weight / 5.0), 6),
                "last_played": last_event["played_at"].isoformat(sep=" "),
            }
        )

    hero_rows: list[dict[str, Any]] = []
    for hero_id, hero_name in hero_names.items():
        players = by_hero.get(hero_id, [])
        players.sort(
            key=lambda row: (
                -row["hybrid_score"],
                -row["effective_games"],
                row["player_name"],
            )
        )
        for rank, row in enumerate(players, 1):
            row["rank"] = rank
        hero_rows.append(
            {
                "hero_id": hero_id,
                "hero_name": hero_name,
                "player_count": len(players),
                "players": players,
            }
        )
    hero_rows.sort(key=lambda row: (row["hero_name"], row["hero_id"]))
    return hero_rows


def build_rankings(
    target_matches: list[dict[str, Any]],
    history: list[dict[str, Any]],
    as_of: datetime,
    included_leagues: list[str],
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    hero_catalog: dict[int, str] | None = None,
) -> dict[str, Any]:
    league_id = str(target_matches[0].get("league_id") or "") if target_matches else ""
    team_rankings = compute_team_rankings(
        target_matches,
        history,
        as_of,
        half_life_days=half_life_days,
    )
    hero_rankings = compute_hero_rankings(
        target_matches,
        history,
        as_of,
        half_life_days=half_life_days,
        hero_catalog=hero_catalog,
    )
    return {
        "schema_version": 1,
        "league": {
            "league_id": league_id,
            "league_name": str(target_matches[0].get("league_name") or league_id)
            if target_matches
            else league_id,
        },
        "as_of": as_of.isoformat(sep=" "),
        "history_league_ids": included_leagues,
        "methodology": {
            "decay_half_life_days": half_life_days,
            "team_score": {
                "elo_weight": 0.72,
                "decayed_win_rate_weight": 0.28,
                "elo_k": DEFAULT_ELO_K,
                "elo_regression_half_life_days": DEFAULT_ELO_REGRESSION_HALF_LIFE_DAYS,
                "win_rate_prior_games": 6.0,
            },
            "player_hero_score": {
                "kda_weight": 0.40,
                "mvp_score_weight": 0.18,
                "participation_weight": 0.12,
                "damage_share_weight": 0.10,
                "gold_pace_weight": 0.08,
                "win_weight": 0.12,
                "prior_effective_games": 4.0,
                "normalization": "within season and role",
            },
        },
        "summary": {
            "history_matches": len(history),
            "team_count": len(team_rankings),
            "hero_count": len(hero_rankings),
            "player_hero_rows": sum(row["player_count"] for row in hero_rankings),
        },
        "team_rankings": team_rankings,
        "hero_rankings": hero_rankings,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", default=CURRENT_LEAGUE_ID)
    parser.add_argument("--exports-root", type=Path, default=REPO_ROOT / "analysis" / "exports")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--half-life-days", type=float, default=DEFAULT_HALF_LIFE_DAYS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.half_life_days <= 0:
        raise ValueError("--half-life-days must be positive")
    target_matches, history, as_of, included_leagues = load_history(
        args.exports_root,
        args.league_id,
    )
    hero_catalog_path = REPO_ROOT / "analysis" / "hero_draft_feature_vectors.json"
    hero_catalog: dict[int, str] = {}
    if hero_catalog_path.is_file():
        try:
            catalog = json.loads(hero_catalog_path.read_text(encoding="utf-8"))
            hero_catalog = {
                int(row["hero_id"]): str(row.get("hero_name") or row["hero_id"])
                for row in catalog.get("rows") or []
                if int(row.get("hero_id") or 0) > 0
            }
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            hero_catalog = {}
    artifact = build_rankings(
        target_matches,
        history,
        as_of,
        included_leagues,
        half_life_days=args.half_life_days,
        hero_catalog=hero_catalog,
    )
    output = args.output or (
        REPO_ROOT / "analysis" / "outputs" / args.league_id / DEFAULT_OUTPUT_NAME
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(artifact, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(artifact['team_rankings'])} teams and "
        f"{artifact['summary']['player_hero_rows']} player-hero rows: {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
