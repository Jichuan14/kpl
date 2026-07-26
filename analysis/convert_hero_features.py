#!/usr/bin/env python3
"""Convert a Chinese hero capability list into model-ready JSON.

The input format is the one used in the design notes, for example::

    【对抗路】
    **英雄: 吕布** | 伤害: 物理、真实 | 控制: 强 | 位移: 大 | 霸体: 有

It also compares the parsed names with the local KPL ``heroes`` table.  The
comparison deliberately uses names rather than guessed IDs: a wrong ID is much
more damaging to a training dataset than a row left unresolved for review.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "backend" / "data" / "kpl_bp.db"
LANE_MAP = {
    "对抗路": "clash",
    "中路": "mid",
    "打野": "jungle",
    "发育路": "farm",
    "游走": "roam",
    "Clash": "clash",
    "Mid": "mid",
    "Jungle": "jungle",
    "Farm": "farm",
    "Roam": "roam",
}
DAMAGE_MAP = {"物理": "physical", "法术": "magic", "真实": "true"}
DAMAGE_MAP.update({"Physical": "physical", "Magic": "magic", "True": "true"})
CONTROL_MAP = {"无": 0, "弱": 1, "强": 2, "None": 0, "Weak": 1, "Strong": 2}
MOBILITY_MAP = {"无": 0, "小": 1, "大": 2, "None": 0, "Small": 1, "Large": 2}
IMMUNITY_MAP = {"无": False, "有": True, "No": False, "Yes": True}
HERO_LINE = re.compile(
    r"\*\*(?:英雄|Hero):\s*(?P<name>[^*]+?)\*\*\s*\|\s*"
    r"(?:伤害|Damage):\s*(?P<damage>[^|]+?)\s*\|\s*"
    r"(?:控制|Control):\s*(?P<control>[^|]+?)\s*\|\s*"
    r"(?:位移|Mobility):\s*(?P<mobility>[^|]+?)\s*\|\s*"
    r"(?:霸体|Unstoppable):\s*(?P<immunity>\S+)"
)
HEAL_LINE = re.compile(r"\*\*(?:英雄|Hero):\s*(?P<name>[^*]+?)\*\*\s*\|\s*Heal:\s*(?P<heal>[012])$")


def parse_source(source: str) -> list[dict]:
    """Parse supplied Markdown, failing loudly on malformed hero rows."""
    lane: str | None = None
    heroes: list[dict] = []
    healing: dict[str, int] = {}
    errors: list[str] = []
    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        header = re.fullmatch(r"(?:【(?P<cn_lane>[^】]+)】|\[(?P<en_lane>[^\]]+)\])", line)
        if header:
            lane = header.group("cn_lane") or header.group("en_lane")
            if lane not in LANE_MAP:
                errors.append(f"line {line_number}: unknown lane {lane!r}")
            continue
        if "英雄:" not in line and "Hero:" not in line:
            continue
        heal_match = HEAL_LINE.fullmatch(line)
        if heal_match:
            healing[heal_match.group("name").strip()] = int(heal_match.group("heal"))
            continue
        match = HERO_LINE.fullmatch(line)
        if not match:
            errors.append(f"line {line_number}: cannot parse hero row: {line}")
            continue
        if lane is None:
            errors.append(f"line {line_number}: hero has no preceding lane header")
            continue
        values = {key: value.strip() for key, value in match.groupdict().items()}
        damage_labels = [part.strip() for part in re.split(r"[、,]", values["damage"])]
        unknown_damage = set(damage_labels) - DAMAGE_MAP.keys()
        if unknown_damage or values["control"] not in CONTROL_MAP or values["mobility"] not in MOBILITY_MAP or values["immunity"] not in IMMUNITY_MAP:
            errors.append(f"line {line_number}: unknown feature value(s): {line}")
            continue
        heroes.append(
            {
                "hero_name": values["name"],
                "primary_lane": LANE_MAP[lane],
                "primary_lane_cn": lane,
                "damage_types": [DAMAGE_MAP[label] for label in damage_labels],
                "control": CONTROL_MAP[values["control"]],
                "mobility": MOBILITY_MAP[values["mobility"]],
                "has_unstoppable": IMMUNITY_MAP[values["immunity"]],
                "heal": 0,
            }
        )
    duplicate_names = [name for name, count in Counter(h["hero_name"] for h in heroes).items() if count > 1]
    if duplicate_names:
        errors.append("duplicate heroes: " + ", ".join(sorted(duplicate_names)))
    if errors:
        raise ValueError("Invalid source data:\n- " + "\n- ".join(errors))
    names = {hero["hero_name"] for hero in heroes}
    unknown_healing_heroes = sorted(set(healing) - names)
    if unknown_healing_heroes:
        raise ValueError("Healing section references unknown heroes: " + ", ".join(unknown_healing_heroes))
    for hero in heroes:
        hero["heal"] = healing.get(hero["hero_name"], 0)
    return heroes


def load_catalog(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        return {
            str(name).strip(): int(hero_id)
            for hero_id, name in conn.execute(
                "SELECT hero_id, hero_name FROM heroes WHERE hero_id > 0 AND hero_name <> ''"
            )
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Markdown file containing the hero capability list")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "analysis" / "hero_features.json")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "analysis" / "hero_feature_coverage.json")
    parser.add_argument("--catalog-db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    heroes = parse_source(args.input.read_text(encoding="utf-8"))
    catalog = load_catalog(args.catalog_db)
    source_names = {hero["hero_name"] for hero in heroes}
    unresolved = sorted(source_names - catalog.keys())
    missing_features = sorted(set(catalog) - source_names)
    for hero in heroes:
        # Null explicitly flags a name that needs a human ID mapping.
        hero["hero_id"] = catalog.get(hero["hero_name"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(heroes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "parsed_feature_rows": len(heroes),
        "catalog_hero_rows": len(catalog),
        "matched": len(heroes) - len(unresolved),
        "source_heroes_not_in_catalog": unresolved,
        "catalog_heroes_missing_features": missing_features,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(heroes)} feature rows to {args.output}")
    print(f"Matched {report['matched']}/{len(heroes)} names against {args.catalog_db}")
    print(f"Coverage report: {args.report}")


if __name__ == "__main__":
    main()
