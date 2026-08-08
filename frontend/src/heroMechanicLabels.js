import { language } from "./i18n";

const categoryLabels = {
  buff: ["Buff", "增益"], control: ["Control", "控制"], damage: ["Damage", "伤害"],
  debuff: ["Debuff", "减益"], defense: ["Defense", "防御"], mobility: ["Mobility", "位移"],
  support: ["Support", "辅助"], sustain: ["Sustain", "续航"], utility: ["Utility", "功能"],
  vulnerability: ["Vulnerability", "弱点"], condition: ["Condition", "条件"],
};

const detailLabels = {
  amplification: ["damage amplification", "增伤"], anti_mobility: ["anti-mobility", "限制位移"], ally_heal: ["ally heal", "治疗友军"], ally_reposition: ["ally reposition", "友军位移"], ally_shield: ["ally shield", "友军护盾"], ally_skill_refresh: ["ally skill refresh", "刷新友军技能"], ally_targeted: ["ally targeted", "以友军为目标"], armor: ["armor reduction", "减护甲"], attack_power: ["attack power", "攻击力"], attack_speed: ["attack speed", "攻速"], back_attack: ["back attack", "背击"], basic_attack_enhancement: ["enhanced basic attack", "强化普攻"], blind: ["blind", "致盲"], channel_or_charge: ["channel / charge", "蓄力/引导"], cleanse: ["cleanse", "净化"], clone_or_mimic: ["clone / mimic", "分身/模仿"], control_immunity: ["control immunity", "免控"], cooldown_reduction: ["cooldown reduction", "冷却缩减"], damage_block: ["damage block", "格挡"], damage_reduction: ["damage reduction", "减伤"], damage_reflect: ["damage reflect", "反伤"], damage_rewind: ["damage rewind", "伤害回溯"], dash: ["dash", "突进"], death_prevention: ["death prevention", "免死"], defense: ["defense", "防御"], delayed_effect: ["delayed effect", "延迟生效"], directional: ["directional", "方向性技能"], disarm: ["disarm", "缴械"], distance_scaling: ["distance scaling", "距离增益"], execute: ["execute", "斩杀"], fear: ["fear", "恐惧"], freeze: ["freeze", "冰冻"], front_attack_penalty: ["front attack penalty", "正面减益"], gold_generation: ["gold generation", "经济获取"], heal: ["healing", "回复"], healing_reduction: ["healing reduction", "重伤"], invulnerable: ["invulnerable", "无敌"], knockback: ["knockback", "击退"], knockup: ["knock-up", "击飞"], lifesteal: ["lifesteal", "吸血"], low_health_condition: ["low-health condition", "低血量条件"], magic_defense: ["magic-defense reduction", "减魔抗"], mana_restore: ["mana restore", "回蓝"], mark: ["mark", "标记"], on_kill_or_assist: ["on kill / assist", "击杀/助攻触发"], percent_health: ["percent-health damage", "百分比生命伤害"], petrify: ["petrify", "石化"], projectile_block: ["projectile block", "阻挡飞行物"], projectile_blockable: ["blocked by projectile defense", "可被飞行物格挡"], proximity: ["proximity", "距离条件"], pull: ["pull", "拉拽"], range_extension: ["range extension", "攻击距离提升"], recast: ["recast", "二段施放"], requires_mark: ["requires mark", "需要标记"], revive: ["revive", "复活"], root: ["root", "定身"], shield: ["shield", "护盾"], shield_break: ["shield break", "破盾"], silence: ["silence", "沉默"], single_target_bonus: ["single-target bonus", "单体增益"], skill_refresh: ["skill refresh", "刷新技能"], slow: ["slow", "减速"], speed_boost: ["speed boost", "加速"], stealth: ["stealth", "隐身"], structure_interaction: ["structure interaction", "防御塔互动"], stun: ["stun", "眩晕"], summon: ["summon", "召唤"], suppress: ["suppress", "压制"], taunt: ["taunt", "嘲讽"], teleport: ["teleport", "传送"], terrain: ["terrain", "地形"], transformation: ["transformation", "变身"], true: ["true damage", "真实伤害"], untargetable: ["untargetable", "不可选中"], vision: ["vision", "视野"], wall_traverse: ["wall traverse", "穿墙"], water_or_river: ["water / river", "水域/河道"], zone: ["zone control", "区域控制"], physical: ["physical damage", "物理伤害"], magic: ["magic damage", "法术伤害"], aoe_basic_attack: ["area basic attack", "范围普攻"],
};

function humanize(value) {
  return value.replaceAll("_", " ");
}

export function mechanicLabel(key) {
  const mechanicName = key.replace(/^mechanic__/, "");
  const prefix = key.startsWith("condition__") ? "condition" : mechanicName.split("_")[0];
  const detail = key.startsWith("condition__")
    ? key.replace(/^condition__/, "")
    : mechanicName.replace(new RegExp(`^${prefix}_`), "");
  const index = language.value === "zh-CN" ? 1 : 0;
  return {
    key,
    label: `${categoryLabels[prefix]?.[index] || humanize(prefix)}：${detailLabels[detail]?.[index] || humanize(detail)}`,
  };
}
