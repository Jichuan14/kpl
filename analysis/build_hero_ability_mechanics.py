"""Build compact hero-mechanics facts from the official Tencent skill catalogue.

The input is a temporary browser extraction containing the official Chinese skill
introductions.  The generated artifact deliberately does not reproduce those
descriptions.  It keeps source URLs and skill names, then converts descriptions
into a controlled mechanics vocabulary suitable for evidence-backed commentary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import REPO_ROOT

DEFAULT_INPUT = Path("/tmp/kpl_tencent_hero_skills.json")
DEFAULT_MODEL = REPO_ROOT / "analysis" / "outputs" / "20260003" / "draft_model.json"
DEFAULT_OUTPUT = REPO_ROOT / "analysis" / "hero_ability_mechanics.json"

# One confirmed catalogue-text false positive. 沈梦溪's passive mentions the
# respawn state, but it does not grant a revival effect.
HERO_MECHANIC_EXCLUSIONS: dict[str, set[str]] = {
    "沈梦溪": {"defense_revive"},
}


# Tags are intentionally mechanical and conservative.  They describe what the
# skill text explicitly says, not whether a hero is strategically good or bad.
MECHANIC_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "damage_physical": ("物理伤害", (r"物理伤害",)),
    "damage_magic": ("法术伤害", (r"法术伤害",)),
    "damage_true": ("真实伤害", (r"真实伤害",)),
    "damage_percent_health": (
        "生命值比例伤害",
        (r"(?:最大|当前|已损失)生命值.{0,18}(?:伤害|额外)",),
    ),
    "damage_execute": ("低生命斩杀", (r"斩杀", r"生命值越低.{0,20}伤害越高")),
    "damage_amplification": (
        "伤害强化",
        (
            r"额外造成.{0,20}伤害",
            r"伤害.{0,12}(?:增加|提升|强化)",
            r"增加.{0,12}伤害",
            r"必定暴击",
        ),
    ),
    "control_slow": ("减速", (r"减速", r"降低.{0,10}移速")),
    "control_stun": ("眩晕", (r"眩晕", r"晕眩")),
    "control_knockup": ("击飞", (r"击飞",)),
    "control_knockback": ("击退", (r"击退",)),
    "control_pull": ("牵引/拉拽", (r"牵引", r"拉回", r"拉向", r"吸向", r"拖拽")),
    "control_root": ("定身", (r"定身",)),
    "control_silence": ("沉默", (r"沉默",)),
    "control_taunt": ("嘲讽", (r"嘲讽",)),
    "control_suppress": ("压制", (r"压制",)),
    "control_freeze": ("冰冻", (r"冰冻", r"冻结")),
    "control_petrify": ("石化", (r"石化",)),
    "control_fear": ("恐惧", (r"恐惧",)),
    "control_disarm": ("缴械", (r"缴械",)),
    "control_blind": ("致盲", (r"致盲",)),
    "control_anti_mobility": (
        "限制位移",
        (r"(?:无法|禁止|不能)位移", r"打断.{0,8}位移"),
    ),
    "mobility_dash": (
        "突进/位移",
        (r"位移", r"突进", r"冲锋", r"冲刺", r"跃向", r"跳向", r"冲向"),
    ),
    "mobility_teleport": ("传送/瞬移", (r"传送", r"瞬移", r"闪烁", r"瞬步")),
    "mobility_wall_traverse": ("穿越地形", (r"穿越墙", r"翻越墙", r"穿墙")),
    "mobility_speed_boost": (
        "移速强化",
        (
            r"(?:增加|提升|获得).{0,12}(?:移速|移动速度)",
            r"(?:移速|移动速度).{0,8}(?:增加|提升)",
        ),
    ),
    "support_ally_reposition": (
        "队友位移/传送",
        (
            r"(?:友方|友军|队友).{0,35}(?:位移|传送|召回|拉至|移动到)",
            r"(?:位移|传送|召回).{0,35}(?:友方|友军|队友)",
        ),
    ),
    "defense_shield": ("护盾", (r"护盾",)),
    "support_ally_shield": (
        "友方护盾",
        (
            r"(?:友方|友军|队友).{0,25}(?:获得|生成|增加).{0,12}护盾",
            r"为.{0,18}(?:友方|友军|队友).{0,18}护盾",
        ),
    ),
    "defense_damage_reduction": (
        "伤害减免",
        (r"免伤", r"减少.{0,12}(?:所受|受到).{0,8}伤害", r"伤害减免"),
    ),
    "defense_damage_block": ("伤害格挡", (r"伤害格挡", r"格挡.{0,8}伤害")),
    "defense_invulnerable": ("无敌", (r"无敌",)),
    "defense_untargetable": ("无法选中", (r"不可选中", r"无法选中")),
    "defense_control_immunity": (
        "控制免疫/霸体",
        (r"免疫.{0,8}控制", r"控制免疫", r"霸体", r"无法被控制"),
    ),
    "defense_cleanse": ("解除控制", (r"解除.{0,12}控制", r"移除.{0,12}控制", r"净化")),
    "defense_revive": ("复活", (r"复活",)),
    "defense_death_prevention": (
        "致命伤害保护",
        (r"免疫死亡", r"不会死亡", r"抵挡.{0,8}致命", r"致命伤害"),
    ),
    "defense_projectile_block": (
        "飞行物阻挡",
        (
            r"(?:抵挡|阻挡|挡住|击落).{0,16}飞行物",
            r"飞行物.{0,16}(?:抵挡|阻挡|挡住|击落)",
        ),
    ),
    "defense_damage_reflect": ("伤害反弹", (r"反弹.{0,10}伤害", r"反射.{0,10}伤害")),
    "support_damage_rewind": (
        "伤害回溯",
        (r"返还.{0,35}(?:受到|所受).{0,20}伤害", r"时光倒流"),
    ),
    "sustain_heal": (
        "生命回复",
        (r"回复.{0,55}生命", r"恢复.{0,55}生命", r"获得.{0,20}回复", r"治疗"),
    ),
    "support_ally_heal": (
        "友方治疗",
        (
            r"(?:友方|友军|队友).{0,30}(?:回复|恢复).{0,12}生命",
            r"为.{0,20}(?:友方|友军|队友).{0,20}(?:回复|恢复|治疗)",
        ),
    ),
    "sustain_lifesteal": ("吸血", (r"吸血",)),
    "sustain_mana_restore": ("法力/能量回复", (r"回复.{0,12}(?:法力|能量)", r"恢复.{0,12}(?:法力|能量)")),
    "buff_attack_speed": ("攻速强化", (r"(?:增加|提升|获得).{0,12}攻速", r"攻速.{0,8}(?:增加|提升)")),
    "buff_attack_power": (
        "攻击强化",
        (r"(?:增加|提升|获得).{0,12}(?:攻击力|物理攻击|法术攻击)",),
    ),
    "buff_defense": (
        "防御强化",
        (
            r"(?:增加|提升|获得|提供).{0,18}(?:物理防御|法术防御|双抗|防御力)",
        ),
    ),
    "debuff_healing_reduction": (
        "降低回复",
        (r"(?:降低|减少).{0,18}(?:生命回复|治疗效果|回复效果)", r"重伤"),
    ),
    "debuff_shield_break": (
        "克制护盾",
        (r"护盾.{0,18}额外伤害", r"对.{0,12}护盾.{0,18}伤害", r"无视.{0,8}护盾"),
    ),
    "debuff_armor": ("降低物理防御", (r"(?:降低|减少).{0,15}(?:物理防御|护甲)",)),
    "debuff_magic_defense": ("降低法术防御", (r"(?:降低|减少).{0,15}(?:法术防御|魔抗)",)),
    "utility_vision": (
        "视野获取/暴露",
        (
            r"获得.{0,12}视野",
            r"获取.{0,12}视野",
            r"暴露.{0,12}视野",
            r"提升.{0,12}视野",
            r"探测",
            r"照亮",
        ),
    ),
    "utility_stealth": ("隐身/伪装", (r"隐身", r"伪装")),
    "utility_zone": (
        "持续区域",
        (r"法阵", r"领域", r"区域.{0,24}持续", r"范围内.{0,24}持续"),
    ),
    "utility_terrain": (
        "创造/改变地形",
        (
            r"(?:创造|生成|制造).{0,16}(?:地形|墙体|水域)",
            r"改变.{0,12}地形",
            r"筑起.{0,12}墙",
        ),
    ),
    "utility_summon": ("召唤物", (r"召唤",)),
    "utility_skill_refresh": (
        "技能刷新",
        (r"刷新.{0,20}技能", r"重置.{0,20}技能.{0,8}冷却", r"技能.{0,16}刷新"),
    ),
    "support_ally_skill_refresh": (
        "友方技能刷新",
        (
            r"刷新.{0,30}(?:友方|友军|队友).{0,25}技能",
            r"(?:友方|友军|队友).{0,30}技能.{0,16}刷新",
        ),
    ),
    "utility_cooldown_reduction": (
        "冷却缩减/返还",
        (
            r"减少.{0,18}冷却",
            r"冷却.{0,12}减少",
            r"返还.{0,18}冷却",
            r"恢复.{0,12}冷却",
            r"冷却缩减",
        ),
    ),
    "utility_mark": ("标记/印记", (r"标记", r"印记")),
    "utility_transformation": ("形态切换", (r"形态", r"变身", r"切换")),
    "utility_basic_attack_enhancement": (
        "强化普攻",
        (r"强化.{0,8}普攻", r"普攻.{0,10}强化", r"下次普通攻击"),
    ),
    "utility_range_extension": (
        "攻击/施法距离强化",
        (
            r"(?:增加|提升).{0,20}(?:攻击|施法|释放)范围",
            r"增加.{0,12}(?:攻击|施法)距离",
            r"(?:攻击|施法)距离.{0,8}增加",
        ),
    ),
    "utility_aoe_basic_attack": (
        "范围普攻",
        (r"普攻.{0,20}范围攻击", r"普通攻击.{0,20}范围伤害"),
    ),
    "utility_gold_generation": (
        "额外经济",
        (r"额外获得.{0,12}金币", r"额外金币"),
    ),
    "utility_structure_interaction": (
        "机关/防御塔交互",
        (r"机关.{0,24}(?:伤害|干扰)", r"防御塔.{0,24}(?:伤害|干扰)"),
    ),
    "utility_clone_or_mimic": (
        "复制/傀儡能力",
        (r"傀儡.{0,30}能力完全相同", r"变化为对应英雄"),
    ),
    "vulnerability_projectile_blockable": (
        "飞行物可被阻挡",
        (r"(?:炮弹|飞行物).{0,18}可被.{0,12}阻挡",),
    ),
}

CONDITION_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    "delayed_effect": ("延迟生效", (r"延迟", r"短暂延迟")),
    "channel_or_charge": ("吟唱/蓄力", (r"吟唱", r"引导", r"蓄力")),
    "recast": ("可再次释放", (r"再次点击", r"再次释放", r"第二段")),
    "requires_mark": ("依赖标记/印记", (r"标记", r"印记")),
    "water_or_river": ("依赖水域/河道", (r"水域", r"河道")),
    "single_target_bonus": ("单目标强化", (r"只命中一个目标", r"单个目标")),
    "on_kill_or_assist": ("击败/助攻触发", (r"击败", r"助攻")),
    "low_health_condition": ("低生命触发/强化", (r"生命值越低", r"生命值低于")),
    "directional": ("方向性技能", (r"指定方向",)),
    "proximity": ("近身/范围条件", (r"附近", r"范围内")),
    "ally_targeted": ("与友方交互", (r"友方", r"友军", r"队友")),
    "back_attack": ("背后攻击条件", (r"身后.{0,20}攻击", r"背后.{0,20}攻击")),
    "front_attack_penalty": ("正面攻击限制", (r"正面.{0,20}攻击",)),
    "distance_scaling": ("随距离强化", (r"距离.{0,30}(?:增加|强化|提升)",)),
}

HARD_CONTROL = {
    "control_stun",
    "control_knockup",
    "control_knockback",
    "control_pull",
    "control_root",
    "control_silence",
    "control_taunt",
    "control_suppress",
    "control_freeze",
    "control_petrify",
    "control_fear",
    "control_disarm",
}


def matching_tags(
    text: str,
    rules: dict[str, tuple[str, tuple[str, ...]]],
) -> tuple[list[str], dict[str, list[str]]]:
    tags: list[str] = []
    evidence: dict[str, list[str]] = {}
    for tag, (_label, patterns) in rules.items():
        matches: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                phrase = match.group(0).strip()
                if phrase and phrase not in matches:
                    matches.append(phrase)
        if matches:
            tags.append(tag)
            evidence[tag] = matches[:3]
    return tags, evidence


def hero_hooks(mechanics: set[str]) -> dict[str, list[str]]:
    provides: set[str] = set()
    answers: set[str] = set()
    enables: set[str] = set()
    vulnerable_to: set[str] = set()

    if mechanics & HARD_CONTROL:
        provides.add("hard_crowd_control")
        enables.update({"control_chain", "delayed_damage_followup"})
    if "control_slow" in mechanics or "control_blind" in mechanics:
        provides.add("soft_crowd_control")
    if mechanics & {"control_knockback", "control_pull"}:
        provides.add("forced_movement")
        enables.add("persistent_area_effects")
    if "control_anti_mobility" in mechanics:
        provides.add("anti_mobility")
        answers.update({"dash", "dive"})
    if "defense_cleanse" in mechanics:
        provides.add("cleanse")
        answers.add("crowd_control")
    if "defense_control_immunity" in mechanics:
        provides.add("control_immunity")
        answers.add("crowd_control")
    if "defense_projectile_block" in mechanics:
        provides.add("projectile_block")
        answers.update({"projectile_damage", "ranged_poke"})
    if "support_ally_reposition" in mechanics:
        provides.add("ally_reposition")
        enables.update({"immobile_carry_protection", "ally_engage_or_escape"})
    if "support_ally_heal" in mechanics:
        provides.add("ally_healing")
        enables.update({"sustained_frontline", "extended_fight"})
    elif "sustain_heal" in mechanics:
        provides.add("self_sustain")
    if "support_ally_shield" in mechanics:
        provides.add("ally_shielding")
        enables.update({"carry_protection", "dive_survivability"})
    elif "defense_shield" in mechanics:
        provides.add("self_shielding")
    if "support_ally_skill_refresh" in mechanics:
        provides.add("ally_skill_refresh")
        enables.add("high_value_skill_reuse")
    elif "utility_skill_refresh" in mechanics:
        provides.add("self_skill_refresh")
        enables.add("ability_reuse")
    if "utility_vision" in mechanics:
        provides.add("vision_reveal")
        answers.add("stealth_or_fog")
    if "utility_terrain" in mechanics:
        provides.add("terrain_control")
        enables.add("choke_point_control")
    if "utility_zone" in mechanics:
        provides.add("zone_control")
        enables.add("objective_or_choke_control")
    if "debuff_healing_reduction" in mechanics:
        provides.add("healing_reduction")
        answers.add("healing_and_sustain")
    if "debuff_shield_break" in mechanics:
        provides.add("shield_counter")
        answers.add("shielding")
    if mechanics & {"mobility_dash", "mobility_teleport", "mobility_wall_traverse"}:
        provides.add("engage_or_escape_mobility")
        enables.update({"dive", "flank"})
    if "defense_revive" in mechanics or "defense_death_prevention" in mechanics:
        provides.add("death_protection")
        enables.add("aggressive_commitment")
    if "support_damage_rewind" in mechanics:
        provides.add("damage_rewind")
        enables.add("extended_fight")
    if "utility_gold_generation" in mechanics:
        provides.add("team_economy_acceleration")
    if "utility_structure_interaction" in mechanics:
        provides.add("structure_pressure")
    if "utility_clone_or_mimic" in mechanics:
        provides.add("hero_mimicry")
    if "vulnerability_projectile_blockable" in mechanics:
        vulnerable_to.add("projectile_block_or_body_block")

    return {
        "provides": sorted(provides),
        "answers": sorted(answers),
        "enables": sorted(enables),
        "vulnerable_to": sorted(vulnerable_to),
    }


def load_project_model(path: Path) -> tuple[dict[str, int], dict[int, str]]:
    with path.open(encoding="utf-8") as source:
        model = json.load(source)
    id_to_name = {
        int(hero_id): str(name)
        for hero_id, name in model.get("hero_names", {}).items()
    }
    name_to_id = {name.strip(): hero_id for hero_id, name in id_to_name.items()}
    return name_to_id, id_to_name


def build_artifact(raw: dict[str, Any], model_path: Path, source_hash: str) -> dict[str, Any]:
    name_to_id, id_to_name = load_project_model(model_path)
    heroes: list[dict[str, Any]] = []

    for source_hero in raw.get("heroes", []):
        hero_name = str(source_hero.get("hero_name") or source_hero.get("name") or "").strip()
        if not hero_name:
            continue
        skill_rows: list[dict[str, Any]] = []
        hero_mechanics: set[str] = set()
        hero_conditions: set[str] = set()
        excluded_mechanics = HERO_MECHANIC_EXCLUSIONS.get(hero_name, set())
        for skill in source_hero.get("skills", []):
            description = str(skill.get("description") or "").strip()
            mechanics, evidence = matching_tags(description, MECHANIC_RULES)
            mechanics = [tag for tag in mechanics if tag not in excluded_mechanics]
            for tag in excluded_mechanics:
                evidence.pop(tag, None)
            conditions, condition_evidence = matching_tags(description, CONDITION_RULES)
            hero_mechanics.update(mechanics)
            hero_conditions.update(conditions)
            skill_rows.append(
                {
                    "slot": str(skill.get("slot") or ""),
                    "skill_name": str(skill.get("name") or "").strip(),
                    "mechanics": mechanics,
                    "conditions": conditions,
                    "summary_zh": "；".join(
                        [*(MECHANIC_RULES[tag][0] for tag in mechanics),
                         *(CONDITION_RULES[tag][0] for tag in conditions)]
                    ),
                    "matched_terms": evidence,
                    "condition_terms": condition_evidence,
                }
            )

        hero_id = name_to_id.get(hero_name)
        heroes.append(
            {
                "hero_id": hero_id,
                "hero_name": hero_name,
                "source_url": str(source_hero.get("source_url") or source_hero.get("href") or ""),
                "skill_count": len(skill_rows),
                "mechanics": sorted(hero_mechanics),
                "conditions": sorted(hero_conditions),
                "commentary_hooks": hero_hooks(hero_mechanics),
                "skills": skill_rows,
            }
        )

    mapped_ids = {int(row["hero_id"]) for row in heroes if row.get("hero_id") is not None}
    catalogue_names = {str(row["hero_name"]) for row in heroes}
    unmapped_catalogue = sorted(
        row["hero_name"] for row in heroes if row.get("hero_id") is None
    )
    missing_project = sorted(
        (
            {"hero_id": hero_id, "hero_name": hero_name}
            for hero_id, hero_name in id_to_name.items()
            if hero_id not in mapped_ids and hero_name not in catalogue_names
        ),
        key=lambda row: (row["hero_name"], row["hero_id"]),
    )

    heroes.sort(
        key=lambda row: (
            row["hero_id"] is None,
            row["hero_id"] if row["hero_id"] is not None else 10**9,
            row["hero_name"],
        )
    )
    return {
        "schema_version": 1,
        "artifact_type": "hero_ability_mechanics",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "publisher": "腾讯游戏",
            "catalogue_url": str(raw.get("source_catalog_url") or ""),
            "source_language": "zh-CN",
            "extracted_at": raw.get("extracted_at"),
            "snapshot_sha256": source_hash,
            "normalization": (
                "Skill names and short matched mechanic terms are retained; full "
                "catalogue descriptions are not reproduced. Tags are deterministic "
                "text-derived capabilities and do not assert strategic causality."
            ),
        },
        "project_model": str(model_path.relative_to(REPO_ROOT)),
        "coverage": {
            "catalogue_hero_count": len(heroes),
            "heroes_with_skills": sum(bool(row["skills"]) for row in heroes),
            "skill_count": sum(len(row["skills"]) for row in heroes),
            "project_model_hero_count": len(id_to_name),
            "mapped_project_hero_count": len(mapped_ids),
            "unmapped_catalogue_heroes": unmapped_catalogue,
            "project_heroes_missing_from_catalogue": missing_project,
        },
        "taxonomy": {
            "mechanics": {
                tag: label for tag, (label, _patterns) in MECHANIC_RULES.items()
            },
            "conditions": {
                tag: label for tag, (label, _patterns) in CONDITION_RULES.items()
            },
            "commentary_hook_groups": [
                "provides",
                "answers",
                "enables",
                "vulnerable_to",
            ],
        },
        "heroes": heroes,
    }


def validate_artifact(artifact: dict[str, Any]) -> None:
    coverage = artifact["coverage"]
    heroes = artifact["heroes"]
    if coverage["catalogue_hero_count"] != len(heroes):
        raise ValueError("Hero coverage count does not match artifact rows")
    if coverage["skill_count"] != sum(len(row["skills"]) for row in heroes):
        raise ValueError("Skill coverage count does not match artifact rows")
    if len({row["hero_name"] for row in heroes}) != len(heroes):
        raise ValueError("Duplicate hero names in mechanics artifact")
    known_tags = set(artifact["taxonomy"]["mechanics"])
    for hero in heroes:
        if not hero["source_url"].startswith("https://pvp.qq.com/"):
            raise ValueError(f"Unexpected source URL for {hero['hero_name']}")
        for skill in hero["skills"]:
            unknown = set(skill["mechanics"]) - known_tags
            if unknown:
                raise ValueError(f"Unknown mechanic tags for {hero['hero_name']}: {unknown}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()

    source_bytes = arguments.input.read_bytes()
    raw = json.loads(source_bytes)
    artifact = build_artifact(
        raw,
        arguments.model.resolve(),
        hashlib.sha256(source_bytes).hexdigest(),
    )
    validate_artifact(artifact)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(arguments.output),
                **artifact["coverage"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
