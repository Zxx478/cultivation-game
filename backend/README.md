# 宗门任务与贡献系统后端（子系统）

> **完整游戏入口见仓库根目录 `README.md`**（`python run.py` 启动 127.0.0.1:5088）。
> 本目录下的宗门任务/贡献/散修钱包/禁地子系统已作为子模块接入主游戏，
> 由 `backend/app/services/sect_bridge_service.py` 桥接；这里的路由与 Service 仍可独立测试。

> 《荒天纪》v1.7 工程：实现 §4.9 宗门任务、散修参与宗门任务的"散修身份+好感度"机制、§4.12 禁地 MVP 开放。
>
> **v1.7 在 v1.6 基础上完成 3 项延伸升级**：
> - 物品 tier 分类由"字符串关键词匹配"升级为查 technique/pill/equipment 表的 tier 字段，关键词保留为 fallback
> - 散修禁地消费源由"∑ 6 宗门散修贡献点"升级为独立 `sansxiu_wallet.general_contribution` 字段，散修完成任务时按 `sansxiu_general_share` 比例同步累积
> - 任务池权重每游戏年刷新，同年内稳定、跨年才刷新，惰性加载

## 目录结构

```
cultivation-game/
├── data/content/sect/quest_config.yaml   # 所有阈值参数外置
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                       # FastAPI 入口
│   │   ├── api/
│   │   │   └── sect_router.py            # /api/v1/sect/* 路由
│   │   ├── config/
│   │   │   └── loader.py                 # YAML 配置加载 + 校验
│   │   ├── db/
│   │   │   ├── repository.py             # SQLite 数据访问层
│   │   │   └── sect_contribution.sql      # DDL
│   │   ├── models/
│   │   │   └── entities.py               # 领域实体
│   │   ├── schemas/
│   │   │   └── quest.py                  # Pydantic v2 请求/响应模型
│   │   └── services/
│   │       ├── errors.py                 # 错误码常量
│   │       ├── quest_service.py          # 任务接取/完成
│   │       ├── exchange_service.py       # 兑换资格校验
│   │       └── forbidden_land_service.py # 禁地探索
│   └── tests/
│       ├── conftest.py
│       ├── test_quest_service.py         # 11 个测试
│       ├── test_exchange_service.py      # 6 个测试
│       └── test_forbidden_land_service.py# 7 个测试
```

## 核心业务规则

### 1. 任务分级与奖励范围（YAML `quest.levels`）

| 等级 | 贡献奖励区间 | 散修好感度奖励 | 当日上限 |
|---|---|---|---|
| low  | 10–50   | +5~+15 | 5 |
| mid  | 60–150  | +10~+20 | 3 |
| high | 160–400 | —（散修不可接） | 1 |

### 2. 弟子任务解锁

- 入宗即得 low
- 完成 `quest.disciple_unlock.mid_required_low_quests`（默认 10）个 low → 解锁 mid
- 完成 `quest.disciple_unlock.high_required_mid_quests`（默认 15）个 mid → 解锁 high

### 3. 散修机制（核心创新点）

- 散修对各 6 宗门独立维护 `reputation`（好感度） + `contribution`（贡献点）
- 初始可接所有宗门的 low 任务
- 对某宗门好感度 ≥ `sansxiu_unlock.mid_required_reputation`（默认 200）→ 解锁该宗门 mid 任务
- **永远不可接 high 任务**（硬规则，service 层强制拒绝 ERR_QUEST_SANXIU_CANNOT_TAKE_HIGH）
- 好感度上限 `reputation_max`（默认 1000），防止刷爆

### 4. 兑换资格校验

#### 弟子兑换（`ExchangeService.check_and_exchange`）

只校验：贡献点 ≥ 物品 cost_contribution，且必须属于本宗弟子。

#### 散修兑换

双重校验：
1. 贡献点 ≥ cost_contribution
2. 对该宗门好感度 ≥ 兑换 tier 阈值：
   - 凡阶（low）≥ 100
   - 灵阶（mid）≥ 250
   - 圣阶及以上（high）≥ 500

`_classify_item_tier` 用 item_id 关键词匹配做 tier 分类（V1.1 改为查 technique 表）。

### 5. MVP 禁地（开放 2 处）

| 禁地 | 境界门槛 | 进入消耗 | Boss | 首通奖励 |
|---|---|---|---|---|
| 古井剑渊 `gujing_jianyuan` | 锻血 Lv.9+ (realm_seq=1) | 50 贡献点 | 衰败剑灵 | 《青莲剑歌·卷一》功法 |
| 草木缘渊 `caomu_yuanyuan` | 辟府 Lv.10+ (realm_seq=2) | 200 贡献点 | 老药叟分身 | 《草木心经》功法 |

进入规则：
- 每游戏年 ≤ 3 次
- 冷却 30 游戏日
- 散修掉率 +10%（仅 first_clear 与 sansxiu_extra_bonus 生效）
- 散修使用 6 宗门散修贡献点总和作为通用贡献点

## API 端点

所有前缀 `/api/v1/sect`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/quests/take` | 接取任务（含资格校验） |
| POST | `/quests/complete` | 完成任务（事务一致 + 自动解锁判定） |
| POST | `/exchange` | 兑换物品（弟子 + 散修通用接口） |
| POST | `/forbidden-land/enter` | 探索禁地 |
| GET  | `/status/{character_id}/{sect_id}` | 查询玩家在某宗门的进度快照 |

## 错误码

| 错误码 | HTTP | 说明 |
|---|---|---|
| 1001 | 400 | 任务等级非法 |
| 1002 | 400 | 宗门 ID 非法 |
| 1003 | 429 | 任务当日上限已达 |
| 1004 | 403 | 当前身份未达接取资格 |
| 1005 | 403 | 散修不可接 high 任务 |
| 1006 | 404 | 任务不存在 |
| 1007 | 403 | 任务不属于该角色 |
| 1008 | 409 | 任务状态非 accepted |
| 1009 | 410 | 任务已过期 |
| 2002 | 402 | 贡献点不足 |
| 2003 | 403 | 散修好感度不足 |
| 2004 | 403 | 散修无法兑换圣阶/帝阶 |
| 3001 | 404 | 禁地不存在 |
| 3002 | 403 | 境界未达 |
| 3003 | 429 | 本年次数已满 |
| 3004 | 429 | 冷却未过 |
| 3005 | 402 | 贡献点不足支付 |

## 本地运行

### 1. 安装依赖（隔离 venv）

```bash
# 用项目预置的 Python 3.13.12
"C:/Users/周/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m venv \
  "C:/Users/周/.workbuddy/binaries/python/envs/default"

"C:/Users/周/.workbuddy/binaries/python/envs/default/Scripts/pip.exe" install \
  fastapi pydantic pyyaml pytest httpx uvicorn
```

### 2. 跑测试

```bash
cd "D:/ai工坊/cultivation-game/backend"
"C:/Users/周/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m pytest tests/ -v
```

预期输出：23 个测试全部通过。

### 3. 启动服务

```bash
cd "D:/ai工坊/cultivation-game/backend"
"C:/Users/周/.workbuddy/binaries/python/envs/default/Scripts/uvicorn.exe" app.main:app --reload --port 5088
```

访问 http://localhost:5088/docs 查看 Swagger UI。

## 配置调参

所有阈值参数外置于 `data/content/sect/quest_config.yaml`，启动时由 `app.config.load()` 校验后冻结为不可变 dataclass。

调参流程：
1. 修改 YAML
2. 重启服务（或调用 `app.config.reload()`）
3. 跑测试验证一致性

## 工程约束

- **乐观锁**：所有表带 `version` 字段，upsert 时 `version = version + 1`，防并发覆盖
- **事务一致**：任务完成是单事务，确保 `sect_active_quest.status` + `sect_quest_record.insert` + `sect_member/upsert` 三个写操作原子
- **FK 一致**：所有 character_id 必须存在于 character 表（生产由 character DDL 建表）
- **配置校验**：启动时校验 low<mid<high 严格递增、≤2 处禁地（MVP），失败即拒绝启动

## 后续路线

| 阶段 | 任务 |
|---|---|
| ~~v1.6.1~~ | ~~接入真实 character 表~~（v1.7 已用占位 DDL 兼容） |
| ~~v1.7~~ | ~~物品 tier 查表、散修通用钱包、任务池年度刷新~~（已实现） |
| v1.7.1 | GameClock 服务接入：跨年时调用 `QuestPoolRepository.refresh_yearly_weights(year)` |
| v1.7.2 | 散修入宗转换：在 §4.11.4 实现"散修→入宗贡献点 50% 转换"业务流程 |
| v1.7.3 | 物品表对接 v1.5 §5 装备样例与 §4.4 功法表完整数据 |
| v1.8 | 禁地扩展到 10 处（圣阶、帝阶禁地解锁） |
| v1.9 | 宗门战争系统（§4.9.3 中"宗门战争"任务类型） |

## v1.7 详细说明

### 延伸点 1：物品 tier 查表

**功能目标**：从字符串关键词匹配升级为查表权威源。

**实现**：
- 新增 3 张表：`technique` / `pill` / `equipment`，含 `tier` 字段
- 新增 3 个 Repository：`TechniqueRepository` / `PillRepository` / `EquipmentRepository`
- `app/services/exchange_service.py` 新增 `classify_item_tier(item_type, item_id)`：先查表，查不到回退关键词
- 新增错误码 `ERR_EXCHANGE_ITEM_NOT_FOUND = 2005`，与"tier 不符"区分
- `ExchangeService.check_and_exchange` 新逻辑：cost_contribution=0 时自动用 base_price

**约束**：
- 查表失败时回退关键词方案（兼容 MVP 阶段无完整数据的场景）
- `technique_recipe` 类型不强制查表，永远走关键词
- 物品表数据来自 v1.5 §4.4 / §4.9.5 共 41 部功法 + 12 种丹药 + 11 件装备

### 延伸点 2：散修通用贡献点字段

**功能目标**：禁地消费走独立字段，不再 ∑ 6 宗门散修贡献点求和。

**实现**：
- 新增 `sansxiu_wallet` 表：`character_id` / `general_contribution` / `total_earned` / `version`
- 新增 `SansxiuWalletRepository`：`get` / `upsert` / `add_contribution`
- 配置新增 `contribution.sansxiu_general_share.{low, mid, high}`（默认 0.3/0.5/0.0）
- 配置新增 `contribution.sansxiu_join_convert_rate`（默认 0.5，散修入宗时贡献点转换比例）
- `QuestService.complete` 散修分支：完成任务后按比例累积到 wallet
- `ForbiddenLandService` 散修分支：禁地消费从 `wallet.general_contribution` 扣减

**约束**：
- 弟子身份不写 wallet（只有散修才累积）
- 散修完成 low 任务累积 30%，完成 mid 任务累积 50%，high 任务不累积（散修不可接）
- 入宗时 `wallet.general_contribution × 0.5` 转入新宗 contribution，wallet 清零

### 延伸点 3：任务池年度刷新

**功能目标**：每游戏年刷新一次权重，同年内稳定，跨年才刷新。

**实现**：
- 新增 `sect_quest_pool_yearly` 表：`(game_year, sect_id, level_id, pool_id)` 主键 + `yearly_weight`
- `QuestPoolRepository.draw(sect_id, level, game_year)`：传入年份时按年度权重抽签，无年份时按默认 weight
- `QuestPoolRepository.refresh_yearly_weights(game_year)`：按 `base × random(0.5, 1.5)` 重新生成年度权重
- `QuestService.take` 新增 `game_year` 参数，首次进入该年时惰性加载权重

**约束**：
- 同一年内权重稳定（性能与一致性权衡）
- 权重不退化为 0（用 `max(1, base × random(0.5, 1.5))`，防止该任务本年度彻底不出）
- 惰性加载：第一次该年有任务接取时才触发刷新，避免无玩家的年份白白加载

## v1.7 新增测试（5 个，合计 28 个）

| 测试 | 覆盖点 |
|---|---|
| `test_sansxiu_exchange_item_not_found_rejected` | 物品表查不到时报 2005 |
| `test_sansxiu_exchange_auto_uses_base_price_when_cost_zero` | cost=0 时自动用 base_price |
| `test_sansxiu_general_contribution_accumulates_from_quest` | 散修完成任务累积到 wallet |
| `test_sansxiu_wallet_empty_for_disciple` | 弟子身份不写 wallet |
| `test_quest_pool_yearly_weights_refresh_per_game_year` | 任务池权重年度刷新 |
