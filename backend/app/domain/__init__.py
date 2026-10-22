"""domain 层：纯函数规则（无 DB / 无 IO / 不用 random）。

模块划分：
- ``level``   修为曲线、连升与溢出
- ``attrs``   10 基础 + 13 衍生 + CP + 软上限 + ExpRate 七层乘算
- ``realm``   突破成功率、惩罚、根基、寿元
- ``combat``  回合制状态机（纯函数，状态可 JSON 往返）
- ``event``   事件筛选、判定、效果 DSL
- ``quest``   主线目标推进与完成判定
"""
from __future__ import annotations

from . import attrs, combat, event, level, quest, realm

__all__ = ["attrs", "combat", "event", "level", "quest", "realm"]
