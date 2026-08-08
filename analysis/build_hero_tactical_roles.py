"""Build a commentary-only tactical-role artifact from Tencent hero sources.

This artifact deliberately stays outside the ML feature vector.  It combines
Tencent's official primary/secondary hero classes, the existing conservative
ability-mechanics tags, and short matched terms from official hero tips and
relationship explanations.  It does not retain full webpage prose.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from common import REPO_ROOT


DEFAULT_MECHANICS = REPO_ROOT / "analysis" / "hero_ability_mechanics.json"
DEFAULT_SPECIALTIES = REPO_ROOT / "analysis" / "hero_features.json"
DEFAULT_OUTPUT = REPO_ROOT / "analysis" / "hero_tactical_roles.json"
HERO_LIST_URL = "https://pvp.qq.com/web201605/js/herolist.json"
KPL_ROLE_SOURCE_URL = "https://jokerofacademics.com/academics/download.php?id=1997"
SHENMENGXI_GUIDE_URL = "https://www.gamersky.com/handbooksy/202109/1424464.shtml"

OFFICIAL_CLASS_LABELS = {
    1: ("warrior", "战士"),
    2: ("mage", "法师"),
    3: ("tank", "坦克"),
    4: ("assassin", "刺客"),
    5: ("marksman", "射手"),
    6: ("support", "辅助"),
}

TACTICAL_ROLE_LABELS = {
    "frontline": "前排承伤",
    "durable_support": "承伤型辅助",
    "primary_engage": "主动开团",
    "secondary_engage": "补充开团",
    "pick_creation": "先手抓机会",
    "peel_disengage": "拆火/劝退",
    "counter_engage": "反打",
    "ally_protection": "队友保护",
    "ally_reposition": "队友位置调整",
    "long_range_poke": "远程消耗",
    "wave_clear": "清线",
    "zone_damage": "区域压制",
    "zone_control": "阵地区域控制",
    "channel_interrupt": "打断蓄力",
    "anti_dive": "限制突进",
    "vision_control": "视野控制",
    "multi_target_control": "多人控制",
    "dive": "突进切入",
    "flank": "绕后侧切",
    "ranged_carry": "远程核心输出",
    "burst_damage": "爆发伤害",
    "sustained_damage": "持续输出",
    "finisher": "收割",
    "siege_pressure": "推进/压塔",
    "sustain_support": "续航辅助",
    "economy_support": "经济辅助",
}

PROFESSIONAL_ARCHETYPE_LABELS = {
    "hard_support": "硬辅",
    "artillery_mage": "炮台法师",
    "utility_support": "功能辅",
}

# These three labels are explicitly named in the cited KPL BP methodology.
PROFESSIONAL_ARCHETYPE_SEEDS = {
    "张飞": ["hard_support"],
    "沈梦溪": ["artillery_mage"],
    "鲁班大师": ["utility_support"],
}

# Human-reviewed mappings for the examples that motivated this artifact.  Each
# role is supported by the cited official page and/or the professional taxonomy.
REVIEWED_ROLE_SEEDS = {
    "张飞": {
        "peel_disengage": ("high", "reviewed_tencent_relationships", ["保护", "击退", "退散"], "https://pvp.qq.com/web201605/herodetail/zhangfei.shtml"),
        "counter_engage": ("high", "reviewed_tencent_relationships", ["保护", "打断"], "https://pvp.qq.com/web201605/herodetail/zhangfei.shtml"),
        "ally_protection": ("high", "reviewed_tencent_relationships", ["提供护盾", "保护"], "https://pvp.qq.com/web201605/herodetail/zhangfei.shtml"),
        "channel_interrupt": ("high", "reviewed_tencent_relationships", ["打断"], "https://pvp.qq.com/web201605/herodetail/zhangfei.shtml"),
    },
    "沈梦溪": {
        "long_range_poke": ("high", "professional_archetype", ["炮台法师", "Poke"], KPL_ROLE_SOURCE_URL),
        "wave_clear": ("medium", "reviewed_hero_guide", ["清线", "远距离清线"], SHENMENGXI_GUIDE_URL),
        "zone_damage": ("high", "reviewed_hero_guide", ["范围伤害", "覆盖范围"], SHENMENGXI_GUIDE_URL),
    },
    "鲁班大师": {
        "primary_engage": ("high", "reviewed_tencent_relationships", ["先手控制", "牵引"], "https://pvp.qq.com/web201605/herodetail/lubandashi.shtml"),
        "pick_creation": ("high", "reviewed_tencent_relationships", ["先手控制", "拉回"], "https://pvp.qq.com/web201605/herodetail/lubandashi.shtml"),
        "peel_disengage": ("high", "reviewed_tencent_relationships", ["牵制", "保护后排"], "https://pvp.qq.com/web201605/herodetail/lubandashi.shtml"),
        "ally_protection": ("high", "reviewed_tencent_relationships", ["保护后排", "提高生存能力"], "https://pvp.qq.com/web201605/herodetail/lubandashi.shtml"),
    },
}

TEXT_ROLE_RULES: dict[str, tuple[str, ...]] = {
    "primary_engage": (r"开团", r"先手控制", r"先手.{0,8}(?:拉|控)", r"进场打控制"),
    "pick_creation": (r"拉回", r"聚拢", r"抓住机会", r"先手控制"),
    "peel_disengage": (r"拆火", r"劝退", r"击退对手", r"将对手.{0,8}退散", r"保持一定距离"),
    "counter_engage": (r"反打", r"保护被控制", r"限制.{0,8}进场"),
    "ally_protection": (r"保护.{0,12}(?:队友|后排|射手|法师|黄忠|后羿)", r"提供护盾", r"提高.{0,8}生存能力"),
    "long_range_poke": (r"远程消耗", r"团战前.{0,12}消耗", r"安全的位置.{0,12}投掷", r"法术射程较远", r"手特别长"),
    "wave_clear": (r"清线", r"清理兵线", r"远距离清线"),
    "zone_damage": (r"范围伤害", r"覆盖范围", r"区域.{0,8}伤害"),
    "channel_interrupt": (r"打断", r"干扰.{0,8}(?:吟唱|蓄力|引导)"),
    "anti_dive": (r"牵制.{0,8}刺客", r"防止.{0,8}突进", r"限制.{0,8}突进"),
    "dive": (r"切入", r"突进后", r"进场收割"),
    "flank": (r"绕后", r"侧翼"),
    "burst_damage": (r"爆发伤害", r"瞬间爆发"),
    "sustained_damage": (r"持续输出",),
    "finisher": (r"收割",),
    "siege_pressure": (r"压塔", r"塔外点塔", r"推进"),
}

RELATIONSHIP_ATTRIBUTABLE_ROLES = {
    "primary_engage",
    "pick_creation",
    "peel_disengage",
    "counter_engage",
    "ally_protection",
    "channel_interrupt",
    "anti_dive",
}


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def fetch(url: str, *, attempts: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 KPL-Draft-Atlas/1.0"},
                timeout=20,
                follow_redirects=True,
            )
            response.raise_for_status()
            return response.content
        except Exception as exc:  # pragma: no cover - exercised only on network failures
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.4 * (attempt + 1))
    assert last_error is not None
    raise last_error


def decode_page(value: bytes) -> str:
    try:
        return value.decode("gb18030")
    except UnicodeDecodeError:
        return value.decode("utf-8", errors="replace")


class TacticalPageParser(HTMLParser):
    """Extract only short official tips and structured relationship text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metrics: dict[str, int] = {}
        self.tips: list[str] = []
        self.relationships: list[dict[str, Any]] = []
        self._capture_tip = False
        self._tip_parts: list[str] = []
        self._relationship_depth = 0
        self._relationship_index = -1
        self._relationship_targets: list[int] = []
        self._desc_depth = 0
        self._capture_desc = False
        self._desc_parts: list[str] = []

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = dict(attrs).get("class") or ""
        return set(value.split())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self._classes(attrs)
        attributes = dict(attrs)
        if tag == "span" and "cover-list-bar" in classes:
            style = attributes.get("style") or ""
            match = re.search(r"width\s*:\s*(\d+)%", style)
            data_class = next((name for name in classes if name.startswith("data-bar")), "")
            if match and data_class:
                self.metrics[data_class] = int(match.group(1))
        if tag == "p" and classes & {"sugg-tips", "equip-tips"}:
            self._capture_tip = True
            self._tip_parts = []
        if tag == "div" and {"hero-info", "l", "info"}.issubset(classes):
            self._relationship_depth = 1
            self._relationship_index += 1
            self._relationship_targets = []
            return
        if self._relationship_depth:
            if tag == "div":
                self._relationship_depth += 1
                if "hero-list-desc" in classes:
                    self._desc_depth = self._relationship_depth
            if tag == "a":
                match = re.fullmatch(r"(\d+)\.shtml", attributes.get("href") or "")
                if match:
                    self._relationship_targets.append(int(match.group(1)))
            if tag == "p" and self._desc_depth:
                self._capture_desc = True
                self._desc_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._capture_tip:
            value = clean_text("".join(self._tip_parts))
            if value:
                self.tips.append(value)
            self._capture_tip = False
        if tag == "p" and self._capture_desc:
            value = clean_text("".join(self._desc_parts))
            if value:
                self.relationships.append(
                    {
                        "group_index": self._relationship_index,
                        "target_hero_id": (
                            self._relationship_targets[len([
                                row for row in self.relationships
                                if row["group_index"] == self._relationship_index
                            ])]
                            if len(self._relationship_targets) > len([
                                row for row in self.relationships
                                if row["group_index"] == self._relationship_index
                            ])
                            else None
                        ),
                        "text": value,
                    }
                )
            self._capture_desc = False
        if tag == "div" and self._relationship_depth:
            if self._relationship_depth == self._desc_depth:
                self._desc_depth = 0
            self._relationship_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture_tip:
            self._tip_parts.append(data)
        if self._capture_desc:
            self._desc_parts.append(data)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_page(page: str) -> dict[str, Any]:
    parser = TacticalPageParser()
    parser.feed(page)
    for index in range(1, 5):
        match = re.search(
            rf'data-bar{index}.*?<i[^>]+style="[^"]*width\s*:\s*(\d+)%',
            page,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if match:
            parser.metrics[f"data-bar{index}"] = int(match.group(1))
    groups = {0: "best_partner", 1: "suppresses", 2: "suppressed_by"}
    relationships = {name: [] for name in groups.values()}
    for row in parser.relationships:
        group = groups.get(int(row["group_index"]))
        if group:
            relationships[group].append(row)
    return {
        "survival_score": parser.metrics.get("data-bar1"),
        "attack_score": parser.metrics.get("data-bar2"),
        "skill_effect_score": parser.metrics.get("data-bar3"),
        "difficulty_score": parser.metrics.get("data-bar4"),
        "tips": parser.tips,
        "relationships": relationships,
    }


def matched_terms(text: str, patterns: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = clean_text(match.group(0))
            if value and value not in matches:
                matches.append(value)
    return matches[:4]


def class_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for field in ("hero_type", "hero_type2"):
        raw_value = row.get(field)
        if raw_value in (None, "", 0, "0"):
            continue
        class_id = int(raw_value)
        if class_id not in OFFICIAL_CLASS_LABELS:
            continue
        key, label = OFFICIAL_CLASS_LABELS[class_id]
        if not any(existing["key"] == key for existing in values):
            values.append({"key": key, "label_zh": label, "source_field": field})
    return values


def role_evidence(
    hero_name: str,
    classes: set[str],
    mechanics: set[str],
    conditions: set[str],
    page: dict[str, Any],
    source_url: str,
) -> list[dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}

    def add(
        role: str,
        confidence: str,
        source_type: str,
        terms: list[str],
        evidence_url: str | None = None,
    ) -> None:
        current = evidence.get(role)
        rank = {"low": 0, "medium": 1, "high": 2}
        if current is None or rank[confidence] > rank[current["confidence"]]:
            evidence[role] = {
                "role": role,
                "confidence": confidence,
                "source_type": source_type,
                "source_url": evidence_url or (source_url if "tencent" in source_type else KPL_ROLE_SOURCE_URL),
                "matched_terms": terms[:4],
            }

    if "tank" in classes:
        add("frontline", "high", "tencent_official_class", ["坦克"])
    if "tank" in classes and "support" in classes:
        add("durable_support", "high", "tencent_official_class", ["坦克", "辅助"])
    if "marksman" in classes:
        add("ranged_carry", "high", "tencent_official_class", ["射手"])
    if mechanics & {"support_ally_shield", "support_ally_heal", "support_damage_rewind"}:
        add("ally_protection", "high", "tencent_skill_mechanics", sorted(mechanics & {"support_ally_shield", "support_ally_heal", "support_damage_rewind"}))
    if "support_ally_reposition" in mechanics:
        add("ally_reposition", "high", "tencent_skill_mechanics", ["support_ally_reposition"])
    if "support_ally_heal" in mechanics:
        add("sustain_support", "high", "tencent_skill_mechanics", ["support_ally_heal"])
    if "utility_vision" in mechanics:
        add("vision_control", "high", "tencent_skill_mechanics", ["utility_vision"])
    if mechanics & {"utility_zone", "utility_terrain"}:
        add("zone_control", "high", "tencent_skill_mechanics", sorted(mechanics & {"utility_zone", "utility_terrain"}))
    if "utility_gold_generation" in mechanics:
        add("economy_support", "high", "tencent_skill_mechanics", ["utility_gold_generation"])
    if "utility_structure_interaction" in mechanics:
        add("siege_pressure", "high", "tencent_skill_mechanics", ["utility_structure_interaction"])
    if "control_anti_mobility" in mechanics:
        add("anti_dive", "high", "tencent_skill_mechanics", ["control_anti_mobility"])
    if mechanics & {"control_pull", "control_taunt", "control_suppress"} and classes & {"tank", "support"}:
        add("primary_engage", "high", "tencent_skill_mechanics", sorted(mechanics & {"control_pull", "control_taunt", "control_suppress"}))
    hard_control = {tag for tag in mechanics if tag.startswith("control_")} - {"control_slow", "control_blind"}
    if len(hard_control) >= 2:
        add("multi_target_control", "medium", "tencent_skill_mechanics", sorted(hard_control)[:4])
    if mechanics & {"control_knockback", "control_pull"} and classes & {"tank", "support"}:
        add("peel_disengage", "medium", "tencent_skill_mechanics", sorted(mechanics & {"control_knockback", "control_pull"}))
    tips_text = " ".join(page.get("tips", []))
    relationship_text = " ".join(
        [
            *(row["text"] for row in page["relationships"]["best_partner"]),
            *(row["text"] for row in page["relationships"]["suppresses"]),
        ]
    )
    for role, patterns in TEXT_ROLE_RULES.items():
        terms = matched_terms(tips_text, patterns)
        if role in RELATIONSHIP_ATTRIBUTABLE_ROLES:
            terms.extend(
                term
                for term in matched_terms(relationship_text, patterns)
                if term not in terms
            )
        if terms:
            add(role, "medium", "tencent_tips_or_relationships", terms)

    for role, (confidence, source_type, terms, evidence_url) in REVIEWED_ROLE_SEEDS.get(hero_name, {}).items():
        add(role, confidence, source_type, list(terms), evidence_url)

    return sorted(evidence.values(), key=lambda row: (row["role"], row["source_type"]))


def compact_relationships(
    page: dict[str, Any],
    hero_names: dict[int, str],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    reason_patterns = tuple(pattern for patterns in TEXT_ROLE_RULES.values() for pattern in patterns)
    for group, rows in page["relationships"].items():
        compact: list[dict[str, Any]] = []
        for row in rows:
            target_id = row.get("target_hero_id")
            if target_id is None:
                continue
            compact.append(
                {
                    "hero_id": int(target_id),
                    "hero_name": hero_names.get(int(target_id), str(target_id)),
                    "matched_terms": matched_terms(row["text"], reason_patterns),
                }
            )
        result[group] = compact
    return result


def build_artifact(
    mechanics: dict[str, Any],
    specialties: list[dict[str, Any]],
    class_catalogue: list[dict[str, Any]],
    page_bytes: dict[str, bytes],
) -> dict[str, Any]:
    classes_by_name = {str(row.get("cname")): row for row in class_catalogue}
    hero_names = {
        int(row["ename"]): str(row["cname"])
        for row in class_catalogue
        if row.get("ename") is not None and row.get("cname")
    }
    lanes_by_name = {
        str(row.get("hero_name")): str(row.get("primary_lane") or "unknown")
        for row in specialties
    }
    heroes: list[dict[str, Any]] = []
    missing_classes: list[str] = []
    missing_pages: list[str] = []

    for source_hero in mechanics.get("heroes", []):
        hero_name = str(source_hero["hero_name"])
        source_url = str(source_hero["source_url"])
        class_source = classes_by_name.get(hero_name, {})
        official_classes = class_rows(class_source)
        if not official_classes:
            missing_classes.append(hero_name)
        raw_page = page_bytes.get(hero_name)
        if raw_page is None:
            missing_pages.append(hero_name)
            page = {"survival_score": None, "attack_score": None, "skill_effect_score": None, "difficulty_score": None, "tips": [], "relationships": {"best_partner": [], "suppresses": [], "suppressed_by": []}}
        else:
            page = parse_page(decode_page(raw_page))
        class_keys = {row["key"] for row in official_classes}
        mechanics_set = set(source_hero.get("mechanics", []))
        conditions_set = set(source_hero.get("conditions", []))
        evidence = role_evidence(
            hero_name,
            class_keys,
            mechanics_set,
            conditions_set,
            page,
            source_url,
        )
        archetypes = [
            {
                "key": key,
                "label_zh": PROFESSIONAL_ARCHETYPE_LABELS[key],
                "confidence": "medium",
                "source_url": KPL_ROLE_SOURCE_URL,
            }
            for key in PROFESSIONAL_ARCHETYPE_SEEDS.get(hero_name, [])
        ]
        heroes.append(
            {
                "hero_id": source_hero.get("hero_id"),
                "hero_name": hero_name,
                "source_url": source_url,
                "primary_lane": lanes_by_name.get(hero_name, "unknown"),
                "official_classes": official_classes,
                "is_tank": "tank" in class_keys,
                "official_attribute_scores": {
                    "survival": page.get("survival_score"),
                    "attack": page.get("attack_score"),
                    "skill_effect": page.get("skill_effect_score"),
                    "difficulty": page.get("difficulty_score"),
                },
                "professional_archetypes": archetypes,
                "tactical_roles": [
                    {
                        "key": row["role"],
                        "label_zh": TACTICAL_ROLE_LABELS[row["role"]],
                        "confidence": row["confidence"],
                    }
                    for row in evidence
                ],
                "role_evidence": evidence,
                "official_relationships": compact_relationships(page, hero_names),
            }
        )

    heroes.sort(key=lambda row: (row["hero_id"] is None, row["hero_id"] or 10**9, row["hero_name"]))
    return {
        "schema_version": 1,
        "artifact_type": "hero_tactical_roles",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "usage": {
            "commentary_only": True,
            "included_in_ml_feature_vectors": False,
            "requires_model_retraining": False,
        },
        "source": {
            "official_class_catalogue_url": HERO_LIST_URL,
            "official_class_catalogue_sha256": hashlib.sha256(json.dumps(class_catalogue, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "official_hero_pages_sha256": hashlib.sha256(b"".join(page_bytes[name] for name in sorted(page_bytes))).hexdigest(),
            "professional_role_source_url": KPL_ROLE_SOURCE_URL,
            "normalization": "Full webpage prose is not retained. Official classes, numeric attribute bars, relationship targets, short matched terms, and controlled tactical-role labels are stored.",
        },
        "coverage": {
            "hero_count": len(heroes),
            "heroes_with_official_classes": sum(bool(row["official_classes"]) for row in heroes),
            "heroes_with_tactical_roles": sum(bool(row["tactical_roles"]) for row in heroes),
            "heroes_with_professional_archetypes": sum(bool(row["professional_archetypes"]) for row in heroes),
            "tank_hero_count": sum(bool(row["is_tank"]) for row in heroes),
            "missing_official_classes": sorted(missing_classes),
            "missing_official_pages": sorted(missing_pages),
        },
        "taxonomy": {
            "official_classes": {key: label for _id, (key, label) in OFFICIAL_CLASS_LABELS.items()},
            "tactical_roles": TACTICAL_ROLE_LABELS,
            "professional_archetypes": PROFESSIONAL_ARCHETYPE_LABELS,
        },
        "heroes": heroes,
    }


def validate_artifact(artifact: dict[str, Any]) -> None:
    heroes = artifact["heroes"]
    if artifact["coverage"]["hero_count"] != len(heroes):
        raise ValueError("Hero coverage count does not match rows")
    if len({row["hero_name"] for row in heroes}) != len(heroes):
        raise ValueError("Duplicate hero names in tactical artifact")
    known_roles = set(artifact["taxonomy"]["tactical_roles"])
    for hero in heroes:
        role_keys = [row["key"] for row in hero["tactical_roles"]]
        if len(role_keys) != len(set(role_keys)):
            raise ValueError(f"Duplicate tactical roles for {hero['hero_name']}")
        if set(role_keys) - known_roles:
            raise ValueError(f"Unknown tactical role for {hero['hero_name']}")
        class_keys = {row["key"] for row in hero["official_classes"]}
        if hero["is_tank"] != ("tank" in class_keys):
            raise ValueError(f"Tank flag disagrees with official classes for {hero['hero_name']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mechanics", type=Path, default=DEFAULT_MECHANICS)
    parser.add_argument("--specialties", type=Path, default=DEFAULT_SPECIALTIES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    arguments = parser.parse_args()

    mechanics = read_json(arguments.mechanics.resolve())
    specialties = read_json(arguments.specialties.resolve())
    class_catalogue = json.loads(fetch(HERO_LIST_URL).decode("utf-8"))
    source_rows = [
        (str(hero["hero_name"]), str(hero["source_url"]))
        for hero in mechanics.get("heroes", [])
    ]
    if not 1 <= arguments.workers <= 16:
        raise ValueError("--workers must be between 1 and 16")
    with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
        bodies = executor.map(lambda row: fetch(row[1]), source_rows)
        page_bytes = {
            hero_name: body
            for (hero_name, _source_url), body in zip(source_rows, bodies)
        }

    artifact = build_artifact(mechanics, specialties, class_catalogue, page_bytes)
    validate_artifact(artifact)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(arguments.output), **artifact["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
