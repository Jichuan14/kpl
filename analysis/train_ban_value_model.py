#!/usr/bin/env python3
"""Train an interpretable opponent-denial model for ban recommendations."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = REPO_ROOT / "analysis" / "exports"
OUTPUT_ROOT = REPO_ROOT / "analysis" / "outputs"
DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"
VERSION = "ban-value-model-v1"
DEFAULT_RECENCY_DECAY = 0.65


@dataclass
class WeightedStat:
    wins: float = 0.0
    games: float = 0.0

    def add(self, won: bool, weight: float) -> None:
        self.wins += weight * float(won)
        self.games += weight

    def row(self, *keys: Any) -> list[Any]:
        return [*keys, round(self.wins, 8), round(self.games, 8)]


@dataclass
class TrainingState:
    total: WeightedStat = field(default_factory=WeightedStat)
    team: dict[str, WeightedStat] = field(
        default_factory=lambda: defaultdict(WeightedStat)
    )
    global_pick: dict[int, WeightedStat] = field(
        default_factory=lambda: defaultdict(WeightedStat)
    )
    team_pick: dict[tuple[str, int], WeightedStat] = field(
        default_factory=lambda: defaultdict(WeightedStat)
    )
    global_ban: dict[int, WeightedStat] = field(
        default_factory=lambda: defaultdict(WeightedStat)
    )
    opponent_ban: dict[tuple[str, int], WeightedStat] = field(
        default_factory=lambda: defaultdict(WeightedStat)
    )
    ally_pair: dict[tuple[int, int], WeightedStat] = field(
        default_factory=lambda: defaultdict(WeightedStat)
    )
    counter_pair: dict[tuple[int, int], WeightedStat] = field(
        default_factory=lambda: defaultdict(WeightedStat)
    )
    behavior: dict[tuple[str, int], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    behavior_total: dict[str, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    opponent_behavior: dict[tuple[str, str, int], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    opponent_behavior_total: dict[tuple[str, str], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    hero_names: dict[int, str] = field(default_factory=dict)
    ban_decisions: int = 0
    battles: int = 0


def read_decisions(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {path}:{line_number}") from exc
                if isinstance(row, dict):
                    rows.append(row)
    if not rows:
        raise ValueError("No BP decisions were found")
    return rows


def chronological_league_ids(target_league_id: str) -> list[str]:
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT b.league_id, MIN(COALESCE(m.start_time, '')) AS first_match
            FROM battles b
            JOIN matches m ON m.match_id = b.match_id
            WHERE b.win_camp IN (1, 2)
            GROUP BY b.league_id
            ORDER BY first_match, b.league_id
            """
        ).fetchall()
    finally:
        connection.close()
    league_ids = [str(row[0]) for row in rows]
    if target_league_id not in league_ids:
        raise ValueError(
            f"Target league {target_league_id!r} has no completed battles"
        )
    return league_ids[: league_ids.index(target_league_id) + 1]


def ensure_decision_export(league_id: str) -> Path:
    export_dir = EXPORT_ROOT / league_id
    matches = export_dir / "matches.jsonl"
    decisions = export_dir / "bp_decisions.jsonl"
    if decisions.is_file():
        return decisions
    export_dir.mkdir(parents=True, exist_ok=True)
    commands = (
        [
            sys.executable,
            str(REPO_ROOT / "analysis" / "export_match_data.py"),
            "--league-id",
            league_id,
            "--output",
            str(matches),
        ],
        [
            sys.executable,
            str(REPO_ROOT / "analysis" / "build_bp_decisions.py"),
            "--input",
            str(matches),
            "--output",
            str(decisions),
        ],
    )
    for command in commands:
        process = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "unknown error").strip()
            raise RuntimeError(
                f"Could not prepare {league_id} ban decisions: {detail}"
            )
    return decisions


def selected_inputs(target_league_id: str) -> list[Path]:
    return [
        ensure_decision_export(league_id)
        for league_id in chronological_league_ids(target_league_id)
    ]


def ban_context(row: dict[str, Any]) -> str:
    return "|".join(
        (
            str(row.get("side") or "unknown"),
            str(int(row.get("team_action_type_number") or 0)),
        )
    )


def group_battles(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("is_peak_battle"):
            continue
        battle_id = str(row.get("battle_id") or "")
        if battle_id:
            grouped[battle_id].append(row)
    return grouped


def build_state(
    rows: list[dict[str, Any]], season_weights: dict[str, float]
) -> TrainingState:
    state = TrainingState()
    for battle_rows in group_battles(rows).values():
        first = battle_rows[0]
        league_id = str(first.get("league_id") or "")
        weight = float(season_weights.get(league_id, 1.0))
        winner = str(first.get("battle_winner_team_id") or "")
        if not winner:
            continue
        teams = {
            str(row.get("acting_team_id") or "")
            for row in battle_rows
            if str(row.get("acting_team_id") or "")
        }
        if len(teams) != 2:
            continue
        picks: dict[str, set[int]] = {team: set() for team in teams}
        bans: list[tuple[str, str, int, dict[str, Any]]] = []
        for row in battle_rows:
            team = str(row.get("acting_team_id") or "")
            opponent = str(row.get("opponent_team_id") or "")
            hero = int(row.get("selected_hero_id") or 0)
            action = str(row.get("action") or "")
            if not team or hero <= 0:
                continue
            state.hero_names[hero] = str(row.get("selected_hero_name") or hero)
            if action == "pick":
                picks.setdefault(team, set()).add(hero)
            elif action == "ban" and hero in {
                int(value) for value in row.get("legal_hero_ids", [])
            }:
                bans.append((team, opponent, hero, row))
        if any(len(team_picks) != 5 for team_picks in picks.values()):
            continue
        state.battles += 1
        for team in teams:
            won = team == winner
            state.total.add(won, weight)
            state.team[team].add(won, weight)
            for hero in picks[team]:
                state.global_pick[hero].add(won, weight)
                state.team_pick[(team, hero)].add(won, weight)
            for first_hero, second_hero in combinations(sorted(picks[team]), 2):
                state.ally_pair[(first_hero, second_hero)].add(won, weight)
            opponent = next(other for other in teams if other != team)
            for own_hero in picks[team]:
                for enemy_hero in picks[opponent]:
                    state.counter_pair[(own_hero, enemy_hero)].add(won, weight)
        for team, opponent, hero, row in bans:
            won = team == winner
            state.global_ban[hero].add(won, weight)
            state.opponent_ban[(opponent, hero)].add(won, weight)
            context = ban_context(row)
            state.behavior[(context, hero)] += weight
            state.behavior_total[context] += weight
            state.opponent_behavior[(context, opponent, hero)] += weight
            state.opponent_behavior_total[(context, opponent)] += weight
            state.ban_decisions += 1
    return state


def stat_effect(
    stat: WeightedStat | None, baseline: float, prior: float
) -> tuple[float, float]:
    if stat is None or stat.games <= 0:
        return 0.0, 0.0
    rate = (stat.wins + prior * baseline) / (stat.games + prior)
    return rate - baseline, stat.games / (stat.games + prior)


def candidate_score(
    state: TrainingState,
    row: dict[str, Any],
    hero_id: int,
) -> float:
    global_rate = state.total.wins / state.total.games if state.total.games else 0.5
    team = str(row.get("acting_team_id") or "")
    opponent = str(row.get("opponent_team_id") or "")
    acting = state.team.get(team)
    enemy = state.team.get(opponent)
    acting_rate = acting.wins / acting.games if acting and acting.games else global_rate
    enemy_rate = enemy.wins / enemy.games if enemy and enemy.games else global_rate
    global_pick, _ = stat_effect(state.global_pick.get(hero_id), global_rate, 40.0)
    enemy_pick, _ = stat_effect(state.team_pick.get((opponent, hero_id)), enemy_rate, 18.0)
    self_pick, _ = stat_effect(state.team_pick.get((team, hero_id)), acting_rate, 18.0)
    global_ban, _ = stat_effect(state.global_ban.get(hero_id), global_rate, 40.0)
    enemy_ban, _ = stat_effect(state.opponent_ban.get((opponent, hero_id)), acting_rate, 18.0)
    enemy_games = enemy.games if enemy else 0.0
    self_games = acting.games if acting else 0.0
    enemy_preference = (
        state.team_pick.get((opponent, hero_id), WeightedStat()).games
        / max(enemy_games, 1.0)
    )
    self_preference = (
        state.team_pick.get((team, hero_id), WeightedStat()).games
        / max(self_games, 1.0)
    )
    synergy = []
    for visible in row.get("current_opponent_picks", []):
        pair = tuple(sorted((hero_id, int(visible))))
        synergy.append(stat_effect(state.ally_pair.get(pair), enemy_rate, 24.0)[0])
    counters = [
        stat_effect(
            state.counter_pair.get((hero_id, int(visible))), enemy_rate, 30.0
        )[0]
        for visible in row.get("current_team_picks", [])
    ]
    context = ban_context(row)
    context_total = state.behavior_total.get(context, 0.0)
    global_behavior = state.behavior.get((context, hero_id), 0.0) / max(context_total, 1.0)
    opponent_total = state.opponent_behavior_total.get((context, opponent), 0.0)
    opponent_behavior = state.opponent_behavior.get((context, opponent, hero_id), 0.0) / max(opponent_total, 1.0)
    behavior = 0.65 * global_behavior + 0.35 * opponent_behavior
    return (
        0.10 * behavior
        + 0.75 * global_pick
        + 1.35 * enemy_pick
        + 0.35 * enemy_preference
        + 0.45 * global_ban
        + 0.70 * enemy_ban
        + 0.70 * (sum(synergy) / len(synergy) if synergy else 0.0)
        + 0.85 * (sum(counters) / len(counters) if counters else 0.0)
        - 0.75 * self_pick
        - 0.25 * self_preference
    )


def validate(
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    season_weights: dict[str, float],
) -> dict[str, Any]:
    state = build_state(train_rows, season_weights)
    ranks: list[int] = []
    for row in test_rows:
        if row.get("is_peak_battle") or row.get("action") != "ban":
            continue
        selected = int(row.get("selected_hero_id") or 0)
        legal = [int(value) for value in row.get("legal_hero_ids", []) if int(value) > 0]
        if selected <= 0 or selected not in legal:
            continue
        ordered = sorted(
            legal,
            key=lambda hero: candidate_score(state, row, hero),
            reverse=True,
        )
        ranks.append(ordered.index(selected) + 1)
    return {
        "ban_decisions": len(ranks),
        "top_1_accuracy": sum(rank <= 1 for rank in ranks) / len(ranks) if ranks else 0.0,
        "top_3_accuracy": sum(rank <= 3 for rank in ranks) / len(ranks) if ranks else 0.0,
        "top_5_accuracy": sum(rank <= 5 for rank in ranks) / len(ranks) if ranks else 0.0,
        "mean_selected_rank": sum(ranks) / len(ranks) if ranks else 0.0,
    }


def serialize_state(
    state: TrainingState,
    league_ids: list[str],
    season_weights: dict[str, float],
    validation: dict[str, Any],
) -> dict[str, Any]:
    def stat_rows(mapping: dict[Any, WeightedStat]) -> list[list[Any]]:
        rows = []
        for key, stat in sorted(mapping.items(), key=lambda item: str(item[0])):
            keys = key if isinstance(key, tuple) else (key,)
            rows.append(stat.row(*keys))
        return rows

    return {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "league_ids": league_ids,
            "target_league_id": league_ids[-1],
            "battle_count": state.battles,
            "ban_decisions": state.ban_decisions,
            "season_weights": season_weights,
        },
        "config": {
            "global_pick_prior": 40.0,
            "team_pick_prior": 18.0,
            "global_ban_prior": 40.0,
            "opponent_ban_prior": 18.0,
            "pair_prior": 24.0,
            "counter_prior": 30.0,
            "policy_weight": 0.70,
            "artifact_behavior_weight": 0.30,
            "uncertainty_scale": 0.08,
        },
        "global": state.total.row(),
        "team": stat_rows(state.team),
        "global_pick": stat_rows(state.global_pick),
        "team_pick": stat_rows(state.team_pick),
        "global_ban": stat_rows(state.global_ban),
        "opponent_ban": stat_rows(state.opponent_ban),
        "ally_pair": stat_rows(state.ally_pair),
        "counter_pair": stat_rows(state.counter_pair),
        "behavior": [
            [context, hero, round(count, 8)]
            for (context, hero), count in sorted(state.behavior.items())
        ],
        "behavior_total": dict(state.behavior_total),
        "opponent_behavior": [
            [context, opponent, hero, round(count, 8)]
            for (context, opponent, hero), count in sorted(state.opponent_behavior.items())
        ],
        "opponent_behavior_total": [
            [context, opponent, round(count, 8)]
            for (context, opponent), count in sorted(state.opponent_behavior_total.items())
        ],
        "hero_names": {str(hero): name for hero, name in state.hero_names.items()},
        "validation": validation,
        "interpretation": "Relative opponent-denial value, not causal win probability.",
    }


def train(args: argparse.Namespace) -> dict[str, Any]:
    paths = selected_inputs(args.league_id)
    league_ids = [path.parent.name for path in paths]
    season_weights = {
        league_id: args.recency_decay ** (len(league_ids) - index - 1)
        for index, league_id in enumerate(league_ids)
    }
    rows = read_decisions(paths)
    final_league = league_ids[-1]
    development = [row for row in rows if str(row.get("league_id")) != final_league]
    holdout = [row for row in rows if str(row.get("league_id")) == final_league]
    validation = validate(development, holdout, season_weights) if development else {}
    state = build_state(rows, season_weights)
    return serialize_state(state, league_ids, season_weights, validation)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validation-output", type=Path)
    parser.add_argument("--recency-decay", type=float, default=DEFAULT_RECENCY_DECAY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0.0 < args.recency_decay <= 1.0:
        raise ValueError("--recency-decay must be in (0, 1]")
    output = args.output or OUTPUT_ROOT / args.league_id / "ban_value_model.json"
    validation_output = args.validation_output or output.with_name("ban_value_validation.json")
    payload = train(args)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validation_payload = {
        "version": VERSION,
        "generated_at": payload["generated_at"],
        "target_league_id": args.league_id,
        "validation": payload["validation"],
    }
    validation_output.write_text(
        json.dumps(validation_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["source"], indent=2))
    print(json.dumps(payload["validation"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
