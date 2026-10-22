"""core 层：随机、时钟、错误、响应。

对外只暴露无 IO 的纯能力，供 domain / services / api 复用。
"""
from __future__ import annotations

from .clock import (
    DAYS_PER_MONTH,
    DAYS_PER_YEAR,
    action_days,
    days_to_years,
    game_month,
    game_year,
    month_of_year,
    years_to_days,
)
from .errors import (
    GAME_ERROR_CODES,
    CODE_TO_HTTP,
    GameError,
    err_payload,
)
from .response import fail, ok
from .rng import RngService

__all__ = [
    "DAYS_PER_MONTH",
    "DAYS_PER_YEAR",
    "action_days",
    "days_to_years",
    "game_month",
    "game_year",
    "month_of_year",
    "years_to_days",
    "GAME_ERROR_CODES",
    "CODE_TO_HTTP",
    "GameError",
    "err_payload",
    "fail",
    "ok",
    "RngService",
]
