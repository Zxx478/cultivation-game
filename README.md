# 荒天纪 · v1.5 MVP

一款以「锻血—辟府」为起点、十二境为长线的**文字模拟修仙网页游戏**。服务端权威（FastAPI + SQLite），前端为原生 ES Module + CSS，无 npm、无 CDN，断网可玩。

> 本仓库同时保留了早期「宗门任务 / 贡献 / 散修钱包 / 禁地」子系统，并已作为子模块接入主游戏。

---

## 1. 快速开始

```bash
cd cultivation-game
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt

# 启动（默认 127.0.0.1:5088）
python run.py
```

打开 <http://127.0.0.1:5088/>，首次进入为创角页。

- 数据文件：`data/game.db`（SQLite，自动创建）
- 存档签名密钥：优先读环境变量 `SAVE_SIGN_SECRET`；未设置时自动生成 `data/.save_secret`
- 换端口：`PORT=5090 python run.py`
- 换数据库：`HUANGTIANJI_DB=D:/tmp/game.db python run.py`

### 跑测试

```bash
python -m pytest backend/tests -q
# 126 passed
```

---

## 2. 已实现范围（对照 v1.5 文档）

| 系统 | 实现 |
|---|---|
| 创角 | 5 体质（凡体 50 / 灵体 30 / 剑骨 10 / 药体 8 / 玄阵心 2）、资质 1–10、悟性 10–30、5 性格、自由加点 5 点（单项 ≤3）、重掷 3 次 |
| 境界 | 12 境配置全量；MVP 开放锻血 1–9、辟府 10–18，Lv.18 封顶 |
| 修为 | `Exp(L)=floor(100×L^1.78×K)`；ExpRate 七层乘算（含方向系数）；单次闭关最多连升 5 级；溢出保留 |
| 行动 | 闭关 / 历练 / 采药 / 切磋 / 打坐；动态耗时 `base×(1+0.40×(境界−1))`；持续闭关（最多 10 连） |
| 突破 | 冲关准备（丹药/护法/吉日/灵地 ≤+30%）、连败补偿、5 连败必成、三档惩罚、根基评价、寿元 <10 年禁突破 |
| 属性 | 10 基础 + 13 衍生 + 战力 CP + 软上限 + 双主题面板 |
| 功法 | 41 部（通用 12 + 宗门 24 + 散修/禁地 5）；学习/参悟/装备（心法 1 + 攻防辅各 3） |
| 战斗 | 回合制 30 回合；普攻/术法/防御/道具/逃跑；先手、破防、暴击、闪避、连战惩罚、敌方 AI；刷新可续战 |
| 装备 | 6 槽；7 档稀有度；主副词条；强化 +5；穿戴/卸下；精炼预留 |
| 丹药/材料 | 6 丹药 + 4 材料 + 5 消耗品；使用/出售/堆叠 |
| 背包 | 60 格 + 储物戒扩容；分类页签 |
| 区域 | 20 区域配置，MVP 开放 6 个；环境系数进入修为公式 |
| 事件 | 20 条；35% 触发（散修 38.5%）；2–3 选项；属性/资源/因果/状态/时间效果；30 游戏月去重 |
| 主线 | 19 条（卷一 10 + 卷二 9）；13 种目标类型；自动解锁 + 领奖幂等 |
| 宗门 | 6 宗门；Lv.10「乱世择门」；入门条件、5 方向树、宗门任务（复用既有子系统）、24 部独有功法兑换 |
| 散修 | 第七条路径；无方向加成；3 条奇遇；独立贡献钱包；入宗 50% 转换 |
| 禁地 | 10 处配置、MVP 启用 2 处；散修限定；Boss 战；首通必得功法；年次数/冷却/贡献校验 |
| 存档 | 3 手动 + 1 自动；服务端快照；HMAC 校验；导出/导入；乐观锁 |
| 界面 | 首页（标题/玩法说明/开始游戏/存档选择）+ 15 个 pane、暗色/宣纸双主题、响应式（1440 / 375 无横向滚动）、事件/突破/战斗弹窗 |

---

## 3. 目录结构

```
cultivation-game/
├── run.py                      # 启动入口（127.0.0.1:5088）
├── requirements.txt
├── data/
│   ├── content/                # 16 个 YAML（唯一内容源）
│   │   ├── game.yaml           # 时间/修为/战斗/突破/背包
│   │   ├── realms.yaml         # 12 境
│   │   ├── techniques.yaml     # 41 部功法
│   │   ├── items.yaml          # 15 件道具
│   │   ├── equipments.yaml     # 6 槽 + 稀有度 + 词条
│   │   ├── physiques.yaml      # 5 体质
│   │   ├── sects.yaml          # 6 宗门 + 24 功法 + 5 方向树
│   │   ├── sansxiu.yaml        # 散修路径 + 3 奇遇
│   │   ├── forbidden_lands.yaml# 10 禁地展示元数据
│   │   ├── events.yaml         # 20 事件
│   │   ├── quests_main.yaml    # 19 主线
│   │   └── ...
│   └── game.db                 # 运行时生成
├── backend/
│   ├── app/
│   │   ├── main.py             # 装配：内容自检 → 建库 → 种子 → 路由 → 静态
│   │   ├── core/               # rng / clock / errors / response
│   │   ├── content/            # loader / registry / validators
│   │   ├── domain/             # level / attrs / realm / combat / event / quest
│   │   ├── db/                 # game.sql + game_repository + 既有 repository
│   │   ├── services/           # 行动/突破/战斗/事件/宗门桥接/存档/发奖
│   │   ├── api/                # game_router + 既有 sect_router
│   │   └── web/                # Jinja2 首屏 + 原生 JS/CSS
│   └── tests/                  # 126 个测试
├── docs/                       # 需求、设计、验收矩阵、UI 规范
└── data/content/sect/quest_config.yaml  # 既有宗门阈值（权威）
```

---

## 4. 核心机制速览

### 4.1 时间与修为

- 单时钟：`age_days`（整数游戏日），1 年 = 360 日，1 月 = 30 日。
- 行动耗时：`max(1, round(base × (1 + 0.40 × (realm_index − 1))))`。
  - 锻血闭关 30 日；辟府闭关 42 日。
- 修为产出：`floor(ExpRate × days)`，`ExpRate` 内部保留浮点，只 floor 一次。
- 修为需求：`floor(100 × L^1.78 × K)`，K 由 `realms.yaml.exp_k_bp` 配置。
- MVP 节奏：锻血约 15 次闭关、辟府约 74 次，合计约 89 次（远低于 900 红线）。

### 4.2 战斗

- 30 回合；先手 `身法×1.5 + 神识×0.5`；平手 rng 决定。
- 伤害 `max(atk×倍率 − def×0.6, atk×0.05) × 破防档 × 暴击 × 0.95–1.05`。
- 逃跑 45% + 先手差，clamp 5%–90%，BOSS −30%，连续失败 3 次禁逃。
- 战斗结束把气血/灵力写回角色；失败无还魂丹则 `dead`。

### 4.3 宗门 / 散修 / 禁地

- 入宗：Lv.10 + 宗门条件（剑宗试剑连胜 3 / 法宗悟性≥25 / 丹宗灵药≥3 / 阵宗神识≥25 / 体宗力量≥12 / 魔道因果≤−100）。
- 散修：无方向加成；奇遇 3 条；贡献独立钱包；入宗时 50% 转换（一次性）。
- 禁地：仅散修；每游戏年 ≤3 次；冷却 30 日；Boss 胜利后才扣贡献、置首通、发功法。

---

## 5. API 概览

统一前缀 `/api/v1`，响应 `{ok, data, error:{code,message,detail}}`。

| 分组 | 端点 |
|---|---|
| 创角/状态 | `GET /meta`（公开内容摘要）、`GET /bootstrap/candidate`、`POST /bootstrap/reroll`、`POST /bootstrap`、`GET /state`、`GET /character` |
| 行动/事件 | `POST /action`、`POST /event/choose` |
| 突破 | `POST /breakthrough/preview`、`POST /breakthrough` |
| 战斗 | `GET /combat`、`POST /combat/action` |
| 功法 | `GET /techniques`、`POST /techniques/learn|meditate|equip|unequip` |
| 背包 | `GET /inventory`、`POST /inventory/use|sell|equip|unequip|enhance` |
| 主线 | `GET /quests`、`POST /quests/claim` |
| 地图 | `GET /map`、`POST /map/travel` |
| 宗门 | `GET /sect`、`POST /sect/join|direction|quest/take|quest/complete|exchange` |
| 散修 | `POST /sect/sansxiu/encounter|convert` |
| 禁地 | `GET /forbidden`、`POST /forbidden/enter` |
| 存档 | `GET /saves`、`POST /saves/save|load|export|import|delete` |
| 日志 | `GET /logs` |

既有宗门子系统仍保留 `/api/v1/sect/*` 原始路由（任务/兑换/禁地直调），游戏 UI 走的是桥接层端点。

---

## 6. 安全说明

- **单机单人定位**：服务只绑定 `127.0.0.1`，未实现登录/多用户权限；不要直接暴露到公网。
- 所有 SQL 参数化；表名/列名只来自内部常量。
- 前端不使用 `innerHTML` 渲染用户输入，Jinja2 自动转义开启。
- 存档导入：HMAC-SHA256 + `schema_version` + `content_version` + `content_hash`，上限 2MB，失败不落库。
- 角色 `rng_seed` 仅存服务端与存档文件，不出现在 `/state`。
- 生产/跨设备：设置同一个 `SAVE_SIGN_SECRET`，否则两端无法互相导入存档。

---

## 7. 与文档的已知偏离

| 项 | 文档 | 实现 | 原因 |
|---|---|---|---|
| 前端 | HTMX + Alpine.js | 原生 ES Module + CSS | 离线环境无法保证 vendor 文件；交互等价 |
| 禁地开启 | v1.5 说 MVP 不开 | 开 2 处（散修限定） | 既有 v1.7 已实现，关闭会浪费已验证资产 |
| 持续闭关 | 未明确 | 支持 1–10 连 | 89 次闭关点击成本过高 |
| 强化材料 | 强化石 | 纯灵石 `200×(等级+1)` | MVP 道具表只有 15 条，避免再加货币 |
| 源码节奏值 | v1.5 表 2,272/22 次 | 公式 2,466/74 次 | 文档表与公式自相矛盾；以公式为准 |
| 结局/轮回/图鉴/道相/离线 | P1/P2 | 未实现 | 明确排除，不破坏 P0 主链 |
| 装备精炼 | MVP 可选 | 未实现（接口未开放） | 与 +5 强化闭环无关 |

---

## 8. 后续待办

1. **数值实测**：用 `tools/` 补一个 1000 局模拟脚本，验证 89 次闭关节奏与宗门/散修强度差。
2. **内容扩充**：卷三 15 任务、剩余 8 处禁地、45 条事件、更多敌人与装备模板。
3. **功法效果落地**：当前部分功法只影响修炼系数/战斗倍率，`attr_pct`、`免死`、`吸血` 等需逐条接入战斗引擎。
4. **方向节点**：已按累计贡献自动点亮并应用部分加成，剩余控制类效果待接入。
5. **存档迁移**：当前 `content_version` 不一致直接拒绝；后续补迁移链。
6. **多周目/结局**：`dead`/坐化已置位，但道途志、轮回继承、结局判定未做。
