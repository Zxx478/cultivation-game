"""FastAPI 应用入口：内容加载 → 建库 → 种子 → 路由 → 静态资源。

启动顺序固定：
1. ``load_all()`` + ``validate``（内容错误在启动期 fail-fast）；
2. ``init_global_conn(db_path)``（game.sql → sect_contribution.sql）；
3. ``seed_quest_pool()`` / ``seed_techniques()`` / ``sync_catalog()``；
4. 挂 ``/api/v1`` 游戏路由 + 既有 ``/api/v1/sect`` 路由；
5. 挂 ``/static`` 与 ``GET /``（模板目录不存在时返回 503，不阻塞 API）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router as sect_router
from .api.game_router import router as game_router
from .content import get_registry, load_all
from .core.errors import GameError
from .core.response import fail
from .db import get_conn, init_global_conn, seed_quest_pool, seed_techniques, transaction
from .db.game_repository import CharacterRepository
from .services.errors import SectServiceError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("HUANGTIANJI_DATA", PROJECT_ROOT / "data"))
WEB_DIR = Path(__file__).resolve().parent / "web"


def _tier_for_sect_table(tier: str) -> str:
    """游戏品阶 → 既有物品主表 tier（low/mid/high）。"""
    return {"fan": "low", "ling": "mid", "bao": "mid", "sheng": "high", "di": "high"}.get(str(tier), "low")


def sync_catalog() -> None:
    """把 41 功法 / 15 道具 / 装备模板同步进既有物品主表。

    既有 ``ExchangeService`` 通过 ``technique/pill/equipment`` 表判定 tier 与
    基准价；不同步会出现「扣了贡献却查不到物品」（2005）。
    """
    registry = get_registry()
    with transaction() as conn:
        for tech in registry.techniques():
            conn.execute(
                """
                INSERT INTO technique (tech_id, name, tier, sub_tier, type, sect_id, base_price, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(tech_id) DO UPDATE SET
                  name = excluded.name, tier = excluded.tier, sub_tier = excluded.sub_tier,
                  type = excluded.type, sect_id = excluded.sect_id,
                  base_price = excluded.base_price, enabled = 1
                """,
                (str(tech["id"]), str(tech.get("name", "")),
                 _tier_for_sect_table(str(tech.get("tier", "fan"))),
                 str(tech.get("sub_tier", "xia")), str(tech.get("type", "attack")),
                 tech.get("sect_id"), int(tech.get("contribution_price") or 0)),
            )
        for item in registry.content.items:
            if str(item.get("kind")) != "pill":
                continue
            conn.execute(
                """
                INSERT INTO pill (pill_id, name, tier, sub_tier, effect, base_price, enabled)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(pill_id) DO UPDATE SET
                  name = excluded.name, tier = excluded.tier, sub_tier = excluded.sub_tier,
                  effect = excluded.effect, base_price = excluded.base_price, enabled = 1
                """,
                (str(item["id"]), str(item.get("name", "")),
                 _tier_for_sect_table(str(item.get("tier", "fan"))),
                 str(item.get("sub_tier", "xia")), str(item.get("effect", "")),
                 int(item.get("price", 0))),
            )
        for template in registry.content.equipments["templates"]:
            conn.execute(
                """
                INSERT INTO equipment (equip_id, name, tier, sub_tier, slot, sect_id, base_price, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(equip_id) DO UPDATE SET
                  name = excluded.name, tier = excluded.tier, sub_tier = excluded.sub_tier,
                  slot = excluded.slot, sect_id = excluded.sect_id,
                  base_price = excluded.base_price, enabled = 1
                """,
                (str(template["id"]), str(template.get("name", "")),
                 _tier_for_sect_table(str(template.get("tier", "fan"))),
                 str(template.get("sub_tier", "xia")), str(template.get("slot", "weapon")),
                 template.get("sect_id"), int(template.get("base_price", 0))),
            )


def _jinja():
    """惰性创建 Jinja2 环境（未安装/模板缺失时返回 None）。"""
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader(str(WEB_DIR / "templates")),
            autoescape=select_autoescape(["html", "xml"]),
            enable_async=False,
        )
        return env
    except Exception:
        return None


def create_app(db_path: str | None = None) -> FastAPI:
    """工厂函数（测试用内存库，生产用 data/game.db）。"""
    app = FastAPI(title="荒天纪", version="1.5.0")
    target = db_path or str(DATA_DIR / "game.db")
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    init_global_conn(db_path=target)
    seed_quest_pool()
    seed_techniques()
    sync_catalog()
    load_all()  # 内容自检（失败即启动失败）

    app.state.today_day = 1
    app.state.current_game_year = 1
    app.state.current_realm_seq = 1

    app.include_router(game_router)
    app.include_router(sect_router)

    if (WEB_DIR / "static").is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    env = _jinja()

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        """首屏（模板缺失返回 503，API 不受影响）。"""
        if env is None:
            return HTMLResponse("<h1>荒天纪</h1><p>模板目录缺失</p>", status_code=503)
        try:
            template = env.get_template("index.html")
            return HTMLResponse(template.render(request=request))
        except Exception as exc:  # pragma: no cover - 模板语法错误时暴露原因
            return HTMLResponse(f"<h1>荒天纪</h1><p>模板渲染失败：{exc}</p>", status_code=503)

    @app.get("/health")
    def health():
        """健康检查。"""
        return {"status": "ok", "version": "1.5.0"}

    @app.middleware("http")
    async def game_clock(request: Request, call_next):
        """把当前角色的 age_days 同步到 app.state，供既有宗门路由使用。"""
        try:
            char = CharacterRepository.get_active()
            if char:
                request.app.state.today_day = int(char["age_days"])
                request.app.state.current_game_year = (int(char["age_days"]) - 1) // 360 + 1
                request.app.state.current_realm_seq = int(char["realm_index"])
        except Exception:
            pass
        return await call_next(request)

    @app.exception_handler(GameError)
    async def game_error_handler(request: Request, exc: GameError):
        """统一错误体 + HTTP 映射。"""
        return JSONResponse(status_code=exc.http_status, content=fail(exc))

    @app.exception_handler(SectServiceError)
    async def sect_error_handler(request: Request, exc: SectServiceError):
        """既有宗门异常兜底（未被 sect_router 捕获时）。"""
        err = GameError(exc.code, exc.message, exc.detail or None)
        return JSONResponse(status_code=err.http_status, content=fail(err))

    return app


app = create_app()
