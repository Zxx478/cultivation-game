"""宗门任务 Service：负责接取/完成/解锁判定。

核心规则（v1.6）：
- 任务分级：low / mid / high，奖励范围见 config.quest_levels
- 弟子入宗即得 low；累计 low 完成数达阈值解锁 mid；累计 mid 完成数达阈值解锁 high
- 散修对每个宗门独立维护 reputation + contribution
- 散修只能接 low（初始）+ mid（reputation ≥ 阈值后解锁）；high 永远不可接
- 完成任务后：
  - 弟子：贡献点 += reward_contribution，记录 low/mid/high_quests_done
  - 散修：对目标宗门的 reputation += reward_reputation, contribution += reward_contribution,
    low/mid_quests_done 累加，reputation 满阈值时 unlocked_mid = True
  - v1.7：散修额外按 sansxiu_general_share 比例累积到 sansxiu_wallet.general_contribution
    （用于禁地消费 + 入宗转换）
"""
from __future__ import annotations

import random
from typing import Tuple

from ..config import load
from ..db import (
    ActiveQuestRepository,
    QuestPoolRepository,
    QuestRecordRepository,
    SansxiuWalletRepository,
    SectMemberRepository,
    SectReputationRepository,
    transaction,
)
from ..models import QuestLevel, QuestStatus, RolePath
from . import errors


# ---------------- 工具 ----------------


def _resolve_level(level_str: str) -> QuestLevel:
    try:
        return QuestLevel(level_str)
    except ValueError:
        raise errors.SectServiceError(
            errors.ERR_QUEST_INVALID_LEVEL,
            f"任务等级非法: {level_str}",
            "合法值: low / mid / high",
        )


def _resolve_role(character_id: int) -> Tuple[RolePath, str]:
    """从数据库查身份。弟子 sect_member 存在且未叛宗即 DISCIPLE，
    否则 SANXIU（散修对 6 宗门各有独立 reputation）。
    """
    member = SectMemberRepository.get(character_id)
    if member is None:
        return RolePath.SANXIU, ""
    return RolePath.DISCIPLE, member["sect_id"]


# ---------------- 主服务 ----------------


class QuestService:
    """任务接取/完成。"""

    # ---------- 接取 ----------

    @staticmethod
    def take(
        character_id: int,
        sect_id: str,
        level_str: str,
        today_day: int,
        game_year: int = 1,
    ) -> dict:
        """接取任务。

        v1.7：新增 game_year 参数，年度权重自动惰性加载。
        """
        cfg = load()

        # 1. 校验宗门 ID
        if sect_id not in cfg.valid_sect_ids:
            raise errors.SectServiceError(
                errors.ERR_QUEST_INVALID_SECT,
                f"宗门 ID 非法: {sect_id}",
            )

        level = _resolve_level(level_str)
        level_cfg = cfg.quest_levels[level.value]

        # 2. 校验身份
        role, member_sect = _resolve_role(character_id)

        # 3. 散修硬规则：不可接 high
        if role == RolePath.SANXIU and level == QuestLevel.HIGH:
            raise errors.SectServiceError(
                errors.ERR_QUEST_SANXIU_CANNOT_TAKE_HIGH,
                "散修不可接取高级宗门任务",
                "符合 v1.5 §4.11.2 散修规则",
            )

        # 4. 资格校验
        if role == RolePath.DISCIPLE:
            # 弟子只可接自己宗门任务
            if member_sect != sect_id:
                raise errors.SectServiceError(
                    errors.ERR_QUEST_INVALID_SECT,
                    f"弟子仅可接取本宗（{member_sect}）任务",
                )
            member = SectMemberRepository.get(character_id)
            if member is None:
                # 理论不可达：_resolve_role 已确认
                raise errors.SectServiceError(errors.GENERIC_ERROR, "弟子身份数据缺失")
            unlocked_mid = bool(member["unlocked_mid"])
            unlocked_high = bool(member["unlocked_high"])
            if level == QuestLevel.MID and not unlocked_mid:
                raise errors.SectServiceError(
                    errors.ERR_QUEST_ROLE_NOT_READY,
                    f"尚未解锁 mid 任务，需先完成 {cfg.disciple_unlock.mid_required_low_quests} 个 low 任务",
                    f"已完成 low: {member['low_quests_done']}",
                )
            if level == QuestLevel.HIGH and not unlocked_high:
                raise errors.SectServiceError(
                    errors.ERR_QUEST_ROLE_NOT_READY,
                    f"尚未解锁 high 任务，需先完成 {cfg.disciple_unlock.high_required_mid_quests} 个 mid 任务",
                    f"已完成 mid: {member['mid_quests_done']}",
                )
        else:
            # 散修：对该宗门 reputation 校验
            rep = SectReputationRepository.get(character_id, sect_id)
            rep = rep or {
                "character_id": character_id, "sect_id": sect_id,
                "reputation": 0, "low_quests_done": 0, "mid_quests_done": 0,
                "unlocked_mid": 0, "contribution": 0, "version": 1,
            }
            current_rep = rep["reputation"]
            # 校验接任务最小好感度（防止魔道路径导致好感度 < 0）
            if current_rep < cfg.sansxiu_min_reputation_to_take_quest:
                raise errors.SectServiceError(
                    errors.ERR_QUEST_ROLE_NOT_READY,
                    f"你对 {sect_id} 的好感度过低，不可接任务",
                    f"当前好感度: {current_rep}，需 ≥ {cfg.sansxiu_min_reputation_to_take_quest}",
                )
            unlocked_mid = bool(rep["unlocked_mid"])
            if level == QuestLevel.MID and not unlocked_mid:
                raise errors.SectServiceError(
                    errors.ERR_QUEST_ROLE_NOT_READY,
                    f"散修对 {sect_id} 好感度未达解锁 mid 阈值",
                    f"当前好感度: {current_rep}，需 ≥ {cfg.sansxiu_unlock.mid_required_reputation}",
                )

        # 5. 当日上限校验
        today_count = ActiveQuestRepository.count_today(
            character_id, level.value, today_day,
        )
        if today_count >= level_cfg.daily_cap:
            raise errors.SectServiceError(
                errors.ERR_QUEST_DAILY_CAP_REACHED,
                f"今日已接 {today_count} 个 {level.value} 任务，达当日上限 {level_cfg.daily_cap}",
            )

        # 6. v1.7：确保年度权重已加载（惰性触发）
        from ..db import QuestPoolRepository
        # 检查该年是否已有权重记录
        conn_for_check = __import__("app.db.repository", fromlist=["get_conn"]).get_conn()
        has_yearly = conn_for_check.execute(
            "SELECT 1 AS ok FROM sect_quest_pool_yearly WHERE game_year = ? LIMIT 1",
            (game_year,),
        ).fetchone()
        if not has_yearly:
            QuestPoolRepository.refresh_yearly_weights(game_year)

        # 7. 抽签（按年度权重）
        pool_quest = QuestPoolRepository.draw(sect_id, level.value, game_year=game_year)
        if pool_quest is None:
            raise errors.SectServiceError(
                errors.ERR_QUEST_NOT_FOUND,
                f"任务池未配置 {sect_id}/{level.value} 任务",
            )

        # 7. 计算奖励
        contribution = random.randint(
            level_cfg.contribution_min, level_cfg.contribution_max,
        )
        # 用池子里的奖励基数作为"灵石"与"时长"基准
        spirit_stone = pool_quest.get("reward_spirit_stone_base", 0)

        # 散修额外获得好感度
        reputation = 0
        if role == RolePath.SANXIU:
            reputation = random.randint(
                level_cfg.reputation_for_sansxiu_min,
                level_cfg.reputation_for_sansxiu_max,
            )

        # 任务有效期：低级 7 天 / 中级 14 天 / 高级 30 天
        expire_days_map = {"low": 7, "mid": 14, "high": 30}
        expire_at = today_day + expire_days_map[level.value]

        # 8. 入库
        with transaction() as conn:
            new_quest_id = ActiveQuestRepository.insert({
                "character_id": character_id,
                "sect_id": sect_id,
                "level": level.value,
                "quest_kind": pool_quest["quest_kind"],
                "target_meta": pool_quest["target_meta"],
                "reward_contribution": contribution,
                "reward_reputation": reputation,
                "reward_spirit_stone": spirit_stone,
                "accepted_at_days": today_day,
                "expire_at_days": expire_at,
            })

        return {
            "quest_id": new_quest_id,
            "sect_id": sect_id,
            "level": level.value,
            "quest_kind": pool_quest["quest_kind"],
            "target_meta": pool_quest["target_meta"],
            "reward_contribution": contribution,
            "reward_reputation": reputation,
            "reward_spirit_stone": spirit_stone,
            "expire_at_days": expire_at,
        }

    # ---------- 完成 ----------

    @staticmethod
    def complete(character_id: int, quest_id: int, today_day: int) -> dict:
        cfg = load()

        quest = ActiveQuestRepository.get(quest_id)
        if quest is None:
            raise errors.SectServiceError(
                errors.ERR_QUEST_NOT_FOUND,
                f"任务不存在: quest_id={quest_id}",
            )
        if quest["character_id"] != character_id:
            raise errors.SectServiceError(
                errors.ERR_QUEST_WRONG_OWNER,
                "任务不属于该角色",
            )
        if quest["status"] != QuestStatus.ACCEPTED.value:
            raise errors.SectServiceError(
                errors.ERR_QUEST_WRONG_STATUS,
                f"任务状态非 accepted，当前: {quest['status']}",
            )
        if today_day > quest["expire_at_days"]:
            with transaction() as conn:
                ActiveQuestRepository.update_status(quest_id, QuestStatus.EXPIRED.value)
            raise errors.SectServiceError(
                errors.ERR_QUEST_QUEST_EXPIRED,
                "任务已过期",
                f"expire_at={quest['expire_at_days']}, today={today_day}",
            )

        role, _ = _resolve_role(character_id)
        level = QuestLevel(quest["level_id"])

        with transaction() as conn:
            # 1. 更新任务状态
            ActiveQuestRepository.update_status(quest_id, QuestStatus.COMPLETED.value)

            # 2. 记录完成
            QuestRecordRepository.insert({
                "character_id": character_id,
                "sect_id": quest["sect_id"],
                "level": level.value,
                "quest_kind": quest["quest_kind"],
                "role_path": role.value,
                "contribution_gained": quest["reward_contribution"],
                "reputation_gained": quest["reward_reputation"],
                "spirit_stone_gained": quest["reward_spirit_stone"],
                "completed_at_days": today_day,
            })

            # 3. 更新身份表（贡献点 + 任务计数 + 解锁判定）
            unlocked_mid_new = False
            unlocked_high_new = False

            if role == RolePath.DISCIPLE:
                member = SectMemberRepository.get(character_id)
                member["contribution"] += quest["reward_contribution"]
                if level == QuestLevel.LOW:
                    member["low_quests_done"] += 1
                    # 解锁 mid 判定
                    if not member["unlocked_mid"] and member["low_quests_done"] >= cfg.disciple_unlock.mid_required_low_quests:
                        member["unlocked_mid"] = True
                        unlocked_mid_new = True
                elif level == QuestLevel.MID:
                    member["mid_quests_done"] += 1
                    if not member["unlocked_high"] and member["mid_quests_done"] >= cfg.disciple_unlock.high_required_mid_quests:
                        member["unlocked_high"] = True
                        unlocked_high_new = True
                elif level == QuestLevel.HIGH:
                    member["high_quests_done"] += 1
                SectMemberRepository.upsert(member)
                new_contribution = member["contribution"]
                new_reputation = 0
            else:
                # 散修：维护对目标宗门的 reputation + contribution
                rep = SectReputationRepository.get(character_id, quest["sect_id"]) or {
                    "character_id": character_id,
                    "sect_id": quest["sect_id"],
                    "reputation": 0,
                    "low_quests_done": 0,
                    "mid_quests_done": 0,
                    "unlocked_mid": 0,
                    "contribution": 0,
                    "version": 1,
                }
                # 钳制好感度上限
                rep["reputation"] = min(
                    cfg.reputation_max,
                    rep["reputation"] + quest["reward_reputation"],
                )
                rep["contribution"] += quest["reward_contribution"]
                if level == QuestLevel.LOW:
                    rep["low_quests_done"] += 1
                    if not rep["unlocked_mid"] and rep["reputation"] >= cfg.sansxiu_unlock.mid_required_reputation:
                        rep["unlocked_mid"] = True
                        unlocked_mid_new = True
                elif level == QuestLevel.MID:
                    rep["mid_quests_done"] += 1
                SectReputationRepository.upsert(rep)
                new_contribution = rep["contribution"]
                new_reputation = rep["reputation"]

                # v1.7 新增：散修按 sansxiu_general_share 比例累积到通用钱包
                share_map = {
                    QuestLevel.LOW: cfg.sansxiu_general_share.low,
                    QuestLevel.MID: cfg.sansxiu_general_share.mid,
                    QuestLevel.HIGH: cfg.sansxiu_general_share.high,
                }
                share = share_map[level]
                general_gain = int(quest["reward_contribution"] * share)
                if general_gain > 0:
                    wallet = SansxiuWalletRepository.add_contribution(
                        character_id, general_gain,
                    )
                    # 记录到返回值便于前端展示
                    # （不直接修改 new_contribution，避免与"该宗门贡献点"混淆）

        return {
            "quest_id": quest_id,
            "sect_id": quest["sect_id"],
            "level": level.value,
            "contribution_gained": quest["reward_contribution"],
            "reputation_gained": quest["reward_reputation"],
            "spirit_stone_gained": quest["reward_spirit_stone"],
            "new_contribution": new_contribution,
            "new_reputation": new_reputation,
            "unlocked_mid": unlocked_mid_new,
            "unlocked_high": unlocked_high_new,
        }
