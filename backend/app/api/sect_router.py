"""FastAPI 路由层：宗门任务+贡献+兑换+禁地。

所有路由前缀 /api/v1/sect。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..schemas import (
    CharacterQuestStatusResponse,
    CompleteQuestRequest,
    CompleteQuestResponse,
    EnterForbiddenLandRequest,
    EnterForbiddenLandResponse,
    ErrorResponse,
    ExchangeRequest,
    ExchangeResponse,
    TakeQuestRequest,
    TakeQuestResponse,
)
from ..services import (
    ERR_EXCHANGE_INSUFFICIENT_CONTRIBUTION,
    ERR_EXCHANGE_INSUFFICIENT_REPUTATION,
    ERR_FORBIDDEN_LAND_CAP_REACHED,
    ERR_FORBIDDEN_LAND_IN_COOLDOWN,
    ERR_FORBIDDEN_LAND_INSUFFICIENT_CONTRIBUTION,
    ERR_FORBIDDEN_LAND_INVALID_ID,
    ERR_FORBIDDEN_LAND_REALM_TOO_LOW,
    ERR_QUEST_DAILY_CAP_REACHED,
    ERR_QUEST_INVALID_LEVEL,
    ERR_QUEST_INVALID_SECT,
    ERR_QUEST_NOT_FOUND,
    ERR_QUEST_QUEST_EXPIRED,
    ERR_QUEST_ROLE_NOT_READY,
    ERR_QUEST_SANXIU_CANNOT_TAKE_HIGH,
    ERR_QUEST_WRONG_OWNER,
    ERR_QUEST_WRONG_STATUS,
    ExchangeService,
    ForbiddenLandService,
    QuestService,
    SectServiceError,
)


router = APIRouter(prefix="/api/v1/sect", tags=["sect"])


# ---------- 错误码 → HTTP 状态码映射 ----------

_HTTP_STATUS_MAP = {
    ERR_QUEST_INVALID_LEVEL: 400,
    ERR_QUEST_INVALID_SECT: 400,
    ERR_QUEST_DAILY_CAP_REACHED: 429,
    ERR_QUEST_ROLE_NOT_READY: 403,
    ERR_QUEST_SANXIU_CANNOT_TAKE_HIGH: 403,
    ERR_QUEST_NOT_FOUND: 404,
    ERR_QUEST_WRONG_OWNER: 403,
    ERR_QUEST_WRONG_STATUS: 409,
    ERR_QUEST_QUEST_EXPIRED: 410,
    ERR_EXCHANGE_INSUFFICIENT_CONTRIBUTION: 402,
    ERR_EXCHANGE_INSUFFICIENT_REPUTATION: 403,
    ERR_FORBIDDEN_LAND_INVALID_ID: 404,
    ERR_FORBIDDEN_LAND_REALM_TOO_LOW: 403,
    ERR_FORBIDDEN_LAND_CAP_REACHED: 429,
    ERR_FORBIDDEN_LAND_IN_COOLDOWN: 429,
    ERR_FORBIDDEN_LAND_INSUFFICIENT_CONTRIBUTION: 402,
}


def _to_http_error(exc: SectServiceError) -> HTTPException:
    status = _HTTP_STATUS_MAP.get(exc.code, 400)
    return HTTPException(
        status_code=status,
        detail=ErrorResponse(
            code=exc.code, message=exc.message, detail=exc.detail or "",
        ).model_dump(),
    )


# ---------- 依赖：当前游戏日 / 当前游戏年 ----------

def _today_day(request: Request) -> int:
    # 实际项目由 game_clock 服务提供；这里从 state 取
    return getattr(request.app.state, "today_day", 1)


def _current_game_year(request: Request) -> int:
    return getattr(request.app.state, "current_game_year", 1)


def _current_realm_seq(request: Request) -> int:
    return getattr(request.app.state, "current_realm_seq", 1)


# ---------- 任务：接取 ----------

@router.post("/quests/take", response_model=TakeQuestResponse)
def take_quest(
    req: TakeQuestRequest,
    request: Request,
):
    """接取宗门任务。

    入参：
    - character_id
    - sect_id：目标宗门
    - level：low / mid / high

    出参：任务详情 + 奖励预览
    """
    today_day = _today_day(request)
    try:
        result = QuestService.take(
            req.character_id, req.sect_id, req.level, today_day,
        )
    except SectServiceError as exc:
        raise _to_http_error(exc)
    return TakeQuestResponse(**result)


# ---------- 任务：完成 ----------

@router.post("/quests/complete", response_model=CompleteQuestResponse)
def complete_quest(
    req: CompleteQuestRequest,
    request: Request,
):
    today_day = _today_day(request)
    try:
        result = QuestService.complete(req.character_id, req.quest_id, today_day)
    except SectServiceError as exc:
        raise _to_http_error(exc)
    return CompleteQuestResponse(**result)


# ---------- 兑换 ----------

@router.post("/exchange", response_model=ExchangeResponse)
def exchange(
    req: ExchangeRequest,
    request: Request,
):
    today_day = _today_day(request)
    try:
        result = ExchangeService.check_and_exchange(
            req.character_id, req.sect_id,
            req.item_type, req.item_id,
            req.cost_contribution, today_day,
        )
    except SectServiceError as exc:
        raise _to_http_error(exc)
    return ExchangeResponse(**result)


# ---------- 禁地 ----------

@router.post("/forbidden-land/enter", response_model=EnterForbiddenLandResponse)
def enter_forbidden_land(
    req: EnterForbiddenLandRequest,
    request: Request,
):
    today_day = _today_day(request)
    current_game_year = _current_game_year(request)
    current_realm_seq = _current_realm_seq(request)
    try:
        result = ForbiddenLandService.enter(
            req.character_id, req.land_id,
            today_day, current_realm_seq, current_game_year,
        )
    except SectServiceError as exc:
        raise _to_http_error(exc)
    return EnterForbiddenLandResponse(**result)


# ---------- 查询：玩家在某宗门的进度 ----------

@router.get("/status/{character_id}/{sect_id}",
            response_model=CharacterQuestStatusResponse)
def get_status(character_id: int, sect_id: str, request: Request):
    from ..db import (
        ActiveQuestRepository, SectMemberRepository, SectReputationRepository,
    )
    from ..config import load
    from ..models import RolePath

    cfg = load()
    if sect_id not in cfg.valid_sect_ids:
        raise HTTPException(status_code=404, detail="宗门不存在")

    member = SectMemberRepository.get(character_id)
    today_day = _today_day(request)
    if member is not None and member["sect_id"] == sect_id:
        role = RolePath.DISCIPLE
        contribution = member["contribution"]
        reputation = None
        low_done = member["low_quests_done"]
        mid_done = member["mid_quests_done"]
        high_done = member["high_quests_done"]
        unlocked_mid = bool(member["unlocked_mid"])
        unlocked_high = bool(member["unlocked_high"])
    else:
        role = RolePath.SANXIU
        rep = SectReputationRepository.get(character_id, sect_id) or {}
        contribution = rep.get("contribution", 0)
        reputation = rep.get("reputation", 0)
        low_done = rep.get("low_quests_done", 0)
        mid_done = rep.get("mid_quests_done", 0)
        high_done = 0
        unlocked_mid = bool(rep.get("unlocked_mid", 0))
        unlocked_high = False    # 散修不可解锁 high

    # 今日接取数（低 + 中 + 高 全部）
    daily_taken = sum(
        ActiveQuestRepository.count_today(character_id, lvl, today_day)
        for lvl in ("low", "mid", "high")
    )

    # 等级 cap 取自配置；这里展示综合上限（low+mid+high 各自上限总和）
    daily_cap = sum(cfg.quest_levels[lvl].daily_cap for lvl in ("low", "mid", "high"))

    return CharacterQuestStatusResponse(
        character_id=character_id,
        sect_id=sect_id,
        role_path=role.value,
        contribution=contribution,
        reputation=reputation,
        low_quests_done=low_done,
        mid_quests_done=mid_done,
        high_quests_done=high_done,
        unlocked_mid=unlocked_mid,
        unlocked_high=unlocked_high,
        daily_taken_quests=daily_taken,
        daily_cap=daily_cap,
    )
