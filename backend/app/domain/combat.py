"""回合制战斗引擎（纯函数，状态可 JSON 往返）。

规则（任务书 §8 红线 7 / 系统设计 §4.4 / PRD §4.8）：
- 30 回合上限，超时判 ``timeout``；
- 先手由 INIT 比较决定，相等时由 rng 决定；
- 伤害 = 攻击 × 倍率 × 破防档 × （暴击）→ 减去减伤率；
- 破防三档：PEN/敌防 ≥ 1.0 → ×1.15；≥ 0.3 → ×1.00；否则 ×0.80（边界取高档）；
- 命中 = 攻方命中 − 守方闪避（clamp 5%–99%）；
- 逃跑 45% + 先手差修正，clamp 5%–90%，BOSS −30%、秘境 −20%，连续失败 3 次禁逃；
- 每回合结束回灵力 3%，防御指令当回合防御 ×1.5 参与减伤；
- 连战惩罚：同年第 n 场敌方属性 ×(1+0.15×(n−1))（上限 ×1.6），第 5 场起玩家力竭 −20%；
- 敌方 AI：MP 足且 roll 过 → 放技能；精英血 < 30% 狂暴一次（攻击 +30%）；
  杂兵血 < 20% 有 30% 概率逃跑（判己方胜利，无掉落由 service 处理）。

设计约束：本模块 **不 import db / fastapi**，不改传入 state（返回新 dict），
所有随机都来自 ``RngService``，stream 后缀保证同 counter 同结果。
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any, Dict, List, Mapping, Optional

from ..core.errors import (
    COMBAT_ACTION_INVALID,
    COMBAT_ENDED,
    FLEE_BANNED,
    GameError,
)
from ..core.rng import RngService

DEFAULT_CFG: Dict[str, Any] = {
    "max_turns": 30,
    "flee_base_bp": 4500,
    "flee_min_bp": 500,
    "flee_max_bp": 9000,
    "flee_boss_penalty_bp": 3000,
    "flee_secret_penalty_bp": 2000,
    "flee_fail_limit": 3,
    "mp_regen_bp_per_turn": 300,
    "crit_damage_base_bp": 15000,
    "consec_battle_step_bp": 1500,
    "consec_battle_cap_bp": 6000,
    "fatigue_bp": 2000,
    "fatigue_from_battle": 5,
    "defend_def_multiplier_bp": 15000,
    "pen_high_bp": 11500,
    "pen_mid_bp": 10000,
    "pen_low_bp": 8000,
    "enemy_ai_skill_bp": 6000,
    "enemy_flee_bp": 3000,
}

ACTION_TYPES = ("attack", "skill", "defend", "item", "flee")
STATUS_ACTIVE = "active"
STATUS_VICTORY = "victory"
STATUS_DEFEAT = "defeat"
STATUS_ESCAPED = "escaped"
STATUS_TIMEOUT = "timeout"


# ---------------- 工具 ----------------

def _cfg(overrides: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """合并外部战斗配置与缺省。"""
    out = dict(DEFAULT_CFG)
    if overrides:
        out.update({k: v for k, v in overrides.items() if v is not None})
    return out


def _side_defaults(side: Mapping[str, Any]) -> Dict[str, Any]:
    """为一方补齐战斗必需字段（缺省值集中在此，便于存档兼容）。"""
    data: Dict[str, Any] = {
        "hp": int(side.get("hp", side.get("hp_max", 1))),
        "hp_max": int(side.get("hp_max", max(1, int(side.get("hp", 1))))),
        "mp": int(side.get("mp", 0)),
        "mp_max": int(side.get("mp_max", max(1, int(side.get("mp", 0))))),
        "atk": float(side.get("atk", 1.0)),
        "def": float(side.get("def", 0.0)),
        "dr_denom": float(side.get("dr_denom", 500.0)),
        "pen": float(side.get("pen", 0.0)),
        "crit_bp": int(side.get("crit_bp", 500)),
        "crit_dmg_bp": int(side.get("crit_dmg_bp", 15000)),
        "hit_bp": int(side.get("hit_bp", 8500)),
        "evade_bp": int(side.get("evade_bp", 300)),
        "init": float(side.get("init", 10.0)),
        "stunned": int(side.get("stunned", 0)),
        "defending": bool(side.get("defending", False)),
        "cooldowns": dict(side.get("cooldowns", {})),
        "skills": [dict(s) for s in side.get("skills", ())],
        "flee_fail": int(side.get("flee_fail", 0)),
        "name": str(side.get("name", "")),
        "id": str(side.get("id", "")),
    }
    data["kind"] = str(side.get("kind", "minion"))
    data["enraged"] = bool(side.get("enraged", False))
    data["flee_hp_pct"] = int(side.get("flee_hp_pct", 2000))
    data["hp_max"] = max(1, data["hp_max"])
    data["hp"] = max(0, min(data["hp"], data["hp_max"]))
    return data


def dr_bp_of(defense: float, dr_denom: float, defending: bool = False,
             cfg: Optional[Mapping[str, Any]] = None) -> int:
    """减伤率（bp）：防御指令时防御值 ×1.5，上限 75%。"""
    conf = _cfg(cfg)
    eff_def = defense * (int(conf["defend_def_multiplier_bp"]) / 10000.0
                         if defending else 1.0)
    if eff_def <= 0:
        return 0
    raw = eff_def / (eff_def + max(dr_denom, 1.0))
    return int(min(7500, round(raw * 10000)))


def pen_multiplier(pen: float, defense: float,
                   cfg: Optional[Mapping[str, Any]] = None) -> float:
    """破防三档倍率（边界取高档）。"""
    conf = _cfg(cfg)
    if defense <= 0:
        return int(conf["pen_high_bp"]) / 10000.0
    ratio = pen / defense
    if ratio >= 1.0:
        return int(conf["pen_high_bp"]) / 10000.0
    if ratio >= 0.3:
        return int(conf["pen_mid_bp"]) / 10000.0
    return int(conf["pen_low_bp"]) / 10000.0


def _hp_pct(side: Mapping[str, Any]) -> int:
    """气血百分比（bp）。"""
    hp_max = max(1, int(side["hp_max"]))
    return int(int(side["hp"]) * 10000 // hp_max)


# ---------------- 状态构造 ----------------

def build_state(player: Mapping[str, Any], enemy: Mapping[str, Any], *,
                is_boss: bool = False, is_secret: bool = False,
                consec_battle: int = 1, max_turns: Optional[int] = None,
                cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """构造初始战斗状态。

    Args:
        player: 玩家战斗属性（hp/hp_max/mp/mp_max/atk/def/dr_denom/pen/
            crit_bp/crit_dmg_bp/hit_bp/evade_bp/init/skills）。
        enemy: 敌方战斗属性（同上 + kind/name/id）。
        is_boss: 是否 BOSS 战（逃跑 −30%，BOSS 不逃）。
        is_secret: 是否秘境战（逃跑 −20%）。
        consec_battle: 本游戏年第几场（≥2 触发敌方加成）。
        max_turns: 覆盖回合上限。
        cfg: ``game.yaml.combat``（None 用缺省）。

    Returns:
        可 JSON 序列化的状态字典。
    """
    conf = _cfg(cfg)
    p = _side_defaults(player)
    e = _side_defaults(enemy)

    step = int(conf["consec_battle_step_bp"])
    cap = int(conf["consec_battle_cap_bp"])
    mult_bp = 10000 + min(max(0, int(consec_battle) - 1) * step, cap)
    if mult_bp > 10000:
        e["atk"] = float(e["atk"]) * mult_bp / 10000.0
        e["def"] = float(e["def"]) * mult_bp / 10000.0
        e["hp_max"] = int(e["hp_max"] * mult_bp // 10000)
        e["hp"] = min(int(e["hp"]) if int(e["hp"]) else e["hp_max"], e["hp_max"])
        if int(consec_battle) > 1:
            e["hp"] = e["hp_max"]

    flags: Dict[str, Any] = {
        "is_boss": bool(is_boss) or e["kind"] == "boss",
        "is_secret": bool(is_secret),
        "consec_battle": int(consec_battle),
        "consec_multiplier_bp": mult_bp,
        "flee_banned": False,
        "enemy_fled": False,
        "fatigued": False,
    }
    if int(consec_battle) >= int(conf["fatigue_from_battle"]):
        fatigue = int(conf["fatigue_bp"])
        p["atk"] = float(p["atk"]) * (10000 - fatigue) / 10000.0
        p["def"] = float(p["def"]) * (10000 - fatigue) / 10000.0
        flags["fatigued"] = True

    return {
        "turn": 1,
        "max_turns": int(max_turns if max_turns is not None else conf["max_turns"]),
        "status": STATUS_ACTIVE,
        "player": p,
        "enemy": e,
        "flags": flags,
        "log": [],
    }


def enemy_from_content(row: Mapping[str, Any], *,
                       cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """把 enemies.yaml 的一行转成战斗属性（不含连战/境界缩放）。

    Args:
        row: 敌人配置行。
        cfg: 战斗配置（仅用于缺省值）。

    Returns:
        可直接传给 ``build_state`` 的敌方属性字典。
    """
    atk = float(row.get("atk", 1))
    skills: List[Dict[str, Any]] = []
    for skill in row.get("skills", ()):
        skills.append({
            "id": str(skill.get("name", "skill")),
            "name": str(skill.get("name", "")),
            "power_pct": int(skill.get("power_pct", 100)),
            "mp_cost": int(skill.get("mp_cost", 0)),
            "cooldown": int(skill.get("cooldown", 0)),
            "effect": str(skill.get("effect", "")),
            "stun_bp": int(skill.get("stun_bp", 3000 if "眩晕" in str(skill.get("effect", "")) or "麻痹" in str(skill.get("effect", "")) else 0)),
        })
    return {
        "id": str(row.get("id", "")),
        "name": str(row.get("name", "")),
        "kind": str(row.get("kind", "minion")),
        "hp": int(row.get("hp", 1)),
        "hp_max": int(row.get("hp", 1)),
        "mp": int(row.get("mp_max", 100)),
        "mp_max": int(row.get("mp_max", 100)),
        "atk": atk,
        "def": float(row.get("def", 0)),
        "dr_denom": float(row.get("dr_denom", 500.0 + math.sqrt(max(atk, 0.0)) * 5.0)),
        "pen": float(row.get("pen", atk * 0.5)),
        "crit_bp": int(row.get("crit_bp", 800)),
        "crit_dmg_bp": int(row.get("crit_dmg_bp", 15000)),
        "hit_bp": int(row.get("hit_bp", 8800)),
        "evade_bp": int(row.get("evade_bp", 200)),
        "init": float(row.get("speed", row.get("init", 10))),
        "skills": skills,
        "flee_hp_pct": 2000,
    }


def player_from_derived(derived: Mapping[str, Any], *, hp: int, mp: int,
                        skills: Optional[List[Mapping[str, Any]]] = None,
                        name: str = "你") -> Dict[str, Any]:
    """把 ``DerivedAttrs``（as_dict）转成玩家战斗属性。"""
    return {
        "id": "player",
        "name": name,
        "kind": "player",
        "hp": int(hp),
        "hp_max": int(derived["hp_max"]),
        "mp": int(mp),
        "mp_max": int(derived["mp_max"]),
        "atk": float(derived["atk"]),
        "def": float(derived["defense"]),
        "dr_denom": float(derived["dr_denom"]),
        "pen": float(derived["pen"]),
        "crit_bp": int(derived["crit_bp"]),
        "crit_dmg_bp": int(derived["crit_dmg_bp"]),
        "hit_bp": int(derived["hit_bp"]),
        "evade_bp": int(derived["evade_bp"]),
        "init": float(derived["init"]),
        "skills": [dict(s) for s in (skills or [])],
    }


# ---------------- 核心：一回合 ----------------

def is_finished(state: Mapping[str, Any]) -> bool:
    """战斗是否已结束。"""
    return str(state.get("status", STATUS_ACTIVE)) != STATUS_ACTIVE


def flee_chance_bp(state: Mapping[str, Any],
                   cfg: Optional[Mapping[str, Any]] = None) -> int:
    """计算当前逃跑成功率（bp，已 clamp 5%–90%）。"""
    conf = _cfg(cfg)
    p = state["player"]
    e = state["enemy"]
    base = int(conf["flee_base_bp"])
    # 先手差修正：每点 ±20bp，最多 ±1000bp
    diff = float(p["init"]) - float(e["init"])
    modifier = int(max(-1000, min(1000, diff * 20)))
    value = base + modifier
    if state["flags"].get("is_boss"):
        value -= int(conf["flee_boss_penalty_bp"])
    if state["flags"].get("is_secret"):
        value -= int(conf["flee_secret_penalty_bp"])
    return max(int(conf["flee_min_bp"]), min(int(conf["flee_max_bp"]), value))


def _log(state: Dict[str, Any], entry: Dict[str, Any]) -> None:
    """追加一条战报（仅保留最近 40 条）。"""
    entry = dict(entry)
    entry.setdefault("turn", state["turn"])
    state["log"].append(entry)
    if len(state["log"]) > 40:
        del state["log"][:-40]


def _check_death(state: Dict[str, Any]) -> None:
    """结算双方生死。"""
    if int(state["enemy"]["hp"]) <= 0:
        state["enemy"]["hp"] = 0
        state["status"] = STATUS_VICTORY
    elif int(state["player"]["hp"]) <= 0:
        state["player"]["hp"] = 0
        state["status"] = STATUS_DEFEAT


def _strike(state: Dict[str, Any], actor: str, power_pct: int, source: str,
            rng: RngService, conf: Dict[str, Any]) -> None:
    """执行一次攻击（含命中/暴击/破防/减伤判定）。"""
    attacker = state[actor]
    defender_key = "enemy" if actor == "player" else "player"
    defender = state[defender_key]
    stream = f"combat:t{state['turn']}:{actor}:{source}"

    hit_bp = max(500, min(9900, int(attacker["hit_bp"]) - int(defender["evade_bp"])))
    if not rng.roll_bp(hit_bp, stream + ":hit"):
        _log(state, {"actor": actor, "action": source, "damage": 0,
                     "miss": True, "text": f"{attacker['name']} 一击落空"})
        return

    raw = float(attacker["atk"]) * (power_pct / 100.0)
    raw *= pen_multiplier(float(attacker["pen"]), float(defender["def"]), conf)
    crit = rng.roll_bp(int(attacker["crit_bp"]), stream + ":crit")
    if crit:
        raw *= int(attacker["crit_dmg_bp"]) / 10000.0
    dr = dr_bp_of(float(defender["def"]), float(defender["dr_denom"]),
                  bool(defender.get("defending")), conf)
    damage = int(math.floor(raw * (1.0 - dr / 10000.0)))
    damage = max(1, damage)
    defender["hp"] = max(0, int(defender["hp"]) - damage)
    _log(state, {
        "actor": actor,
        "action": source,
        "damage": damage,
        "crit": crit,
        "dr_bp": dr,
        "target_hp": defender["hp"],
        "text": f"{attacker['name']} {'暴击' if crit else '命中'}，{damage} 点伤害",
    })
    _check_death(state)


def _player_action(state: Dict[str, Any], action: Mapping[str, Any],
                   rng: RngService, conf: Dict[str, Any]) -> None:
    """处理玩家指令。"""
    kind = str(action.get("type", ""))
    if kind not in ACTION_TYPES:
        raise GameError(COMBAT_ACTION_INVALID, f"未知战斗指令：{kind}")
    player = state["player"]

    if kind == "attack":
        _strike(state, "player", 100, "attack", rng, conf)
        return

    if kind == "skill":
        skill_id = str(action.get("skill_id", ""))
        skill = next((s for s in player["skills"] if str(s.get("id")) == skill_id), None)
        if skill is None:
            raise GameError(COMBAT_ACTION_INVALID, f"未掌握技能：{skill_id}")
        cost = int(skill.get("mp_cost", 0))
        cooldown = int(skill.get("cooldown", 0))
        remain = int(player["cooldowns"].get(skill_id, 0))
        if remain > 0:
            raise GameError(COMBAT_ACTION_INVALID, f"技能冷却中（{remain} 回合）")
        if int(player["mp"]) < cost:
            raise GameError(COMBAT_ACTION_INVALID, f"灵力不足（需 {cost}）")
        player["mp"] = int(player["mp"]) - cost
        if cooldown > 0:
            player["cooldowns"][skill_id] = cooldown
        _strike(state, "player", int(skill.get("power_pct", 100)), f"skill:{skill_id}", rng, conf)
        stun_bp = int(skill.get("stun_bp", 0))
        if stun_bp > 0 and state["status"] == STATUS_ACTIVE:
            if rng.roll_bp(stun_bp, f"combat:t{state['turn']}:player:stun"):
                state["enemy"]["stunned"] = 1
                _log(state, {"actor": "player", "action": "stun", "damage": 0,
                             "text": f"{state['enemy']['name']} 陷入僵直"})
        return

    if kind == "defend":
        player["defending"] = True
        _log(state, {"actor": "player", "action": "defend", "damage": 0,
                     "text": "你凝气护体"})
        return

    if kind == "item":
        heal_pct = int(action.get("heal_pct", 0))
        heal_mp_pct = int(action.get("mp_pct", 0))
        if heal_pct == 0 and heal_mp_pct == 0:
            raise GameError(COMBAT_ACTION_INVALID, "道具效果缺失")
        if heal_pct:
            amount = int(player["hp_max"] * heal_pct / 100)
            player["hp"] = min(int(player["hp_max"]), int(player["hp"]) + amount)
        if heal_mp_pct:
            amount = int(player["mp_max"] * heal_mp_pct / 100)
            player["mp"] = min(int(player["mp_max"]), int(player["mp"]) + amount)
        _log(state, {"actor": "player", "action": "item", "damage": -heal_pct,
                     "text": f"你服下道具，气血 {player['hp']}/{player['hp_max']}"})
        return

    # flee
    if state["flags"].get("flee_banned"):
        raise GameError(FLEE_BANNED, "退路已断，无法再逃")
    if action.get("guaranteed") and not state["flags"].get("is_boss"):
        state["status"] = STATUS_ESCAPED
        _log(state, {"actor": "player", "action": "flee", "damage": 0,
                     "text": "符纸碎裂，你借土遁脱出战圈"})
        return
    chance = flee_chance_bp(state, conf)
    if rng.roll_bp(chance, f"combat:t{state['turn']}:player:flee"):
        state["status"] = STATUS_ESCAPED
        _log(state, {"actor": "player", "action": "flee", "damage": 0,
                     "text": "你抽身后退，脱出战圈"})
        return
    player["flee_fail"] = int(player["flee_fail"]) + 1
    if int(player["flee_fail"]) >= int(conf["flee_fail_limit"]):
        state["flags"]["flee_banned"] = True
    _log(state, {"actor": "player", "action": "flee", "damage": 0,
                 "text": "退路被封，未能脱身"})


def _enemy_action(state: Dict[str, Any], rng: RngService,
                  conf: Dict[str, Any]) -> None:
    """敌方 AI：技能优先、精英狂暴、杂兵濒死逃跑、BOSS 不逃。"""
    enemy = state["enemy"]
    stream = f"combat:t{state['turn']}:enemy"

    if enemy["kind"] == "elite" and not enemy["enraged"] and _hp_pct(enemy) < 3000:
        enemy["enraged"] = True
        enemy["atk"] = float(enemy["atk"]) * 1.3
        _log(state, {"actor": "enemy", "action": "enrage", "damage": 0,
                     "text": f"{enemy['name']} 气血翻涌，攻势暴涨"})

    if enemy["kind"] == "minion" and _hp_pct(enemy) <= int(enemy["flee_hp_pct"]):
        if rng.roll_bp(int(conf["enemy_flee_bp"]), stream + ":flee"):
            state["status"] = STATUS_VICTORY
            state["flags"]["enemy_fled"] = True
            _log(state, {"actor": "enemy", "action": "flee", "damage": 0,
                         "text": f"{enemy['name']} 掉头逃入林间"})
            return

    candidates = [s for s in enemy["skills"]
                  if int(s.get("mp_cost", 0)) <= int(enemy["mp"])
                  and int(enemy["cooldowns"].get(str(s.get("id")), 0)) <= 0
                  and int(s.get("power_pct", 0)) > 0]
    if candidates and rng.roll_bp(int(conf["enemy_ai_skill_bp"]), stream + ":skill"):
        skill = max(candidates, key=lambda s: int(s.get("power_pct", 0)))
        sid = str(skill.get("id"))
        enemy["mp"] = int(enemy["mp"]) - int(skill.get("mp_cost", 0))
        if int(skill.get("cooldown", 0)) > 0:
            enemy["cooldowns"][sid] = int(skill.get("cooldown", 0))
        _strike(state, "enemy", int(skill.get("power_pct", 100)), f"skill:{sid}", rng, conf)
        stun_bp = int(skill.get("stun_bp", 0))
        if stun_bp > 0 and state["status"] == STATUS_ACTIVE:
            if rng.roll_bp(stun_bp, stream + ":stun"):
                state["player"]["stunned"] = 1
                _log(state, {"actor": "enemy", "action": "stun", "damage": 0,
                             "text": "你被震得眼前一黑"})
        return

    _strike(state, "enemy", 100, "attack", rng, conf)


def step(state: Mapping[str, Any], action: Mapping[str, Any],
         rng: RngService, *, cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """推进一回合。

    Args:
        state: 当前状态（不会被原地修改）。
        action: ``{type: attack|skill|defend|item|flee, skill_id?, heal_pct?, mp_pct?}``。
        rng: ``RngService``。
        cfg: 战斗配置。

    Returns:
        新状态字典（可直接 JSON 序列化落库）。

    Raises:
        GameError: 4014 战斗已结束 / 4026 指令非法 / 4027 禁逃。
    """
    if is_finished(state):
        raise GameError(COMBAT_ENDED, "战斗已结束")
    conf = _cfg(cfg)
    new_state: Dict[str, Any] = copy.deepcopy(dict(state))
    new_state.setdefault("flags", {})
    new_state.setdefault("log", [])

    player = new_state["player"]
    enemy = new_state["enemy"]
    enemy["defending"] = False
    # 防御在回合开始即生效：敌方先手时也能吃到减伤（否则防御等于白按）
    player["defending"] = (
        str(action.get("type")) == "defend" and int(player.get("stunned", 0)) == 0
    )

    player_first = float(player["init"]) > float(enemy["init"])
    if float(player["init"]) == float(enemy["init"]):
        player_first = rng.roll_bp(5000, f"combat:t{new_state['turn']}:initiative")

    order = ["player", "enemy"] if player_first else ["enemy", "player"]
    for actor in order:
        if new_state["status"] != STATUS_ACTIVE:
            break
        side = new_state[actor]
        if int(side.get("stunned", 0)) > 0:
            side["stunned"] = int(side["stunned"]) - 1
            _log(new_state, {"actor": actor, "action": "stunned", "damage": 0,
                             "text": f"{side['name']} 僵直，未能行动"})
            continue
        if actor == "player":
            _player_action(new_state, action, rng, conf)
        else:
            _enemy_action(new_state, rng, conf)

    # 回合末结算
    if new_state["status"] == STATUS_ACTIVE:
        for side in (new_state["player"], new_state["enemy"]):
            regen = int(int(side["mp_max"]) * int(conf["mp_regen_bp_per_turn"]) / 10000)
            side["mp"] = min(int(side["mp_max"]), int(side["mp"]) + max(0, regen))
            side["defending"] = False
            cooldowns = {k: v - 1 for k, v in side["cooldowns"].items() if v - 1 > 0}
            side["cooldowns"] = cooldowns
        new_state["turn"] = int(new_state["turn"]) + 1
        if int(new_state["turn"]) > int(new_state["max_turns"]):
            new_state["status"] = STATUS_TIMEOUT
            _log(new_state, {"actor": "system", "action": "timeout", "damage": 0,
                             "text": "三十回合已过，双方各自退开"})

    return new_state


def apply_revive(state: Mapping[str, Any], hp_pct: int = 50) -> Dict[str, Any]:
    """还魂丹复活：把 ``defeat`` 拉回 ``active``，回 ``hp_pct`` 气血。"""
    new_state: Dict[str, Any] = copy.deepcopy(dict(state))
    if new_state.get("status") != STATUS_DEFEAT:
        return new_state
    player = new_state["player"]
    player["hp"] = max(1, int(int(player["hp_max"]) * hp_pct / 100))
    player["stunned"] = 0
    player["flee_fail"] = 0
    new_state["status"] = STATUS_ACTIVE
    new_state["flags"]["revived"] = True
    _log(new_state, {"actor": "player", "action": "revive", "damage": 0,
                     "text": "九转还魂，一口气又被吊了回来"})
    return new_state


def to_json(state: Mapping[str, Any]) -> str:
    """状态 → JSON（存档 / 会话持久化）。"""
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def from_json(payload: str) -> Dict[str, Any]:
    """JSON → 状态（与 ``to_json`` 严格互逆）。"""
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise GameError(COMBAT_ACTION_INVALID, "战斗会话 JSON 结构非法")
    return data
