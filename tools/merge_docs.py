"""把 PRD 与技术方案合并成一份完整需求文档。

设计要点：
1. 不重排章节编号 —— 两份文档里有大量 `§x.y` 交叉引用，重编号会让它们全部失效。
   合并版按「第一部分 / 第二部分」分卷，原编号原样保留，靠顶部导航表做索引。
2. 合并时可插入一份主理人的交叉核对记录（冲突 → 裁决 → 落点），
   这类信息只存在于两文档之间，任何单份文档都没有。
3. 幂等：重复执行覆盖同一路径，不产生副本。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
PRD = DOCS / "01-PRD-产品需求文档.md"
TECH = DOCS / "02-技术方案.md"
OUT = DOCS / "00-需求文档-完整版.md"


def split_head(body: str) -> tuple[list[str], str]:
    """拆出正文前的标题/元信息块，返回 (meta 行, 正文)。正文从第一个二级标题开始。"""
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("## "):
            return lines[:i], "\n".join(lines[i:])
    return lines, ""


def meta_blockquote(head: list[str]) -> str:
    """把元信息压成一段引用块，去掉重复的 H1 标题与分隔线。"""
    kept = []
    for ln in head:
        s = ln.strip()
        if not s or s.startswith("# ") or s == "---":
            continue
        kept.append(s.lstrip("> ").strip())
    return "\n".join(f"> {k}" for k in kept[:6])


def strip_tail_rules(text: str) -> str:
    return re.sub(r"\n-{3,}\s*$", "", text.rstrip()) + "\n"


NAV = """\
## 阅读导航

你要求的 11 个板块与本文档章节的对应关系如下。**章节编号沿用各分卷原编号**，`§x.y` 形式的引用在全文中仍然有效。

| # | 板块 | 所在位置 |
|---|---|---|
| 1 | 游戏定位与目标用户 | 第一部分 §1（含 §1.5 功能优先级总表）|
| 2 | 核心玩法与游戏循环 | 第一部分 §2（含 §2.4 时间标度）|
| 3 | 修仙境界与成长体系 | 第一部分 §3（境界阶梯 / 经验曲线 / 突破判定 / 冲关准备）|
| 4 | 角色属性与功法设定 | 第一部分 §4.1–4.7 ＋ 第二部分 §3.4 属性字典（单一事实源）|
| 5 | 装备加成与道具 | 第一部分 §5（6 槽位 / 词条池 / 强化精炼镶嵌祭炼）|
| 6 | 剧情与主线支线任务 | 第一部分 §6（含 §6.0 等级↔卷↔区域映射、§6.6 区域清单）|
| 7 | 存档与进度机制 | 第一部分 §7（服务端权威存档 / 离线收益 / 轮回）|
| 8 | 用户界面与交互流程 | 第一部分 §8（12 界面 / 交互流程图 / 排版规范）|
| 9 | 技术架构与模块划分 | 第二部分 §1 选型 ＋ §2 总体架构 ＋ §3 模块与目录 ＋ §4 核心引擎 ＋ §6 API |
| 10 | 数据存储方案 | 第二部分 §5（DDL / YAML 配置 / 存档序列化 / 并发一致性）|
| 11 | 可扩展性与后续迭代规划 | 第二部分 §7（扩展点 / 路线图 / MVP 裁剪 / 风险）|
| 附 | IP 与合规 | 第一部分 §9（定性、风险、15 组改名词表）|
| 附 | 工程硬约定 | 第二部分 附录 A（属性命名、单位、生成列等）|
"""

CROSSCHECK = """\
## 交叉核对记录

产品与技术两份文档并行编写，合并前做了字段级交叉核对，共发现并修正 **14 处硬冲突**。这些冲突单看任一份文档都发现不了，记录在此以便追溯：

| # | 冲突 | 错在哪 | 裁决 |
|---|---|---|---|
| 1 | 存档权威方 | PRD 写「浏览器 IndexedDB 主存」，技术方案是 SQLite 服务端 DDL | 服务端 SQLite 唯一权威；浏览器只存 UI 偏好。理由：PRD 自己要求的防作弊（时间倒退检测 / 种子不可刷 / 收益上限）在纯客户端全部失效 |
| 2 | 基础属性数量 | PRD 10 项，DDL 只有 6 项（缺根骨、悟性、道心、因果）| 扩为 10 项，`str/con/spr/sen/bon/wis/agi/luk/mnd/kar`；`insight`、`power` 双双重命名废弃 |
| 3 | 功法槽位 | PRD 心法 1 ＋ 攻/防/辅各 3 ＝ 10 槽，DDL 只有 4 槽 | 0=心法，1–3=攻击，4–6=防御，7–9=辅助，部分唯一索引兜底 |
| 4 | 装备槽枚举 | PRD 有本命法器、储物戒，DDL 是 boots、talisman | 统一 6 槽并补 `natal_level`（祭炼 0–100），后两槽用部分唯一索引强制唯一 |
| 5 | 修为曲线 | `Exp(L)=100×L^1.9×K` 实算累计 **5.14 亿**，文档写 3.1 亿；节点 36/54/72 偏差 +33.6%/+22.9%/−13.3% | 指数改 **1.78**，累计 3.03 亿（−2.3%），K 表整数不动；节点表由脚本生成，禁止手改 |
| 6 | 时间标度 | 同一份文档里「1 游戏日 = 60 现实秒」与「1 现实分钟 = 12 游戏日」并存，差 12 倍；后又叠加 `ACTION_SPEEDUP` 成为三套时钟 | 收敛为 `1 游戏日 ≡ 60 现实秒`（唯一换算）＋ 行动轨不映射现实时间 ＋ 离线轨 `1 现实小时 = 60 游戏日`。`ACTION_SPEEDUP` 删除 |
| 7 | 离线与寿元 | 「离线不推进 age_days、不消耗寿元」 | 改为**推进并消耗**（剩余 1 年即停、不判死）。原设计下离线零成本拿产量、在线每次闭关都付寿元，离线严格优于在线，寿元系统失效 |
| 8 | 修为速率单位 | 早期为「修为/秒」，按当时换算一次闭关产出 52,200，而锻血全境仅需约 4,700 | 单位定为「修为/游戏日」，`一次闭关产出 = ExpRate × 30`；全周目 871 次闭关、约 72.6 游戏年 |
| 9 | 等级派生 | 文档用 Lv.1–100，库里只有 realm_major/minor，缺 L 无法查 K 表 | `L = 9×(major−1)+minor`，major/minor 均 1-based，大帝 Lv.100 特例；`level` 用 SQLite 生成列，物理上不可能漂移 |
| 10 | 境界命名 | 正文用原著名，§9.3 又要求用原创名，工程师不知道数据表用哪套 | 主名用本作用名（锻血/辟府/化灵/刻纹/布阵/尊者/燃灯/归一…），原著名只作对照列，禁止进入代码与界面 |
| 11 | 体质命名 | §9.3 第 9 行写成「太古战体 → 太古战体」，两端同词；同时把「太古战体」列入禁用词，与上线名打架 | 统一为 **荒天战体**（与书名《荒天纪》同源）；MVP 三体质权重配平为 70/29/1 |
| 12 | 前端栈 | PRD 抬头写 Vite/React/MUI（模板残留），技术方案是服务端渲染 | 以技术方案为准：FastAPI ＋ Jinja2 ＋ HTMX ＋ Alpine ＋ 预编译 Tailwind |
| 13 | 合规词泄漏 | 架构师原先把原著名词写进随包发布的 `terms.yaml`，等于禁用词照样进发行版 | 拆为 `terms.yaml`（发布）＋ `blacklist.txt`（发布）＋ `ref_map.yaml`（禁打包，仅法务自查），并加打包闸门 |
| 14 | 节点值手算错 | 汇编时给出的 9 个节点值中有 3 个（L=9 / L=18 / L=90）与公式实算不符 | 由产品经理逐位复算纠正为 4,995 / 30,878 / 9,029,691；累计 303,005,690。**结论：所有数值表一律由脚本生成，任何人（含主理人）不得手写** |
"""


def main() -> None:
    prd_meta, prd_body = split_head(PRD.read_text(encoding="utf-8"))
    tech_meta, tech_body = split_head(TECH.read_text(encoding="utf-8"))

    parts = [
        "# 《荒天纪》需求文档（完整版）\n",
        "> 文字模拟修仙网页游戏 · Python 技术栈 · 世界观参考辰东《完美世界》（致敬向同人改编）\n",
        f"> 合并自 `{PRD.name}` 与 `{TECH.name}`，章节编号沿用原稿，未重排。\n",
        "> 合并日期：2026-10-21 ｜ 汇编：交付总监 齐活林\n",
        "\n---\n",
        NAV,
        "\n---\n",
        CROSSCHECK,
        "\n---\n",
        "# 第一部分 · 产品需求（PRD）\n",
        meta_blockquote(prd_meta) + "\n",
        strip_tail_rules(prd_body.rstrip()),
        "\n---\n",
        "# 第二部分 · 技术方案\n",
        meta_blockquote(tech_meta) + "\n",
        strip_tail_rules(tech_body.rstrip()),
    ]

    OUT.write_text("\n".join(parts), encoding="utf-8")

    text = OUT.read_text(encoding="utf-8")
    print(f"写入 {OUT}")
    print(f"行数 {len(text.splitlines())} ｜ 字符 {len(text)}")
    print(f"H1 {text.count(chr(10) + '# ')} ｜ H2 {text.count(chr(10) + '## ')} ｜ H3 {text.count(chr(10) + '### ')}")


if __name__ == "__main__":
    main()
