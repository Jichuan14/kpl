"""Build and query a contextual probability model for KPL BP simulations.

The model is deliberately interpretable.  For each legal candidate it starts
with the historical probability of selection at the current action, side, and
team action number.  It then applies shrunk lifts for heroes already visible
in the draft (own/opponent picks and bans).  The resulting scores are masked
to legal heroes and normalized to a probability distribution.

Examples:

    # Train from every exported season and write the default 2026 S3 artifact.
    python3 analysis/build_draft_model.py

    # Train from explicit exports, then score and simulate a saved board state.
    python3 analysis/build_draft_model.py \
      --input analysis/exports/20260001/bp_decisions.jsonl \
      --input analysis/exports/20260002/bp_decisions.jsonl \
      --input analysis/exports/20260003/bp_decisions.jsonl \
      --state analysis/example_draft_state.json

State files need ``bp_order`` (the next action, 1 through 20) and may provide
``blue_picks``, ``red_picks``, ``blue_bans``, ``red_bans``, and
``legal_hero_ids``.  If ``legal_hero_ids`` is absent, all trained heroes not
already used are considered legal.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from common import REPO_ROOT, connect, has_table

DEFAULT_OUTPUT = REPO_ROOT / "analysis" / "outputs" / "20260003" / "draft_model.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "analysis" / "outputs"
DEFAULT_INPUTS = sorted((REPO_ROOT / "analysis" / "exports").glob("*/bp_decisions.jsonl"))
ROLE_FIELDS = {
    "own_pick": "current_team_picks",
    "opponent_pick": "current_opponent_picks",
    "own_ban": "current_team_bans",
    "opponent_ban": "current_opponent_bans",
}


def read_decisions(paths: Iterable[Path]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for path in paths:
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
                decisions.append(row)
    if not decisions:
        raise ValueError("No BP decisions were found in the supplied input files")
    return decisions


def legal_heroes(decision: dict[str, Any]) -> set[int]:
    legal = {int(hero_id) for hero_id in decision.get("legal_hero_ids", []) if int(hero_id) > 0}
    selected = int(decision.get("selected_hero_id") or 0)
    if selected:
        legal.add(selected)
    return legal


def context_key(decision: dict[str, Any]) -> str:
    return "|".join(
        (
            str(decision.get("action") or "unknown"),
            str(decision.get("side") or "unknown"),
            str(int(decision.get("team_action_type_number") or 0)),
        )
    )


def visible_sources(decision: dict[str, Any]) -> Iterable[tuple[str, int]]:
    for role, field in ROLE_FIELDS.items():
        for hero_id in decision.get(field, []):
            hero = int(hero_id)
            if hero > 0:
                yield role, hero


def relation_key(context: str, role: str, source_id: int, target_id: int) -> str:
    return f"{context}|{role}|{source_id}|{target_id}"


def path_label(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_hero_metadata() -> dict[int, dict[str, str]]:
    """Use the local hero catalog so legal-but-unselected heroes keep labels."""
    try:
        with connect() as database:
            rows = database.execute(
                "SELECT hero_id, hero_name, hero_icon FROM heroes WHERE hero_id > 0"
            ).fetchall()
    except FileNotFoundError:
        return {}
    catalog = {
        int(row["hero_id"]): {
            "hero_name": str(row["hero_name"] or ""),
            "hero_icon": str(row["hero_icon"] or ""),
            "positions": [],
        }
        for row in rows
    }
    with connect() as database:
        positions_available = has_table(database, "hero_positions")
    if positions_available:
        with connect() as database:
            position_rows = database.execute(
                "SELECT hero_id, position FROM hero_positions WHERE position > 0"
            ).fetchall()
    else:
        position_rows = []
    for row in position_rows:
        hero_id = int(row["hero_id"])
        if hero_id in catalog:
            catalog[hero_id]["positions"].append(int(row["position"]))
    return catalog


def draft_sequence(decisions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    battles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        if not decision.get("is_peak_battle"):
            battles[str(decision.get("battle_id") or "")].append(decision)
    sequences: Counter[tuple[tuple[int, str, str, int], ...]] = Counter()
    for battle in battles.values():
        sequence = tuple(
            (
                int(row.get("bp_order") or 0),
                str(row.get("action") or ""),
                str(row.get("side") or ""),
                int(row.get("team_action_type_number") or 0),
            )
            for row in sorted(battle, key=lambda row: int(row.get("bp_order") or 0))
        )
        if sequence:
            sequences[sequence] += 1
    if not sequences:
        raise ValueError("Could not infer a draft sequence")
    sequence, _ = sequences.most_common(1)[0]
    return [
        {
            "bp_order": order,
            "action": action,
            "side": side,
            "team_action_type_number": slot,
        }
        for order, action, side, slot in sequence
    ]


def build_model(
    decisions: list[dict[str, Any]],
    *,
    alpha: float,
    min_relation_selections: int,
    shrinkage: float,
    max_lift: float,
    input_paths: list[Path],
) -> dict[str, Any]:
    usable = [
        row
        for row in decisions
        if not row.get("is_peak_battle")
        and str(row.get("action") or "") in {"pick", "ban"}
        and int(row.get("selected_hero_id") or 0) > 0
        and legal_heroes(row)
    ]
    base_selections: Counter[tuple[str, int]] = Counter()
    base_opportunities: Counter[tuple[str, int]] = Counter()
    action_selections: Counter[tuple[str, int]] = Counter()
    action_opportunities: Counter[tuple[str, int]] = Counter()
    relation_selections: Counter[str] = Counter()
    hero_names: dict[int, str] = {}

    for row in usable:
        context = context_key(row)
        action = str(row["action"])
        selected = int(row["selected_hero_id"])
        hero_names[selected] = str(row.get("selected_hero_name") or selected)
        base_selections[(context, selected)] += 1
        action_selections[(action, selected)] += 1
        for candidate in legal_heroes(row):
            base_opportunities[(context, candidate)] += 1
            action_opportunities[(action, candidate)] += 1
        for role, source in visible_sources(row):
            relation_selections[relation_key(context, role, source, selected)] += 1

    retained_relations = {
        key for key, selections in relation_selections.items() if selections >= min_relation_selections
    }
    relation_opportunities: Counter[str] = Counter()
    for row in usable:
        context = context_key(row)
        legal = legal_heroes(row)
        for role, source in visible_sources(row):
            prefix = f"{context}|{role}|{source}|"
            for candidate in legal:
                key = f"{prefix}{candidate}"
                if key in retained_relations:
                    relation_opportunities[key] += 1

    all_hero_ids = sorted({hero for _, hero in action_opportunities})
    catalog = load_hero_metadata()
    base_rows = [
        {
            "context": context,
            "hero_id": hero_id,
            "selections": base_selections[(context, hero_id)],
            "opportunities": opportunities,
        }
        for (context, hero_id), opportunities in sorted(base_opportunities.items())
    ]
    action_rows = [
        {
            "action": action,
            "hero_id": hero_id,
            "selections": action_selections[(action, hero_id)],
            "opportunities": opportunities,
        }
        for (action, hero_id), opportunities in sorted(action_opportunities.items())
    ]
    relation_rows = [
        {
            "key": key,
            "selections": relation_selections[key],
            "opportunities": relation_opportunities[key],
        }
        for key in sorted(retained_relations)
        if relation_opportunities[key]
    ]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "training_inputs": [path_label(path) for path in input_paths],
        "training_decisions": len(usable),
        "hero_ids": all_hero_ids,
        "hero_names": {
            str(hero_id): catalog.get(hero_id, {}).get("hero_name")
            or hero_names.get(hero_id, str(hero_id))
            for hero_id in all_hero_ids
        },
        "hero_icons": {
            str(hero_id): catalog.get(hero_id, {}).get("hero_icon", "")
            for hero_id in all_hero_ids
        },
        "hero_positions": {
            str(hero_id): catalog.get(hero_id, {}).get("positions", [])
            for hero_id in all_hero_ids
        },
        "role_ids": sorted(
            {
                position
                for hero_id in all_hero_ids
                for position in catalog.get(hero_id, {}).get("positions", [])
            }
        ),
        "draft_sequence": draft_sequence(usable),
        "config": {
            "alpha": alpha,
            "min_relation_selections": min_relation_selections,
            "shrinkage": shrinkage,
            "max_lift": max_lift,
        },
        "base": base_rows,
        "action": action_rows,
        "relations": relation_rows,
    }


def index_model(model: dict[str, Any]) -> dict[str, Any]:
    base = {(row["context"], int(row["hero_id"])): row for row in model["base"]}
    action = {(row["action"], int(row["hero_id"])): row for row in model["action"]}
    relations = {row["key"]: row for row in model["relations"]}
    return {"model": model, "base": base, "action": action, "relations": relations}


def state_context(state: dict[str, Any], step: dict[str, Any]) -> str:
    return f"{step['action']}|{step['side']}|{int(step['team_action_type_number'])}"


def state_sources(state: dict[str, Any], side: str) -> Iterable[tuple[str, int]]:
    own = "blue" if side == "blue" else "red"
    opponent = "red" if own == "blue" else "blue"
    for role, values in (
        ("own_pick", state.get(f"{own}_picks", [])),
        ("opponent_pick", state.get(f"{opponent}_picks", [])),
        ("own_ban", state.get(f"{own}_bans", [])),
        ("opponent_ban", state.get(f"{opponent}_bans", [])),
    ):
        for hero_id in values:
            hero = int(hero_id)
            if hero > 0:
                yield role, hero


def legal_state_heroes(
    state: dict[str, Any], model: dict[str, Any], step: dict[str, Any] | None = None
) -> list[int]:
    used = {
        int(hero)
        for key in ("blue_picks", "red_picks", "blue_bans", "red_bans")
        for hero in state.get(key, [])
    }
    if "legal_hero_ids" in state:
        candidates = sorted(
            {
                int(hero)
                for hero in state["legal_hero_ids"]
                if int(hero) > 0 and int(hero) not in used
            }
        )
    else:
        candidates = [hero for hero in model["hero_ids"] if hero not in used]
    if step is None:
        step = next(
            (
                item
                for item in model["draft_sequence"]
                if int(item["bp_order"]) == int(state["bp_order"])
            ),
            None,
        )
    if not step or step["action"] != "pick":
        return candidates
    team_picks = state.get(f"{step['side']}_picks", [])
    return [
        hero_id
        for hero_id in candidates
        if roles_are_feasible(model, [*team_picks, hero_id])
    ]


def roles_are_feasible(model: dict[str, Any], hero_ids: Iterable[int]) -> bool:
    """Return whether each picked hero can occupy a different eligible role."""
    role_map = model.get("hero_positions", {})
    assignments: dict[int, int] = {}

    def assign(hero_id: int, visited: set[int]) -> bool:
        for role_id in role_map.get(str(int(hero_id)), []):
            if role_id in visited:
                continue
            visited.add(role_id)
            assigned_hero = assignments.get(role_id)
            if assigned_hero is None or assign(assigned_hero, visited):
                assignments[role_id] = int(hero_id)
                return True
        return False

    return all(assign(int(hero_id), set()) for hero_id in hero_ids)


def predict(index: dict[str, Any], state: dict[str, Any], step: dict[str, Any]) -> list[dict[str, Any]]:
    model = index["model"]
    config = model["config"]
    context = state_context(state, step)
    action = step["action"]
    alpha = float(config["alpha"])
    max_log_lift = math.log(float(config["max_lift"]))
    candidates = legal_state_heroes(state, model, step)
    sources = list(state_sources(state, step["side"]))
    scored: list[tuple[int, float]] = []

    for hero_id in candidates:
        action_row = index["action"].get((action, hero_id))
        action_probability = (
            action_row["selections"] / action_row["opportunities"]
            if action_row and action_row["opportunities"]
            else 1e-9
        )
        base_row = index["base"].get((context, hero_id))
        base_probability = (
            (base_row["selections"] + alpha * action_probability)
            / (base_row["opportunities"] + alpha)
            if base_row
            else action_probability
        )
        score = math.log(max(base_probability, 1e-12))
        for role, source_id in sources:
            relation = index["relations"].get(relation_key(context, role, source_id, hero_id))
            if not relation:
                continue
            relation_probability = (
                relation["selections"] + alpha * base_probability
            ) / (relation["opportunities"] + alpha)
            lift = relation_probability / max(base_probability, 1e-12)
            weight = relation["opportunities"] / (
                relation["opportunities"] + float(config["shrinkage"])
            )
            score += weight * max(-max_log_lift, min(max_log_lift, math.log(lift)))
        scored.append((hero_id, score))

    maximum = max((score for _, score in scored), default=0.0)
    weights = [(hero, math.exp(score - maximum)) for hero, score in scored]
    total = sum(weight for _, weight in weights) or 1.0
    return [
        {
            "hero_id": hero_id,
            "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
            "probability": weight / total,
        }
        for hero_id, weight in sorted(weights, key=lambda row: row[1], reverse=True)
    ]


def apply_action(state: dict[str, Any], step: dict[str, Any], hero_id: int) -> None:
    key = f"{step['side']}_{'picks' if step['action'] == 'pick' else 'bans'}"
    state.setdefault(key, []).append(hero_id)
    if "legal_hero_ids" in state:
        state["legal_hero_ids"] = [
            candidate for candidate in state["legal_hero_ids"] if int(candidate) != hero_id
        ]


def simulate(index: dict[str, Any], state: dict[str, Any], rollouts: int, seed: int | None) -> dict[str, Any]:
    for side in ("blue", "red"):
        if not roles_are_feasible(index["model"], state.get(f"{side}_picks", [])):
            raise ValueError(
                f"{side.title()} picks cannot be assigned to distinct eligible roles"
            )
    sequence = index["model"]["draft_sequence"]
    start_order = int(state["bp_order"])
    steps = [step for step in sequence if int(step["bp_order"]) >= start_order]
    if not steps or int(steps[0]["bp_order"]) != start_order:
        raise ValueError(f"bp_order={start_order} is not in the trained draft sequence")
    rng = random.Random(seed)
    event_counts: dict[int, Counter[int]] = defaultdict(Counter)
    ban_counts: Counter[int] = Counter()
    for _ in range(rollouts):
        current = json.loads(json.dumps(state))
        for step in steps:
            probabilities = predict(index, current, step)
            if not probabilities:
                break
            population = [row["hero_id"] for row in probabilities]
            weights = [row["probability"] for row in probabilities]
            selected = rng.choices(population, weights=weights, k=1)[0]
            event_counts[int(step["bp_order"])][selected] += 1
            if step["action"] == "ban":
                ban_counts[selected] += 1
            apply_action(current, step, selected)
    names = index["model"]["hero_names"]
    return {
        "rollouts": rollouts,
        "next_actions": {
            str(order): [
                {
                    "hero_id": hero_id,
                    "hero_name": names.get(str(hero_id), str(hero_id)),
                    "probability": count / rollouts,
                }
                for hero_id, count in counts.most_common(10)
            ]
            for order, counts in event_counts.items()
        },
        "banned_by_end": [
            {
                "hero_id": hero_id,
                "hero_name": names.get(str(hero_id), str(hero_id)),
                "probability": count / rollouts,
            }
            for hero_id, count in ban_counts.most_common(20)
        ],
    }


def write_model(model: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(model, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"Wrote {path_label(output)} with "
        f"{model['training_decisions']} decisions and "
        f"{len(model['relations'])} contextual relations."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", dest="inputs")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--per-season",
        action="store_true",
        help="Write one model per input season, trained on that season and earlier inputs.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Parent directory for --per-season artifacts.",
    )
    parser.add_argument("--alpha", type=float, default=12.0)
    parser.add_argument("--min-relation-selections", type=int, default=2)
    parser.add_argument("--shrinkage", type=float, default=20.0)
    parser.add_argument("--max-lift", type=float, default=3.0)
    parser.add_argument("--state", type=Path, help="Optional state JSON to score and simulate")
    parser.add_argument("--rollouts", type=int, default=1000)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    inputs = args.inputs or DEFAULT_INPUTS
    missing = [path for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing input files: {', '.join(map(str, missing))}")
    if args.alpha <= 0 or args.shrinkage <= 0 or args.max_lift <= 1:
        raise ValueError("--alpha and --shrinkage must be positive; --max-lift must exceed 1")
    if args.min_relation_selections < 1 or args.rollouts < 1:
        raise ValueError("--min-relation-selections and --rollouts must be at least 1")

    if args.per_season:
        if args.state:
            raise ValueError("--state cannot be used with --per-season; score a specific model afterward")
        ordered_inputs = sorted(inputs, key=lambda path: path.parent.name)
        for index, target_input in enumerate(ordered_inputs, 1):
            model = build_model(
                read_decisions(ordered_inputs[:index]),
                alpha=args.alpha,
                min_relation_selections=args.min_relation_selections,
                shrinkage=args.shrinkage,
                max_lift=args.max_lift,
                input_paths=ordered_inputs[:index],
            )
            write_model(model, args.output_root / target_input.parent.name / "draft_model.json")
        return

    model = build_model(
        read_decisions(inputs),
        alpha=args.alpha,
        min_relation_selections=args.min_relation_selections,
        shrinkage=args.shrinkage,
        max_lift=args.max_lift,
        input_paths=inputs,
    )
    write_model(model, args.output)
    if args.state:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("State JSON must contain an object")
        index = index_model(model)
        step = next(
            (item for item in model["draft_sequence"] if int(item["bp_order"]) == int(state["bp_order"])),
            None,
        )
        if not step:
            raise ValueError(f"bp_order={state['bp_order']} is not in the trained draft sequence")
        result = {
            "next_step": step,
            "next_action_probabilities": predict(index, state, step)[:20],
            "simulation": simulate(index, state, args.rollouts, args.seed),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
