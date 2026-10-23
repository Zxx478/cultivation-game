/**
 * core/state.js · 客户端只读缓存
 *
 * 权威状态永远在服务端：这里只缓存「最近一次 GET /api/v1/state 的返回值」，
 * 并在刷新后通知订阅者重绘。前端不做任何数值推算（仅做展示用的比率/文案兜底）。
 *
 * 本地 localStorage 只存 ui.prefs（主题、动效、上次所在 pane），不含任何角色数据。
 */

import { api, ApiError, apiEvents } from './api.js';

const PREFS_KEY = 'ui.prefs';
const DEFAULT_PREFS = { theme: 'night', motion: true, lastPane: 'main' };

/** 安全读取嵌套字段，取第一个非空值；支持 'a.b.c' 路径。 */
export function pick(source, ...paths) {
  for (const path of paths) {
    if (!path) continue;
    const value = getPath(source, path);
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return undefined;
}

function getPath(source, path) {
  if (source == null) return undefined;
  if (typeof path === 'string' && !path.includes('.')) {
    return source[path];
  }
  let cursor = source;
  for (const key of String(path).split('.')) {
    if (cursor == null) return undefined;
    cursor = cursor[key];
  }
  return cursor;
}

export function num(value, fallback = null) {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function arr(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value === 'object') return Object.values(value);
  return [];
}

const first = (...values) => {
  for (const value of values) {
    if (value !== undefined && value !== null) return value;
  }
  return undefined;
};

/** 把 /state 归一化成视图模型；任何字段缺失都退化为 null，由 pane 渲染「—」。 */
function normalize(raw) {
  const root = raw && typeof raw === 'object' ? raw : {};
  const c = first(root.character, root.char, {}) || {};
  const cult = first(root.cultivation, {}) || {};
  const vitals = first(root.vitals, root.vital, root.condition, {}) || {};
  const life = first(root.lifespan, {}) || {};
  const region = first(root.region, {}) || {};
  const identity = first(root.identity, root.path, {}) || {};
  const action = first(root.action, root.actions, {}) || {};
  /** 本境时间档位（服务端按境界查表下发）：'label' 如「3 个月」，'days' 如 90 */
  const tier = first(root.time_tier, root.timeTier, {}) || {};

  const level = num(first(pick(c, 'level'), pick(root, 'level')));
  const expNeed = num(first(
    pick(c, 'exp_need', 'next_exp', 'exp_next', 'need_exp'),
    pick(cult, 'exp_need'),
    pick(root, 'exp_need'),
  ));
  const cultivation = num(first(pick(c, 'cultivation', 'exp'), pick(cult, 'cultivation'), 0), 0);
  const explicitPct = num(first(
    pick(c, 'cultivation_pct', 'exp_pct', 'percent'),
    pick(cult, 'pct', 'percent', 'ratio'),
  ));
  const expPct = explicitPct !== null
    ? (explicitPct > 1.5 ? explicitPct / 100 : explicitPct)
    : (expNeed ? Math.min(1, cultivation / expNeed) : null);

  const hp = num(first(pick(c, 'hp'), pick(vitals, 'hp'), 0), 0);
  const hpMax = num(first(pick(c, 'hp_max', 'max_hp', 'hpMax'), pick(vitals, 'hp_max', 'max_hp')));
  const mp = num(first(pick(c, 'mp'), pick(vitals, 'mp'), 0), 0);
  const mpMax = num(first(pick(c, 'mp_max', 'max_mp', 'mpMax'), pick(vitals, 'mp_max', 'max_mp')));

  const ageDays = num(first(pick(c, 'age_days'), pick(root, 'age_days')));
  const lifespanDays = num(first(
    pick(c, 'lifespan_max_days', 'lifespan_days'),
    pick(life, 'max_days', 'lifespan_max_days'),
  ));
  const remainingDays = num(first(
    pick(c, 'remaining_days', 'lifespan_remaining_days'),
    pick(life, 'remaining_days'),
    ageDays !== null && lifespanDays !== null ? Math.max(0, lifespanDays - (ageDays - 1)) : null,
  ));
  const remainingYears = num(first(
    pick(c, 'remaining_years', 'lifespan_years'),
    pick(life, 'remaining_years', 'years'),
    remainingDays !== null ? remainingDays / 360 : null,
  ));
  const lifePct = num(first(
    pick(c, 'remaining_pct', 'lifespan_pct'),
    pick(life, 'pct', 'ratio'),
    remainingDays !== null && lifespanDays ? Math.min(1, remainingDays / lifespanDays) : null,
  ));

  const gameYear = num(first(pick(root, 'game_year'), pick(root, 'time.game_year'), pick(c, 'game_year')));

  const seclusionDays = num(first(
    pick(root, 'action_days.seclusion'),
    pick(action, 'base_days.seclusion', 'costs.seclusion', 'days_per_action.seclusion'),
    pick(root, 'action_base_days.seclusion'),
  ));
  const closedDoorTimes = num(first(
    pick(root, 'closed_door_times', 'closed_door_remaining', 'closed_door_left'),
    pick(c, 'closed_door_times', 'closed_door_remaining'),
    pick(action, 'closed_door_remaining', 'closed_door_times'),
    pick(root, 'summary.closed_door_times'),
  ), remainingDays !== null && seclusionDays ? Math.floor(remainingDays / seclusionDays) : null);

  const pendingRaw = first(root.pending_event, root.pendingEvent, c.pending_event, null);
  const pendingEvent = pendingRaw && typeof pendingRaw === 'object' && Object.keys(pendingRaw).length
    ? pendingRaw
    : (typeof pendingRaw === 'string' && pendingRaw ? { instance_id: pendingRaw } : null);

  const combatRaw = first(root.combat, null);
  const combatActive = Boolean(
    combatRaw && (combatRaw.status === 'active' || combatRaw.active === true),
  );

  const name = first(pick(c, 'name', 'dao_name'), '—');
  const realmName = first(
    pick(c, 'realm_display', 'realm_name', 'realm'),
    [pick(c, 'realm_index'), pick(c, 'realm_stage')].every((v) => v !== undefined)
      ? `${pick(c, 'realm_index')} · ${pick(c, 'realm_stage')}`
      : undefined,
  );

  return {
    raw: root,
    hasCharacter: Boolean(pick(c, 'id') !== undefined || pick(root, 'character') || pick(root, 'has_character') === true),
    charId: num(pick(c, 'id')),
    name: String(name),
    gender: pick(c, 'gender'),
    level,
    realmName: realmName === undefined ? null : String(realmName),
    realmIndex: num(pick(c, 'realm_index')),
    realmStage: num(pick(c, 'realm_stage')),
    cultivation,
    expNeed,
    expPct,
    hp: hpMax > 0 && (!hp || hp <= 0) ? hpMax : hp,
    hpMax,
    mp: mpMax > 0 && (!mp || mp <= 0) ? mpMax : mp,
    mpMax,
    ageDays,
    lifespanDays,
    remainingDays,
    remainingYears,
    lifePct,
    spiritStone: num(pick(c, 'spirit_stone', 'stones', 'stone'), 0),
    karma: num(pick(c, 'karma'), 0),
    daoHeart: num(pick(c, 'dao_heart'), null),
    regionId: pick(c, 'region_id', 'region'),
    regionName: first(pick(region, 'name', 'display_name'), pick(c, 'region_name'), null),
    regionEnv: num(first(pick(region, 'env_coef', 'coef'), pick(c, 'region_env_coef', 'env_coef'))),
    physique: pick(c, 'physique'),
    physiqueName: pick(c, 'physique_name'),
    talent: num(pick(c, 'talent')),
    personality: pick(c, 'personality'),
    personalityName: pick(c, 'personality_name'),
    pathId: pick(c, 'path_id', 'path'),
    sectId: pick(c, 'sect_id'),
    sectName: pick(c, 'sect_name'),
    directionId: pick(c, 'direction_id', 'direction'),
    foundation: pick(c, 'foundation'),
    status: pick(c, 'status', 'state'),
    version: num(pick(c, 'version'), null),
    gameYear,
    closedDoorTimes,
    seclusionDays,
    actionBaseDays: first(pick(root, 'action_base_days'), null),
    actionDays: first(pick(root, 'action_days'), null),
    timeTierLabel: pick(tier, 'label') || null,
    timeTierDays: num(pick(tier, 'days')),
    canBreakthrough: pick(root, 'can_breakthrough', 'cultivation.can_breakthrough') === true,
    breakthroughReason: first(pick(root, 'breakthrough_reason', 'cultivation.breakthrough_reason'), null),
    mainQuest: first(root.main_quest, root.quest, null),
    pendingEvent,
    combatActive,
    combat: combatRaw,
    logs: arr(first(root.logs, root.event_log, root.events, [])),
    milestones: arr(first(root.milestones, root.milestone_log, [])),
    prologue: pick(root, 'prologue', 'intro', 'guide.prologue'),
    expRate: first(root.exp_rate, cult.rate, cult.detail, null),
    gainPreview: first(root.gain_preview, cult.gain, cult.preview, root.action_preview, null),
    remainingActions: first(root.remaining_actions, cult.remaining_actions, null),
    identity,
  };
}

function emptyView() {
  return normalize({ character: null });
}

function readPrefs() {
  try {
    const stored = localStorage.getItem(PREFS_KEY);
    if (!stored) return { ...DEFAULT_PREFS };
    const parsed = JSON.parse(stored);
    return {
      theme: parsed && parsed.theme === 'paper' ? 'paper' : 'night',
      motion: !(parsed && parsed.motion === false),
      lastPane: (parsed && typeof parsed.lastPane === 'string' && parsed.lastPane) || DEFAULT_PREFS.lastPane,
    };
  } catch (err) {
    return { ...DEFAULT_PREFS };
  }
}

class Store {
  constructor() {
    this.raw = null;
    this.view = emptyView();
    this.hasCharacter = false;
    this.prefs = readPrefs();
    this.listeners = new Set();
    this.busyKeys = new Set();
    this.error = null;
    this.loading = false;
    /** 无角色时置真：外壳隐藏导航，只留创角 */
    this.stage = 'boot';
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit(reason = 'update') {
    this.listeners.forEach((listener) => {
      try {
        listener(this.view, reason);
      } catch (err) {
        console.error('[state] 订阅者异常', err);
      }
    });
  }

  setState(data) {
    this.raw = data && typeof data === 'object' ? data : {};
    this.view = normalize(this.raw);
    this.hasCharacter = this.view.hasCharacter;
    this.error = null;
    this.loading = false;
    this.stage = this.hasCharacter ? 'playing' : 'creation';
    this.emit('state');
    return this.view;
  }

  markNoCharacter() {
    this.hasCharacter = false;
    this.stage = 'creation';
    this.view = emptyView();
    this.emit('no-character');
  }

  async refresh(options = {}) {
    if (this.loading && !options.force) return this.view;
    this.loading = true;
    try {
      const data = await api.state();
      return this.setState(data);
    } catch (err) {
      this.loading = false;
      this.error = err;
      if (!options.silent) throw err;
      return this.view;
    }
  }

  /* —— 忙碌标记：所有写操作按钮的「结算中…」状态由此统一管理 —— */
  beginBusy(key) {
    this.busyKeys.add(key);
  }

  endBusy(key) {
    this.busyKeys.delete(key);
  }

  isBusy(key) {
    return this.busyKeys.has(key);
  }

  /* —— UI 偏好（只存主题/动效/位置）—— */
  savePrefs() {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(this.prefs));
    } catch (err) {
      /* 隐私模式下静默失败 */
    }
  }

  setTheme(theme) {
    this.prefs.theme = theme === 'paper' ? 'paper' : 'night';
    this.savePrefs();
    applyPrefs(this.prefs);
    this.emit('prefs');
  }

  toggleTheme() {
    this.setTheme(this.prefs.theme === 'paper' ? 'night' : 'paper');
    return this.prefs.theme;
  }

  setMotion(on) {
    this.prefs.motion = Boolean(on);
    this.savePrefs();
    applyPrefs(this.prefs);
    this.emit('prefs');
  }

  toggleMotion() {
    this.setMotion(!this.prefs.motion);
    return this.prefs.motion;
  }

  setLastPane(pane) {
    if (!pane) return;
    this.prefs.lastPane = pane;
    this.savePrefs();
  }
}

export function applyPrefs(prefs = store.prefs) {
  const root = document.documentElement;
  root.dataset.theme = prefs.theme === 'paper' ? 'paper' : 'night';
  root.dataset.motion = prefs.motion === false ? 'off' : 'on';
}

export const store = new Store();

/* 无角色：api 层已统一拦截 4003，这里记录供 app.js 切 pane */
apiEvents.onNoCharacter = () => store.markNoCharacter();

export { normalize, DEFAULT_PREFS, PREFS_KEY };
export default store;
