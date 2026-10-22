# data/content · 内容层说明（v1.5.0）

本目录是《荒天纪》v1.5 的**唯一内容来源**。引擎（`backend/app/content/loader.py`）
按 `manifest.yaml` 的顺序读取 → 冻结为 dataclass → 启动自检 → 计算 `content_hash`。
所有数值改动只改 YAML，不改代码。文件全部 UTF-8，`yaml.safe_load_all` 可直接解析。

加载顺序：`game → realms → physiques → talents → personalities → techniques →
items → equipments → enemies → regions → events → quests_main → sects →
sansxiu → forbidden_lands`（共 16 个文件）

---

## 1. 逐文件 schema

| 文件 | 顶层键 | 条目字段（类型 · 必填 · 取值） |
|---|---|---|
| `manifest.yaml` | `content_version` / `schema_version` / `counts` / `files` / `external_refs` | `counts.*` int；`files[].order/name/role` |
| `game.yaml` | `time` / `action_base_days` / `exp` / `event` / `combat` / `breakthrough` / `creation` / `bag` / `save` / `attrs` / `path` / `forbidden` / `region_drop` | 全部为标量或一层嵌套；概率与系数一律 **bp 整数**（10000 = 100%） |
| `realms.yaml` | `realms[]` | `id` str 必填枚举；`display_name` str；`order` 1–12；`level_start/level_end` 1–100；`base_lifespan_years` int；`exp_k_bp` int；`power_coef` float；`breakthrough_base_bp` int；`enabled` bool；`per_level_growth{str,con,spr,sen,bon,agi}` int；`breakthrough_items[{item_id,qty}]`；`unlock_features[]` str |
| `physiques.yaml` | `physiques[]` | `id` 枚举（`fan_ti/ling_ti/jian_gu/yao_ti/xuan_zhen_xin`）；`weight_bp` 合计 10000；`cultivation_bp`；`attr_bonus_pct{...}`；`breakthrough_bp`；`special[{key,value,desc}]`；`affinity{body,sword,spell,alchemy,array}` ∈{1.5,1.3,1.0,0.8}；`init_attr_base` |
| `talents.yaml` | `talents[]` | `grade` 1–10；`coef_bp = 10000+(grade-5)*600`；`roll_weight_bp` 合计 10000 |
| `personalities.yaml` | `personalities[]` | `id`（含 `zhongyong`，对齐 DB 默认值）；`check_mod_bp{hard,wits,talk,retreat}` ∈{-500,0,800}；`option_preference` 合计 100 |
| `techniques.yaml` | `techniques[]` | `id` 枚举；`tier` fan\|ling\|bao\|sheng\|di；`sub_tier` xia\|zhong\|shang；`type` heart\|attack\|defense\|support；`required_realm_index` 1–12；`max_layer` 9；`source[]` general\|sect\|sansxiu\|forbidden；`sect_id` str\|null；`contribution_price` int\|null；`cultivation_multiplier_bp`；`layer_formula{base_coef_bp,growth_bp_per_layer}`；`skill{power_pct,mp_cost,cooldown,effect}` |
| `items.yaml` | `items[]` | `id` 枚举；`kind` pill\|material\|consumable；`tier`；`sub_tier`；`effect` str 枚举；`params{}`；`price` int；`stack` int（材999/丹99/耗99） |
| `equipments.yaml` | `slots[]` `rarity[]` `affix_pool[]` `enhance{}` `templates[]` | `slots`: `id/main_affix/main_values{xia,zhong,shang}`；`rarity`: `id/weight_bp(合计10000)/affix_count/value_multiplier_bp`；`affix_pool`: `id/min_pct/max_pct/weight_bp(合计10000)`；`enhance`: `max_level=5`,`bonus_per_level_bp=800`；`templates`: `id/tier/sub_tier/seed_sub_tier/slot/rarity/sect_id/base_price` |
| `enemies.yaml` | `enemies[]` | `id` 枚举；`level` 1–100；`realm_index` 1–12；`kind` minion\|elite\|boss；`hp/atk/def/speed` int；`force_level_coef` float；`skills[{name,power_pct,mp_cost,cooldown,effect}]`；`drops[{item_id,rate_bp,qty}]`；`exp_reward`/`stone_reward`；`placeholder` bool（可选） |
| `regions.yaml` | `regions[]` | `id` 枚举；`level_min/level_max`；`env_coef` ∈{1.0,1.5,2.0,3.0,5.0}；`env_label`；`materials[]` item_id；`material_note` str；`enemies[]`；`boss` enemy_id\|null；`travel_days`；`safe_zone`；`enabled`；`desc` |
| `events.yaml` | `events[]` | `id`；`title`；`body` ≤200 字；`category` resource\|npc\|combat\|fortune\|trap\|karma；`weight` int；`region_ids[]`；`level_min/level_max`；`forced_by_quest`（可选）；`choices[2..4]{id,label,risk,check{attr,difficulty_bp}\|null,time_cost_days,success_effects[],fail_effects[]}` |
| `quests_main.yaml` | `quests[]` | `id` quest_01..19；`order` 1–19；`volume` 1\|2；`title/desc`；`level_min/level_max`；`trigger_level`；`region_id`；`prereq[]`；`objective_rule` all\|any_group；`objectives[{type,...,count,group}]`；`rewards{exp,stones,items,equipments,techniques,attrs,contribution,flags}`；`unlock_next[]` |
| `sects.yaml` | `sects[]` `directions[]` | `sects`: `id/name/desc/direction/join_requirements{min_level,trial_win_streak,attrs,materials_any,materials_qty,karma_max,text}/techniques[{tech_id,contribution_price}]`；`directions`: `id/name/layers[3]{layer,nodes[3]{id,name,layer,required_total_contribution,effects,desc}}` |
| `sansxiu.yaml` | `path` `encounters[]` `convert` | `path`: `id/direction_coef_bp=10000/sen_multiplier_bp=12000/event_multiplier_bp=11000/sect_quest_levels[low,mid]/...`；`encounters[3]`: `id/name/desc/trigger{kind,base_bp,source[],realm_min}/weight(合计100)/check{attr,difficulty_bp}\|null/consumes{type,qty}/result/technique_id/contribution`；`convert`: `rate_bp=5000/round_mode/zero_wallet_after/once/flag_key` |
| `forbidden_lands.yaml` | `lands[]` | `id`（= quest_config 的 land_id）；`tier`；`required_realm_index`；`level_min`；`difficulty`；`entry_days`；`entry_contribution`；`boss_id` enemy_id\|null；`sansxiu_only=true`；`enabled`；`first_clear_rewards[{type,...}]`；`repeat_rewards[]` |

**bp 约定**：所有概率/系数写作 `int` bp，`10000 = 100%`、`800 = 8%`，避免 YAML 浮点误差。

---

## 2. 效果（effects）类型字典

事件与奖励共用一套 effect 元素：`{type, ...}`。

| type | 附加键 | 说明 |
|---|---|---|
| `cultivation` | `value` | 修为增减 |
| `spirit_stone` | `value` | 灵石增减（可为负=花费） |
| `item` / `item_loss` | `item_id, qty` | 获得 / 扣除道具 |
| `karma` | `value` | 因果（−1000..1000） |
| `hp_pct` / `mp_pct` | `value` | 气血/灵力百分比变化（负为伤） |
| `attr` | `key, value` | 基础属性永久增减 |
| `dao_heart` | `value` | 道心（单独通道，非 attr） |
| `status` | `key` | injured / poisoned / heart_crack / qi_deviation |
| `favor` | `npc_id, value` | NPC 好感 |
| `technique` | `tech_id` | 直接学功法 |
| `flag` | `key` | 剧情标记（写 character_flag） |
| `buff` | `key, value, duration_days` | 限时增益（时间秘境用） |
| `contribution` | `value` | 宗门/散修贡献 |
| `type: skill_unlock` | `key, value` | 方向树解锁的技能（仅 sects.effects 内） |

---

## 3. ID 清单（供引擎直接对照）

**realms（12）**：`forging_blood open_palace spirit_transform engrave_pattern array_master venerable kindle_lamp unify_one sage great_sage quasi_emperor emperor`（enabled 仅前 2）

**physiques（5）**：`fan_ti ling_ti jian_gu yao_ti xuan_zhen_xin`｜**talents**：grade 1–10｜**personalities（5）**：`gu_ao wen_hou jiao_xia feng_dian zhongyong`

**techniques（41）**
通用 12：`duanxue_jue shanyue_quan tuna_qingmu kurong_jing_can pifu_yinlei guanshu_lang jiuyou_shihun_zhua jingang_buhuai_shen dijing_huangtian guankong_zhaixing qinglian_jian_ge_v1 kurong_jing_zhong`
青云 4：`qingfeng_three_strikes qingyun_jian_jue_v2_yixian wan_jian_guizong_chuwen qingyun_dian_dinglan`
万法 4：`guanwei_rumiao xiaocheng_wanfa_tu fadao_zhenjie_lingpian zhutian_facang`
丹鼎 4：`bianyao_baiwen danfang_jichu baicao_tongxuan shennong_neijing_canj`
玄阵 4：`jianzhen_pu guanzhen_yan sanqianjie_rumen zhoutian_xingdou_zhenjing`
体修 4：`cuiti_jue dahuang_lianji_jue dahuang_zhan_tian_lu_v2 bahuang_liuhe_bumie`
魔道 4：`shihun_mo_jing xuesha_mo_dian youming_zhen_jue jiuyou_busi_zhang`
散修/禁地 5：`caomu_xin_jing tashan_bu qianji_jing_chu beihuang_leisha_jue beichen_di_dian`

**items（15）**
pill 6：`juqi_dan xi_sui_dan po_jing_dan jingxin_dan peiyuan_dan jiuzhuan_huanshun`
material 4：`zhixue_cao shou_gu long_xue_cao ling_ye`
consumable 5：`huiqi_san juling_fu dundi_fu pifu_shi chuanxun_yujian`

**equipments（11 模板）**：`shanyue_dajian qingfeng_jian jiuyou_zhua xuanbu_daopao xuantie_zhanjia lingshi_guan fayuan_lingzhu huangshi_huwan shennong_liandan_lu yinhun_fan yijie_chuwu_jie`（= `SEED_EQUIPMENT` 全部）
槽位 6：`weapon armor helm accessory natal storage`｜稀有度 7：`crude common fine rare epic legend myth`

**regions（20）**：`duanhuai_village back_hill herb_slope qingyun_outer spirit_creek palace_secret ancient_ruin_1 ancient_ruin_2 ancient_ruin_3 sect_forbidden engrave_cliff north_waste shi_pass five_cities dark_abyss holy_outskirt time_secret heaven_road emperor_pass emperor_tomb`
（enabled 6 = 前六个）

**enemies（23）**：`dushan_shanmao huangyuan_lang shanye_tufei shougu_yaolang shilian_kuilei dushan_shanmao_wang shuaibai_jianling gujing_jianpo caomu_yaoteng lingmai_shou waimen_dizi laoyaosou_fenshen mijing_shouwei yinxiu_cike mijing_shouling liu_wujiu` ＋ 禁地占位 7：`jiuyou_shizhe wanjian_wumingshi leimu heian_jieling shengyu_shoumuzhe diguan_yinghun xianmen_shouhu`

**events（20）**：`village_cry jiu_ming_zhi_en xue_zhai_hui_xiang ling_mai_xian_shi tian_jiang_ling_yu ku_shu_yu_xia lao_qiao_fu jian_ke_tiao_xin shao_nv_zeng_li yao_sou_du_wan huang_shou_lan_lu jie_fei_jie_dao yuan_jia_xun_chou can_po_gong_fa_ye gu_jing_qing_ming yi_xiang_gan_wu du_zhang_mi_lu jin_zhi_huan_jing xin_mo_di_yu shan_jian_mi_lu`

**quests（19）**：`quest_01..quest_19`（卷一 quest_01–10，卷二 quest_11–19）

**sects（6）**：`qingyun_sword wanfa_spell taishang_pill xuanzhen_array dahuang_body youming_demon`｜**directions（5）**：`body sword spell alchemy array`（节点 `{dir}_l{1-3}_n{1-3}`，共 45）

**sansxiu（奇遇 3）**：`gujing_jiansheng → qinglian_jian_ge_v1`、`liulang_yaosou → caomu_xin_jing`、`shanjian_milu → tashan_bu`（weight 20/50/30 = 100）

**forbidden_lands（10）**：`gujing_jianyuan caomu_yuanyuan jiuyou_diyu wanjian_yizhong beihuang_leiyu heian_jiezhong shengyu_yuangu diguan_ningxue xianmen_huanjing shijian_mijing`（enabled 2 = 前二）

---

## 4. 引擎对接要点

1. **行动耗时**：`days = action_base_days[kind] × realms[order].time_tier_days / action_tier_base_days`（档位 1 月→10000 年，即 30→3,600,000 日；基准 30）。见 `docs/12-境界时间档位与修为曲线设计-v1.0.md`。
2. **修为**：`ExpRate = (5 + wis×0.8) × (1+0.35×(realm_index−1)) × 功法 × 体质 × 环境 × 心境 × 状态 × exp_rate_global_bp/10000`；单级需求 `floor(100 × L^1.78 × exp_k_bp/10000)`。
3. **层数系数**：`base_coef_bp × (1 + 0.08 × n)`，n = 1..9。
4. **事件**：先按 `category_weights_bp` 定类别，再按 `weight` 抽；`check=null` 视为必成；成功率 `clamp(difficulty_bp + 属性 + 性格 + 运气×0.3% + 因果修正, 500, 9500)`。
5. **主线**：`objective_rule: any_group` 时，`group` 内全部达成即 `ready`；`all` 时需全达成。`level_min/max` 是**窗口**（允许相邻任务交界重叠），校验取"并集覆盖 1–18 无空洞"。
6. **宗门兑换**：`sects[].techniques[].contribution_price` 必须与 `techniques[].contribution_price` 一致（本包已对齐，校验脚本会断言）。
7. **禁地**：数值权威在 `data/content/sect/quest_config.yaml`；本文件只补充展示元数据 + Boss + 首通功法。

---

## 5. 自检统计（`yaml.safe_load_all` 实测）

| 项 | 期望 | 实测 |
|---|---|---|
| 文件数 | 16 | **16** ✅ |
| realms / enabled | 12 / 2 | **12 / 2** ✅（`forging_blood, open_palace`） |
| techniques（唯一 id） | 41 | **41（41 唯一）** ✅，覆盖 `SEED_TECHNIQUES` 36 全部，新增 5 |
| items | 6+4+5 | **pill 6 / material 4 / consumable 5** ✅ |
| equipments | 6 槽 / 7 档 / 11 词条 / 11 模板 | **6 / 7 / 11 / 11** ✅；权重和均 10000 |
| physiques（权重和） | 5 / 10000 | **5 / 10000** ✅ |
| talents / personalities | 10 / 5 | **10 / 5** ✅ |
| regions / enabled | 20 / 6 | **20 / 6** ✅ |
| enemies（minion/elite/boss） | ≥12 | **23**（5/6/12，含 7 禁地占位）✅ |
| events（分类） | 20 | **20**：resource 3 / npc 4 / combat 3 / fortune 3 / trap 4 / karma 3 ✅ |
| quests（卷一/卷二） | 19（10+9） | **19（10+9）** ✅；等级并集覆盖 1–18 无空洞 |
| 目标类型覆盖 | ≥12 | **13 种** ✅（含 event_choice） |
| sects / 独有功法 | 6 / 24 | **6 / 24** ✅；方向树 5×3×3=**45** 节点 |
| sansxiu 奇遇 | 3 | **3** ✅（weight 合计 100；technique_id 全部可达） |
| forbidden_lands / enabled | 10 / 2 | **10 / 2** ✅（`gujing_jianyuan, caomu_yuanyuan`） |
| 跨文件引用（tech/item/enemy/region/event/land/sect/equip/realm-item/sansxiu-tech） | 全可达 | **0 处缺失、0 处悬空** ✅ |
| 事件每选项负面代价 | 100% | **100%** ✅ |

**MVP 偏离备忘（设备侧）**：强化**不消耗"强化石"道具**（items.yaml 只有 15 条，无此道具），
按其裁决折算为纯灵石 `200 × (当前强化等级 + 1)`，上限 +5、每级 +8%、`+1..+5` 失败不降级。

---

## 6. 口径冲突与偏离（已上报主理人）

| # | 冲突 | 本包处理 |
|---|---|---|
| 1 | `quest_config.yaml` 古井剑渊首通写 `qinglian_sword_song_v1`，而 `SEED_TECHNIQUES` 是 `qinglian_jian_ge_v1` | 以 `qinglian_jian_ge_v1` 为准（`forbidden_lands.yaml`）；包 B 需改 `quest_config.yaml` 对齐 |
| 2 | 任务书「辟府石」同列材料与消耗品；`huiqi_san` 同列丹药与消耗品 | 各保留一条：`pifu_shi`→consumable，材料位补 `shou_gu`（主线 #3 奖励要用） |
| 3 | 主线 19 任务 vs 卷一 9 个等级：严格"level_range 无重叠"算术上不成立 | `level_min/max` 取窗口制，校验改「并集覆盖 1–18、无空洞」；相邻任务允许交界重叠 |
| 4 | 后 10 境突破辅材（刻纹刀/归一石/帝血等）不在 15 条 items 内 | 统一用 `ling_ye` 占位，V1.1 扩 items 后逐境替换，不动引擎 |
| 5 | 14 个未开放区域的特产材料无对应 item | `materials: []` + `material_note` 纯文本，避免引用悬空 id |
| 6 | 原 §5.4 强化 +15 + 强化石 | 按裁决改 **+5**、纯灵石（`200 × (当前等级+1)`），`enhance` 段注明 |
| 7 | §4.12.1 帝关凝血首通写《帝经·荒天篇》，§4.12.2 该地功法是《北辰帝典》 | 取 §4.12.2（`beichen_di_dian`），保证 5 部禁地功法各有来源 |
| 8 | 方向树要求"5 方向各 3 层 × 3 节点"，任务书挂在 `sects` 下 | 放顶层 `directions`（45 节点），宗门以 `direction` 引用，避免 6×45 重复 |
