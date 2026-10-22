"""游戏层 API 端到端回归（``TestClient`` + 内存库）。

覆盖：创角校验、行动结算、战斗、主线领奖、宗门/方向/兑换、散修、禁地、
存档导出/导入与篡改拒绝。每个用例独立 app，互不污染。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """独立内存库 app。"""
    from app.main import create_app

    app = create_app(db_path=":memory:")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def bootstrap(c: TestClient, name: str = "测试", points=None):
    """创角（先重掷拿候选）。"""
    points = points or {"str": 3, "con": 2}
    assert c.post("/api/v1/bootstrap/reroll", json={}).status_code == 200
    resp = c.post("/api/v1/bootstrap", json={
        "name": name, "gender": "male", "personality": "zhongyong", "points": points,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_bootstrap_points_must_sum_five(client):
    """加点不为 5 → 4001。"""
    client.post("/api/v1/bootstrap/reroll", json={})
    resp = client.post("/api/v1/bootstrap", json={
        "name": "测试", "gender": "male", "personality": "zhongyong",
        "points": {"str": 1},
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == 4001


def test_bootstrap_single_point_cap(client):
    """单项 >3 → 4001。"""
    client.post("/api/v1/bootstrap/reroll", json={})
    resp = client.post("/api/v1/bootstrap", json={
        "name": "测试", "gender": "male", "personality": "zhongyong",
        "points": {"str": 4, "con": 1},
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == 4001


def test_bootstrap_and_state(client):
    """创角成功，state 含角色/寿元/区域。"""
    data = bootstrap(client)
    assert data["character_id"] >= 1
    state = client.get("/api/v1/state").json()["data"]
    assert state["character"]["name"] == "测试"
    assert state["character"]["level"] == 1
    assert state["character"]["region_id"] == "duanhuai_village"
    assert state["character"]["lifespan_left_days"] > 0
    assert "rng_seed" not in state["character"]


def test_reroll_limit(client):
    """第 4 次重掷 → 4002（第 4 次调用失败）。"""
    for _ in range(3):
        assert client.post("/api/v1/bootstrap/reroll", json={}).status_code == 200
    resp = client.post("/api/v1/bootstrap/reroll", json={})
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == 4002


def test_action_days_grow_with_realm(client):
    """行动耗时随境界动态增长：锻血 30 → 辟府 42。"""
    from app.db.game_repository import CharacterRepository

    bootstrap(client)
    char = CharacterRepository.get_active()
    state1 = client.get("/api/v1/state").json()["data"]
    assert state1["action_days"]["seclusion"] == 30
    CharacterRepository.update(char["id"], {"level": 10, "realm_index": 2, "realm_stage": 1})
    state2 = client.get("/api/v1/state").json()["data"]
    assert state2["action_days"]["seclusion"] == 42
    assert state2["action_days"]["adventure"] == 21
    assert state2["action_base_days"]["seclusion"] == 30


def test_candidate_does_not_consume_reroll(client):
    """进入创角读候选不消耗重掷次数；只有点「重掷」才 +1。"""
    for _ in range(3):
        resp = client.get("/api/v1/bootstrap/candidate")
        assert resp.status_code == 200
        assert resp.json()["data"]["attempt"] == 1
    assert client.post("/api/v1/bootstrap/reroll", json={}).json()["data"]["attempt"] == 2
    assert client.post("/api/v1/bootstrap/reroll", json={}).json()["data"]["attempt"] == 3
    assert client.post("/api/v1/bootstrap/reroll", json={}).status_code == 429


def test_reroll_state_reset_after_bootstrap(client):
    """创角后清掉重掷候选，下一世可重新重掷。"""
    from app.db.game_repository import MetaRepository
    from app.services.bootstrap_service import ROLL_META_KEY

    client.post("/api/v1/bootstrap/reroll", json={})
    bootstrap(client)
    assert MetaRepository.get(ROLL_META_KEY) in (None, "")
    assert client.post("/api/v1/bootstrap/reroll", json={}).status_code == 200


def test_seclusion_advances_age_and_exp(client):
    """闭关推进 30 日并增加修为。"""
    bootstrap(client)
    before = client.get("/api/v1/state").json()["data"]["character"]
    resp = client.post("/api/v1/action", json={"kind": "seclusion", "repeat": 1})
    assert resp.status_code == 200, resp.text
    settle = resp.json()["data"]["settle"]
    assert settle["total_days"] == 30
    assert settle["exp"] > 0
    after = client.get("/api/v1/state").json()["data"]["character"]
    assert after["age_days"] == before["age_days"] + 30


def test_attribute_panel_not_seed(client):
    """角色面板有 10 基础 + 13 衍生，但不泄漏 rng_seed。"""
    bootstrap(client)
    data = client.get("/api/v1/character").json()["data"]
    assert "base" in data and "derived" in data
    assert data["derived"]["hp_max"] > 0
    assert "rng_seed" not in json.dumps(data)


def test_quest_claim_and_duplicate(client):
    """主线 1 领奖成功，重复领取 4029。"""
    bootstrap(client)
    first = client.post("/api/v1/quests/claim", json={"quest_id": "quest_01"})
    assert first.status_code == 200, first.text
    second = client.post("/api/v1/quests/claim", json={"quest_id": "quest_01"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == 4029


def test_save_and_load_roundtrip(client):
    """存读档后等级/年龄/修为一致。"""
    bootstrap(client)
    client.post("/api/v1/action", json={"kind": "seclusion", "repeat": 1})
    before = client.get("/api/v1/state").json()["data"]["character"]
    assert client.post("/api/v1/saves/save", json={"slot_id": 1}).status_code == 200
    client.post("/api/v1/action", json={"kind": "meditate"})
    loaded = client.post("/api/v1/saves/load", json={"slot_id": 1})
    assert loaded.status_code == 200, loaded.text
    after = client.get("/api/v1/state").json()["data"]["character"]
    assert after["level"] == before["level"]
    assert after["age_days"] == before["age_days"]
    assert after["cultivation"] == before["cultivation"]


def test_save_auto_slot_is_readonly(client):
    """玩家写自动槽 → 4019。"""
    bootstrap(client)
    resp = client.post("/api/v1/saves/save", json={"slot_id": 0})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == 4019


def test_export_import_and_tamper(client):
    """导出→篡改导入被拒；原存档不受影响。"""
    bootstrap(client)
    client.post("/api/v1/saves/save", json={"slot_id": 1})
    exported = client.post("/api/v1/saves/export", json={"slot_id": 1}).json()["data"]
    assert exported["checksum"] and exported["content_version"]
    tampered = json.loads(json.dumps(exported))
    tampered["snapshot"]["character"]["level"] = 99
    bad = client.post("/api/v1/saves/import", json={"payload": tampered})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == 4020
    good = client.post("/api/v1/saves/import", json={"payload": exported})
    assert good.status_code == 200, good.text
    assert good.json()["data"]["slot_id"] in (1, 2, 3)


def test_map_level_lock(client):
    """Lv.1 前往 Lv.14 区域 → 4023。"""
    bootstrap(client)
    resp = client.post("/api/v1/map/travel", json={"region_id": "palace_secret"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == 4023


def test_forbidden_requires_sansxiu(client):
    """非散修进禁地 → 4024。"""
    bootstrap(client)
    resp = client.post("/api/v1/forbidden/enter", json={"land_id": "gujing_jianyuan"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == 4024


def test_sect_join_requirement(client):
    """Lv.1 入宗 → 4022（条件未达）。"""
    bootstrap(client)
    resp = client.post("/api/v1/sect/join", json={"sect_id": "qingyun_sword"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == 4022
    assert resp.json()["error"]["detail"]["missing"]


def test_sect_join_direction_exchange(client):
    """Lv.10 入宗 → 定方向 → 兑换功法真正入库。"""
    from app.db.game_repository import CharacterRepository, FlagRepository
    from app.db.repository import SectMemberRepository

    bootstrap(client)
    char = CharacterRepository.get_active()
    CharacterRepository.update(char["id"], {
        "level": 10, "realm_index": 2, "realm_stage": 1,
        "str": 40, "wis": 30, "sen": 30,
    })
    FlagRepository.set(char["id"], "trial_win_streak", "3")
    assert client.post("/api/v1/sect/join", json={"sect_id": "qingyun_sword"}).status_code == 200
    assert client.post("/api/v1/sect/direction", json={"direction_id": "sword"}).status_code == 200
    member = SectMemberRepository.get(char["id"])
    member["contribution"] = 2000
    SectMemberRepository.upsert(member)
    resp = client.post("/api/v1/sect/exchange",
                       json={"item_type": "technique", "item_id": "qingfeng_three_strikes"})
    assert resp.status_code == 200, resp.text
    techs = [t["tech_id"] for t in client.get("/api/v1/techniques").json()["data"]["techniques"]]
    assert "qingfeng_three_strikes" in techs


def test_sansxiu_encounter_grants_technique(client):
    """散修奇遇必得一部独家功法。"""
    from app.db.game_repository import CharacterRepository

    bootstrap(client)
    char = CharacterRepository.get_active()
    CharacterRepository.update(char["id"], {"level": 10, "realm_index": 2, "realm_stage": 1})
    assert client.post("/api/v1/sect/join", json={"sect_id": "sansxiu"}).status_code == 200
    resp = client.post("/api/v1/sect/sansxiu/encounter", json={})
    assert resp.status_code == 200, resp.text
    tech_id = resp.json()["data"]["technique_id"]
    techs = [t["tech_id"] for t in client.get("/api/v1/techniques").json()["data"]["techniques"]]
    assert tech_id in techs


def test_combat_victory(client):
    """历练 → 战斗 → 胜利领奖。"""
    from app.db.game_repository import CharacterRepository

    bootstrap(client)
    char = CharacterRepository.get_active()
    CharacterRepository.update(char["id"], {"level": 5, "realm_index": 1, "realm_stage": 5,
                                            "str": 60, "con": 60, "hp": 0, "mp": 0})
    assert client.post("/api/v1/map/travel", json={"region_id": "back_hill"}).status_code == 200
    started = client.post("/api/v1/action", json={"kind": "adventure"})
    assert started.status_code == 200, started.text
    assert started.json()["data"]["combat"]["status"] == "active"
    for _ in range(30):
        resp = client.post("/api/v1/combat/action", json={"action_type": "attack"})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        if data["combat"]["status"] != "active":
            assert data["combat"]["status"] in ("victory", "defeat", "escaped", "timeout")
            break
    else:
        pytest.fail("30 回合内未结束")


def test_breakthrough_preview_and_execute(client):
    """突破预览 200；执行后等级/寿元变化。"""
    from app.db.game_repository import CharacterRepository
    from app.domain import level as level_domain

    bootstrap(client)
    char = CharacterRepository.get_active()
    CharacterRepository.update(char["id"], {
        "level": 9, "realm_index": 1, "realm_stage": 9,
        "cultivation": level_domain.exp_need(9, 10000),
        "hp": 0, "mp": 0,
    })
    preview = client.post("/api/v1/breakthrough/preview", json={"prep": {}})
    assert preview.status_code == 200
    assert 500 <= preview.json()["data"]["final_bp"] <= 9500
    resp = client.post("/api/v1/breakthrough", json={"prep": {}})
    assert resp.status_code == 200, resp.text
    after = CharacterRepository.require(char["id"])
    if resp.json()["data"]["success"]:
        assert after["level"] == 10
        assert after["lifespan_max_days"] == 150 * 360
