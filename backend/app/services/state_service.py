"""主界面 / 角色面板聚合。

只读为主：``GET /state`` 与 ``GET /character`` 不产生任何写副作用；
主线进度中的「绝对类目标」（等级/境界/持有物）在读取时即时同步，
真正的落库发生在 ``/action``、``/quests/claim`` 等写接口。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional

from ..content import get_registry
from ..core.clock import action_days, game_year
from ..core.errors import NO_ACTIVE_CHARACTER, GameError
from ..db.game_repository import (
    CharacterRepository,
    CombatSessionRepository,
    EquipmentRepository,
    FlagRepository,
    InventoryRepository,
    LogRepository,
    QuestRepository,
    TechniqueRepository,
)
from ..db.repository import SectMemberRepository, SansxiuWalletRepository
from ..domain import level as level_domain
from ..domain import quest as quest_domain
from . import stats_service
from .context import active_char


def _safe_json(value: Any, fallback: Any) -> Any:
    """JSON 解析（脏数据回退）。"""
    if value in (None, ""):
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _kills(char_id: int) -> Dict[str, int]:
    """从 flag 读取击杀计数（``kill:<enemy_id>``）。"""
    out: Dict[str, int] = {}
    for key, value in FlagRepository.all_for(char_id).items():
        if key.startswith("kill:"):
            try:
                out[key.split(":", 1)[1]] = int(value)
            except (TypeError, ValueError):
                continue
    return out


def _items(char_id: int) -> Dict[str, int]:
    """背包快照（item_id → qty）。"""
    return {str(row["item_id"]): int(row["qty"])
            for row in InventoryRepository.list_for(char_id)}


def _quest_rows(char_id: int, char: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """19 条主线的运行时视图（含 locked/active/ready/completed）。"""
    registry = get_registry()
    stored = {str(row["quest_id"]): row for row in QuestRepository.list_for(char_id)}
    completed = {qid for qid, row in stored.items()
                 if str(row.get("status")) == "completed"}
    snapshot = {
        "level": int(char["level"]),
        "realm_index": int(char["realm_index"]),
        "items": _items(char_id),
        "kills": _kills(char_id),
    }
    rows: List[Dict[str, Any]] = []
    for quest in registry.quest_chain():
        qid = str(quest["id"])
        row = stored.get(qid) or {}
        progress = row.get("progress") or {}
        progress, changed = quest_domain.sync(quest, progress, snapshot)
        # 对话类目标：MVP 无独立对话面板，进入任务面板即视为已对话
        for index, obj in enumerate(quest.get("objectives", ())):
            if str(obj.get("type")) == "talk":
                key = str(index)
                progress[key] = max(int(progress.get(key, 0)), int(obj.get("count", 1)))
        status = quest_domain.status_of(quest, completed=completed, progress=progress)
        if status == "locked" and quest_domain.prereq_met(quest, completed):
            status = "active"
        if status == "active" and quest_domain.is_ready(quest, progress):
            status = "ready"
        rows.append({
            "id": qid,
            "order": int(quest["order"]),
            "volume": int(quest.get("volume", 1)),
            "title": str(quest.get("title", "")),
            "desc": str(quest.get("desc", "")),
            "region_id": str(quest.get("region_id", "")),
            "level_min": int(quest.get("level_min", 1)),
            "level_max": int(quest.get("level_max", 18)),
            "status": status,
            "objectives": quest_domain.snapshot_of(quest, progress),
            "rewards": dict(quest.get("rewards") or {}),
            "progress_dirty": bool(changed),
            "step": int(row.get("step", 0) or 0),
        })
    return rows


def _pending_event(char: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """待决事件（不暴露判定成功率）。"""
    raw = _safe_json(char.get("pending_event"), None)
    if not raw:
        return None
    try:
        event = get_registry().event(str(raw["event_id"]))
    except Exception:
        return None
    options = []
    for choice in event.get("choices", ()):
        options.append({
            "id": str(choice.get("id")),
            "label": str(choice.get("label")),
            "risk": str(choice.get("risk", "normal")),
            "time_cost_days": int(choice.get("time_cost_days", 0)),
        })
    return {
        "instance_id": str(raw.get("instance_id", "")),
        "event_id": str(event["id"]),
        "title": str(event.get("title", "")),
        "body": str(event.get("body", "")),
        "category": str(event.get("category", "")),
        "options": options,
        "created_at_days": int(raw.get("created_at_days", 0)),
    }


def _combat_view(char_id: int) -> Optional[Dict[str, Any]]:
    """战斗会话（供刷新恢复）。"""
    row = CombatSessionRepository.get(char_id)
    if row is None:
        return None
    session = dict(row.get("session") or {})
    if not session:
        return None
    return {
        "status": str(row.get("status", "active")),
        "turn": int(row.get("turn", 1)),
        "max_turns": int(session.get("max_turns", 30)),
        "player": dict(session.get("player") or {}),
        "enemy": dict(session.get("enemy") or {}),
        "flags": dict(session.get("flags") or {}),
        "log": list(session.get("log") or [])[-20:],
        "is_boss": bool((session.get("flags") or {}).get("is_boss")),
    }


def _sect_view(char_id: int, char: Mapping[str, Any]) -> Dict[str, Any]:
    """宗门 / 散修身份视图（权威仍在既有表）。"""
    member = SectMemberRepository.get(char_id)
    wallet = SansxiuWalletRepository.get(char_id)
    return {
        "path_id": str(char.get("path_id") or ""),
        "sect_id": str(char.get("sect_id") or ""),
        "direction_id": str(char.get("direction_id") or ""),
        "role": "sect" if member else ("sansxiu" if char.get("path_id") == "sansxiu" else "none"),
        "role_raw": "disciple" if member else ("sansxiu" if char.get("path_id") == "sansxiu" else "none"),
        "contribution": int(member["contribution"]) if member else 0,
        "unlocked_mid": bool(member["unlocked_mid"]) if member else False,
        "unlocked_high": bool(member["unlocked_high"]) if member else False,
        "low_quests_done": int(member["low_quests_done"]) if member else 0,
        "mid_quests_done": int(member["mid_quests_done"]) if member else 0,
        "sansxiu_contribution": int(wallet.get("general_contribution", 0)),
        "sansxiu_total_earned": int(wallet.get("total_earned", 0)),
    }


def state() -> Dict[str, Any]:
    """主界面聚合快照。"""
    char = active_char()
    stats = stats_service.build(char)
    registry = get_registry()
    char_id = int(char["id"])
    level = int(char["level"])
    need = level_domain.exp_need(level, registry.exp_k_bp(level))
    realm = registry.realm_by_index(int(char["realm_index"]))
    region = registry.region(str(char.get("region_id") or "duanhuai_village"))
    left_days = int(char["lifespan_max_days"]) - int(char["age_days"])
    # 闭关实际耗时随境界增长（锻血 30 / 辟府 42…），可闭关次数必须用它，不能用固定 30
    seclusion_days = max(1, action_days(registry.action_base_days("seclusion"),
                                       int(char["realm_index"])))
    closed_door_times = max(0, left_days // seclusion_days)

    quests = _quest_rows(char_id, char)
    current_quest = next((q for q in quests if q["status"] in ("active", "ready")), None)
    can_break = (level >= int(realm["level_end"])
                 and int(char["cultivation"]) >= need
                 and left_days >= 3600
                 and int(realm["order"]) < 12
                 and bool(registry.realm_by_index(int(realm["order"]) + 1).get("enabled")))
    retreat_gain = int(stats.exp_breakdown.get("gain_per_days", 0) or 0)
    remaining_level = (0 if need <= 0 else max(0, -(-(int(need) - int(char["cultivation"])) // max(1, retreat_gain))))
    realm_total = sum(level_domain.exp_need(lv, registry.exp_k_bp(lv))
                      for lv in range(level, int(realm["level_end"])))
    realm_remaining = max(0, realm_total - int(char["cultivation"]))
    realm_times = 0 if retreat_gain <= 0 else max(0, -(-realm_remaining // max(1, retreat_gain)))
    exp_rate_view = {
        "total": round(float(stats.exp_rate), 2),
        "base": round(float(stats.exp_breakdown.get("base", 0)), 2),
        "realm_growth": round(float(stats.exp_breakdown.get("realm_growth", 1)), 3),
        "tech_coef": round(float(stats.exp_breakdown.get("tech_coef", 1)), 3),
        "physique": round(float(stats.exp_breakdown.get("physique", 1)), 3),
        "env": round(float(stats.exp_breakdown.get("env", 1)), 3),
        "mood": round(float(stats.exp_breakdown.get("mood", 1)), 3),
        "status": round(float(stats.exp_breakdown.get("status", 1)), 3),
        "direction": round(float(stats.exp_breakdown.get("direction", 1)), 3),
        "layers": [
            {"key": "base", "name": "基础", "coef": round(float(stats.exp_breakdown.get("base", 0)), 2),
             "note": f"5 + 悟性 {int(stats.base.get('wis', 10))} × 0.8"},
            {"key": "realm_growth", "name": "境界成长", "coef": round(float(stats.exp_breakdown.get("realm_growth", 1)), 3),
             "note": f"第 {int(char['realm_index'])} 境"},
            {"key": "tech_coef", "name": "功法", "coef": round(float(stats.exp_breakdown.get("tech_coef", 1)), 3),
             "note": "已装备心法"},
            {"key": "physique", "name": "体质", "coef": round(float(stats.exp_breakdown.get("physique", 1)), 3), "note": ""},
            {"key": "env", "name": "环境", "coef": round(float(stats.exp_breakdown.get("env", 1)), 3),
             "note": str(region.get("env_label", ""))},
            {"key": "mood", "name": "心境", "coef": round(float(stats.exp_breakdown.get("mood", 1)), 3),
             "note": f"道心 {int(char.get('dao_heart', 10))}"},
            {"key": "status", "name": "状态", "coef": round(float(stats.exp_breakdown.get("status", 1)), 3),
             "note": str(char.get("status") or "normal")},
            {"key": "direction", "name": "方向", "coef": round(float(stats.exp_breakdown.get("direction", 1)), 3),
             "note": str(char.get("direction_id") or "无")},
        ],
        "gain_per_days": retreat_gain,
    }
    # 自动槽 / 战斗 / 事件均从服务端恢复
    return {
        "character": {
            "id": char_id,
            "name": str(char.get("name") or "无名"),
            "gender": str(char.get("gender") or "unknown"),
            "personality": str(char.get("personality") or "zhongyong"),
            "physique": str(char.get("physique") or "fan_ti"),
            "physique_name": str(registry.physique(str(char.get("physique") or "fan_ti")).get("name", "")),
            "personality_name": str(registry.personality(str(char.get("personality") or "zhongyong")).get("name", "")),
            "talent": int(char.get("talent", 5)),
            "level": level,
            "realm_index": int(char["realm_index"]),
            "realm_stage": int(char["realm_stage"]),
            "realm_name": str(realm.get("display_name", "")),
            "cultivation": int(char["cultivation"]),
            "exp_need": int(need),
            "exp_progress_bp": level_domain.progress_bp(level, int(char["cultivation"]), registry.exp_k_bp(level)),
            "exp_overflow": int(char.get("exp_overflow", 0)),
            "age_days": int(char["age_days"]),
            "age_year": int((int(char["age_days"]) - 1) // 360 + 1),
            "lifespan_max_days": int(char["lifespan_max_days"]),
            "lifespan_left_days": left_days,
            "lifespan_left_years": round(left_days / 360.0, 2),
            "remaining_days": left_days,
            "remaining_years": round(left_days / 360.0, 2),
            "remaining_pct": round(max(0.0, min(1.0, left_days / max(1, int(char["lifespan_max_days"])))), 4),
            "lifespan_low": left_days < int(char["lifespan_max_days"]) * 0.2,
            "spirit_stone": int(char.get("spirit_stone", 0)),
            "karma": int(char.get("karma", 0)),
            "dao_heart": int(char.get("dao_heart", 10)),
            "status": str(char.get("status") or "normal"),
            "foundation": str(char.get("foundation") or ""),
            "region_id": str(char.get("region_id") or ""),
            "region_name": str(region.get("name", "")),
            "region_env_coef": float(region.get("env_coef", 1.0)),
            "can_breakthrough": bool(can_break),
            "can_seclude_times": closed_door_times,
            "hp": int(stats.hp),
            "hp_max": int(stats.derived.hp_max),
            "mp": int(stats.mp),
            "mp_max": int(stats.derived.mp_max),
            "cp": int(stats.derived.cp),
            "bag_slots": int(stats.bag_slots),
            "exp_rate": round(float(stats.exp_rate), 2),
            "retreat_gain": retreat_gain,
            "closed_door_times": closed_door_times,
            "remaining_actions": {"level": int(remaining_level), "realm": int(realm_times)},
            "breakthrough_reason": "冲击下一境" if can_break else "境界未圆满",
        },
        "exp_rate": exp_rate_view,
        "identity": _sect_view(char_id, char),
        "quests": quests,
        "current_quest": current_quest,
        "quests_completed": sum(1 for q in quests if q["status"] == "completed"),
        "pending_event": _pending_event(char),
        "combat": _combat_view(char_id),
        "logs": LogRepository.events(char_id, 30),
        "milestones": LogRepository.milestones(char_id, 20),
        "game_year": game_year(int(char["age_days"])),
        "closed_door_times": closed_door_times,
        "can_breakthrough": bool(can_break),
        "breakthrough_reason": "冲击下一境" if can_break else "境界未圆满",
        "remaining_actions": {"level": int(remaining_level), "realm": int(realm_times)},
        "gain_preview": {
            "seclusion": {
                "exp": int(retreat_gain),
                "days": int(action_days(registry.action_base_days("seclusion"), int(char["realm_index"]))),
                "pct": round(retreat_gain / max(1, int(need)), 4),
            }
        },
        # 基础耗时（配置值）与当前境界实际耗时（动态增长）分开暴露，便于 UI 与调参
        "action_base_days": dict(registry.action_base_days_table()),
        "action_days": {
            kind: int(action_days(int(days), int(char["realm_index"])))
            for kind, days in registry.action_base_days_table().items()
        },
        "version": int(char.get("version", 1)),
    }


def character_panel() -> Dict[str, Any]:
    """角色面板（10 基础 + 13 衍生 + 装备/功法清单）。"""
    char = active_char()
    stats = stats_service.build(char)
    registry = get_registry()
    techniques = []
    for row in TechniqueRepository.list_for(int(char["id"])):
        try:
            tech = registry.technique(str(row["tech_id"]))
        except Exception:
            continue
        techniques.append({
            "tech_id": str(row["tech_id"]),
            "name": str(tech.get("name", "")),
            "tier": str(tech.get("tier", "")),
            "sub_tier": str(tech.get("sub_tier", "")),
            "type": str(tech.get("type", "")),
            "layer": int(row.get("layer", 1)),
            "sense": int(row.get("sense", 0)),
            "equipped_slot": str(row.get("equipped_slot") or ""),
            "description": str(tech.get("description", "")),
        })
    equipment = []
    for row in EquipmentRepository.list_for(int(char["id"])):
        template = {}
        try:
            template = registry.equip_template(str(row["template_id"]))
        except Exception:
            pass
        equipment.append({
            "instance_id": int(row["instance_id"]),
            "template_id": str(row["template_id"]),
            "name": str(template.get("name", row["template_id"])),
            "slot": str(row["slot"]),
            "rarity": str(row["rarity"]),
            "enhance": int(row["enhance"]),
            "equipped": bool(row["equipped"]),
            "affixes": list(row.get("affixes") or []),
        })
    return {
        **stats.panel(),
        "name": str(char.get("name") or "无名"),
        "techniques": techniques,
        "equipment": equipment,
        "sect": _sect_view(int(char["id"]), char),
    }
