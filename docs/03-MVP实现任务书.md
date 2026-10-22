# 《荒天纪》v1.5 · MVP 实现任务书（团队共享基线）

> 本文件由主理人（齐活林）在开工前锁定，作为产品/架构/工程/QA 的唯一共享口径。
> 需求源：`docs/00-需求文档-完整版.md`（3727 行基线）+ `docs/00-需求文档-完整版-v1.5.md`（最新增量）。
> 冲突时优先级：**v1.5 增量 > 完整版基线 > 02-技术方案**。

---

## 1. 本次交付目标

交付一个**可直接启动、可完整游玩、可存档读档**的文字模拟修仙网页游戏，覆盖 v1.5 文档中 MVP（P0）全部需求，并把已有的「宗门任务 / 贡献 / 散修钱包 / 禁地」后端切片**完整接入主游戏**（不是并存的两套系统）。

验收以「玩家从创角到 Lv.18、入宗门或走散修、做宗门任务、兑换功法、探索 2 处禁地、打完 19 个主线任务」为一条可跑通的完整链路。

---

## 2. 现有代码盘点（必须复用，不得推倒重写）

| 模块 | 现状 | 处理方式 |
|---|---|---|
| `backend/app/db/sect_contribution.sql` | 宗门/散修/禁地/物品主表 DDL，`character` 仅为占位表 | **保留**；新增 `game.sql` 建完整 `character` 表，启动时先执行 `game.sql` 再执行宗门 DDL（`IF NOT EXISTS` 兼容） |
| `backend/app/db/repository.py` | 宗门任务/好感/钱包/禁地/物品主表 Repository + 种子数据 | **保留并复用**；`seed_quest_pool()`、`seed_techniques()` 启动时调用 |
| `app/services/quest_service.py` | 宗门任务接取/完成、散修好感度、钱包累积 | **直接复用**；上层包装 `SectGameService`，把 age_days/game_year 传入并同步奖励到主游戏 |
| `app/services/exchange_service.py` | 兑换资格校验 + 扣贡献 | **复用校验**；在游戏层包装，兑换成功后**真正发放物品/功法**到背包 |
| `app/services/forbidden_land_service.py` | 2 处禁地进入/扣贡献/掉落 | **复用**；游戏层增加 `path_id == sansxiu` 前置校验、Boss 战斗、掉落入包、功法学习 |
| `app/config/loader.py` + `data/content/sect/quest_config.yaml` | 阈值外置 | **保留**；不破坏既有 28 个测试 |
| `backend/tests/*` | 28 个既有测试 | **必须继续全绿**；新增测试放 `backend/tests/game/` |

> 关键约束：既有宗门 Service 的入参/返回值/异常码不变；所有扩展都在新层完成。

---

## 3. 技术栈与目标平台（锁定）

| 项 | 决策 | 说明 |
|---|---|---|
| 平台 | **桌面 + 移动自适应网页** | 单页应用式布局，服务端权威 |
| 后端 | Python 3.13 + FastAPI + Uvicorn | 单进程，端口 **5088** |
| 模板 | Jinja2 服务端渲染首屏 | 不引入 npm / 构建链 |
| 前端 | **原生 ES Module JS + CSS**（无 CDN 依赖） | 文档原定 HTMX+Alpine；离线环境无法保证 vendor 文件可得，改用等价的原生 fetch + 局部 DOM 更新，交互能力不降级。此偏离需在 README 说明 |
| 数据库 | SQLite（WAL） | 服务端唯一权威；`data/game.db` |
| 配置 | YAML（中文注释） | 启动期加载 + 校验 + 冻结 |
| 随机 | 自研 `RngService`：`seed + counter + stream`，HMAC-SHA256 派生 | 可复现、防刷新重roll；counter 与业务同事务落库 |
| 测试 | pytest + httpx | 既有 28 + 新增 ≥ 40 |
| 安全 | 参数化 SQL、Jinja2 自动转义、导入签名校验、输入白名单 | 见 §9 |

---

## 4. MVP 范围（v1.5 §0.1 / §0.4 锁定）

### 4.1 必做（P0）

| # | 模块 | 范围 |
|---|---|---|
| 1 | 创角 | 姓名、性别（文字描述）、**5 种体质（凡体 50% / 灵体 30% / 剑骨 10% / 药体 8% / 玄阵心 2%）**、资质 1–10 品、悟性 10–30、5 种性格、**自由加点 5 点（可分配到 10 项属性，单项 ≤ +3）**；可重掷 3 次；末页告知「宗门/方向/散修」三路径 |
| 2 | 境界 | Lv.1–18（锻血 1–9、辟府 10–18），12 境配置全量存在但 `enabled` 只开 2 境 |
| 3 | 修为 | `Exp(L)=floor(100×L^1.78×K)`；ExpRate 七层乘算（含方向系数）；**闭关耗时按境界档位查表**（锻血 1 个月=30 日 / 辟府 3 个月=90 日，见 `docs/12`）；单次最多连升 5 级；溢出保留；支持持续闭关（批量） |
| 4 | 行动 | **档位查表**：`days = round_half_up(base_days × tier_days / 30)`，`tier_days` 取自 `realms.yaml.time_tier_days`（1 月/3 月/6 月/1 年/3 年/5 年/8 年/10 年/20 年/100 年/1000 年/10000 年）；闭关 base 30 / 历练 15 / 采药 10 / 切磋 3 / 打坐 10 / 突破 3 |
| 5 | 突破 | 冲关准备（丹药/护法/吉日/灵地，上限 +30%）、连败补偿、5 连败必成、三档惩罚、根基评价、寿元 <10 年禁突破 |
| 6 | 属性 | 10 基础 + 13 衍生；战力 CP；软上限 |
| 7 | 功法 | 41 部全量配置；学习/参悟/装备（心法 1 + 攻/防/辅各 3）；品阶系数、层数 1–9、越阶惩罚、走火入魔 |
| 8 | 战斗 | 回合制 30 回合；普攻/术法/防御/道具/逃跑；伤害/破防/暴击/闪避/先手/连战惩罚/敌方 AI；战斗会话可刷新恢复 |
| 9 | 装备 | 6 槽；7 档稀有度；主副词条；强化 +5；精炼（MVP 可选）；装备/卸下 |
| 10 | 丹药/材料 | 6 种丹药 + 4 材料 + 5 消耗品；堆叠；使用/出售 |
| 11 | 背包 | 60 格 + 储物戒扩容；分类页签 |
| 12 | 区域 | 20 区域全量配置；MVP 开放 6 个（断槐村/村后山/药坡/青云宗外门/灵脉溪谷/辟府秘境）；前往耗时 + 环境系数 |
| 13 | 事件 | **20 条事件**可触发；35% 触发率；2–4 选项；属性/资源/因果/时间效果；30 游戏月不重复 |
| 14 | 主线 | **19 个任务**（卷一 10 + 卷二 9），目标类型：对话/闭关次数/击杀/采集/交付/突破/入宗/定方向；奖励 + 解锁 |
| 15 | 宗门 | 6 宗门；辟府 Lv.10 触发「乱世择门」；各有入门条件；贡献任务（复用）+ 4 部独有功法兑换（共 24 部）；方向系统 5 系 |
| 16 | 散修 | 第七条路径；无方向加成；奇遇池 +10% 触发；3 部独家功法可得；散修贡献点独立钱包；入宗时 50% 转换 |
| 17 | 禁地 | 10 处配置；**MVP 启用 2 处**（古井剑渊/草木缘渊，沿用 v1.7 既有实现）；散修限定；Boss 战；首通必得功法；Boss 掉落入包 |
| 18 | 新手引导 | 前 3 步（创角 → 序章 → 首次闭关），可跳过非强制项 |
| 19 | 存档 | 3 手动 + 1 自动；服务端快照；导出/导入 JSON（含 schema/content/checksum）；乐观锁 |
| 20 | 界面 | 12 个界面全部可达；暗色水墨风；响应式；伤害飘字/修为条/寿元警戒/事件弹窗 |

### 4.2 不做（P1/P2，明确排除）

离线收益、轮回/多周目、成就/图鉴、道相、神通独立系统、炼丹炼器独立玩法、州域势力战、云存档、多人。
配置位可预留，但 UI 与接口不承诺。

### 4.3 关键裁决（文档内部冲突时）

| 冲突 | 裁决 |
|---|---|
| §0.4「体质 5 种」vs §4.7「MVP 3 种」 | 以 **v1.4 §4.7.1** 为准：凡体 50% / 灵体 30% / 剑骨 10% / 药体 8% / 玄阵心 2%，权重和 100；全部 `enabled`。体质提供属性加成、修炼系数、突破加成与方向适配（1.3/1.0/0.8） |
| §0.1「装备 3 槽」vs §5.1「6 槽」 | 实现 **6 槽**（武器/护甲/头冠/饰品/本命/储物戒），强化上限 **+5** |
| v1.5「禁地 MVP 不开」vs 既有 v1.7 已开 2 处 | **开 2 处**，但强绑定散修身份；配置文件 `enabled: true` 可一键关闭 |
| §3.3 单次闭关修为（辟府 8,552）vs v1.5 表（2,272） | 以**公式 + v1.5 单次闭关值**为准；v1.5 节奏表「22 次」与「181,871/2,272=80 次」自相矛盾，属文档算术错误。工程按公式（ExpRate 内部保留浮点，只在最终结算 floor 一次）：锻血单次约 1,305（15 次）、辟府约 2,466（74 次），MVP 合计 **89 次**，远低于 900 红线；用「持续闭关」批量降低点击成本。 |
| §6.0 主线 19 任务 vs 卷一示例 10 任务 | 卷一 10 + 卷二 9 = **19**，逐条配置化，不写死文案分支 |

---

## 5. 目录与分层（目标结构）

```
cultivation-game/
├── run.py                      # 启动入口（uvicorn，5088）
├── requirements.txt
├── README.md                   # 启动/玩法/配置/接口/偏离说明
├── data/
│   ├── game.db                 # 运行时生成
│   └── content/
│       ├── manifest.yaml
│       ├── game.yaml           # 全局：时间/修为/战斗/突破/背包/存档
│       ├── realms.yaml         # 12 境（enabled 前 2）
│       ├── techniques.yaml     # 41 部（含宗门/散修/禁地）
│       ├── items.yaml          # 丹药/材料/消耗品
│       ├── equipments.yaml     # 装备模板 + 词条池
│       ├── physiques.yaml
│       ├── talents.yaml
│       ├── personalities.yaml
│       ├── regions.yaml        # 20 区域
│       ├── enemies.yaml        # 怪物/Boss
│       ├── events.yaml         # 20 事件
│       ├── quests_main.yaml    # 19 主线
│       ├── sects.yaml          # 6 宗门 + 24 独有功法 + 方向树
│       ├── sansxiu.yaml        # 散修路径 + 奇遇池
│       └── forbidden_lands.yaml# 10 禁地（enabled 2）
├── backend/
│   ├── app/
│   │   ├── main.py             # create_app：DB/content/路由/异常/静态
│   │   ├── core/               # rng / clock / errors / response
│   │   ├── content/            # loader / registry / validators
│   │   ├── domain/             # level / stats / realm / combat / event / quest
│   │   ├── db/                 # game.sql + game_repository.py + 既有 repository
│   │   ├── services/           # game_service / action / breakthrough / combat / save / bootstrap / sect_bridge
│   │   ├── api/                # game_router + 既有 sect_router
│   │   └── web/                # templates + static
│   └── tests/                  # 既有 + game/
└── docs/                       # 本任务书 + 设计文档 + 验收记录
```

---

## 6. 核心数据模型（必须满足）

- `character`：id、name、gender、personality、physique、talent、level、realm_index、realm_stage、cultivation、exp_overflow、hp、mp、age_days、lifespan_max_days、spirit_stone、karma、dao_heart、str/con/spr/sen/bon/wis/agi/luk、region_id、path_id、sect_id、direction_id、foundation、fail_streak、realm_attempts、status、rng_seed、rng_counter、last_settle_at、version、created_at
- `character_technique`：char_id、tech_id、layer、sense、equipped_slot
- `character_equipment`：instance_id、char_id、template_id、slot、rarity、enhance、affixes_json、equipped
- `character_inventory`：char_id、item_id、item_type、qty（材料/丹药/消耗品堆叠；装备走 equipment 表）
- `character_quest`：char_id、quest_id、status、step、progress_json
- `character_flag`：char_id、key、value（用于主线/路径/一次性事件）
- `character_relation`：char_id、npc_id、favor、karma
- `combat_session`：char_id、session_json、status、updated_at
- `save_slot`：slot_id（1–3 手动 + 0 自动）、char_id、snapshot_json、content_version、checksum、created_at
- `event_log` / `milestone_log`：限长，供 UI 日志与验收
- `meta`：k/v（active_character_id、content_version 等）

所有写操作单事务；`character.version` 乐观锁；`rng_counter` 同事务递增。

---

## 7. 接口契约（必须有）

页面：`GET /`（游戏主界面）、`GET /health`。

JSON API 前缀 `/api/v1`：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/bootstrap` | 创角（校验加点=5、重掷次数） |
| GET | `/state` | 主界面聚合快照（角色/修为/寿元/区域/任务/日志） |
| POST | `/action` | 闭关/历练/采药/切磋/打坐（返回结算 + 事件/战斗会话） |
| POST | `/breakthrough/preview` | 成功率明细 |
| POST | `/breakthrough` | 执行突破 |
| POST | `/event/choose` | 事件选项 |
| GET | `/combat` \| POST `/combat/action` | 战斗会话 / 回合行动 |
| GET | `/character` | 属性 + 衍生 + 明细 |
| GET | `/techniques` \| POST `/techniques/learn` `/equip` `/meditate` | 功法 |
| GET | `/inventory` \| POST `/inventory/use` `/sell` `/equip` `/unequip` `/enhance` | 背包装备 |
| GET | `/quests` \| POST `/quests/claim` | 主线/支线 |
| GET | `/map` \| POST `/map/travel` | 区域 |
| GET | `/sect` \| POST `/sect/join` `/sect/leave-direction` `/sect/quest/take` `/complete` `/exchange` `/sansxiu/encounter` `/sansxiu/convert` | 宗门/散修（内部调既有 Service） |
| GET | `/forbidden` \| POST `/forbidden/enter` | 禁地 |
| GET | `/saves` \| POST `/saves/save` `/load` `/export` `/import` `/delete` | 存档 |
| GET | `/logs` | 事件/里程碑 |

统一响应：`{ok: bool, data: any, error: {code,message,detail}?}`；HTTP 状态与错误码对齐既有约定。

---

## 8. 数值红线（QA 必测）

1. 创角加点必须正好 5 点，可分配到 10 项属性、单项 ≤ +3；负数/超额/单项超 3 被拒。
2. 修为曲线单调；Lv.1→2 需 63；Lv.9→10 需 3,187；Lv.18 封顶。（`K` 见 `realms.yaml.exp_k_bp`，锻血 6,381 / 辟府 12,046）
3. 单次闭关最多连升 5 级，溢出保留且不丢失。
4. 行动耗时 = `round_half_up(base_days × tier_days / 30)`，`tier_days` 按境界查表；锻血闭关 30 日、辟府闭关 90 日；`age_days` 只增不减。批量闭关每轮前须校验剩余寿元，超出即截断。
5. 突破成功率 clamp 5%–95%；连败补偿 +15%/次、上限 +30%；5 连败后第 6 次必成。
6. 寿元 < 10 年禁止突破；寿元 ≤ 0 触发坐化结局。
7. 战斗 30 回合上限；逃跑率 clamp 5%–90%，BOSS −30%；连续逃跑失败 3 次禁逃。
8. 宗门加入需 Lv.10 + 条件（剑宗试剑连胜 3 场 / 法宗悟性≥25 / 丹宗灵药≥3 / 阵宗神识≥25 / 体宗力量≥12 / 魔道因果≤−100）。
9. 散修不可接 high 任务（既有硬规则）；散修入宗贡献 50% 转换且钱包清零。
10. 禁地仅散修可进；每游戏年 ≤3 次；冷却 30 日；首通必得指定功法。
11. 存档写入 version 冲突返回 409；导入校验 checksum 失败拒绝。
12. 刷新页面不得改变随机结果（同一 rng_counter 不重roll）。

---

## 9. 安全基线（安全复核按此检查）

| 面 | 要求 |
|---|---|
| SQL | 全部参数化；禁止 f-string 拼 SQL；表名/列名不得来自用户输入 |
| 输入 | Pydantic 白名单枚举；数值范围校验；字符串长度限制；路径/ID 不得拼文件路径 |
| XSS | Jinja2 自动转义开启；前端渲染用户输入（角色名/道号）必须 `textContent` 或转义，禁止 `innerHTML` 直插 |
| 存档导入 | HMAC-SHA256 校验 + schema/content 版本 + 大小上限（≤2MB）+ JSON 结构校验；失败拒绝且不落库 |
| 并发 | 写接口乐观锁；重复提交幂等（request_id 可选）；战斗会话归属校验 |
| 权限 | 所有角色操作校验 `character_id` 属于当前 active slot，禁止越权读写他人存档 |
| 随机 | 禁止 `random` 模块直接用于奖励/突破；统一 RngService，种子不暴露给前端 |
| 敏感信息 | 无硬编码密钥；签名密钥从环境变量读取，默认开发值需在 README 标注 |
| 依赖 | 仅使用已安装/requirements 锁定版本；不引入 CDN 运行时依赖 |

---

## 10. 交付物

1. 可运行代码（`run.py` 一条命令启动）。
2. 全部 YAML 内容（中文，含注释）。
3. 测试：既有 28 + 新增 ≥ 40，全绿。
4. `README.md`：启动、玩法、目录、配置、API、已知边界与偏离说明。
5. `docs/04-实现说明.md`：整体结构 + 关键逻辑（公式/状态机/对接点）+ 需求覆盖矩阵。
6. 安全复核记录（对话内汇总，不强制落盘）。
