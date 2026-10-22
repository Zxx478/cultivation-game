"""禁地 Service：MVP 开放 2 处低阶禁地，散修掉率 +10%。

进入规则：
1. land_id 必须在 cfg.forbidden_land_ids 内（MVP 仅 2 处）
2. 角色境界序号 ≥ land.required_realm_seq
3. 本游戏年进入次数 < cfg.forbidden_entries_per_game_year（=3）
4. 冷却未过：now - last_entry_at_days >= cfg.forbidden_cooldown_days（=30）
5. 贡献点：
   - 弟子：用本宗贡献点
   - 散修：v1.7 改用 sansxiu_wallet.general_contribution（独立字段，不再用 ∑ 6 宗门散修贡献点求和）

散修掉率加成：
- 首通奖励 +10% 额外随机掉"散修独家"奖励
- 散修额外 bonus 走 land.sansxiu_extra_bonus 列表

执行：
1. 校验
2. 扣贡献点
3. 加 entry_count_this_year, last_entry_at_days
4. 抽取奖励
5. 写 forbidden_land_log
"""
from __future__ import annotations

import json
import random
from typing import List, Tuple

from ..config import load
from ..db import (
    ForbiddenLandLogRepository,
    ForbiddenLandProgressRepository,
    SansxiuWalletRepository,
    SectMemberRepository,
    transaction,
)
from ..models import RolePath
from . import errors


class ForbiddenLandService:

    @staticmethod
    def _resolve_role_and_contribution(character_id: int) -> Tuple[RolePath, int, dict]:
        """返回 (role, current_contribution, wallet_or_member)。

        v1.7：散修改用 sansxiu_wallet.general_contribution（独立字段）
        """
        member = SectMemberRepository.get(character_id)
        if member is not None:
            return RolePath.DISCIPLE, member["contribution"], member
        # 散修：使用通用钱包
        wallet = SansxiuWalletRepository.get(character_id)
        return RolePath.SANXIU, wallet["general_contribution"], wallet

    @staticmethod
    def _roll_rewards(
        rewards: List,
        is_sansxiu: bool,
        sansxiu_bonus_rate: float,
        sansxiu_extra: List,
    ) -> List[dict]:
        """按 drop_rate 抽取奖励，散修额外抽 sansxiu_extra。"""
        out = []
        for r in rewards:
            roll = random.random()
            effective_rate = r.drop_rate
            if is_sansxiu:
                # 散修的整体加成只作用于 first_clear
                # repeat_rewards 不再叠 bonus（避免重复刷爆）
                effective_rate = min(1.0, effective_rate + (sansxiu_bonus_rate if r.type == "technique" else 0))
            if roll < effective_rate:
                out.append({
                    "type": r.type,
                    "item_id": r.item_id,
                    "tech_id": r.tech_id,
                    "mat_id": r.mat_id,
                    "amount": r.amount,
                    "drop_rate": r.drop_rate,
                })
        # 散修额外 bonus
        if is_sansxiu:
            for r in sansxiu_extra:
                if random.random() < r.drop_rate:
                    out.append({
                        "type": r.type,
                        "item_id": r.item_id,
                        "tech_id": r.tech_id,
                        "mat_id": r.mat_id,
                        "amount": r.amount,
                        "drop_rate": r.drop_rate,
                    })
        return out

    @staticmethod
    def enter(
        character_id: int,
        land_id: str,
        today_day: int,
        current_realm_seq: int,
        current_game_year: int,
    ) -> dict:
        cfg = load()

        # 1. 校验 land_id 在 MVP 列表内
        if land_id not in cfg.forbidden_land_ids:
            raise errors.SectServiceError(
                errors.ERR_FORBIDDEN_LAND_INVALID_ID,
                f"MVP 不开放禁地: {land_id}",
                f"当前 MVP 开放: {cfg.forbidden_land_ids}",
            )

        land = cfg.forbidden_lands[land_id]

        # 2. 境界
        if current_realm_seq < land.required_realm_seq:
            raise errors.SectServiceError(
                errors.ERR_FORBIDDEN_LAND_REALM_TOO_LOW,
                f"境界未达 {land.display_name} 进入要求",
                f"current_realm_seq={current_realm_seq}, required={land.required_realm_seq}",
            )

        # 3. 取出/创建进度记录
        progress = ForbiddenLandProgressRepository.get(character_id, land_id)
        if progress is None:
            progress = {
                "character_id": character_id,
                "land_id": land_id,
                "is_first_cleared": 0,
                "entry_count_this_year": 0,
                "last_entry_at_days": None,
                "version": 1,
            }
        # 若年份切换重置 entry_count
        # last_year 用与 current_game_year 一致的口径：
        #   game_year = (day - 1) // 360 + 1   （today=1 → year=1，today=360 → year=1，today=361 → year=2）
        if progress["last_entry_at_days"] is not None:
            last_year = (progress["last_entry_at_days"] - 1) // 360 + 1
        else:
            last_year = None
        if last_year != current_game_year:
            progress["entry_count_this_year"] = 0

        # 4. 次数上限
        if progress["entry_count_this_year"] >= cfg.forbidden_entries_per_game_year:
            raise errors.SectServiceError(
                errors.ERR_FORBIDDEN_LAND_CAP_REACHED,
                f"本游戏年已进入 {progress['entry_count_this_year']} 次 {land.display_name}",
                f"上限: {cfg.forbidden_entries_per_game_year}",
            )

        # 5. 冷却
        if progress["last_entry_at_days"] is not None:
            since_last = today_day - progress["last_entry_at_days"]
            if since_last < cfg.forbidden_cooldown_days:
                raise errors.SectServiceError(
                    errors.ERR_FORBIDDEN_LAND_IN_COOLDOWN,
                    f"禁地冷却未过，还需 {cfg.forbidden_cooldown_days - since_last} 游戏日",
                )

        # 6. 角色身份与贡献点
        role, current_contrib, member_or_rep = ForbiddenLandService._resolve_role_and_contribution(
            character_id,
        )
        # 散修已经在 _resolve_role_and_contribution 里汇总，这里不再重复
        if current_contrib < land.cost_contribution:
            raise errors.SectServiceError(
                errors.ERR_FORBIDDEN_LAND_INSUFFICIENT_CONTRIBUTION,
                f"贡献点不足以支付禁地进入消耗",
                f"current={current_contrib}, required={land.cost_contribution}",
            )

        # 7. 执行进入 + 结算
        is_first_clear = not progress["is_first_cleared"]
        is_sansxiu = role == RolePath.SANXIU

        with transaction() as conn:
            # 扣贡献点
            if role == RolePath.DISCIPLE:
                member = member_or_rep
                member["contribution"] -= land.cost_contribution
                SectMemberRepository.upsert(member)
                remaining_contribution = member["contribution"]
                sect_id_for_log = member["sect_id"]
            else:
                # v1.7：散修用 sansxiu_wallet.general_contribution
                wallet = SansxiuWalletRepository.get(character_id)
                wallet["general_contribution"] -= land.cost_contribution
                SansxiuWalletRepository.upsert(wallet)
                remaining_contribution = wallet["general_contribution"]
                sect_id_for_log = None

            # 抽奖励
            if is_first_clear:
                rewards = ForbiddenLandService._roll_rewards(
                    land.first_clear_rewards,
                    is_sansxiu,
                    land.sansxiu_bonus_reward_rate,
                    land.sansxiu_extra_bonus,
                )
            else:
                rewards = ForbiddenLandService._roll_rewards(
                    land.repeat_rewards,
                    is_sansxiu,
                    land.sansxiu_bonus_reward_rate,
                    land.sansxiu_extra_bonus,
                )

            # 更新进度
            progress["is_first_cleared"] = 1
            progress["entry_count_this_year"] += 1
            progress["last_entry_at_days"] = today_day
            ForbiddenLandProgressRepository.upsert(progress)

            # 写日志
            attempt_no = ForbiddenLandLogRepository.count_attempts(character_id, land_id) + 1
            ForbiddenLandLogRepository.insert({
                "character_id": character_id,
                "land_id": land_id,
                "role_path": role.value,
                "sect_id": sect_id_for_log,
                "attempt": attempt_no,
                "is_first_clear": is_first_clear,
                "rewards_json": json.dumps(rewards, ensure_ascii=False),
                "success": 1,
                "entered_at_days": today_day,
            })

        return {
            "land_id": land_id,
            "role_path": role.value,
            "is_first_clear": is_first_clear,
            "boss_name": land.boss_name,
            "boss_force_level_coef": land.boss_force_level_coef,
            "rewards": rewards,
            "cost_contribution": land.cost_contribution,
            "remaining_contribution": remaining_contribution,
            "entry_count_this_year": progress["entry_count_this_year"],
        }
