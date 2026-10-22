"""内容自检：启动期硬断言。

任何一条不过都**拒绝启动**（system design §2.2 / 风险 R6），
避免"扣了贡献发不出功法"这类运行期错位。
返回前会把全部问题聚合打印，便于一次性修完。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from ..core.clock import DAYS_PER_MONTH, DAYS_PER_YEAR

TIERS = {"fan", "ling", "bao", "sheng", "di"}
SUB_TIERS = {"xia", "zhong", "shang"}
TECH_TYPES = {"heart", "attack", "defense", "support"}
TECH_SOURCES = {"general", "sect", "sansxiu", "forbidden"}
ITEM_KINDS = {"pill", "material", "consumable"}
ENEMY_KINDS = {"minion", "elite", "boss"}
EVENT_CATEGORIES = {"resource", "npc", "combat", "fortune", "trap", "karma"}
OBJ_TYPES = {
    "talk", "cultivate", "kill", "collect", "deliver", "reach_level",
    "breakthrough", "join_sect", "choose_direction", "complete_sect_quest",
    "sansxiu_encounter", "enter_forbidden", "event_choice",
}
# 任务书要求至少覆盖 12 种（含 event_choice 为 13 种）
REQUIRED_OBJ_TYPES = OBJ_TYPES - {"event_choice"}
SLOT_IDS = {"weapon", "armor", "helm", "accessory", "natal", "storage"}
ENV_COEFS = {1.0, 1.5, 2.0, 3.0, 5.0}
AFFINITY_VALUES = {0.8, 1.0, 1.3, 1.5}
PERSONALITY_MODS = {-500, 0, 800}


class ContentValidationError(RuntimeError):
    """内容自检失败（携带全部问题清单）。"""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: List[str] = list(problems)
        head = "、".join(self.problems[:8])
        more = "" if len(self.problems) <= 8 else f" …（共 {len(self.problems)} 条）"
        super().__init__(f"content validation failed: {head}{more}")


def _check_counts(content: Any, problems: List[str]) -> None:
    declared = dict(content.counts or {})
    actual = {
        "realms": len(content.realms),
        "physiques": len(content.physiques),
        "talents": len(content.talents),
        "personalities": len(content.personalities),
        "techniques": len(content.techniques),
        "items": len(content.items),
        "equipment_templates": len(content.equipments["templates"]),
        "equipment_slots": len(content.equipments["slots"]),
        "affix_pool": len(content.equipments["affix_pool"]),
        "rarity_tiers": len(content.equipments["rarity"]),
        "regions": len(content.regions),
        "enemies": len(content.enemies),
        "events": len(content.events),
        "quests_main": len(content.quests),
        "sects": len(content.sects),
        "sect_techniques": sum(len(s["techniques"]) for s in content.sects),
        "directions": len(content.directions),
        "direction_nodes": len(content.direction_node_by_id),
        "sansxiu_encounters": len(content.encounters),
        "forbidden_lands": len(content.lands),
    }
    for key, value in actual.items():
        if key in declared and int(declared[key]) != value:
            problems.append(f"counts.{key}: declared {declared[key]} != actual {value}")


def _check_unique(items: Sequence[Mapping[str, Any]], key: str,
                  label: str, problems: List[str]) -> None:
    seen: Set[str] = set()
    for item in items:
        ident = str(item.get(key))
        if ident in seen:
            problems.append(f"{label}: duplicated {key} '{ident}'")
        seen.add(ident)


#: 12 境时间档位的**权威序列**（按 order 依次），单位游戏日。
#: 文案：1 月 / 3 月 / 6 月 / 1 年 / 3 年 / 5 年 / 8 年 / 10 年 / 20 年 / 100 年 / 1000 年 / 10000 年
REALM_TIME_TIER_DAYS: Tuple[int, ...] = (
    30, 90, 180, 360, 1080, 1800, 2880, 3600, 7200, 36000, 360000, 3600000,
)


def _tier_label_days(label: str) -> int:
    """把「N 个月」/「N 年」解析成日数；格式非法返回 -1。"""
    text = str(label).strip()
    for suffix, unit in (("个月", DAYS_PER_MONTH), ("年", DAYS_PER_YEAR)):
        if text.endswith(suffix):
            head = text[: -len(suffix)].strip()
            if head.isdigit():
                return int(head) * unit
    return -1


def _check_realms(content: Any, problems: List[str]) -> None:
    orders = [int(r["order"]) for r in content.realms]
    if orders != list(range(1, len(content.realms) + 1)):
        problems.append("realms.order must be 1..12 contiguous")
    prev_end = 0
    prev_k = 0
    prev_life = 0
    prev_tier = 0
    for realm in content.realms:
        start, end = int(realm["level_start"]), int(realm["level_end"])
        if start != prev_end + 1:
            problems.append(f"realm {realm['id']}: level_start {start} != prev_end+1 ({prev_end + 1})")
        if end < start:
            problems.append(f"realm {realm['id']}: level_end < level_start")
        prev_end = end
        if int(realm["exp_k_bp"]) <= 0:
            problems.append(f"realm {realm['id']}: exp_k_bp must be > 0")
        if int(realm["base_lifespan_years"]) <= 0:
            problems.append(f"realm {realm['id']}: base_lifespan_years must be > 0")

        # --- 时间档位：必须是权威序列，且严格递增、整月 ---
        tier = int(realm["time_tier_days"])
        if tier <= 0:
            problems.append(f"realm {realm['id']}: time_tier_days must be > 0")
        elif tier % DAYS_PER_MONTH != 0:
            problems.append(f"realm {realm['id']}: time_tier_days {tier} not a whole month")
        if tier <= prev_tier:
            problems.append(f"realm {realm['id']}: time_tier_days {tier} not increasing (prev {prev_tier})")
        prev_tier = tier
        label = str(realm.get("time_tier_label", ""))
        if _tier_label_days(label) != tier:
            problems.append(f"realm {realm['id']}: time_tier_label '{label}' "
                            f"inconsistent with time_tier_days {tier}")

        # --- 修为曲线：K 单调不减（高境不得比低境更容易 = 禁止倒挂） ---
        k = int(realm["exp_k_bp"])
        if k < prev_k:
            problems.append(f"realm {realm['id']}: exp_k_bp {k} < prev {prev_k} (倒挂)")
        prev_k = k

        # --- 寿元：严格递增（突破是整体重算，不允许回退） ---
        life = int(realm["base_lifespan_years"])
        if life <= prev_life:
            problems.append(f"realm {realm['id']}: base_lifespan_years {life} "
                            f"not increasing (prev {prev_life})")
        prev_life = life

        for need in realm.get("breakthrough_items", ()):
            if str(need["item_id"]) not in content.item_by_id:
                problems.append(f"realm {realm['id']}: unknown item '{need['item_id']}'")
    if prev_end != 100:
        problems.append(f"realms must cover level 1..100, ended at {prev_end}")
    tiers = tuple(int(r["time_tier_days"]) for r in content.realms)
    if tiers != REALM_TIME_TIER_DAYS:
        problems.append(f"realms.time_tier_days must be exactly {REALM_TIME_TIER_DAYS}, got {tiers}")
    enabled = [r["id"] for r in content.realms if r.get("enabled")]
    if enabled != ["forging_blood", "open_palace"]:
        problems.append(f"enabled realms must be exactly ['forging_blood','open_palace'], got {enabled}")


def _check_physiques(content: Any, problems: List[str]) -> None:
    total = sum(int(p["weight_bp"]) for p in content.physiques)
    if total != 10000:
        problems.append(f"physiques weight_bp sum must be 10000, got {total}")
    for p in content.physiques:
        for key, value in p["affinity"].items():
            if float(value) not in AFFINITY_VALUES:
                problems.append(f"physique {p['id']}: affinity.{key}={value} not in {AFFINITY_VALUES}")
        if int(p["cultivation_bp"]) <= 0:
            problems.append(f"physique {p['id']}: cultivation_bp must be > 0")


def _check_talents(content: Any, problems: List[str]) -> None:
    grades = sorted(int(t["grade"]) for t in content.talents)
    if grades != list(range(1, 11)):
        problems.append(f"talents.grade must be 1..10, got {grades}")
    for t in content.talents:
        expect = 10000 + (int(t["grade"]) - 5) * 600
        if int(t["coef_bp"]) != expect:
            problems.append(f"talent grade {t['grade']}: coef_bp {t['coef_bp']} != {expect}")
    roll = sum(int(t.get("roll_weight_bp", 0)) for t in content.talents)
    if roll != 10000:
        problems.append(f"talents roll_weight_bp sum must be 10000, got {roll}")


def _check_personalities(content: Any, problems: List[str]) -> None:
    ids = [p["id"] for p in content.personalities]
    if "zhongyong" not in ids:
        problems.append("personalities must contain 'zhongyong' (DB default)")
    for p in content.personalities:
        for key, value in p["check_mod_bp"].items():
            if int(value) not in PERSONALITY_MODS:
                problems.append(f"personality {p['id']}: check_mod_bp.{key}={value} not in {PERSONALITY_MODS}")


def _check_techniques(content: Any, problems: List[str]) -> None:
    for tech in content.techniques:
        tid = tech["id"]
        if tech["tier"] not in TIERS:
            problems.append(f"technique {tid}: tier '{tech['tier']}' invalid")
        if tech["sub_tier"] not in SUB_TIERS:
            problems.append(f"technique {tid}: sub_tier '{tech['sub_tier']}' invalid")
        if tech["type"] not in TECH_TYPES:
            problems.append(f"technique {tid}: type '{tech['type']}' invalid")
        for src in tech["source"]:
            if src not in TECH_SOURCES:
                problems.append(f"technique {tid}: source '{src}' invalid")
        if int(tech["required_realm_index"]) not in content.realm_by_index:
            problems.append(f"technique {tid}: required_realm_index out of range")
        if int(tech["max_layer"]) != 9:
            problems.append(f"technique {tid}: max_layer must be 9")
        if "sect" in tech["source"]:
            if not tech.get("sect_id"):
                problems.append(f"technique {tid}: sect source without sect_id")
            if tech.get("contribution_price") is None:
                problems.append(f"technique {tid}: sect source without contribution_price")
            if tech.get("sect_id") and str(tech["sect_id"]) not in content.sect_by_id:
                problems.append(f"technique {tid}: unknown sect '{tech['sect_id']}'")
        if int(tech["cultivation_multiplier_bp"]) <= 0:
            problems.append(f"technique {tid}: cultivation_multiplier_bp must be > 0")


def _check_items(content: Any, problems: List[str]) -> None:
    buckets: Dict[str, int] = {}
    for item in content.items:
        kind = item["kind"]
        if kind not in ITEM_KINDS:
            problems.append(f"item {item['id']}: kind '{kind}' invalid")
        buckets[kind] = buckets.get(kind, 0) + 1
        if int(item["price"]) < 0:
            problems.append(f"item {item['id']}: price must be >= 0")
        if int(item["stack"]) <= 0:
            problems.append(f"item {item['id']}: stack must be > 0")
        if not item.get("effect"):
            problems.append(f"item {item['id']}: effect required")
    expected = {"pill": 6, "material": 4, "consumable": 5}
    for kind, count in expected.items():
        if buckets.get(kind, 0) != count:
            problems.append(f"items.{kind}: expected {count}, got {buckets.get(kind, 0)}")


def _check_equipments(content: Any, problems: List[str]) -> None:
    eq = content.equipments
    slots = {s["id"] for s in eq["slots"]}
    if slots != SLOT_IDS:
        problems.append(f"equipment slots mismatch: {sorted(slots)}")
    rarity_total = sum(int(r["weight_bp"]) for r in eq["rarity"])
    if rarity_total != 10000:
        problems.append(f"rarity weight_bp sum must be 10000, got {rarity_total}")
    affix_total = sum(int(a["weight_bp"]) for a in eq["affix_pool"])
    if affix_total != 10000:
        problems.append(f"affix_pool weight_bp sum must be 10000, got {affix_total}")
    enhance = eq["enhance"]
    if int(enhance["max_level"]) != 5:
        problems.append("enhance.max_level must be 5 (MVP)")
    if int(enhance["bonus_per_level_bp"]) != 800:
        problems.append("enhance.bonus_per_level_bp must be 800 (8%)")
    for tpl in eq["templates"]:
        if tpl["slot"] not in SLOT_IDS:
            problems.append(f"equipment {tpl['id']}: slot '{tpl['slot']}' invalid")
        if tpl.get("sect_id") and str(tpl["sect_id"]) not in content.sect_by_id:
            problems.append(f"equipment {tpl['id']}: unknown sect '{tpl['sect_id']}'")


def _check_enemies(content: Any, problems: List[str]) -> None:
    if len(content.enemies) < 12:
        problems.append(f"enemies must be >= 12, got {len(content.enemies)}")
    for enemy in content.enemies:
        if enemy["kind"] not in ENEMY_KINDS:
            problems.append(f"enemy {enemy['id']}: kind '{enemy['kind']}' invalid")
        if int(enemy["hp"]) <= 0 or int(enemy["atk"]) <= 0:
            problems.append(f"enemy {enemy['id']}: hp/atk must be > 0")
        if int(enemy["realm_index"]) not in content.realm_by_index:
            problems.append(f"enemy {enemy['id']}: realm_index out of range")
        for drop in enemy.get("drops", ()):
            if str(drop["item_id"]) not in content.item_by_id:
                problems.append(f"enemy {enemy['id']}: unknown drop item '{drop['item_id']}'")


def _check_regions(content: Any, problems: List[str]) -> None:
    for region in content.regions:
        if float(region["env_coef"]) not in ENV_COEFS:
            problems.append(f"region {region['id']}: env_coef {region['env_coef']} invalid")
        if int(region["level_min"]) > int(region["level_max"]):
            problems.append(f"region {region['id']}: level_min > level_max")
        for enemy_id in region.get("enemies", ()):
            if str(enemy_id) not in content.enemy_by_id:
                problems.append(f"region {region['id']}: unknown enemy '{enemy_id}'")
        boss = region.get("boss")
        if boss is not None and str(boss) not in content.enemy_by_id:
            problems.append(f"region {region['id']}: unknown boss '{boss}'")
        for item_id in region.get("materials", ()):
            if str(item_id) not in content.item_by_id:
                problems.append(f"region {region['id']}: unknown material '{item_id}'")

    enabled = [r["id"] for r in content.enabled_regions()]
    expected = ["duanhuai_village", "back_hill", "herb_slope",
                "qingyun_outer", "spirit_creek", "palace_secret"]
    if enabled != expected:
        problems.append(f"enabled regions must be {expected}, got {enabled}")
    covered: Set[int] = set()
    for region in content.enabled_regions():
        covered.update(range(int(region["level_min"]), int(region["level_max"]) + 1))
    gaps = [lv for lv in range(1, 19) if lv not in covered]
    if gaps:
        problems.append(f"enabled regions must cover level 1..18, gaps={gaps}")


def _negative_cost(choice: Mapping[str, Any]) -> bool:
    """判断某选项是否含至少一项负面代价。"""
    if int(choice.get("time_cost_days", 0)) > 0:
        return True
    effects = list(choice.get("success_effects") or ()) + list(choice.get("fail_effects") or ())
    for eff in effects:
        etype = eff.get("type")
        value = int(eff.get("value", 0))
        if etype in ("karma", "hp_pct", "mp_pct", "dao_heart") and value < 0:
            return True
        if etype == "attr" and value < 0:
            return True
        if etype == "item_loss":
            return True
    return False


def _check_events(content: Any, problems: List[str]) -> None:
    for event in content.events:
        eid = event["id"]
        if event["category"] not in EVENT_CATEGORIES:
            problems.append(f"event {eid}: category '{event['category']}' invalid")
        choices = event["choices"]
        if not (2 <= len(choices) <= 4):
            problems.append(f"event {eid}: choices must be 2..4, got {len(choices)}")
        if len(event.get("body", "")) > 200:
            problems.append(f"event {eid}: body exceeds 200 chars")
        for region_id in event.get("region_ids", ()):
            if str(region_id) not in content.region_by_id:
                problems.append(f"event {eid}: unknown region '{region_id}'")
        for choice in choices:
            if not _negative_cost(choice):
                problems.append(f"event {eid}/{choice['id']}: no negative cost")
            effects = list(choice.get("success_effects") or ()) + list(choice.get("fail_effects") or ())
            for eff in effects:
                etype = eff.get("type")
                if etype in ("item", "item_loss") and str(eff.get("item_id")) not in content.item_by_id:
                    problems.append(f"event {eid}: unknown item '{eff.get('item_id')}'")
                if etype == "technique" and str(eff.get("tech_id")) not in content.technique_by_id:
                    problems.append(f"event {eid}: unknown technique '{eff.get('tech_id')}'")


def _check_quests(content: Any, problems: List[str]) -> None:
    orders = [int(q["order"]) for q in content.quests]
    if orders != list(range(1, len(content.quests) + 1)):
        problems.append("quests.order must be 1..19 contiguous")
    qids = {q["id"] for q in content.quests}
    used_types: Set[str] = set()
    covered: Set[int] = set()
    for quest in content.quests:
        qid = quest["id"]
        if quest["region_id"] not in content.region_by_id:
            problems.append(f"quest {qid}: unknown region '{quest['region_id']}'")
        for prereq in quest.get("prereq", ()):
            if str(prereq) not in qids:
                problems.append(f"quest {qid}: unknown prereq '{prereq}'")
        for nxt in quest.get("unlock_next", ()):
            if str(nxt) not in qids:
                problems.append(f"quest {qid}: unknown unlock_next '{nxt}'")
        covered.update(range(int(quest["level_min"]), int(quest["level_max"]) + 1))
        if quest.get("objective_rule") not in ("all", "any_group"):
            problems.append(f"quest {qid}: objective_rule invalid")
        for obj in quest["objectives"]:
            otype = obj["type"]
            used_types.add(otype)
            if otype not in OBJ_TYPES:
                problems.append(f"quest {qid}: unknown objective type '{otype}'")
            if otype == "kill" and str(obj["enemy_id"]) not in content.enemy_by_id:
                problems.append(f"quest {qid}: unknown enemy '{obj['enemy_id']}'")
            if otype in ("collect", "deliver") and str(obj["item_id"]) not in content.item_by_id:
                problems.append(f"quest {qid}: unknown item '{obj['item_id']}'")
            if otype == "event_choice" and str(obj["event_id"]) not in content.event_by_id:
                problems.append(f"quest {qid}: unknown event '{obj['event_id']}'")
            if otype == "enter_forbidden" and str(obj["land_id"]) not in content.land_by_id:
                problems.append(f"quest {qid}: unknown land '{obj['land_id']}'")
        rewards = quest["rewards"]
        for entry in rewards.get("items", ()):
            if str(entry["item_id"]) not in content.item_by_id:
                problems.append(f"quest {qid}: unknown reward item '{entry['item_id']}'")
        for tech_id in rewards.get("techniques", ()):
            if str(tech_id) not in content.technique_by_id:
                problems.append(f"quest {qid}: unknown reward technique '{tech_id}'")
        for template_id in rewards.get("equipments", ()):
            if str(template_id) not in content.equip_template_by_id:
                problems.append(f"quest {qid}: unknown reward equipment '{template_id}'")

    missing_types = REQUIRED_OBJ_TYPES - used_types
    if missing_types:
        problems.append(f"quest objective types missing: {sorted(missing_types)}")
    gaps = [lv for lv in range(1, 19) if lv not in covered]
    if gaps:
        problems.append(f"quest level ranges must cover 1..18 (union), gaps={gaps}")


def _check_sects(content: Any, problems: List[str]) -> None:
    if len(content.sects) != 6:
        problems.append(f"sects must be 6, got {len(content.sects)}")
    total = 0
    for sect in content.sects:
        total += len(sect["techniques"])
        if sect["direction"] not in content.direction_by_id:
            problems.append(f"sect {sect['id']}: unknown direction '{sect['direction']}'")
        for entry in sect["techniques"]:
            tech_id = str(entry["tech_id"])
            tech = content.technique_by_id.get(tech_id)
            if tech is None:
                problems.append(f"sect {sect['id']}: unknown technique '{tech_id}'")
                continue
            if str(tech.get("sect_id")) != sect["id"]:
                problems.append(f"sect {sect['id']}/{tech_id}: technique.sect_id mismatch")
            if tech.get("contribution_price") != entry["contribution_price"]:
                problems.append(
                    f"sect {sect['id']}/{tech_id}: price {entry['contribution_price']} "
                    f"!= technique {tech.get('contribution_price')}")
    if total != 24:
        problems.append(f"sect techniques must be 24, got {total}")


def _check_directions(content: Any, problems: List[str]) -> None:
    expected = {"body", "sword", "spell", "alchemy", "array"}
    if {d["id"] for d in content.directions} != expected:
        problems.append("directions must be body/sword/spell/alchemy/array")
    for direction in content.directions:
        layers = direction["layers"]
        if len(layers) != 3:
            problems.append(f"direction {direction['id']}: must have 3 layers")
        prev = -1
        for layer in layers:
            if len(layer["nodes"]) != 3:
                problems.append(f"direction {direction['id']} layer {layer['layer']}: must have 3 nodes")
            for node in layer["nodes"]:
                value = int(node["required_total_contribution"])
                if value < prev:
                    problems.append(
                        f"direction {direction['id']}: node {node['id']} contribution "
                        f"{value} < previous {prev}")
                prev = value


def _check_sansxiu(content: Any, problems: List[str]) -> None:
    if len(content.encounters) != 3:
        problems.append(f"sansxiu encounters must be 3, got {len(content.encounters)}")
    total = sum(int(e["weight"]) for e in content.encounters)
    if total != 100:
        problems.append(f"sansxiu encounter weights must sum to 100, got {total}")
    for encounter in content.encounters:
        tech_id = str(encounter["technique_id"])
        if tech_id not in content.technique_by_id:
            problems.append(f"sansxiu encounter {encounter['id']}: unknown technique '{tech_id}'")
        check = encounter.get("check")
        if check is not None and not (0 < int(check["difficulty_bp"]) <= 10000):
            problems.append(f"sansxiu encounter {encounter['id']}: difficulty_bp out of range")
    path = content.sansxiu["path"]
    if int(path["direction_coef_bp"]) != 10000:
        problems.append("sansxiu path.direction_coef_bp must be 10000 (no direction bonus)")
    if int(content.sansxiu["convert"]["rate_bp"]) != 5000:
        problems.append("sansxiu convert.rate_bp must be 5000 (50%)")


def _check_lands(content: Any, problems: List[str]) -> None:
    enabled = [x["id"] for x in content.lands if x.get("enabled")]
    expected = ["gujing_jianyuan", "caomu_yuanyuan"]
    if enabled != expected:
        problems.append(f"enabled lands must be {expected}, got {enabled}")
    # 按阶固定的进入成本（§6.7：凡30/灵60/宝120/圣240/帝360 日；
    # 贡献 50/200/800/5000/20000），不按文件顺序断言（第 10 处时间秘境为圣阶但排在末尾）
    entry_cost = {
        "fan": (30, 50), "ling": (60, 200), "bao": (120, 800),
        "sheng": (240, 5000), "di": (360, 20000),
    }
    for land in content.lands:
        tier = land["tier"]
        if tier not in entry_cost:
            problems.append(f"land {land['id']}: tier '{tier}' invalid")
            continue
        want_days, want_contribution = entry_cost[tier]
        if int(land["entry_days"]) != want_days:
            problems.append(
                f"land {land['id']}: entry_days {land['entry_days']} != {want_days} (tier {tier})")
        if int(land["entry_contribution"]) != want_contribution:
            problems.append(
                f"land {land['id']}: entry_contribution {land['entry_contribution']} "
                f"!= {want_contribution} (tier {tier})")
        boss = land.get("boss_id")
        if boss is not None and str(boss) not in content.enemy_by_id:
            problems.append(f"land {land['id']}: unknown boss '{boss}'")
        if int(land["required_realm_index"]) not in content.realm_by_index:
            problems.append(f"land {land['id']}: required_realm_index out of range")
        for reward in land.get("first_clear_rewards", ()):
            if reward["type"] == "technique" and str(reward["tech_id"]) not in content.technique_by_id:
                problems.append(f"land {land['id']}: unknown technique '{reward['tech_id']}'")
            if reward["type"] == "item" and str(reward.get("item_id")) not in content.item_by_id:
                problems.append(f"land {land['id']}: unknown item '{reward.get('item_id')}'")


def _check_game(content: Any, problems: List[str]) -> None:
    game = content.game
    time_cfg = game.get("time", {})
    if int(time_cfg.get("days_per_year", 0)) != 360:
        problems.append("game.time.days_per_year must be 360")
    if int(time_cfg.get("days_per_month", 0)) != 30:
        problems.append("game.time.days_per_month must be 30")
    base_days = game.get("action_base_days", {})
    for key, value in (("seclusion", 30), ("adventure", 15), ("gather", 10),
                       ("spar", 3), ("meditate", 10), ("breakthrough", 3)):
        if int(base_days.get(key, -1)) != value:
            problems.append(f"game.action_base_days.{key} must be {value}")
    if int(game.get("exp", {}).get("exp_rate_global_bp", 0)) != 15000:
        problems.append("game.exp.exp_rate_global_bp must be 15000")
    # 档位缩放基准必须等于锻血档位，否则「档位 → 实际日数」会整体漂移
    tier_base = int(game.get("action_tier_base_days", 0))
    if tier_base != REALM_TIME_TIER_DAYS[0]:
        problems.append(f"game.action_tier_base_days must be {REALM_TIME_TIER_DAYS[0]}, "
                        f"got {tier_base}")
    if "realm_time_factor" in game:
        problems.append("game.realm_time_factor is deprecated; "
                        "use realms.time_tier_days + action_tier_base_days")
    event_cfg = game.get("event", {})
    if int(event_cfg.get("trigger_bp", 0)) != 3500:
        problems.append("game.event.trigger_bp must be 3500")
    if "sansxiu_bonus_bp" in event_cfg:
        problems.append("game.event.sansxiu_bonus_bp is deprecated; "
                        "use sansxiu_event_multiplier_bp (relative)")
    if int(event_cfg.get("sansxiu_event_multiplier_bp", 0)) != 11000:
        problems.append("game.event.sansxiu_event_multiplier_bp must be 11000")
    weights = event_cfg.get("category_weights_bp", {})
    if sum(int(v) for v in weights.values()) != 10000:
        problems.append("game.event.category_weights_bp must sum to 10000")
    if int(game.get("breakthrough", {}).get("prepare_cap_bp", 0)) != 3000:
        problems.append("game.breakthrough.prepare_cap_bp must be 3000")


_CHECKERS = (
    _check_game,
    _check_counts,
    _check_realms,
    _check_physiques,
    _check_talents,
    _check_personalities,
    _check_techniques,
    _check_items,
    _check_equipments,
    _check_enemies,
    _check_regions,
    _check_events,
    _check_quests,
    _check_sects,
    _check_directions,
    _check_sansxiu,
    _check_lands,
)

_UNIQUE_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("realms", "id"), ("physiques", "id"), ("personalities", "id"),
    ("techniques", "id"), ("items", "id"), ("enemies", "id"),
    ("regions", "id"), ("events", "id"), ("quests", "id"),
    ("sects", "id"), ("lands", "id"), ("directions", "id"),
)


def validate(content: Any) -> None:
    """执行全部自检。

    Args:
        content: ``Content`` 冻结快照。

    Raises:
        ContentValidationError: 存在任一问题（消息含前 8 条）。
    """
    problems: List[str] = []
    for attr, key in _UNIQUE_SECTIONS:
        items = getattr(content, attr)
        _check_unique(items, key, attr, problems)
    _check_unique(content.encounters, "id", "sansxiu.encounters", problems)
    for checker in _CHECKERS:
        checker(content, problems)
    if problems:
        raise ContentValidationError(problems)


def check_all(content: Any) -> List[str]:
    """返回问题清单而不抛异常（供调试/测试断言具体条目）。"""
    problems: List[str] = []
    for attr, key in _UNIQUE_SECTIONS:
        _check_unique(getattr(content, attr), key, attr, problems)
    _check_unique(content.encounters, "id", "sansxiu.encounters", problems)
    for checker in _CHECKERS:
        checker(content, problems)
    return problems
