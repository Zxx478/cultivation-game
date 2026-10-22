"""宗门 / 方向 / 散修 / 禁地的游戏层桥接。

原则：**既有宗门子系统仍是权威**（``sect_member`` / ``sect_reputation`` /
``sansxiu_wallet`` / ``forbidden_land_*``），本服务只做三件事：
1. 把 ``age_days`` 翻译成既有权重接口需要的 ``today_day / game_year / realm_seq``；
2. 把既有 Service 的奖励真正发到角色侧（功法入库、装备/道具入包、灵石入账）；
3. 补齐游戏层才有的前置（Lv.10、散修限定、贡献转换、方向树展示）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ..content import get_registry
from ..core.clock import game_year
from ..core.errors import (
    ACTION_INVALID,
    ATTR_REQUIREMENT_NOT_MET,
    GAME_ERROR_CODES,
    PATH_NOT_ALLOWED,
    GameError,
)
from ..db.game_repository import (
    CharacterRepository,
    EquipmentRepository,
    FlagRepository,
    InventoryRepository,
    TechniqueRepository,
)
from ..db.repository import (
    ForbiddenLandProgressRepository,
    SansxiuWalletRepository,
    SectMemberRepository,
    atomic,
)
from ..services import ExchangeService, ForbiddenLandService, QuestService, SectServiceError
from . import main_quest_service
from .context import active_char, writable_char
from .grant_service import GrantService
from .save_service import SaveService
from .stats_service import build as build_stats


def _to_game_error(exc: Exception) -> GameError:
    """既有 ``SectServiceError`` → ``GameError``（保留原业务码）。"""
    if isinstance(exc, SectServiceError):
        return GameError(exc.code, exc.message, exc.detail or None)
    if isinstance(exc, GameError):
        return exc
    return GameError(5000, str(exc))


def _char() -> Dict[str, Any]:
    """当前角色（存活），**写操作用**：待决事件/战斗中一律拒绝。"""
    return writable_char()


def _read_char() -> Dict[str, Any]:
    """当前角色（存活），**只读用**：不拦截待决事件/战斗。"""
    return active_char(alive=True)


def _identity(char: Mapping[str, Any]) -> Dict[str, Any]:
    """身份聚合（含既有表权威值）。"""
    member = SectMemberRepository.get(int(char["id"]))
    wallet = SansxiuWalletRepository.get(int(char["id"]))
    return {
        "path_id": str(char.get("path_id") or ""),
        "sect_id": str(char.get("sect_id") or ""),
        "role": "sect" if member else ("sansxiu" if char.get("path_id") == "sansxiu" else "none"),
        "role_raw": "disciple" if member else ("sansxiu" if char.get("path_id") == "sansxiu" else "none"),
        "contribution": int(member["contribution"]) if member else 0,
        "member": dict(member) if member else None,
        "wallet": dict(wallet) if wallet else None,
    }


def _missing_join(sec: Mapping[str, Any], char: Mapping[str, Any]) -> List[str]:
    """返回未满足的入门条件文案（空列表 = 可入）。"""
    req = dict(sec.get("join_requirements") or {})
    missing: List[str] = []
    if int(char["level"]) < int(req.get("min_level", 10)):
        missing.append(f"境界需达 Lv.{int(req.get('min_level', 10))}")
    for key, need in dict(req.get("attrs") or {}).items():
        if int(char.get(key, 0)) < int(need):
            missing.append(f"{key} 需 ≥ {int(need)}（当前 {int(char.get(key, 0))}）")
    materials = list(req.get("materials_any") or ())
    if materials:
        qty = int(req.get("materials_qty", 1))
        total = sum(int(InventoryRepository.get(int(char["id"]), str(m))["qty"])
                    for m in materials if InventoryRepository.get(int(char["id"]), str(m)))
        if total < qty:
            missing.append(f"需持 {'/'.join(materials)} ≥ {qty} 株（当前 {total}）")
    trial = int(req.get("trial_win_streak", 0))
    if trial:
        try:
            wins = int(FlagRepository.get(int(char["id"]), "trial_win_streak", "0") or 0)
        except (TypeError, ValueError):
            wins = 0
        if wins < trial:
            missing.append(f"试剑台连胜需 {trial} 场（当前 {wins}）")
    karma_max = req.get("karma_max")
    if karma_max is not None and int(char.get("karma", 0)) > int(karma_max):
        missing.append(f"因果需 ≤ {int(karma_max)}（当前 {int(char.get('karma', 0))}）")
    return missing


def overview() -> Dict[str, Any]:
    """宗门面板：身份 + 6 宗门条件 + 方向树 + 兑换 + 散修。"""
    char = _read_char()
    registry = get_registry()
    identity = _identity(char)
    sects: List[Dict[str, Any]] = []
    for sec in registry.sects():
        sects.append({
            "id": str(sec["id"]),
            "name": str(sec.get("name", "")),
            "desc": str(sec.get("desc", "")),
            "direction": str(sec.get("direction", "")),
            "join_requirements": dict(sec.get("join_requirements") or {}),
            "missing": _missing_join(sec, char),
            "joined": identity["sect_id"] == str(sec["id"]),
        })
    directions = []
    member = identity.get("member") or {}
    try:
        from ..db.repository import get_conn
        row = get_conn().execute(
            "SELECT COALESCE(SUM(contribution_gained), 0) AS total "
            "FROM sect_quest_record WHERE character_id = ?", (int(char["id"]),)).fetchone()
        total_contribution = int(row["total"] or 0) if row else int(member.get("contribution", 0))
    except Exception:
        total_contribution = int(member.get("contribution", 0))
    sect_direction = None
    if identity["sect_id"]:
        try:
            sect_direction = str(registry.sect(identity["sect_id"]).get("direction") or "")
        except Exception:
            sect_direction = None
        if identity["sect_id"] == "youming_demon":
            sect_direction = None  # 魔道可修任意方向
    for direction in registry.content.directions:
        if sect_direction and str(direction["id"]) != sect_direction:
            continue
        layers = []
        for layer in direction.get("layers", ()):
            nodes = []
            for node in layer.get("nodes", ()):
                need = int(node.get("required_total_contribution", 0))
                nodes.append({
                    "id": str(node.get("id")),
                    "name": str(node.get("name", "")),
                    "layer": int(node.get("layer", layer.get("layer", 1))),
                    "required_total_contribution": need,
                    "unlocked": total_contribution >= need and identity["sect_id"] != "",
                    "effects": list(node.get("effects") or []),
                    "desc": str(node.get("desc", "")),
                })
            layers.append({"layer": int(layer.get("layer", 1)), "nodes": nodes})
        directions.append({"id": str(direction["id"]), "name": str(direction["name"]),
                           "layers": layers})
    exchange = []
    for sec in registry.sects():
        if identity["sect_id"] and identity["sect_id"] != str(sec["id"]):
            continue
        for offer in sec.get("techniques", ()):
            try:
                tech = registry.technique(str(offer["tech_id"]))
            except Exception:
                continue
            exchange.append({
                "item_type": "technique",
                "item_id": str(offer["tech_id"]),
                "name": str(tech.get("name", "")),
                "tier": str(tech.get("tier", "")),
                "required_realm_index": int(tech.get("required_realm_index", 1)),
                "price": int(offer.get("contribution_price", 0)),
                "sect_id": str(sec["id"]),
                "sect_name": str(sec.get("name", "")),
            })
    wallet = identity.get("wallet") or {}
    active_quest = None
    from ..db.repository import get_conn
    row = get_conn().execute(
        "SELECT * FROM sect_active_quest WHERE character_id = ? AND status = 'accepted' "
        "ORDER BY quest_id DESC LIMIT 1", (int(char["id"]),)).fetchone()
    if row is not None:
        import json as _json
        try:
            target_meta = _json.loads(row["target_meta"])
        except (TypeError, ValueError):
            target_meta = {}
        active_quest = {
            "quest_id": int(row["quest_id"]),
            "sect_id": str(row["sect_id"]),
            "level": str(row["level_id"]),
            "quest_kind": str(row["quest_kind"]),
            "title": f"{row['sect_id']} · {row['quest_kind']}",
            "target_meta": target_meta,
            "reward_contribution": int(row["reward_contribution"]),
            "reward_spirit_stone": int(row["reward_spirit_stone"]),
            "expire_at_days": int(row["expire_at_days"]),
            "status": str(row["status"]),
            "rewards": [
                {"label": f"贡献 +{int(row['reward_contribution'])}"},
                {"label": f"灵石 +{int(row['reward_spirit_stone'])}"},
            ],
            "objectives": [{"desc": str(k), "current": 0, "target": 1} for k in target_meta],
        }
    return {
        **identity,
        "sect_name": next((s["name"] for s in sects if s["id"] == identity["sect_id"]), ""),
        "direction_name": next((d["name"] for d in directions if d["id"] == char.get("direction_id")), ""),
        "character": {"level": int(char["level"]), "path_id": str(char.get("path_id") or ""),
                      "direction_id": str(char.get("direction_id") or "")},
        "sects": sects,
        "joinable": [s for s in sects if not s["missing"]],
        "directions": directions,
        "exchange": exchange,
        "sansxiu": {
            "general_contribution": int(wallet.get("general_contribution", 0)),
            "total_earned": int(wallet.get("total_earned", 0)),
            "converted": bool(FlagRepository.get(int(char["id"]), "sansxiu_converted", "")),
        },
        "sect_quest": active_quest,
        "sect_quest_remaining": None,
        "hint": "方向节点按累计贡献点亮；宗门任务每游戏年限次。" if identity["sect_id"] else "Lv.10 后可入宗，或选择散修。",
    }


@atomic
def join(sect_id: str) -> Dict[str, Any]:
    """加入宗门，或选择散修。

    Raises:
        GameError: 4022 条件未达 / 4024 身份不可回退。
    """
    char = _char()
    registry = get_registry()
    identity = _identity(char)
    if sect_id == "sansxiu":
        if identity["sect_id"]:
            raise GameError(PATH_NOT_ALLOWED, "已入宗门，不可再退回散修")
        if str(char.get("path_id")) == "sansxiu":
            return {"path_id": "sansxiu", "already": True}
        if int(char["level"]) < 10:
            raise GameError(ATTR_REQUIREMENT_NOT_MET, "辟府境 Lv.10 后方可择路")
        CharacterRepository.update(int(char["id"]),
                                   {"path_id": "sansxiu", "sect_id": None},
                                   expected_version=int(char["version"]))
        main_quest_service.advance(int(char["id"]), {"type": "join_sect", "sect_id": "sansxiu"})
        SaveService.autosave(int(char["id"]))
        return {"path_id": "sansxiu", "role": "sansxiu"}

    try:
        sec = registry.sect(str(sect_id))
    except Exception:
        raise GameError(1002, f"宗门不存在：{sect_id}")
    if identity["sect_id"] and identity["sect_id"] != str(sect_id):
        raise GameError(PATH_NOT_ALLOWED, "一届只能入一个宗门")
    missing = _missing_join(sec, char)
    if missing:
        raise GameError(ATTR_REQUIREMENT_NOT_MET, "入门条件未达", {"missing": missing})

    # 散修 → 宗门：通用贡献 50% 转换（一次性）
    converted = 0
    wallet = SansxiuWalletRepository.get(int(char["id"]))
    if str(char.get("path_id")) == "sansxiu" and not FlagRepository.get(int(char["id"]), "sansxiu_converted", ""):
        converted = int(int(wallet.get("general_contribution", 0)) * 0.5)
        wallet["general_contribution"] = 0
        SansxiuWalletRepository.upsert(wallet)
        FlagRepository.set(int(char["id"]), "sansxiu_converted", "1")

    member = SectMemberRepository.get(int(char["id"]))
    if member is None:
        member = {
            "character_id": int(char["id"]), "sect_id": str(sect_id),
            "path_id": str(sec.get("direction", "body")),
            "joined_at_days": int(char["age_days"]), "contribution": converted,
            "low_quests_done": 0, "mid_quests_done": 0, "high_quests_done": 0,
            "unlocked_mid": False, "unlocked_high": False, "is_defected": False,
            "version": 1,
        }
    else:
        member["sect_id"] = str(sect_id)
        member["contribution"] = int(member["contribution"]) + converted
    SectMemberRepository.upsert(member)
    CharacterRepository.update(int(char["id"]),
                               {"path_id": str(sect_id), "sect_id": str(sect_id)},
                               expected_version=int(char["version"]))
    main_quest_service.advance(int(char["id"]), {"type": "join_sect", "sect_id": str(sect_id)})
    SaveService.autosave(int(char["id"]))
    return {"path_id": str(sect_id), "sect_id": str(sect_id),
            "sect_name": str(sec.get("name", "")), "converted_contribution": converted}


@atomic
def choose_direction(direction_id: str) -> Dict[str, Any]:
    """定方向（首次免费；入宗后才能定）。"""
    char = _char()
    identity = _identity(char)
    if not identity["sect_id"]:
        raise GameError(PATH_NOT_ALLOWED, "尚未入宗，不能定方向")
    registry = get_registry()
    try:
        direction = registry.direction(str(direction_id))
    except Exception:
        raise GameError(4022, f"方向不存在：{direction_id}")
    sec = registry.sect(identity["sect_id"])
    if str(sec.get("direction")) != "body" and str(sec.get("direction")) != str(direction_id):
        # 魔道可用任意方向，其余宗门只能选本宗方向
        raise GameError(4022, "该方向与本宗不符")
    CharacterRepository.update(int(char["id"]), {"direction_id": str(direction_id)},
                               expected_version=int(char["version"]))
    main_quest_service.advance(int(char["id"]), {"type": "choose_direction",
                                                 "direction": str(direction_id)})
    SaveService.autosave(int(char["id"]))
    return {"direction_id": str(direction_id), "direction_name": str(direction.get("name", ""))}


@atomic
def take_sect_quest(level: str) -> Dict[str, Any]:
    """接宗门任务（复用既有 Service）。"""
    char = _char()
    identity = _identity(char)
    if identity["sect_id"]:
        sect_id = identity["sect_id"]
    else:
        raise GameError(PATH_NOT_ALLOWED, "散修需先与某宗门建立好感（当前无宗门）")
    try:
        return QuestService.take(int(char["id"]), sect_id, level,
                                 today_day=int(char["age_days"]),
                                 game_year=game_year(int(char["age_days"])))
    except Exception as exc:
        raise _to_game_error(exc)


@atomic
def complete_sect_quest(quest_id: int) -> Dict[str, Any]:
    """完成宗门任务，并把灵石同步到角色。"""
    char = _char()
    try:
        result = QuestService.complete(int(char["id"]), int(quest_id),
                                       today_day=int(char["age_days"]))
    except Exception as exc:
        raise _to_game_error(exc)
    stones = int(result.get("spirit_stone_gained", 0))
    if stones:
        CharacterRepository.update(int(char["id"]),
                                   {"spirit_stone": int(char["spirit_stone"]) + stones},
                                   expected_version=int(char["version"]))
    main_quest_service.advance(int(char["id"]), {"type": "complete_sect_quest", "count": 1})
    SaveService.autosave(int(char["id"]))
    return result


@atomic
def exchange(item_type: str, item_id: str) -> Dict[str, Any]:
    """兑换（校验全部前置 → 扣费 → 发放，整笔单事务）。"""
    char = _char()
    identity = _identity(char)
    registry = get_registry()
    item_type = str(item_type)
    item_id = str(item_id)

    # ---- 1. 预检：内容存在 + 境界 + 类型，全部在扣费之前 ----
    tech: Optional[Mapping[str, Any]] = None
    template: Optional[Mapping[str, Any]] = None
    item: Optional[Mapping[str, Any]] = None
    if item_type == "technique":
        try:
            tech = registry.technique(item_id)
        except Exception:
            raise GameError(ACTION_INVALID, f"功法不存在：{item_id}")
        if int(tech.get("required_realm_index", 1)) > int(char["realm_index"]):
            raise GameError(ATTR_REQUIREMENT_NOT_MET, "境界不足，暂不能修习该功法")
    elif item_type == "equipment":
        try:
            template = registry.equip_template(item_id)
        except Exception:
            raise GameError(ACTION_INVALID, f"装备不存在：{item_id}")
    elif item_type in ("pill", "material", "consumable"):
        try:
            item = registry.item(item_id)
        except Exception:
            raise GameError(ACTION_INVALID, f"物品不存在：{item_id}")
        if item_type in ("material", "consumable"):
            raise GameError(ACTION_INVALID, "该物品类型暂不支持宗门兑换")
    else:
        raise GameError(ACTION_INVALID, f"未知兑换类型：{item_type}")

    # ---- 2. 确定宗门（散修按功法反查；其余类型需显式宗门） ----
    sect_id = identity["sect_id"]
    if not sect_id and tech is not None:
        sect_id = str(tech.get("sect_id") or "")
    if not sect_id:
        raise GameError(PATH_NOT_ALLOWED, "无法确定兑换宗门")

    # ---- 3. 背包容量预检（丹药类入包前） ----
    if item is not None and InventoryRepository.get(int(char["id"]), item_id) is None:
        used = InventoryRepository.distinct_slots(int(char["id"]))
        if used >= int(char["bag_slots"]):
            raise GameError(4015, "行囊已满", {"used": used, "slots": int(char["bag_slots"])})

    # ---- 4. 扣费（加入当前外层事务，失败整体回滚） ----
    try:
        result = ExchangeService.check_and_exchange(
            int(char["id"]), sect_id, item_type, item_id, 0,
            int(char["age_days"]))
    except Exception as exc:
        raise _to_game_error(exc)

    # ---- 5. 发放 ----
    granted: Dict[str, Any] = {}
    if tech is not None:
        TechniqueRepository.upsert(int(char["id"]), item_id, layer=1)
        granted["technique"] = item_id
    elif template is not None:
        instance_id = EquipmentRepository.create(
            int(char["id"]), item_id, slot=str(template["slot"]),
            rarity=str(template.get("rarity") or "common"))
        granted["equipment_instance"] = instance_id
    elif item is not None:
        InventoryRepository.add(int(char["id"]), item_id, str(item["kind"]), 1)
        granted["item"] = item_id
    SaveService.autosave(int(char["id"]))
    return {**result, "granted": granted}


@atomic
def sansxiu_encounter() -> Dict[str, Any]:
    """散修奇遇（3 条池，按权重抽；每游戏年有次数上限）。"""
    char = _char()
    if str(char.get("path_id")) != "sansxiu":
        raise GameError(PATH_NOT_ALLOWED, "只有散修可触发奇遇")
    from ..config import load as load_sect_cfg
    cfg = load_sect_cfg()
    year = game_year(int(char["age_days"]))
    key = f"sansxiu_encounter_year:{year}"
    used = int(FlagRepository.get(int(char["id"]), key, "0") or 0)
    if used >= int(cfg.sansxiu_encounter_max_per_year):
        raise GameError(3003, "本游戏年奇遇次数已用尽")
    FlagRepository.set(int(char["id"]), key, str(used + 1))
    registry = get_registry()
    from ..core.rng import RngService
    rng = RngService(str(char["rng_seed"]), int(char["rng_counter"]))
    pool = [(e, int(e.get("weight", 1))) for e in registry.encounters()
            if int(dict(e.get("trigger") or {}).get("realm_min", 1)) <= int(char["realm_index"])]
    if not pool:
        pool = [(e, int(e.get("weight", 1))) for e in registry.encounters()]
    enc = rng.weighted_pick(pool, stream="sansxiu:encounter")
    tech_id = str(enc.get("technique_id") or "")
    if tech_id:
        TechniqueRepository.upsert(int(char["id"]), tech_id, layer=1)
    contribution = int(enc.get("contribution", 0) or 0)
    if contribution:
        wallet = SansxiuWalletRepository.get(int(char["id"]))
        wallet["general_contribution"] = int(wallet["general_contribution"]) + contribution
        wallet["total_earned"] = int(wallet["total_earned"]) + contribution
        SansxiuWalletRepository.upsert(wallet)
    CharacterRepository.update(int(char["id"]), {"rng_counter": int(rng.counter) + 1},
                               expected_version=int(char["version"]))
    main_quest_service.advance(int(char["id"]), {"type": "sansxiu_encounter", "count": 1})
    SaveService.autosave(int(char["id"]))
    return {"encounter_id": str(enc.get("id")), "title": str(enc.get("name", "")),
            "body": str(enc.get("desc", "")), "technique_id": tech_id,
            "contribution": contribution}


def convert_sansxiu() -> Dict[str, Any]:
    """散修贡献转换入口（实际转换在入宗时完成）。"""
    char = _char()
    if str(char.get("path_id")) != "sansxiu":
        return {"converted": False, "message": "当前不是散修"}
    wallet = SansxiuWalletRepository.get(int(char["id"]))
    return {"converted": False, "general_contribution": int(wallet.get("general_contribution", 0)),
            "message": "入宗时自动按 50% 转换；请先在宗门面板选择入宗"}


def forbidden_list() -> Dict[str, Any]:
    """禁地面板：10 处配置 + 已启用 2 处 + 进度。"""
    char = _read_char()
    registry = get_registry()
    from ..config import load as load_sect_cfg
    cfg = load_sect_cfg()
    lands = []
    for land in registry.lands():
        land_id = str(land["id"])
        enabled = land_id in cfg.forbidden_land_ids and bool(land.get("enabled"))
        progress = ForbiddenLandProgressRepository.get(int(char["id"]), land_id)
        progress = progress or {}
        last = progress.get("last_entry_at_days")
        cooldown_left = max(0, int(cfg.forbidden_cooldown_days) - (int(char["age_days"]) - int(last))) if last else 0
        lands.append({
            "id": land_id,
            "name": str(land.get("name", "")),
            "tier": str(land.get("tier", "")),
            "required_realm_index": int(land.get("required_realm_index", 1)),
            "level_min": int(land.get("level_min", 1)),
            "entry_days": int(land.get("entry_days", 30)),
            "entry_contribution": int(land.get("entry_contribution", 0)),
            "enabled": enabled,
            "locked_reason": (
                "尚未开放" if not enabled
                else "散修限定" if char.get("path_id") != "sansxiu"
                else f"需 Lv.{int(land.get('level_min', 1))}" if int(char["level"]) < int(land.get("level_min", 1))
                else ""
            ),
            "lock_reason": (
                "尚未开放" if not enabled
                else "散修限定" if char.get("path_id") != "sansxiu"
                else f"需 Lv.{int(land.get('level_min', 1))}" if int(char["level"]) < int(land.get("level_min", 1))
                else ""
            ),
            "year_entries_left": max(0, int(cfg.forbidden_entries_per_game_year) - int(progress.get("entry_count_this_year", 0))),
            "cooldown_left_days": cooldown_left,
            "desc": str(land.get("desc", "")),
            "first_cleared": bool(progress.get("is_first_cleared")),
            "entries_this_year": int(progress.get("entry_count_this_year", 0)),
            "cooldown_days_left": cooldown_left,
        })
    return {"lands": lands, "path_id": str(char.get("path_id") or ""),
            "entries_per_year": int(cfg.forbidden_entries_per_game_year),
            "sansxiu_only": True}


@atomic
def enter_forbidden(land_id: str) -> Dict[str, Any]:
    """进入禁地：散修限定 → 扣耗时 → 开 Boss 战。"""
    char = _char()
    if str(char.get("path_id")) != "sansxiu":
        raise GameError(PATH_NOT_ALLOWED, "只有散修可进入禁地")
    registry = get_registry()
    from ..config import load as load_sect_cfg
    cfg = load_sect_cfg()
    if str(land_id) not in cfg.forbidden_land_ids:
        raise GameError(3001, f"该禁地未开放：{land_id}")
    land = registry.land(str(land_id))
    if int(char["realm_index"]) < int(land.get("required_realm_index", 1)):
        raise GameError(3002, "境界未达，禁地入口不显")
    if int(char["level"]) < int(land.get("level_min", 1)):
        raise GameError(3002, "境界未达，禁地入口不显")
    progress = ForbiddenLandProgressRepository.get(int(char["id"]), str(land_id)) or {}
    last = progress.get("last_entry_at_days")
    if last and int(char["age_days"]) - int(last) < int(cfg.forbidden_cooldown_days):
        raise GameError(3004, "禁地冷却未过")
    year = game_year(int(char["age_days"]))
    if int(progress.get("entry_count_this_year", 0)) >= int(cfg.forbidden_entries_per_game_year):
        # 跨年重置由既有 Service 负责，这里只做提示
        raise GameError(3003, "本游戏年禁地次数已用尽")
    wallet = SansxiuWalletRepository.get(int(char["id"]))
    if int(wallet.get("general_contribution", 0)) < int(land.get("entry_contribution", 0)):
        raise GameError(3005, "散修贡献点不足")

    # 扣进入耗时（先扣时间，再战；失败只损失时间）
    CharacterRepository.update(int(char["id"]),
                               {"age_days": int(char["age_days"]) + int(land.get("entry_days", 30))},
                               expected_version=int(char["version"]))
    char = CharacterRepository.require(int(char["id"]))
    attempt = int(progress.get("entry_count_this_year", 0)) + 1
    from .action_service import _start_combat
    combat = _start_combat(int(char["id"]), char, enemy_id=str(land.get("boss_id")),
                           is_boss=True, kind="forbidden",
                           extra={"land_id": str(land_id), "attempt": attempt})
    return {"combat": combat, "land_id": str(land_id), "attempt": attempt}


@atomic
def complete_forbidden(char_id: int, land_id: str, attempt: int) -> Dict[str, Any]:
    """Boss 胜利后：调既有 Service 扣贡献/置首通/掉落，再发放到角色。"""
    char = CharacterRepository.require(char_id)
    try:
        result = ForbiddenLandService.enter(
            char_id, str(land_id), int(char["age_days"]),
            int(char["realm_index"]), game_year(int(char["age_days"])))
    except Exception as exc:
        raise _to_game_error(exc)
    granted: Dict[str, Any] = {}
    for reward in result.get("rewards", ()):
        rtype = str(reward.get("type"))
        if rtype in ("technique", "technique_recipe") and reward.get("tech_id"):
            TechniqueRepository.upsert(char_id, str(reward["tech_id"]), layer=1)
            granted.setdefault("techniques", []).append(str(reward["tech_id"]))
        elif rtype == "material" and reward.get("mat_id"):
            try:
                item = get_registry().item(str(reward["mat_id"]))
                InventoryRepository.add(char_id, str(reward["mat_id"]), str(item["kind"]),
                                        int(reward.get("amount", 1)))
                granted.setdefault("items", {})[str(reward["mat_id"])] = int(reward.get("amount", 1))
            except Exception:
                pass
        elif rtype == "spirit_stone":
            CharacterRepository.update(char_id,
                                       {"spirit_stone": int(char["spirit_stone"]) + int(reward.get("amount", 0))},
                                       expected_version=int(CharacterRepository.require(char_id)["version"]))
            granted["spirit_stone"] = int(reward.get("amount", 0))
        elif rtype == "contribution":
            wallet = SansxiuWalletRepository.get(char_id)
            wallet["general_contribution"] = int(wallet["general_contribution"]) + int(reward.get("amount", 0))
            wallet["total_earned"] = int(wallet["total_earned"]) + int(reward.get("amount", 0))
            SansxiuWalletRepository.upsert(wallet)
            granted["contribution"] = int(reward.get("amount", 0))
    main_quest_service.advance(char_id, {"type": "enter_forbidden", "land_id": str(land_id),
                                         "count": 1})
    SaveService.autosave(char_id)
    return {"land_id": str(land_id), "first_clear": bool(result.get("is_first_clear")),
            "rewards": result.get("rewards", []), "granted": granted}
