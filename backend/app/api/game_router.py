"""游戏层 REST 路由（``/api/v1``）。

约定：
- 成功 ``{ok: true, data: ...}``；失败抛 ``GameError``，由 ``main.py`` 的
  全局异常处理器转 ``{ok:false,error:{code,message,detail}}`` + HTTP 映射；
- 所有写接口都从 ``meta.active_char_id`` 解析当前角色，**不接受前端传
  character_id**（单机单角色，天然杜绝越权读写）；
- 既有宗门 1xxx–3xxx 错误码原样透传。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from ..content import get_registry
from ..core.response import ok
from ..schemas.game import (
    ActionRequest,
    BootstrapRequest,
    BreakthroughRequest,
    CombatActionRequest,
    DirectionRequest,
    EmptyRequest,
    EventChooseRequest,
    ExchangeRequest,
    ForbiddenEnterRequest,
    ImportRequest,
    InventoryRequest,
    QuestClaimRequest,
    RerollRequest,
    SaveRequest,
    SectJoinRequest,
    SectQuestCompleteRequest,
    SectQuestTakeRequest,
    TechniqueRequest,
    TravelRequest,
)
from ..services import (
    action_service,
    bootstrap_service,
    breakthrough_service,
    combat_service,
    main_quest_service,
    progress_service,
    save_service,
    sect_bridge_service,
    state_service,
    story_event_service,
)

router = APIRouter(prefix="/api/v1", tags=["game"])


# ---------------- 创角 / 状态 ----------------

@router.get("/bootstrap/candidate")
def candidate():
    """读取当前创角候选；无候选则掷一次（不额外消耗重掷次数）。"""
    return ok(bootstrap_service.BootstrapService.candidate())


@router.post("/bootstrap/reroll")
def reroll(req: RerollRequest | None = None):
    """重掷体质/资质/悟性（最多 3 次）。"""
    return ok(bootstrap_service.BootstrapService.reroll())


@router.post("/bootstrap")
def bootstrap(req: BootstrapRequest):
    """创角。"""
    data = bootstrap_service.BootstrapService.create(req.model_dump())
    return ok(data)


@router.get("/meta")
def meta():
    """公开的内容摘要（无需角色）：版本、行动基础耗时、境界表。"""
    registry = get_registry()
    realms = [{
        "id": str(r["id"]),
        "name": str(r.get("display_name", "")),
        "order": int(r["order"]),
        "level_start": int(r["level_start"]),
        "level_end": int(r["level_end"]),
        "base_lifespan_years": int(r["base_lifespan_years"]),
        "enabled": bool(r.get("enabled")),
    } for r in registry.realms()]
    return ok({
        "content_version": str(registry.content.content_version),
        "action_base_days": dict(registry.action_base_days_table()),
        "realms": realms,
        "counts": dict(registry.content.counts),
    })


@router.get("/state")
def state():
    """主界面聚合快照。"""
    return ok(state_service.state())


@router.get("/character")
def character():
    """角色面板（10 基础 + 13 衍生 + 功法/装备）。"""
    return ok(state_service.character_panel())


# ---------------- 行动 / 事件 / 突破 ----------------

@router.post("/action")
def action(req: ActionRequest):
    """执行行动（闭关/历练/采药/切磋/打坐）。"""
    return ok(action_service.execute(req.kind, repeat=req.repeat))


@router.post("/event/choose")
def event_choose(req: EventChooseRequest):
    """选择事件选项。"""
    return ok(story_event_service.choose(req.instance_id, req.option_id))


@router.post("/breakthrough/preview")
def breakthrough_preview(req: BreakthroughRequest | None = None):
    """突破成功率明细。"""
    return ok(breakthrough_service.preview(req.prep if req else None))


@router.post("/breakthrough")
def breakthrough(req: BreakthroughRequest | None = None):
    """执行突破。"""
    return ok(breakthrough_service.execute(req.prep if req else None))


# ---------------- 战斗 ----------------

@router.get("/combat")
def combat():
    """当前战斗会话（刷新恢复）。"""
    return ok(combat_service.current())


@router.post("/combat/action")
def combat_action(req: CombatActionRequest):
    """战斗回合。"""
    return ok(combat_service.act(req.action_type, skill_id=req.skill_id, item_id=req.item_id))


# ---------------- 功法 ----------------

@router.get("/techniques")
def techniques():
    """已学功法与装备槽。"""
    return ok(progress_service.list_techniques())


@router.post("/techniques/learn")
def technique_learn(req: TechniqueRequest):
    """学习功法。"""
    return ok(progress_service.learn(req.tech_id))


@router.post("/techniques/meditate")
def technique_meditate(req: TechniqueRequest):
    """参悟升层。"""
    return ok(progress_service.meditate(req.tech_id))


@router.post("/techniques/equip")
def technique_equip(req: TechniqueRequest):
    """装备功法。"""
    return ok(progress_service.equip(req.tech_id, req.slot or ""))


@router.post("/techniques/unequip")
def technique_unequip(req: TechniqueRequest):
    """卸下功法。"""
    return ok(progress_service.unequip(req.tech_id, req.slot))


# ---------------- 背包 ----------------

@router.get("/inventory")
def inventory(tab: Optional[str] = Query(default=None, max_length=16)):
    """背包分页。"""
    return ok(progress_service.list_inventory(tab))


@router.post("/inventory/use")
def inventory_use(req: InventoryRequest):
    """使用物品。"""
    return ok(progress_service.use_item(req.item_id or "", req.qty))


@router.post("/inventory/sell")
def inventory_sell(req: InventoryRequest):
    """出售物品。"""
    return ok(progress_service.sell_item(req.item_id or "", req.qty))


@router.post("/inventory/equip")
def inventory_equip(req: InventoryRequest):
    """穿戴装备。"""
    return ok(progress_service.equip_equipment(int(req.instance_id or 0)))


@router.post("/inventory/unequip")
def inventory_unequip(req: InventoryRequest):
    """卸下装备。"""
    return ok(progress_service.unequip_equipment(int(req.instance_id or 0)))


@router.post("/inventory/enhance")
def inventory_enhance(req: InventoryRequest):
    """强化装备（+5 上限）。"""
    return ok(progress_service.enhance(int(req.instance_id or 0)))


# ---------------- 主线 ----------------

@router.get("/quests")
def quests():
    """19 条主线进度。"""
    data = state_service.state()
    return ok({"quests": data["quests"], "completed": data["quests_completed"]})


@router.post("/quests/claim")
def quest_claim(req: QuestClaimRequest):
    """领取主线奖励。"""
    char = state_service.active_char()
    return ok(main_quest_service.claim(int(char["id"]), req.quest_id))


# ---------------- 地图 ----------------

@router.get("/map")
def map_view():
    """20 区域（已开放 6 个）。"""
    return ok(progress_service.list_map())


@router.post("/map/travel")
def map_travel(req: TravelRequest):
    """前往区域。"""
    return ok(progress_service.travel(req.region_id))


# ---------------- 宗门 / 散修 ----------------

@router.get("/sect")
def sect_overview():
    """宗门/方向/散修面板。"""
    return ok(sect_bridge_service.overview())


@router.post("/sect/join")
def sect_join(req: SectJoinRequest):
    """入宗或选择散修。"""
    return ok(sect_bridge_service.join(req.sect_id))


@router.post("/sect/direction")
def sect_direction(req: DirectionRequest):
    """定方向。"""
    return ok(sect_bridge_service.choose_direction(req.direction_id))


@router.post("/sect/leave-direction")
def sect_direction_alias(req: DirectionRequest):
    """定方向（历史别名）。"""
    return ok(sect_bridge_service.choose_direction(req.direction_id))


@router.post("/sect/quest/take")
def sect_quest_take(req: SectQuestTakeRequest):
    """接宗门任务。"""
    return ok(sect_bridge_service.take_sect_quest(req.level))


@router.post("/sect/quest/complete")
def sect_quest_complete(req: SectQuestCompleteRequest):
    """完成宗门任务。"""
    return ok(sect_bridge_service.complete_sect_quest(req.quest_id))


@router.post("/sect/exchange")
def sect_exchange(req: ExchangeRequest):
    """宗门兑换（真正发放）。"""
    return ok(sect_bridge_service.exchange(req.item_type, req.item_id))


@router.post("/sect/sansxiu/encounter")
def sansxiu_encounter(req: EmptyRequest | None = None):
    """散修奇遇。"""
    return ok(sect_bridge_service.sansxiu_encounter())


@router.post("/sect/sansxiu/convert")
def sansxiu_convert(req: EmptyRequest | None = None):
    """散修贡献转换说明。"""
    return ok(sect_bridge_service.convert_sansxiu())


# ---------------- 禁地 ----------------

@router.get("/forbidden")
def forbidden():
    """10 处禁地（启用 2 处）+ 进度。"""
    return ok(sect_bridge_service.forbidden_list())


@router.post("/forbidden/enter")
def forbidden_enter(req: ForbiddenEnterRequest):
    """进入禁地（散修限定）。"""
    return ok(sect_bridge_service.enter_forbidden(req.land_id))


# ---------------- 存档 ----------------

@router.get("/saves")
def saves():
    """3 手动 + 1 自动槽。"""
    return ok({"slots": save_service.SaveService.list_slots()})


@router.post("/saves/save")
def save(req: SaveRequest):
    """保存到手动槽（0 自动槽玩家不可写）。"""
    if int(req.slot_id) == 0:
        raise _slot_error()
    return ok(save_service.SaveService.save_slot(
        int(req.slot_id), label=req.label, expected_version=req.expected_version))


@router.post("/saves/load")
def save_load(req: SaveRequest):
    """读取存档。"""
    return ok(save_service.SaveService.load(int(req.slot_id),
                                            expected_version=req.expected_version))


@router.post("/saves/export")
def save_export(req: SaveRequest):
    """导出存档 JSON。"""
    return ok(save_service.SaveService.export(int(req.slot_id)))


@router.post("/saves/import")
def save_import(req: ImportRequest):
    """导入存档（校验四项后新建角色 + 空槽）。"""
    return ok(save_service.SaveService.import_payload(req.payload, slot_id=req.slot_id))


@router.post("/saves/delete")
def save_delete(req: SaveRequest):
    """删除手动槽。"""
    return ok(save_service.SaveService.delete(int(req.slot_id)))


# ---------------- 日志 ----------------

@router.get("/logs")
def logs():
    """事件流 + 里程碑。"""
    data = state_service.state()
    return ok({"events": data["logs"], "milestones": data["milestones"]})


def _slot_error():
    """自动槽玩家不可写（4019）。"""
    from ..core.errors import SAVE_SLOT_INVALID, GameError
    return GameError(SAVE_SLOT_INVALID, "自动存档槽由系统写入，玩家不可覆盖")
