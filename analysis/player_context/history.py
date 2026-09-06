"""Deterministic as-of histories, roster hypotheses, and familiarity features."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable


ROLE_IDS = (2, 4, 5, 6, 7)
HISTORY_K = 20
MAX_AGE_DAYS = 120
HALF_LIFE_DAYS = 30.0


def normalize_player_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()


def player_key(team_id: str, player_name: str) -> str:
    return f"{team_id}:{normalize_player_name(player_name)}"


def parse_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


@dataclass(frozen=True)
class PlayerAppearance:
    team_id: str
    player_key: str
    player_name: str
    hero_id: int
    role_id: int


@dataclass(frozen=True)
class HistoricalGame:
    event_date: date
    match_id: str
    battle_id: str
    battle_seq: int
    appearances: tuple[PlayerAppearance, ...]


def load_historical_games(paths: Iterable[Path]) -> list[HistoricalGame]:
    games: list[HistoricalGame] = []
    for path in paths:
        with path.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                match = json.loads(line)
                event_date = parse_date(str(match["start_time"]))
                for battle in match.get("battles", []):
                    appearances = []
                    for player in battle.get("players", []):
                        role = int(player.get("position") or 0)
                        hero = int(player.get("hero_id") or 0)
                        team = str(player.get("team_id") or "")
                        name = str(player.get("player_name") or "")
                        if role in ROLE_IDS and hero > 0 and team and name:
                            appearances.append(PlayerAppearance(team, player_key(team, name), name, hero, role))
                    games.append(HistoricalGame(event_date, str(match["match_id"]), str(battle["battle_id"]), int(battle.get("battle_seq") or 0), tuple(appearances)))
    return sorted(games, key=lambda game: (game.event_date, game.match_id, game.battle_seq, game.battle_id))


class TemporalContextBuilder:
    """Build contexts solely from games on dates strictly before the cutoff."""

    version = "player_context_v1"

    def __init__(self, games: Iterable[HistoricalGame], hero_ids: list[int], *, aliases: dict[str, str] | None = None):
        self.games = tuple(sorted(games, key=lambda game: (game.event_date, game.match_id, game.battle_seq, game.battle_id)))
        self.hero_ids = tuple(int(value) for value in hero_ids)
        self.hero_to_index = {hero: index for index, hero in enumerate(self.hero_ids)}
        self.aliases = dict(aliases or {})
        self.alias_sha256 = hashlib.sha256(json.dumps(self.aliases, sort_keys=True).encode()).hexdigest()

    def eligible_games(self, cutoff: date) -> tuple[HistoricalGame, ...]:
        return tuple(game for game in self.games if game.event_date < cutoff and 0 <= (cutoff - game.event_date).days <= MAX_AGE_DAYS)

    @staticmethod
    def _decay(age_days: int) -> float:
        return 0.5 ** (age_days / HALF_LIFE_DAYS)

    def roster(self, team_id: str, cutoff: date) -> list[list[dict[str, Any]]]:
        eligible = [game for game in self.eligible_games(cutoff) if any(a.team_id == team_id for a in game.appearances)][-20:]
        output = []
        for role in ROLE_IDS:
            counts: Counter[str] = Counter()
            names: dict[str, str] = {}
            for game in eligible:
                weight = self._decay((cutoff - game.event_date).days)
                for appearance in game.appearances:
                    if appearance.team_id == team_id and appearance.role_id == role:
                        key = self.aliases.get(appearance.player_key, appearance.player_key)
                        counts[key] += weight; names[key] = appearance.player_name
            retained = sorted(counts, key=lambda key: (-counts[key], key))[:2]
            retained_mass = sum(counts[key] for key in retained)
            unknown_mass = 1.0 + sum(counts.values()) - retained_mass
            total = retained_mass + unknown_mass
            components = [{"player_key": key, "player_name": names[key], "weight": counts[key] / total} for key in retained]
            components.append({"player_key": "<unknown>", "player_name": "", "weight": unknown_mass / total})
            output.append(components)
        return output

    def player_history(self, key: str, cutoff: date, *, role_id: int | None = None) -> dict[str, Any]:
        rows = []
        effective = 0.0
        for game in self.eligible_games(cutoff):
            for appearance in game.appearances:
                canonical = self.aliases.get(appearance.player_key, appearance.player_key)
                if canonical == key and (role_id is None or appearance.role_id == role_id):
                    age = (cutoff - game.event_date).days
                    effective += self._decay(age)
                    rows.append({"hero_id": appearance.hero_id, "role_id": appearance.role_id, "age_days": age,
                                 "match_id": game.match_id, "battle_id": game.battle_id})
        return {"tokens": rows[-HISTORY_K:], "eligible_games": len(rows), "n_eff": effective,
                "reliability": effective / (effective + 10.0), "last_age_days": rows[-1]["age_days"] if rows else None}

    def hero_role_prior(self, cutoff: date) -> list[list[float]]:
        counts = [[1e-4] * 5 for _ in self.hero_ids]
        for game in self.eligible_games(cutoff):
            weight = self._decay((cutoff - game.event_date).days)
            for appearance in game.appearances:
                index = self.hero_to_index.get(appearance.hero_id)
                if index is not None:
                    counts[index][ROLE_IDS.index(appearance.role_id)] += weight
        return [[value / sum(row) for value in row] for row in counts]

    def familiarity(self, team_id: str, roster: list[list[dict[str, Any]]], cutoff: date) -> list[list[float]]:
        eligible = self.eligible_games(cutoff)
        league = defaultdict(Counter); team = defaultdict(Counter); players = defaultdict(Counter)
        for game in eligible:
            weight = self._decay((cutoff - game.event_date).days)
            for a in game.appearances:
                league[a.role_id][a.hero_id] += weight
                if a.team_id == team_id: team[a.role_id][a.hero_id] += weight
                players[(self.aliases.get(a.player_key, a.player_key), a.role_id)][a.hero_id] += weight
        result = [[0.0] * len(self.hero_ids) for _ in ROLE_IDS]
        for role_index, role in enumerate(ROLE_IDS):
            league_total = sum(league[role].values())
            league_p = {hero: (league[role][hero] + 1e-4) / (league_total + 1e-4 * len(self.hero_ids)) for hero in self.hero_ids}
            team_total = sum(team[role].values())
            team_p = {hero: (team[role][hero] + 10 * league_p[hero]) / (team_total + 10) for hero in self.hero_ids}
            for component in roster[role_index]:
                if component["player_key"] == "<unknown>":
                    probabilities = team_p
                else:
                    counter = players[(component["player_key"], role)]; total = sum(counter.values())
                    probabilities = {hero: (counter[hero] + 10 * team_p[hero]) / (total + 10) for hero in self.hero_ids}
                for hero_index, hero in enumerate(self.hero_ids):
                    result[role_index][hero_index] += component["weight"] * probabilities[hero]
        return result

    def build(self, own_team_id: str, opponent_team_id: str, cutoff: date) -> dict[str, Any]:
        own_roster, opponent_roster = self.roster(own_team_id, cutoff), self.roster(opponent_team_id, cutoff)
        rosters = [own_roster, opponent_roster]
        histories = [[[self.player_history(c["player_key"], cutoff, role_id=ROLE_IDS[r]) if c["player_key"] != "<unknown>" else {"tokens": [], "eligible_games": 0, "n_eff": 0.0, "reliability": 0.0, "last_age_days": None} for c in roster[r]] for r in range(5)] for roster in rosters]
        maximum_date = max((game.event_date for game in self.eligible_games(cutoff)), default=None)
        identity = {"version": self.version, "cutoff": cutoff.isoformat(), "teams": [own_team_id, opponent_team_id],
                    "max_source_date": maximum_date.isoformat() if maximum_date else None, "alias_sha256": self.alias_sha256}
        return {"context_id": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
                "context_as_of": cutoff.isoformat(), "maximum_source_event_date": identity["max_source_date"],
                "roster_source": "inferred_prior_dates", "rosters": rosters, "histories": histories,
                "familiarity": [self.familiarity(own_team_id, own_roster, cutoff), self.familiarity(opponent_team_id, opponent_roster, cutoff)],
                "hero_role_prior": self.hero_role_prior(cutoff), "identity": identity}
