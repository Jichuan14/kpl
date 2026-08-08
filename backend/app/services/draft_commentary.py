"""Grounded post-selection commentary evidence for the draft simulator.

This module joins low-level ability mechanics with higher-level tactical roles
before asking the language model to narrate.  The model never has to discover
hero facts or infer that every controller and damage dealer form a real combo.
"""

from __future__ import annotations

import json
import logging
import re
from hashlib import sha256
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agent.service import KimiConfigurationError, build_kimi_client
from app.config import get_settings
from app.services.analysis_pipeline import ANALYSIS_DIR, OUTPUT_ROOT


ACTION_ZH = {"pick": "选择", "ban": "禁用"}
MECHANIC_PRIORITY = (
    "defense_projectile_block",
    "defense_cleanse",
    "support_ally_reposition",
    "support_ally_skill_refresh",
    "control_suppress",
    "control_knockup",
    "control_pull",
    "control_stun",
    "defense_shield",
    "mobility_dash",
)
HARD_CONTROL = {
    "control_stun",
    "control_knockup",
    "control_knockback",
    "control_pull",
    "control_root",
    "control_taunt",
    "control_suppress",
    "control_freeze",
    "control_petrify",
    "control_fear",
}
MOBILITY = {"mobility_dash", "mobility_teleport", "mobility_wall_traverse"}
SHIELDING = {"defense_shield", "support_ally_shield"}
SUSTAIN = {"sustain_heal", "support_ally_heal", "sustain_lifesteal"}
PRECISION_CONDITIONS = {
    "channel_or_charge",
    "delayed_effect",
    "directional",
    "distance_scaling",
}
TACTICAL_PROTECTION = {
    "peel_disengage",
    "counter_engage",
    "ally_protection",
    "anti_dive",
}
TACTICAL_POKE = {"long_range_poke", "zone_damage", "siege_pressure"}
TACTICAL_ENGAGE = {"primary_engage", "pick_creation"}
TACTICAL_FOLLOW_UP = {
    "zone_damage",
    "zone_control",
    "burst_damage",
    "finisher",
    "multi_target_control",
}
TACTICAL_DIVE = {"dive", "flank"}
GENERIC_MECHANIC_PAIR_RULES = {
    "control_precision_follow_up",
    "ally_control_precision_follow_up",
    "control_chain",
    "protect_dive",
}
TACTICAL_ROLE_DISPLAY_ORDER = (
    "peel_disengage",
    "counter_engage",
    "ally_protection",
    "anti_dive",
    "primary_engage",
    "pick_creation",
    "ally_reposition",
    "frontline",
    "long_range_poke",
    "zone_damage",
    "zone_control",
    "ranged_carry",
    "burst_damage",
    "finisher",
    "siege_pressure",
    "dive",
    "flank",
    "sustain_support",
)
TACTICAL_ROLE_MECHANICS = {
    "peel_disengage": HARD_CONTROL | {"control_knockback", "control_pull"},
    "counter_engage": HARD_CONTROL | SHIELDING | {"defense_projectile_block"},
    "ally_protection": SHIELDING | SUSTAIN | {"support_ally_reposition", "defense_projectile_block"},
    "anti_dive": HARD_CONTROL | {"control_anti_mobility"},
    "primary_engage": HARD_CONTROL | {"control_pull"},
    "pick_creation": HARD_CONTROL | {"control_pull", "support_ally_reposition"},
    "ally_reposition": {"support_ally_reposition"},
    "frontline": {"defense_damage_reduction", "defense_invulnerable", "defense_shield"},
    "vision_control": {"utility_vision"},
    "zone_control": {"utility_zone", "utility_terrain"},
    "sustain_support": SUSTAIN,
}
TACTICAL_MECHANIC_DISPLAY_ORDER = (
    "support_ally_reposition",
    "defense_projectile_block",
    "defense_cleanse",
    "control_suppress",
    "control_knockback",
    "control_pull",
    "control_knockup",
    "control_stun",
    "control_anti_mobility",
    "support_ally_shield",
    "support_ally_heal",
    "defense_shield",
    "defense_damage_reduction",
    "utility_vision",
    "utility_zone",
    "utility_terrain",
)
UNSUPPORTED_INFERENCE_MARKERS = {
    "一技能",
    "二技能",
    "三技能",
    "大招",
    "主动追求",
    "明显意图",
    "显然是",
    "必然",
    "稳赢",
    "胜率提升",
    "频繁拿出",
    "高频",
    "主动选",
    "战术库",
    "受青睐",
    "偏爱",
    "惯用",
    "招牌",
    "说明这一",
    "证明",
    "lift",
}
logger = logging.getLogger(__name__)
_LLM_COMMENTARY_CACHE: dict[str, dict[str, Any]] = {}

COMMENTATOR_SYSTEM_PROMPT = """你是王者荣耀职业赛事的BP解说，重点解释英雄在阵容中的实际分工、具体技能联动与克制关系。
你只能使用给定 claims 中的事实，不能补充英雄技能、选手意图、版本结论或胜负预测。
输出严格 JSON：{"commentary":"...","used_evidence_ids":["claim_1"]}。
commentary 使用简洁自然的中文，最多三句、180个汉字；必须使用 required_evidence_ids 对应的事实，
但 claim ID 只能放进 used_evidence_ids，严禁在 commentary 正文中出现 claim、claim_1、证据编号或类似引用标记。
必须说明“谁提供什么机制、谁如何受益或被限制”，不能只说
“补控制”“补伤害”或“二者配合很好”。同时存在战队联动与机制证据时，要把实际使用倾向
和机制原因连成同一段分析。历史响应只能表述为职业赛场上的选择倾向，不能当作因果克制。
优先使用 claims 已给出的战术分工，例如开团、拆火、反打、消耗、区域压制和位置调整；
不得自行把“控制英雄 + 伤害英雄”概括为联动，也不得改变 claims 中写明的职责方向。
不得写 claims detail 中没有出现的技能名称、技能序号、“大招”、保护对象或战队主观意图；
不得把英雄自身护盾改写成给队友提供保护。
统计结论只能改写为“数据显示该组合出现倾向高于队内常态”，不得写成高频、偏爱、惯用、
招牌、战术库或主动选择，也不要输出 lift 等英文字段名。
若证据不足，明确使用“更像是”或“目前更值得关注”。"""


@lru_cache(maxsize=8)
def _json(path_text: str) -> dict[str, Any]:
    with Path(path_text).open(encoding="utf-8") as source:
        return json.load(source)


@lru_cache(maxsize=32)
def _jsonl(path_text: str) -> tuple[dict[str, Any], ...]:
    path = Path(path_text)
    if not path.is_file():
        return ()
    with path.open(encoding="utf-8") as source:
        return tuple(json.loads(line) for line in source if line.strip())


def _mechanics_artifact() -> dict[str, Any]:
    return _json(str(ANALYSIS_DIR / "hero_ability_mechanics.json"))


def _mechanics() -> dict[int, dict[str, Any]]:
    return {
        int(row["hero_id"]): row
        for row in _mechanics_artifact()["heroes"]
        if row.get("hero_id") is not None
    }


def _tactical_artifact() -> dict[str, Any]:
    return _json(str(ANALYSIS_DIR / "hero_tactical_roles.json"))


def _tactics() -> dict[int, dict[str, Any]]:
    return {
        int(row["hero_id"]): row
        for row in _tactical_artifact()["heroes"]
        if row.get("hero_id") is not None
    }


def _hero_profiles() -> dict[int, dict[str, Any]]:
    tactical_by_id = _tactics()
    return {
        hero_id: {**mechanic, "tactical": tactical_by_id.get(hero_id, {})}
        for hero_id, mechanic in _mechanics().items()
    }


def _has(tags: set[str], prefix: str) -> bool:
    return any(tag.startswith(prefix) for tag in tags)


def _has_any(values: set[str], expected: set[str]) -> bool:
    return bool(values & expected)


def _has_damage(tags: set[str]) -> bool:
    return _has(tags, "damage_")


def _roles(hero: dict[str, Any]) -> set[str]:
    return {
        str(row["key"])
        for row in hero.get("tactical", {}).get("tactical_roles", [])
    }


def _role_labels(hero: dict[str, Any], role_keys: set[str]) -> list[str]:
    labels = {
        str(row["key"]): str(row["label_zh"])
        for row in hero.get("tactical", {}).get("tactical_roles", [])
        if row.get("key") in role_keys
    }
    ordered_keys = [key for key in TACTICAL_ROLE_DISPLAY_ORDER if key in labels]
    ordered_keys.extend(sorted(set(labels) - set(ordered_keys)))
    return [labels[key] for key in ordered_keys]


def _pair_key(first: dict[str, Any], second: dict[str, Any]) -> tuple[int, int]:
    return tuple(sorted((int(first["hero_id"]), int(second["hero_id"]))))


def _claim(kind: str, detail: str, *, confidence: str, priority: int, **facts: Any) -> dict[str, Any]:
    return {
        "id": f"{kind}_{priority}_{len(detail)}",
        "kind": kind,
        "detail": detail,
        "confidence": confidence,
        "priority": priority,
        **facts,
    }


def _trend_claim(
    rows: tuple[dict[str, Any], ...], *, team_id: str, team_name: str,
    hero_id: int, action: str, role: str,
) -> dict[str, Any] | None:
    row = next((
        item for item in rows
        if str(item.get("team_id")) == team_id
        and int(item.get("hero_id", 0)) == hero_id
        and item.get("action") == action
        and item.get("context_level") == "overall"
    ), None)
    if not row or int(row.get("legal_opportunity_count", 0)) < 6:
        return None
    recent = float(row.get("smoothed_probability_given_legal", 0))
    season = float(row.get("season_smoothed_probability", 0))
    change = float(row.get("probability_change_vs_season", recent - season))
    if abs(change) < 0.03:
        return None
    direction = "上升" if change > 0 else "下降"
    action_zh = ACTION_ZH.get(action, action)
    subject = "对手" if role == "opponent" else team_name
    return _claim(
        "战队趋势",
        f"{subject}近{row.get('recent_match_window', 5)}场对该英雄的{action_zh}优先级{direction}"
        f"（{recent:.0%}，赛季{season:.0%}）",
        confidence="medium",
        priority=80 if role == "opponent" else 60,
        team_id=team_id,
        sample_size=int(row["legal_opportunity_count"]),
        recent_rate=recent,
        season_rate=season,
        change=change,
        role=role,
    )


def _mechanic_claim(selected: dict[str, Any], *, action: str, allies: list[dict[str, Any]], enemies: list[dict[str, Any]]) -> dict[str, Any] | None:
    tags = set(selected.get("mechanics", []))
    relevant: list[str] = []
    if "defense_projectile_block" in tags and any("vulnerability_projectile_blockable" in set(hero.get("mechanics", [])) for hero in enemies):
        relevant.append("defense_projectile_block")
    if "defense_cleanse" in tags and any(_has(set(hero.get("mechanics", [])), "control_") for hero in enemies):
        relevant.append("defense_cleanse")
    if "support_ally_reposition" in tags and allies:
        relevant.append("support_ally_reposition")
    if action == "ban" and _has(tags, "control_"):
        relevant.extend(tag for tag in ("control_suppress", "control_knockup", "control_pull", "control_stun") if tag in tags)
    relevant.extend(tag for tag in MECHANIC_PRIORITY if tag in tags and tag not in relevant)
    if not relevant:
        relevant = sorted(tags)[:2]
    labels = _mechanics_artifact()["taxonomy"]["mechanics"]
    relevant = relevant[:3]
    return _claim(
        "技能机制",
        " + ".join(labels[tag] for tag in relevant),
        confidence="high",
        priority=35,
        mechanic_keys=relevant,
        hero_id=selected["hero_id"],
    )


def _tactical_identity_claim(selected: dict[str, Any]) -> dict[str, Any] | None:
    """Explain what the selected hero is trying to do, with mechanic support."""
    roles = _roles(selected)
    preferred = (
        TACTICAL_PROTECTION
        | TACTICAL_ENGAGE
        | TACTICAL_POKE
        | TACTICAL_DIVE
        | {"ally_reposition", "frontline", "ranged_carry", "sustain_support", "vision_control"}
    )
    labels = _role_labels(selected, roles & preferred)[:3]
    if not labels:
        labels = [
            str(row["label_zh"])
            for row in selected.get("tactical", {}).get("official_classes", [])
        ][:2]
    if not labels:
        return None

    mechanic_labels = _mechanics_artifact()["taxonomy"]["mechanics"]
    tags = set(selected.get("mechanics", []))
    relevant_mechanics: set[str] = set()
    for role in roles & preferred:
        relevant_mechanics.update(TACTICAL_ROLE_MECHANICS.get(role, set()))
    keys = [
        key for key in TACTICAL_MECHANIC_DISPLAY_ORDER
        if key in tags and key in relevant_mechanics
    ][:2]
    detail = f"{selected['hero_name']}的战术分工偏向{'、'.join(labels)}"
    if keys:
        detail += f"，对应机制包括{'、'.join(mechanic_labels[key] for key in keys)}"
    return _claim(
        "战术定位",
        detail,
        confidence="high" if selected.get("tactical", {}).get("tactical_roles") else "medium",
        priority=58,
        hero_id=int(selected["hero_id"]),
        tactical_role_keys=sorted(roles & preferred),
        mechanic_keys=keys,
    )


def _tactical_pair_claim(
    first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any] | None:
    """Return one role-aware lineup relationship, independent of pick order."""
    first_roles, second_roles = _roles(first), _roles(second)
    pair = list(_pair_key(first, second))

    for protector, beneficiary, protector_roles, beneficiary_roles in (
        (first, second, first_roles, second_roles),
        (second, first, second_roles, first_roles),
    ):
        protection = protector_roles & TACTICAL_PROTECTION
        poke = beneficiary_roles & TACTICAL_POKE
        if protection and poke:
            return _claim(
                "阵容联动",
                f"{protector['hero_name']}负责{'、'.join(_role_labels(protector, protection)[:2])}并稳住阵型，"
                f"{beneficiary['hero_name']}可以保持距离承担{'、'.join(_role_labels(beneficiary, poke)[:2])}",
                confidence="high",
                priority=124,
                source_hero_id=int(protector["hero_id"]),
                target_hero_id=int(beneficiary["hero_id"]),
                hero_pair=pair,
                tactical_role_keys=sorted(protection | poke),
                rule="protect_poke_structure",
            )

    for mover, beneficiary, mover_roles, beneficiary_roles in (
        (first, second, first_roles, second_roles),
        (second, first, second_roles, first_roles),
    ):
        carry_roles = beneficiary_roles & (TACTICAL_POKE | {"ranged_carry"})
        if "ally_reposition" in mover_roles and carry_roles:
            return _claim(
                "阵容联动",
                f"{mover['hero_name']}负责队友位置调整，帮助{beneficiary['hero_name']}获得更安全的"
                f"{'、'.join(_role_labels(beneficiary, carry_roles)[:2])}空间",
                confidence="high",
                priority=123,
                source_hero_id=int(mover["hero_id"]),
                target_hero_id=int(beneficiary["hero_id"]),
                hero_pair=pair,
                tactical_role_keys=sorted({"ally_reposition"} | carry_roles),
                rule="reposition_carry_structure",
            )

    for engager, follower, engage_roles, follower_roles in (
        (first, second, first_roles, second_roles),
        (second, first, second_roles, first_roles),
    ):
        engage = engage_roles & TACTICAL_ENGAGE
        follow_up = follower_roles & TACTICAL_FOLLOW_UP
        if engage and follow_up:
            return _claim(
                "阵容联动",
                f"{engager['hero_name']}负责{'、'.join(_role_labels(engager, engage)[:2])}，"
                f"{follower['hero_name']}再衔接{'、'.join(_role_labels(follower, follow_up)[:2])}",
                confidence="high",
                priority=120,
                source_hero_id=int(engager["hero_id"]),
                target_hero_id=int(follower["hero_id"]),
                hero_pair=pair,
                tactical_role_keys=sorted(engage | follow_up),
                rule="engage_follow_up_structure",
            )

    for frontliner, beneficiary, frontline_roles, beneficiary_roles in (
        (first, second, first_roles, second_roles),
        (second, first, second_roles, first_roles),
    ):
        carry_roles = beneficiary_roles & (TACTICAL_POKE | {"ranged_carry"})
        if "frontline" in frontline_roles and carry_roles:
            return _claim(
                "阵容联动",
                f"{frontliner['hero_name']}承担前排承伤，给{beneficiary['hero_name']}留出"
                f"{'、'.join(_role_labels(beneficiary, carry_roles)[:2])}的站位空间",
                confidence="medium",
                priority=112,
                source_hero_id=int(frontliner["hero_id"]),
                target_hero_id=int(beneficiary["hero_id"]),
                hero_pair=pair,
                tactical_role_keys=sorted({"frontline"} | carry_roles),
                rule="frontline_carry_structure",
            )
    return None


def _tactical_interaction_claims(
    selected: dict[str, Any],
    allies: list[dict[str, Any]],
    enemies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    claims = [
        claim
        for ally in allies
        if (claim := _tactical_pair_claim(selected, ally)) is not None
    ]
    selected_roles = _roles(selected)
    for enemy in enemies:
        enemy_roles = _roles(enemy)
        pair = list(_pair_key(selected, enemy))
        if selected_roles & TACTICAL_PROTECTION and enemy_roles & TACTICAL_DIVE:
            defensive_roles = selected_roles & TACTICAL_PROTECTION
            dive_roles = enemy_roles & TACTICAL_DIVE
            claims.append(_claim(
                "克制关系",
                f"{selected['hero_name']}可以用{'、'.join(_role_labels(selected, defensive_roles)[:2])}"
                f"限制{enemy['hero_name']}的{'、'.join(_role_labels(enemy, dive_roles)[:2])}",
                confidence="high",
                priority=121,
                source_hero_id=int(selected["hero_id"]),
                target_hero_id=int(enemy["hero_id"]),
                hero_pair=pair,
                tactical_role_keys=sorted(defensive_roles | dive_roles),
                rule="peel_answers_dive",
            ))
        elif selected_roles & TACTICAL_DIVE and enemy_roles & TACTICAL_PROTECTION:
            defensive_roles = enemy_roles & TACTICAL_PROTECTION
            dive_roles = selected_roles & TACTICAL_DIVE
            claims.append(_claim(
                "克制关系",
                f"{selected['hero_name']}虽然承担{'、'.join(_role_labels(selected, dive_roles)[:2])}，"
                f"但需要处理{enemy['hero_name']}的{'、'.join(_role_labels(enemy, defensive_roles)[:2])}",
                confidence="high",
                priority=119,
                source_hero_id=int(enemy["hero_id"]),
                target_hero_id=int(selected["hero_id"]),
                hero_pair=pair,
                tactical_role_keys=sorted(defensive_roles | dive_roles),
                rule="dive_faces_peel",
            ))
    return claims


def _official_relationship_claims(
    selected: dict[str, Any], enemies: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Expose only exact opponent relationships from the Tencent sidecar."""
    relationships = selected.get("tactical", {}).get("official_relationships", {})
    enemies_by_id = {int(hero["hero_id"]): hero for hero in enemies}
    claims: list[dict[str, Any]] = []
    for relation_key, direction in (("suppresses", "压制"), ("suppressed_by", "被压制")):
        for row in relationships.get(relation_key, []):
            target_id = int(row.get("hero_id") or 0)
            enemy = enemies_by_id.get(target_id)
            if enemy is None:
                continue
            terms = [str(term) for term in row.get("matched_terms", []) if term][:2]
            if direction == "压制":
                detail = f"腾讯官方英雄关系资料将{selected['hero_name']}列为能够压制{enemy['hero_name']}的一方"
                source_id, target_id_for_claim = int(selected["hero_id"]), target_id
            else:
                detail = f"腾讯官方英雄关系资料显示{selected['hero_name']}会受到{enemy['hero_name']}压制"
                source_id, target_id_for_claim = target_id, int(selected["hero_id"])
            if terms:
                detail += f"，关系说明涉及{'、'.join(terms)}"
            claims.append(_claim(
                "官方克制",
                detail,
                confidence="high",
                priority=109,
                source_hero_id=source_id,
                target_hero_id=target_id_for_claim,
                matched_terms=terms,
                rule=f"tencent_{relation_key}",
            ))
    return claims


def _interaction_claims(selected: dict[str, Any], allies: list[dict[str, Any]], enemies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tags = set(selected.get("mechanics", []))
    conditions = set(selected.get("conditions", []))
    claims: list[dict[str, Any]] = []
    for enemy in enemies:
        enemy_tags = set(enemy.get("mechanics", []))
        enemy_conditions = set(enemy.get("conditions", []))
        if "defense_projectile_block" in tags and "vulnerability_projectile_blockable" in enemy_tags:
            claims.append(_claim("克制关系", f"{selected['hero_name']}的飞行物阻挡能直接压缩{enemy['hero_name']}的输出空间", confidence="high", priority=115, source_hero_id=selected["hero_id"], target_hero_id=enemy["hero_id"], rule="projectile_block"))
        elif _has_any(tags, {"defense_cleanse", "defense_control_immunity"}) and _has_any(enemy_tags, HARD_CONTROL):
            answer = "解控" if "defense_cleanse" in tags else "霸体/控制免疫"
            claims.append(_claim("克制关系", f"{selected['hero_name']}的{answer}能部分化解{enemy['hero_name']}的硬控价值", confidence="high", priority=110, source_hero_id=selected["hero_id"], target_hero_id=enemy["hero_id"], rule="control_answer"))
        elif "control_anti_mobility" in tags and _has_any(enemy_tags, MOBILITY):
            claims.append(_claim("克制关系", f"{selected['hero_name']}的限制位移机制能约束{enemy['hero_name']}的突进与撤离", confidence="high", priority=108, source_hero_id=selected["hero_id"], target_hero_id=enemy["hero_id"], rule="anti_mobility"))
        elif "debuff_shield_break" in tags and _has_any(enemy_tags, SHIELDING):
            claims.append(_claim("克制关系", f"{selected['hero_name']}的破盾机制能针对{enemy['hero_name']}的护盾保护", confidence="high", priority=106, source_hero_id=selected["hero_id"], target_hero_id=enemy["hero_id"], rule="shield_break"))
        elif "debuff_healing_reduction" in tags and _has_any(enemy_tags, SUSTAIN):
            claims.append(_claim("克制关系", f"{selected['hero_name']}的回复压制能限制{enemy['hero_name']}的持续作战能力", confidence="high", priority=104, source_hero_id=selected["hero_id"], target_hero_id=enemy["hero_id"], rule="healing_reduction"))
        elif "utility_vision" in tags and "utility_stealth" in enemy_tags:
            claims.append(_claim("克制关系", f"{selected['hero_name']}的视野获取能帮助队伍处理{enemy['hero_name']}的隐身/伪装", confidence="high", priority=102, source_hero_id=selected["hero_id"], target_hero_id=enemy["hero_id"], rule="vision_stealth"))
        elif _has_any(tags, HARD_CONTROL) and "channel_or_charge" in enemy_conditions:
            claims.append(_claim("克制关系", f"{selected['hero_name']}的硬控能干扰{enemy['hero_name']}的蓄力空间", confidence="medium", priority=96, source_hero_id=selected["hero_id"], target_hero_id=enemy["hero_id"], rule="interrupt_channel"))
    for ally in allies:
        ally_tags = set(ally.get("mechanics", []))
        ally_conditions = set(ally.get("conditions", []))
        if "support_ally_reposition" in tags and (_has(ally_tags, "damage_") or "utility_range_extension" in ally_tags):
            claims.append(_claim("阵容联动", f"{selected['hero_name']}能帮助{ally['hero_name']}调整安全输出位置或完成进退场", confidence="high", priority=105, source_hero_id=selected["hero_id"], target_hero_id=ally["hero_id"], rule="ally_reposition"))
        elif "debuff_armor" in tags and "damage_physical" in ally_tags:
            claims.append(_claim("阵容联动", f"{selected['hero_name']}降低物防后，{ally['hero_name']}的物理伤害能直接受益", confidence="high", priority=104, source_hero_id=selected["hero_id"], target_hero_id=ally["hero_id"], rule="armor_shred_physical"))
        elif "debuff_magic_defense" in tags and "damage_magic" in ally_tags:
            claims.append(_claim("阵容联动", f"{selected['hero_name']}降低法防后，{ally['hero_name']}的法术伤害能直接受益", confidence="high", priority=104, source_hero_id=selected["hero_id"], target_hero_id=ally["hero_id"], rule="magic_shred_magic"))
        elif _has_any(tags, HARD_CONTROL) and _has_damage(ally_tags) and _has_any(ally_conditions, PRECISION_CONDITIONS):
            claims.append(_claim("阵容联动", f"{selected['hero_name']}的硬控能为{ally['hero_name']}的蓄力、延迟或方向性技能创造更稳定的命中窗口", confidence="high", priority=102, source_hero_id=selected["hero_id"], target_hero_id=ally["hero_id"], rule="control_precision_follow_up"))
        elif _has_any(tags, {"control_knockback", "control_pull"}) and "utility_zone" in ally_tags:
            claims.append(_claim("阵容联动", f"{selected['hero_name']}的强制位移能把目标送入或留在{ally['hero_name']}的持续区域内", confidence="high", priority=101, source_hero_id=selected["hero_id"], target_hero_id=ally["hero_id"], rule="forced_movement_zone"))
        elif _has_any(tags, HARD_CONTROL) and _has_any(ally_tags, HARD_CONTROL):
            claims.append(_claim("阵容联动", f"{selected['hero_name']}与{ally['hero_name']}可以衔接硬控，延长对手无法行动的窗口", confidence="medium", priority=94, source_hero_id=selected["hero_id"], target_hero_id=ally["hero_id"], rule="control_chain"))
        elif _has_any(tags, {"support_ally_shield", "support_ally_heal", "support_damage_rewind"}) and _has_any(ally_tags, MOBILITY):
            claims.append(_claim("阵容联动", f"{selected['hero_name']}提供的保护能提高{ally['hero_name']}突进后的容错", confidence="medium", priority=92, source_hero_id=selected["hero_id"], target_hero_id=ally["hero_id"], rule="protect_dive"))

        if _has_any(ally_tags, HARD_CONTROL) and _has_damage(tags) and _has_any(conditions, PRECISION_CONDITIONS):
            claims.append(_claim("阵容联动", f"{ally['hero_name']}的硬控能为{selected['hero_name']}的蓄力、延迟或方向性技能创造更稳定的命中窗口", confidence="high", priority=103, source_hero_id=ally["hero_id"], target_hero_id=selected["hero_id"], rule="ally_control_precision_follow_up"))
    precise_pairs = {
        frozenset((int(claim["source_hero_id"]), int(claim["target_hero_id"])))
        for claim in claims
        if claim.get("rule") in {"control_precision_follow_up", "ally_control_precision_follow_up"}
    }
    return [
        claim
        for claim in claims
        if not (
            claim.get("rule") == "control_chain"
            and frozenset((int(claim["source_hero_id"]), int(claim["target_hero_id"]))) in precise_pairs
        )
    ]


def _pairing_claim(rows: tuple[dict[str, Any], ...], *, team_id: str, team_name: str, selected_id: int, own_ids: set[int]) -> dict[str, Any] | None:
    candidates = [
        row for row in rows
        if str(row.get("team_id")) == team_id
        and selected_id in {int(row.get("hero_a_id", 0)), int(row.get("hero_b_id", 0))}
        and bool(({int(row.get("hero_a_id", 0)), int(row.get("hero_b_id", 0))} - {selected_id}) & own_ids)
        and int(row.get("selection_count", 0)) >= 3
        and int(row.get("legal_completion_opportunity_count", 0)) >= 8
        and float(row.get("smoothed_lift", 0)) >= 1.5
    ]
    if not candidates:
        return None
    row = max(candidates, key=lambda item: (float(item["smoothed_lift"]), int(item["selection_count"])))
    selections = int(row["selection_count"])
    opportunities = int(row["legal_completion_opportunity_count"])
    return _claim("战队联动", f"{team_name}本赛季在{opportunities}次合法补全机会中完成{row['pair_name']}共{selections}次，出现倾向约为队内常态的{float(row['smoothed_lift']):.1f}倍", confidence="medium", priority=98, sample_size=selections, legal_opportunities=opportunities, lift=float(row["smoothed_lift"]), pair_name=row["pair_name"])


def _historical_counter_claim(
    rows: tuple[dict[str, Any], ...],
    *,
    selected: dict[str, Any],
    enemies: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find a supported league-wide response pattern; never call it causal proof."""
    enemy_ids = {int(hero["hero_id"]) for hero in enemies}
    candidates = [
        row
        for row in rows
        if row.get("relation") == "counter_pick"
        and row.get("context_level") == "overall"
        and not bool(row.get("is_peak_battle"))
        and int(row.get("opponent_hero_id", 0)) in enemy_ids
        and int(row.get("candidate_hero_id", 0)) == int(selected["hero_id"])
        and int(row.get("selection_count", 0)) >= 5
        and int(row.get("legal_opportunity_count", 0)) >= 30
        and float(row.get("smoothed_lift") or 0) >= 1.3
    ]
    if not candidates:
        return None
    row = max(
        candidates,
        key=lambda item: (
            float(item.get("smoothed_lift") or 0),
            int(item.get("selection_count") or 0),
        ),
    )
    selections = int(row["selection_count"])
    opportunities = int(row["legal_opportunity_count"])
    lift = float(row["smoothed_lift"])
    enemy_name = str(row["opponent_hero_name"])
    return _claim(
        "历史应对",
        f"职业赛场面对{enemy_name}时，{selected['hero_name']}在{opportunities}次合法机会中被选择{selections}次，选择倾向约为常态的{lift:.1f}倍",
        confidence="medium",
        priority=86,
        source_hero_id=int(row["opponent_hero_id"]),
        target_hero_id=int(selected["hero_id"]),
        sample_size=selections,
        legal_opportunities=opportunities,
        lift=lift,
        rule="historical_counter_pick_response",
    )


def _composition_claim(selected: dict[str, Any], allies: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Describe a concrete multi-hero structure once at least three picks exist."""
    roster = [*allies, selected]
    if len(roster) < 3:
        return None

    tactical_protectors = [hero for hero in roster if _roles(hero) & TACTICAL_PROTECTION]
    tactical_pokers = [hero for hero in roster if _roles(hero) & TACTICAL_POKE]
    if tactical_protectors and tactical_pokers:
        protector = tactical_protectors[0]
        poker = next((hero for hero in tactical_pokers if hero is not protector), None)
        if poker is not None:
            return _claim(
                "阵容结构",
                f"这套阵容更接近保护消耗体系：{protector['hero_name']}负责拆火、反打或保护阵型，"
                f"{poker['hero_name']}保持距离进行消耗和区域压制",
                confidence="high",
                priority=118,
                archetype="protect_poke",
                hero_ids=[int(hero["hero_id"]) for hero in roster],
                tactical_role_keys=sorted(
                    (_roles(protector) & TACTICAL_PROTECTION)
                    | (_roles(poker) & TACTICAL_POKE)
                ),
            )

    tactical_engagers = [hero for hero in roster if _roles(hero) & TACTICAL_ENGAGE]
    tactical_followers = [hero for hero in roster if _roles(hero) & TACTICAL_FOLLOW_UP]
    if tactical_engagers and tactical_followers:
        engager = tactical_engagers[0]
        follower = next((hero for hero in tactical_followers if hero is not engager), None)
        if follower is not None:
            return _claim(
                "阵容结构",
                f"这套阵容形成先手接后续压制的结构：{engager['hero_name']}负责开团或抓机会，"
                f"{follower['hero_name']}衔接区域、爆发或控制",
                confidence="high",
                priority=116,
                archetype="engage_follow_up",
                hero_ids=[int(hero["hero_id"]) for hero in roster],
                tactical_role_keys=sorted(
                    (_roles(engager) & TACTICAL_ENGAGE)
                    | (_roles(follower) & TACTICAL_FOLLOW_UP)
                ),
            )

    controllers = [
        hero for hero in roster
        if _has_any(set(hero.get("mechanics", [])), HARD_CONTROL)
    ]
    precision_damage = [
        hero for hero in roster
        if _has_damage(set(hero.get("mechanics", [])))
        and _has_any(set(hero.get("conditions", [])), PRECISION_CONDITIONS)
    ]
    protectors = [
        hero for hero in roster
        if _has_any(
            set(hero.get("mechanics", [])),
            {
                "support_ally_reposition",
                "support_ally_shield",
                "support_ally_heal",
                "support_damage_rewind",
                "defense_projectile_block",
            },
        )
    ]
    divers = [
        hero for hero in roster
        if _has_any(set(hero.get("mechanics", [])), MOBILITY)
        and (_has_damage(set(hero.get("mechanics", []))) or hero in controllers)
    ]
    zone_heroes = [
        hero for hero in roster
        if _has_any(set(hero.get("mechanics", [])), {"utility_zone", "utility_terrain"})
    ]
    forced_movers = [
        hero for hero in roster
        if _has_any(set(hero.get("mechanics", [])), {"control_knockback", "control_pull"})
    ]

    controller = next((hero for hero in controllers if hero not in precision_damage), None)
    beneficiary = next((hero for hero in precision_damage if hero is not controller), None)
    if controller and beneficiary:
        return _claim(
            "阵容结构",
            f"这套阵容已经形成控制定点后接蓄力/方向性技能的结构：{controller['hero_name']}负责限制位置，{beneficiary['hero_name']}负责后续命中",
            confidence="high",
            priority=91,
            archetype="control_precision_follow_up",
            hero_ids=[int(hero["hero_id"]) for hero in roster],
        )
    if protectors and precision_damage:
        protector = protectors[0]
        beneficiary = next((hero for hero in precision_damage if hero is not protector), precision_damage[0])
        return _claim(
            "阵容结构",
            f"这套阵容更接近保护输出体系：{protector['hero_name']}提供保护或位置调整，{beneficiary['hero_name']}获得更安全的技能释放空间",
            confidence="medium",
            priority=89,
            archetype="protect_precision_carry",
            hero_ids=[int(hero["hero_id"]) for hero in roster],
        )
    if len(divers) >= 2 and controllers:
        names = "、".join(hero["hero_name"] for hero in divers[:3])
        return _claim(
            "阵容结构",
            f"这套阵容具备多人进场结构，{names}都能利用位移接近目标，并由硬控开启集火窗口",
            confidence="medium",
            priority=88,
            archetype="multi_hero_dive",
            hero_ids=[int(hero["hero_id"]) for hero in roster],
        )
    if zone_heroes and forced_movers:
        return _claim(
            "阵容结构",
            f"这套阵容具备区域控制结构：{forced_movers[0]['hero_name']}负责改变站位，{zone_heroes[0]['hero_name']}利用地形或持续区域限制走位",
            confidence="high",
            priority=90,
            archetype="forced_movement_zone",
            hero_ids=[int(hero["hero_id"]) for hero in roster],
        )
    if len(controllers) >= 2:
        names = "、".join(hero["hero_name"] for hero in controllers[:3])
        return _claim(
            "阵容结构",
            f"这套阵容由{names}组成多段硬控链，重点是连续限制而不是单次控制",
            confidence="medium",
            priority=87,
            archetype="control_chain",
            hero_ids=[int(hero["hero_id"]) for hero in roster],
        )
    return None


def _required_claim_ids(evidence: list[dict[str, Any]]) -> list[str]:
    if not evidence:
        return []
    contextual = next(
        (claim for claim in evidence if claim["kind"] in {"克制关系", "官方克制", "阵容联动", "阵容结构", "战术定位"}),
        None,
    )
    team = next((claim for claim in evidence if claim["kind"] == "战队联动"), None)
    required = [claim["id"] for claim in (contextual, team) if claim is not None]
    return list(dict.fromkeys(required)) or [evidence[0]["id"]]


def _commentary_cache_key(brief: dict[str, Any]) -> str:
    return sha256(json.dumps(brief, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _extract_json(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _contains_unsupported_inference(text: str) -> bool:
    normalized = text.casefold()
    return any(marker.casefold() in normalized for marker in UNSUPPORTED_INFERENCE_MARKERS)


def _contains_visible_evidence_reference(text: str) -> bool:
    return bool(
        re.search(r"(?i)claim[\s_-]*\d+", text)
        or re.search(r"(?:证据|依据|论据)[\s_-]*(?:编号)?[\s_-]*\d+", text)
    )


def _generate_llm_commentary(brief: dict[str, Any]) -> dict[str, Any] | None:
    """Use Kimi only as a constrained narrator over already-grounded claims."""
    cache_key = _commentary_cache_key(brief)
    if cached := _LLM_COMMENTARY_CACHE.get(cache_key):
        return cached
    try:
        settings = get_settings()
        commentary_settings = settings.model_copy(
            update={"kimi_timeout_seconds": min(settings.kimi_timeout_seconds, 3.0)}
        )
        client = build_kimi_client(commentary_settings)
        response = client.chat.completions.create(
            model=settings.kimi_model,
            messages=[
                {"role": "system", "content": COMMENTATOR_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(brief, ensure_ascii=False)},
            ],
            max_tokens=220,
            # Kimi K2.6 only accepts temperature=0.6. Grounding comes from the
            # constrained evidence brief and output validation, not sampling.
            temperature=0.6,
            extra_body={"thinking": {"type": "disabled"}},
        )
        parsed = _extract_json(str(response.choices[0].message.content or ""))
    except KimiConfigurationError:
        return None
    except Exception as exc:  # Provider failure must not block the BP simulator.
        logger.warning("draft_commentary_provider_unavailable", extra={"error_type": type(exc).__name__})
        return None
    if not parsed:
        return None
    text = str(parsed.get("commentary") or "").strip()
    used_ids = [str(item) for item in parsed.get("used_evidence_ids", [])]
    allowed_ids = {claim["id"] for claim in brief["claims"]}
    required_ids = set(brief["instructions"]["required_evidence_ids"])
    if (
        not text
        or len(text) > 180
        or _contains_unsupported_inference(text)
        or _contains_visible_evidence_reference(text)
        or not required_ids.issubset(used_ids)
        or not set(used_ids).issubset(allowed_ids)
    ):
        return None
    result = {"commentary": text, "used_evidence_ids": used_ids}
    if len(_LLM_COMMENTARY_CACHE) >= 512:
        _LLM_COMMENTARY_CACHE.pop(next(iter(_LLM_COMMENTARY_CACHE)))
    _LLM_COMMENTARY_CACHE[cache_key] = result
    return result


def build_selection_commentary(*, league_id: str, state: dict[str, Any], selected_hero_id: int, model_type: str = "learnable", use_llm: bool = True) -> dict[str, Any]:
    profiles_by_id = _hero_profiles()
    selected = profiles_by_id.get(selected_hero_id)
    if selected is None:
        raise ValueError("Selected hero has no mechanics profile.")
    action, side = str(state["action"]), str(state["side"])
    opponent_side = "red" if side == "blue" else "blue"
    team_id, team_name = str(state[f"{side}_team_id"]), str(state[f"{side}_team_name"])
    opponent_id, opponent_name = str(state[f"{opponent_side}_team_id"]), str(state[f"{opponent_side}_team_name"])
    own_ids = {int(hero_id) for hero_id in state.get(f"{side}_picks", [])}
    enemy_ids = {int(hero_id) for hero_id in state.get(f"{opponent_side}_picks", [])}
    allies = [profiles_by_id[hero_id] for hero_id in own_ids if hero_id in profiles_by_id]
    enemies = [profiles_by_id[hero_id] for hero_id in enemy_ids if hero_id in profiles_by_id]
    trend_rows = _jsonl(str(OUTPUT_ROOT / league_id / "team_recent_trends.jsonl"))
    evidence: list[dict[str, Any]] = []
    own_trend = _trend_claim(trend_rows, team_id=team_id, team_name=team_name, hero_id=selected_hero_id, action=action, role="acting")
    if own_trend:
        evidence.append(own_trend)
    if action == "ban":
        opponent_trend = _trend_claim(trend_rows, team_id=opponent_id, team_name=opponent_name, hero_id=selected_hero_id, action="pick", role="opponent")
        if opponent_trend:
            evidence.append(opponent_trend)
    mechanic = _mechanic_claim(selected, action=action, allies=allies, enemies=enemies)
    if mechanic:
        evidence.append(mechanic)
    tactical_identity = _tactical_identity_claim(selected)
    if tactical_identity:
        evidence.append(tactical_identity)
    if action == "pick":
        tactical_interactions = _tactical_interaction_claims(selected, allies, enemies)
        tactical_pairs = {
            tuple(claim["hero_pair"])
            for claim in tactical_interactions
            if claim.get("hero_pair")
        }
        mechanic_interactions = [
            claim
            for claim in _interaction_claims(selected, allies, enemies)
            if not (
                claim.get("rule") in GENERIC_MECHANIC_PAIR_RULES
                and tuple(sorted((int(claim["source_hero_id"]), int(claim["target_hero_id"]))))
                in tactical_pairs
            )
        ]
        evidence.extend(tactical_interactions)
        evidence.extend(mechanic_interactions)
        evidence.extend(_official_relationship_claims(selected, enemies))
        pairing = _pairing_claim(_jsonl(str(OUTPUT_ROOT / league_id / "team_synergy_stats.jsonl")), team_id=team_id, team_name=team_name, selected_id=selected_hero_id, own_ids=own_ids)
        if pairing:
            evidence.append(pairing)
        historical_counter = _historical_counter_claim(
            _jsonl(str(OUTPUT_ROOT / league_id / "counter_pick_stats.jsonl")),
            selected=selected,
            enemies=enemies,
        )
        if historical_counter:
            evidence.append(historical_counter)
        composition = _composition_claim(selected, allies)
        if composition:
            evidence.append(composition)
    if any(claim["kind"] in {"克制关系", "官方克制", "阵容联动", "阵容结构"} for claim in evidence):
        evidence = [claim for claim in evidence if claim["kind"] != "技能机制"]
    evidence.sort(key=lambda row: row["priority"], reverse=True)
    evidence = evidence[:5]
    for index, claim in enumerate(evidence, start=1):
        claim["id"] = f"claim_{index}"
    clauses = [claim["detail"] for claim in evidence[:2]]
    commentary = f"{team_name}{ACTION_ZH[action]}{selected['hero_name']}。" + ("；".join(clauses) + "。" if clauses else "这一手的阵容意图还需要后续选人确认。")
    result = {
        "selected_hero": {"hero_id": selected_hero_id, "hero_name": selected["hero_name"]},
        "event": {"action": action, "side": side, "bp_order": state.get("bp_order"), "team": team_name, "opponent": opponent_name},
        "commentary": commentary,
        "evidence": evidence,
        "llm_brief": {
            "event": {"action": ACTION_ZH[action], "hero": selected["hero_name"], "team": team_name, "opponent": opponent_name, "bp_order": state.get("bp_order")},
            "claims": evidence,
            "instructions": {
                "language": "zh-CN",
                "max_sentences": 3,
                "max_characters": 180,
                "required_evidence_ids": _required_claim_ids(evidence),
                "optional_evidence_ids": [
                    claim["id"]
                    for claim in evidence
                    if claim["id"] not in _required_claim_ids(evidence)
                ],
                "forbidden": [
                    "未提供证据的选手意图",
                    "基于样本率的因果结论",
                    "无证据的胜负预测",
                    "只说补控制或补伤害而不解释具体技能连接",
                    "把控制与伤害自动视为有效联动",
                    "改变战术角色中明确写出的开团、拆火、消耗或保护职责",
                ],
            },
        },
    }
    generated = _generate_llm_commentary(result["llm_brief"]) if use_llm else None
    if generated:
        result["fallback_commentary"] = result["commentary"]
        result["commentary"] = generated["commentary"]
        result["commentary_source"] = "kimi"
        result["used_evidence_ids"] = generated["used_evidence_ids"]
    else:
        result["commentary_source"] = "deterministic"
        result["used_evidence_ids"] = []
    return result
