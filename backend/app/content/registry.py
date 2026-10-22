"""内容索引与查询门面。

services / domain 只通过 Registry 取内容，不直接读 YAML；
所有「查不到」都抛 ``GameError(CONTENT_INVALID)``——内容已在启动期自检，
运行期真出现缺失说明进程内容与存档不匹配，属不可恢复错误。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core.errors import CONTENT_INVALID, GameError
from .loader import Content, load_all


class Registry:
    """内容查询门面（无状态，可安全共享）。"""

    __slots__ = ("_content",)

    def __init__(self, content: Content) -> None:
        """绑定一份冻结内容快照。"""
        self._content = content

    # ---------------- 基础 ----------------

    @property
    def content(self) -> Content:
        """底层冻结快照。"""
        return self._content

    def _get(self, table: Mapping[str, Mapping[str, Any]], ident: str, kind: str) -> Mapping[str, Any]:
        try:
            return table[str(ident)]
        except KeyError as exc:
            raise GameError(CONTENT_INVALID, f"未知内容 id：{kind}={ident}",
                            {"kind": kind, "id": str(ident)}) from exc

    # ---------------- 实体 ----------------

    def realm(self, realm_id: str) -> Mapping[str, Any]:
        """按 id 取境界。"""
        return self._get(self._content.realm_by_id, realm_id, "realm")

    def realm_by_index(self, realm_index: int) -> Mapping[str, Any]:
        """按大境序号（1–12）取境界。"""
        try:
            return self._content.realm_by_index[int(realm_index)]
        except KeyError as exc:
            raise GameError(CONTENT_INVALID, f"未知境界序号 {realm_index}") from exc

    def realm_of_level(self, level: int) -> Mapping[str, Any]:
        """按全局等级取境界。"""
        return self._content.realm_of_level(int(level))

    def realm_index_of_level(self, level: int) -> int:
        """按全局等级取大境序号。"""
        return self._content.realm_index_of_level(int(level))

    def exp_k_bp(self, level: int) -> int:
        """按等级取修为曲线系数 K（bp）。"""
        return self._content.realm_exp_k_bp(int(level))

    def realm_time_tier_days(self, realm_index: int) -> int:
        """按大境序号（1–12）取时间档位日数（一次闭关的日数）。"""
        return self._content.realm_time_tier_days(int(realm_index))

    def realm_time_tier_label(self, realm_index: int) -> str:
        """按大境序号取时间档位文案（如「3 年」）。"""
        return self._content.realm_time_tier_label(int(realm_index))

    def action_tier_base_days(self) -> int:
        """档位缩放基准（= 锻血档位日数，``game.yaml.action_tier_base_days``）。"""
        return int(self._content.game["action_tier_base_days"])

    def technique(self, tech_id: str) -> Mapping[str, Any]:
        """取功法。"""
        return self._get(self._content.technique_by_id, tech_id, "technique")

    def item(self, item_id: str) -> Mapping[str, Any]:
        """取道具。"""
        return self._get(self._content.item_by_id, item_id, "item")

    def enemy(self, enemy_id: str) -> Mapping[str, Any]:
        """取敌人。"""
        return self._get(self._content.enemy_by_id, enemy_id, "enemy")

    def region(self, region_id: str) -> Mapping[str, Any]:
        """取区域。"""
        return self._get(self._content.region_by_id, region_id, "region")

    def event(self, event_id: str) -> Mapping[str, Any]:
        """取事件。"""
        return self._get(self._content.event_by_id, event_id, "event")

    def quest(self, quest_id: str) -> Mapping[str, Any]:
        """取主线任务。"""
        return self._get(self._content.quest_by_id, quest_id, "quest")

    def sect(self, sect_id: str) -> Mapping[str, Any]:
        """取宗门。"""
        return self._get(self._content.sect_by_id, sect_id, "sect")

    def land(self, land_id: str) -> Mapping[str, Any]:
        """取禁地。"""
        return self._get(self._content.land_by_id, land_id, "land")

    def physique(self, physique_id: str) -> Mapping[str, Any]:
        """取体质。"""
        return self._get(self._content.physique_by_id, physique_id, "physique")

    def talent(self, grade: int) -> Mapping[str, Any]:
        """按品级取资质（1–10）。"""
        try:
            return self._content.talent_by_grade[int(grade)]
        except KeyError as exc:
            raise GameError(CONTENT_INVALID, f"未知资质品级 {grade}") from exc

    def personality(self, personality_id: str) -> Mapping[str, Any]:
        """取性格。"""
        return self._get(self._content.personality_by_id, personality_id, "personality")

    def encounter(self, encounter_id: str) -> Mapping[str, Any]:
        """取散修奇遇。"""
        return self._get(self._content.encounter_by_id, encounter_id, "encounter")

    def equip_template(self, template_id: str) -> Mapping[str, Any]:
        """取装备模板。"""
        return self._get(self._content.equip_template_by_id, template_id, "equipment")

    def direction(self, direction_id: str) -> Mapping[str, Any]:
        """取方向树。"""
        return self._get(self._content.direction_by_id, direction_id, "direction")

    def direction_node(self, node_id: str) -> Mapping[str, Any]:
        """取方向树节点。"""
        return self._get(self._content.direction_node_by_id, node_id, "direction_node")

    # ---------------- 列表 ----------------

    def realms(self) -> Tuple[Mapping[str, Any], ...]:
        """12 境（按 order）。"""
        return self._content.realms

    def enabled_realms(self) -> Tuple[Mapping[str, Any], ...]:
        """MVP 开放境界。"""
        return self._content.enabled_realms()

    def regions(self, *, only_enabled: bool = False) -> Tuple[Mapping[str, Any], ...]:
        """区域列表。"""
        return self._content.enabled_regions() if only_enabled else self._content.regions

    def lands(self, *, only_enabled: bool = False) -> Tuple[Mapping[str, Any], ...]:
        """禁地列表。"""
        return self._content.enabled_lands() if only_enabled else self._content.lands

    def techniques(self, *, sect_id: Optional[str] = None) -> Tuple[Mapping[str, Any], ...]:
        """功法列表；给定 ``sect_id`` 时只返回该宗独有功法（按贡献价升序）。"""
        if sect_id is None:
            return self._content.techniques
        rows = [t for t in self._content.techniques if t.get("sect_id") == sect_id]
        return tuple(sorted(rows, key=lambda t: int(t.get("contribution_price") or 0)))

    def events(self) -> Tuple[Mapping[str, Any], ...]:
        """全部事件。"""
        return self._content.events

    def quests(self) -> Tuple[Mapping[str, Any], ...]:
        """19 条主线（按 order）。"""
        return self._content.quests

    def sects(self) -> Tuple[Mapping[str, Any], ...]:
        """6 宗门。"""
        return self._content.sects

    def encounters(self) -> Tuple[Mapping[str, Any], ...]:
        """散修奇遇池（3 条）。"""
        return self._content.encounters

    # ---------------- 组合查询 ----------------

    def events_for(self, region_id: str, level: int,
                   *, include_forced: bool = False) -> Tuple[Mapping[str, Any], ...]:
        """按区域 + 等级过滤事件池（不含主线强制事件）。"""
        out: List[Mapping[str, Any]] = []
        for event in self._content.events:
            if event.get("forced_by_quest") and not include_forced:
                continue
            if region_id not in event.get("region_ids", ()):
                continue
            if not (int(event["level_min"]) <= int(level) <= int(event["level_max"])):
                continue
            out.append(event)
        return tuple(out)

    def category_weight_bp(self, category: str) -> int:
        """类别权重（bp）；未配置返回 0。"""
        weights = self._content.game["event"]["category_weights_bp"]
        return int(weights.get(category, 0))

    def events_weight_pool(self, region_id: str, level: int) -> Tuple[Tuple[Mapping[str, Any], int], ...]:
        """事件 × 组合权重（类别权重 × 事件权重）的抽取池。"""
        pool: List[Tuple[Mapping[str, Any], int]] = []
        for event in self.events_for(region_id, level):
            weight = self.category_weight_bp(event["category"]) * int(event["weight"])
            if weight > 0:
                pool.append((event, weight))
        return tuple(pool)

    def regions_at_level(self, level: int, *, only_enabled: bool = True) -> Tuple[Mapping[str, Any], ...]:
        """某等级可去的区域（等级落在区间内且已开放）。"""
        source = self.regions(only_enabled=only_enabled)
        return tuple(r for r in source
                     if int(r["level_min"]) <= int(level) <= int(r["level_max"]))

    def sect_offers(self, sect_id: str) -> Tuple[Mapping[str, Any], ...]:
        """宗门兑换表：``[{tech_id, contribution_price, name, tier}]``。"""
        sect = self.sect(sect_id)
        out: List[Dict[str, Any]] = []
        for entry in sect["techniques"]:
            tech = self.technique(entry["tech_id"])
            out.append({
                "tech_id": entry["tech_id"],
                "contribution_price": int(entry["contribution_price"]),
                "name": tech["name"],
                "tier": tech["tier"],
                "sub_tier": tech["sub_tier"],
                "required_realm_index": int(tech["required_realm_index"]),
            })
        return tuple(out)

    def direction_nodes(self, direction_id: str) -> Tuple[Mapping[str, Any], ...]:
        """某方向的全部节点（按贡献门槛升序）。"""
        direction = self.direction(direction_id)
        nodes = [node for layer in direction["layers"] for node in layer["nodes"]]
        return tuple(sorted(nodes, key=lambda n: int(n["required_total_contribution"])))

    def quest_chain(self) -> Tuple[Mapping[str, Any], ...]:
        """按 order 返回全部主线。"""
        return self._content.quests

    def next_quest(self, quest_id: str) -> Tuple[Mapping[str, Any], ...]:
        """某任务的解锁后继（可能为空）。"""
        quest = self.quest(quest_id)
        return tuple(self.quest(qid) for qid in quest.get("unlock_next", ()))

    def first_quest(self) -> Mapping[str, Any]:
        """起始任务（order 最小且 prereq 为空）。"""
        for quest in self._content.quests:
            if not quest.get("prereq"):
                return quest
        raise GameError(CONTENT_INVALID, "主线缺少起始任务（prereq 为空）")

    # ---------------- 配置片段 ----------------

    def action_base_days(self, kind: str) -> int:
        """行动基准耗时。"""
        table = self._content.game["action_base_days"]
        if kind not in table:
            raise GameError(CONTENT_INVALID, f"未知行动 kind：{kind}")
        return int(table[kind])

    def action_base_days_table(self) -> Mapping[str, Any]:
        """全部行动基准耗时。"""
        return self._content.game["action_base_days"]

    def mood_coef_bp(self, dao_heart: int) -> int:
        """按道心取心境系数（bp）。"""
        for band in self._content.game["mood_coef_bp"]:
            if int(band["min"]) <= dao_heart <= int(band["max"]):
                return int(band["coef_bp"])
        return 10000

    def status_coef_bp(self, status: str) -> int:
        """按状态取状态系数（bp）。"""
        return int(self._content.game["status_coef_bp"].get(status, 10000))

    def combat_config(self) -> Mapping[str, Any]:
        """战斗全局系数。"""
        return self._content.game["combat"]

    def breakthrough_config(self) -> Mapping[str, Any]:
        """突破全局系数。"""
        return self._content.game["breakthrough"]

    def event_config(self) -> Mapping[str, Any]:
        """事件全局系数。"""
        return self._content.game["event"]

    def exp_config(self) -> Mapping[str, Any]:
        """修为全局系数。"""
        return self._content.game["exp"]

    def bag_config(self) -> Mapping[str, Any]:
        """背包配置。"""
        return self._content.game["bag"]

    def attrs_config(self) -> Mapping[str, Any]:
        """属性初始值与软上限。"""
        return self._content.game["attrs"]

    def path_config(self) -> Mapping[str, Any]:
        """身份/路径配置。"""
        return self._content.game["path"]

    def sansxiu_config(self) -> Mapping[str, Any]:
        """散修规则（path/convert）。"""
        return self._content.sansxiu

    def rarity_pool(self) -> Tuple[Tuple[Mapping[str, Any], int], ...]:
        """稀有度抽取池（key=稀有度条目, weight=bp）。"""
        return tuple((r, int(r["weight_bp"])) for r in self._content.equipments["rarity"])

    def affix_pool(self) -> Tuple[Mapping[str, Any], ...]:
        """副词条池（11 条）。"""
        return tuple(self._content.equipments["affix_pool"])

    def equip_slot(self, slot_id: str) -> Mapping[str, Any]:
        """槽位定义。"""
        for slot in self._content.equipments["slots"]:
            if slot["id"] == slot_id:
                return slot
        raise GameError(CONTENT_INVALID, f"未知装备槽位：{slot_id}")


_REGISTRY: Optional[Registry] = None


def get_registry(content: Optional[Content] = None) -> Registry:
    """取全局 Registry（首次调用惰性加载内容）。"""
    global _REGISTRY
    if content is not None:
        _REGISTRY = Registry(content)
        return _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = Registry(load_all())
    return _REGISTRY


def reset_registry() -> None:
    """清空 Registry 缓存（测试用）。"""
    global _REGISTRY
    _REGISTRY = None
