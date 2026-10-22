"""创角：重掷与建角。

规则（任务书 §8 红线 1 / 系统设计 §4.1）：
- 体质按 ``weight_bp`` 抽（凡 50% / 灵 30% / 剑骨 10% / 药体 8% / 玄阵心 2%）；
- 资质按 ``roll_weight_bp`` 抽（1–10 品）；悟性 10–30；
- 自由加点 **恰好 5 点**，可分配到 10 项属性，**单项 ≤ +3**，不得为负；
- 可重掷 **3 次**，第 4 次抛 4002；
- 建角落地：character + 玄布道袍（装备于护甲槽）+ 《锻血诀》第 1 层 +
  主线 quest_01 = active + 自动存档 slot 0 + meta.active_char_id。
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Mapping, Optional

from ..content import get_registry
from ..core.clock import DAYS_PER_YEAR
from ..core.errors import POINTS_INVALID, REROLL_EXHAUSTED, GameError
from ..core.rng import RngService
from ..db.game_repository import (
    CHARACTER_FIELDS,
    CharacterRepository,
    EquipmentRepository,
    FlagRepository,
    MetaRepository,
    QuestRepository,
    TechniqueRepository,
)
from ..domain import attrs as attrs_domain
from ..domain import level as level_domain
from .grant_service import GrantService

ROLL_META_KEY = "creation_roll"
ROLL_LIMIT = 3
STARTER_EQUIPMENT = "xuanbu_daopao"   # 粗布衣（玄布道袍，凡·普通，护甲槽）
STARTER_TECHNIQUE = "duanxue_jue"     # 《锻血诀》第 1 层
STARTER_QUEST = "quest_01"

POINT_KEYS = attrs_domain.BASE_KEYS  # 10 项
POINT_TOTAL = 5
POINT_SINGLE_MAX = 3


class BootstrapService:
    """创角服务（重掷 + 建角）。"""

    # ---------------- 重掷 ----------------

    @staticmethod
    def candidate() -> Dict[str, Any]:
        """读取当前候选；没有候选则掷一次（不消耗额外次数）。"""
        roll = BootstrapService.roll_state()
        if roll:
            registry = get_registry()
            attempt = int(roll.get("attempt", 1))
            try:
                physique = registry.physique(str(roll["physique"]))
                talent = registry.talent(int(roll["talent"]))
            except Exception:
                physique, talent = None, None
            return {
                "physique": str(roll["physique"]),
                "physique_name": str((physique or {}).get("name", "")),
                "physique_desc": str((physique or {}).get("desc", "")),
                "talent": int(roll["talent"]),
                "talent_name": str((talent or {}).get("name", "")),
                "wis": int(roll["wis"]),
                "attempt": attempt,
                "remaining": ROLL_LIMIT - attempt,
            }
        return BootstrapService.reroll()

    @staticmethod
    def reroll(*, rng_seed: Optional[str] = None) -> Dict[str, Any]:
        """掷一次体质/资质/悟性，返回候选。

        Args:
            rng_seed: 测试可注入固定种子；生产用 ``secrets`` 随机种子。

        Returns:
            ``{physique, physique_name, talent, talent_name, wis, attempt, remaining}``

        Raises:
            GameError: 4002 重掷次数用尽。
        """
        state = BootstrapService.roll_state()
        attempt = int(state.get("attempt", 0)) + 1
        if attempt > ROLL_LIMIT:
            raise GameError(REROLL_EXHAUSTED, "重掷次数已用尽",
                            {"limit": ROLL_LIMIT})

        registry = get_registry()
        rng = RngService(rng_seed or secrets.token_hex(32), attempt)
        physique = rng.weighted_pick(
            [(p, int(p["weight_bp"])) for p in registry.content.physiques], stream="creation:physique")
        talent = rng.weighted_pick(
            [(t, int(t["roll_weight_bp"])) for t in registry.content.talents], stream="creation:talent")
        wis = rng.randint(10, 30, stream="creation:wis")

        payload = {
            "attempt": attempt,
            "physique": str(physique["id"]),
            "talent": int(talent["grade"]),
            "wis": int(wis),
        }
        MetaRepository.set(ROLL_META_KEY, _dumps(payload))
        return {
            "physique": payload["physique"],
            "physique_name": physique["name"],
            "physique_desc": physique.get("desc", ""),
            "talent": payload["talent"],
            "talent_name": talent["name"],
            "wis": payload["wis"],
            "attempt": attempt,
            "remaining": ROLL_LIMIT - attempt,
        }

    @staticmethod
    def roll_state() -> Dict[str, Any]:
        """读取当前候选（未掷过返回空 dict）。"""
        raw = MetaRepository.get(ROLL_META_KEY)
        if not raw:
            return {}
        try:
            import json
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    # ---------------- 建角 ----------------

    @staticmethod
    def create(req: Mapping[str, Any]) -> Dict[str, Any]:
        """建角。

        Args:
            req: ``{name, gender, personality, points{str..karma}, }``；
                体质/资质/悟性取最近一次 reroll 结果（没有则自动掷一次）。

        Returns:
            ``{character_id, character, prologue}``

        Raises:
            GameError: 4001 加点非法 / 4002 重掷用尽。
        """
        points = BootstrapService._validate_points(req.get("points") or {})
        roll = BootstrapService.roll_state()
        if not roll:
            BootstrapService.reroll()
            roll = BootstrapService.roll_state()

        registry = get_registry()
        physique = registry.physique(str(roll["physique"]))
        profession_base = int(physique.get("init_attr_base", 10))
        wis_base = int(roll["wis"])

        base: Dict[str, int] = {}
        for key in attrs_domain.BASE_KEYS:
            if key == "wis":
                base[key] = wis_base
            elif key == "luk":
                base[key] = 5
            elif key == "mnd":
                base[key] = 10
            elif key == "karma":
                base[key] = 0
            else:
                base[key] = profession_base
            base[key] += points.get(key, 0)

        caps = registry.attrs_config().get("soft_cap", {})
        base = attrs_domain.apply_soft_caps(base, caps)

        realm = registry.realm_by_index(1)
        lifespan = int(realm["base_lifespan_years"]) * DAYS_PER_YEAR
        derived = attrs_domain.compute_derived(base, float(realm["power_coef"]))

        payload: Dict[str, Any] = {
            "name": str(req.get("name", "")).strip()[:12] or "无名",
            "gender": str(req.get("gender", "unknown")),
            "personality": str(req.get("personality", "zhongyong")),
            "physique": str(physique["id"]),
            "talent": int(roll["talent"]),
            "level": 1,
            "realm_index": 1,
            "realm_stage": 1,
            "cultivation": 0,
            "exp_overflow": 0,
            "hp": derived.hp_max,
            "mp": derived.mp_max,
            "age_days": 1,
            "lifespan_max_days": lifespan,
            "spirit_stone": 0,
            "karma": base["karma"],
            "dao_heart": base["mnd"],
            "region_id": "duanhuai_village",
            "path_id": "",
            "sect_id": None,
            "direction_id": "",
            "foundation": "",
            "status": "normal",
            "rng_seed": secrets.token_hex(32),
            "rng_counter": 0,
            "version": 1,
        }
        for key in ("str", "con", "spr", "sen", "bon", "wis", "agi", "luk"):
            payload[key] = base[key]

        char_id = CharacterRepository.create(payload)

        # 初始装备：粗布衣（玄布道袍）直接穿上
        template = registry.equip_template(STARTER_EQUIPMENT)
        instance_id = EquipmentRepository.create(
            char_id, STARTER_EQUIPMENT, slot=str(template["slot"]),
            rarity=str(template.get("rarity") or "common"), equipped=False)
        EquipmentRepository.equip(char_id, instance_id)

        # 初始功法：《锻血诀》第 1 层
        TechniqueRepository.upsert(char_id, STARTER_TECHNIQUE, layer=1)

        # 序章主线
        QuestRepository.upsert(char_id, STARTER_QUEST, status="active", step=0, progress={})
        FlagRepository.set(char_id, "intro_done", "0")

        # 自动存档（失败不应阻塞建角）
        try:
            from .save_service import SaveService

            SaveService.save_slot(0, char_id=char_id, label="auto:create")
        except GameError:  # pragma: no cover - 自动槽异常不阻断创角
            pass

        char = CharacterRepository.require(char_id)
        # 清掉本次创角的候选，避免下一世继承已用尽的重掷次数
        MetaRepository.delete(ROLL_META_KEY)
        return {
            "character_id": char_id,
            "character": char,
            "derived": derived.as_dict(),
            "starter_equipment_instance": instance_id,
            "prologue": BootstrapService.prologue(char),
        }

    # ---------------- 内部 ----------------

    @staticmethod
    def _validate_points(points: Mapping[str, Any]) -> Dict[str, int]:
        """校验加点：恰好 5 点、单项 ≤3、非负、键在 10 项内。

        前端把「道心」写作 ``dao_heart``，后端基础属性键为 ``mnd``，这里做别名归一。
        """
        aliases = {"dao_heart": "mnd"}
        normalized = {aliases.get(str(k), str(k)): v for k, v in points.items()}
        unknown = [k for k in normalized if k not in POINT_KEYS]
        if unknown:
            raise GameError(POINTS_INVALID, f"未知属性：{unknown}", {"unknown": unknown})
        clean: Dict[str, int] = {}
        for key in POINT_KEYS:
            raw = normalized.get(key, 0)
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise GameError(POINTS_INVALID, f"属性 {key} 取值非法：{raw!r}")
            if value < 0:
                raise GameError(POINTS_INVALID, f"属性 {key} 不可为负", {"key": key})
            if value > POINT_SINGLE_MAX:
                raise GameError(POINTS_INVALID, f"单项最多 +{POINT_SINGLE_MAX}（{key}={value}）",
                                {"key": key, "value": value})
            clean[key] = value
        total = sum(clean.values())
        if total != POINT_TOTAL:
            raise GameError(POINTS_INVALID,
                            f"自由加点必须正好 {POINT_TOTAL} 点，当前 {total}",
                            {"total": total, "expected": POINT_TOTAL})
        return clean

    @staticmethod
    def prologue(char: Mapping[str, Any]) -> str:
        """序章文案（半文半白，随体质变化一句）。"""
        registry = get_registry()
        try:
            physique_name = registry.physique(str(char["physique"]))["name"]
        except GameError:  # pragma: no cover - 内容缺失兜底
            physique_name = "凡体"
        name = char.get("name") or "无名"
        return (
            f"断槐村的老槐断了一半，另一半还活着。\n"
            f"你叫{name}，{physique_name}。\n"
            f"老瞎子坐在树下，眼窝是两个浅坑。他说：先把血烧起来，再谈别的。"
        )

    @staticmethod
    def summary(char: Mapping[str, Any]) -> Dict[str, Any]:
        """创角回包用的角色摘要（不含 rng_seed）。"""
        return {k: char[k] for k in char if k in CHARACTER_FIELDS and k not in
                ("rng_seed", "pending_event", "last_settle_at")}


def _dumps(value: Any) -> str:
    """JSON 序列化（中文不转义）。"""
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
