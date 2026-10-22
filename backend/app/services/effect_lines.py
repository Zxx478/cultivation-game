"""事件效果 → 中文展示行。

唯一职责：把 ``EffectOutcome.as_dict()`` 那份「引擎键名」翻译成玩家看得懂的中文行。
数值仍由服务端给出，本模块只做键名翻译与已格式化文案，避免前端把
``str`` / ``luk`` / ``chance_bp`` 这类内部键名直接抛给玩家。

硬约束：
- ``chance_bp`` / ``success`` / ``flags`` 等内部键一律不出现在展示行里
  （设计 §3.5：只展示性质，不展示具体成功率）。
- 物品 / 功法名称取自内容注册表；取不到才回退成 id。
- 未知的英文键宁可不显示，也不把内部键名露给玩家。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

#: 十项基础属性的中文名（与 character.js 的 BASIC_LABELS 保持一致）
#: 注意：基础属性键是 ``mnd``（道心），``dao_heart`` 只是角色表列名/效果键别名。
ATTR_LABELS: Dict[str, str] = {
    "str": "力量",
    "con": "体魄",
    "spr": "灵力",
    "sen": "神识",
    "bon": "根骨",
    "wis": "悟性",
    "agi": "身法",
    "luk": "气运",
    "mnd": "道心",
    "karma": "因果",
    "dao_heart": "道心",
}

#: 角色状态的中文名（与 ui.js 的 statusBadge 保持一致）
STATUS_LABELS: Dict[str, str] = {
    "normal": "无恙",
    "injured": "负伤",
    "qi_deviation": "走火入魔",
    "dead": "已坐化",
    "deceased": "已坐化",
}

#: 只做内部记账、绝不展示的键
INTERNAL_KEYS = frozenset({
    "success",
    "chance_bp",
    "effects",
    "flags",
    "unhandled",
    "rng_counter",
})


def _is_ascii_identifier(token: str) -> bool:
    """判断一个键是否是内部英文标识（用于兜底时避免漏出英文）。"""
    return bool(token) and all(ch.isascii() for ch in token)


def _name_of(registry: Any, kind: str, target_id: Any) -> str:
    """从内容注册表取展示名；取不到回退 id。"""
    raw_id = str(target_id)
    getter = getattr(registry, kind, None)
    if getter is None:
        return raw_id
    try:
        row = getter(raw_id)
    except Exception:
        return raw_id
    if isinstance(row, Mapping):
        name = row.get("name") or row.get("display_name")
        if name:
            return str(name)
    return raw_id


def _signed(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build(effects: Optional[Mapping[str, Any]], registry: Any, *,
          time_days: int = 0) -> List[Dict[str, Any]]:
    """把一份效果字典译成按阅读顺序排列的中文条目。

    Args:
        effects: ``EffectOutcome.as_dict()`` 的结果（含大量零值键）。
        registry: 内容注册表，用于物品 / 功法名称。
        time_days: 本次结算额外消耗的天数（0 表示不显示）。

    Returns:
        ``[{label, text, tone}]``；``tone`` 为 ``up`` / ``down`` / ``None``。
    """
    lines: List[Dict[str, Any]] = []

    def add(label: Any, text: Any, tone: Optional[str] = None) -> None:
        if text is None or text == "":
            return
        lines.append({"label": str(label), "text": str(text), "tone": tone})

    def add_num(label: Any, value: Any, suffix: str = "") -> None:
        number = _signed(value)
        if number is None:
            add(label, value)
            return
        if number == 0:
            return
        add(label, f"{number:+d}{suffix}", "up" if number > 0 else "down")

    for key, value in dict(effects or {}).items():
        name = str(key)
        if name in INTERNAL_KEYS:
            continue

        if name == "cultivation":
            add_num("修为", value)
        elif name == "spirit_stone":
            add_num("灵石", value)
        elif name == "karma":
            add_num("因果", value)
        elif name == "dao_heart":
            add_num("道心", value)
        elif name == "hp_pct":
            add_num("气血", value, "%")
        elif name == "mp_pct":
            add_num("灵力", value, "%")
        elif name == "contribution":
            add_num("贡献", value)
        elif name == "attr":
            for attr_key, delta in dict(value or {}).items():
                add_num(ATTR_LABELS.get(str(attr_key), str(attr_key)), delta)
        elif name in ("statuses", "status"):
            items = value if isinstance(value, (list, tuple, set)) else [value]
            for item in items:
                add("状态", STATUS_LABELS.get(str(item), str(item)))
        elif name == "items_gained":
            for item_id, qty in dict(value or {}).items():
                add(f"所得·{_name_of(registry, 'item', item_id)}", f"×{int(qty)}", "up")
        elif name == "items_lost":
            for item_id, qty in dict(value or {}).items():
                add(f"失去·{_name_of(registry, 'item', item_id)}", f"×{int(qty)}", "down")
        elif name == "techniques":
            for tech_id in value or ():
                add("习得功法", _name_of(registry, "technique", tech_id), "up")
        elif name == "favors":
            for npc_id, delta in dict(value or {}).items():
                add_num(f"好感·{npc_id}", delta)
        elif name == "buffs":
            for buff in value or ():
                if not isinstance(buff, Mapping):
                    continue
                amount = _signed(buff.get("value")) or 0
                days = _signed(buff.get("duration_days")) or 0
                text = f"{amount:+d}" if amount else "—"
                if days:
                    text += f" · {days} 日"
                add("增益", text, "up" if amount >= 0 else "down")
        elif not _is_ascii_identifier(name):
            # 未来若出现中文自定义键，原样展示；英文内部键一律不露出
            add(name, value)

    if time_days:
        add("耗时", f"{int(time_days)} 日", "down")
    return lines
