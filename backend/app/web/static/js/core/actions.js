/**
 * core/actions.js · 行动与结算的共用逻辑
 *
 * 只做「发请求 → 刷新状态 → 通知浮层同步」三件事；结算数字全部来自服务端。
 */

import { api } from './api.js';
import { store, pick, arr, num } from './state.js';
import { el, fill, fmt, fmtDelta, toastError, clear, append } from './ui.js';

/** 行动白名单（与 game.yaml 的 action_base_days 键一致） */
export const ACTION_KINDS = ['seclusion', 'adventure', 'gather', 'spar', 'meditate'];

export const ACTION_LABELS = {
  seclusion: '闭关',
  adventure: '历练',
  gather: '采药',
  spar: '切磋',
  meditate: '打坐',
  breakthrough: '突破',
};

/**
 * 提交一次行动（含持续闭关的 repeat）。
 * @param {string} kind
 * @param {{repeat?:number, expectedVersion?:number}} options
 */
export function performAction(kind, options = {}) {
  const body = { kind };
  const repeat = num(options.repeat, null);
  if (repeat !== null && repeat > 1) body.repeat = repeat;
  const version = num(options.expectedVersion, null);
  if (version !== null) body.expected_version = version;
  return api.action(body);
}

/** 刷新 /state 并让 app 依状态同步浮层（事件/战斗） */
export async function refreshAndSync(options = {}) {
  try {
    await store.refresh({ force: true });
  } catch (err) {
    if (!options.silent) toastError(err);
    return null;
  }
  document.dispatchEvent(new CustomEvent('game:sync'));
  return store.view;
}

/**
 * 把 /action 的返回拆成可读的结算行（缺字段即跳过）。
 * @returns {Array<{label:string, value:string, tone?:'up'|'down'}>}
 */
export function settleLines(data) {
  const root = data && typeof data === 'object' ? data : {};
  const settle = pick(root, 'settle', 'summary', 'result') || root;
  const lines = [];

  const days = num(pick(settle, 'total_days', 'days', 'time_cost_days'));
  if (days !== null) lines.push({ label: '耗时', value: `${fmt(days)} 日`, tone: 'down' });

  const exp = num(pick(settle, 'total_exp', 'exp', 'cultivation_gain', 'gain'));
  if (exp !== null) lines.push({ label: '修为', value: fmtDelta(exp), tone: exp >= 0 ? 'up' : 'down' });

  const levels = num(pick(settle, 'levels_up', 'level_up', 'level_gain'));
  if (levels !== null) lines.push({ label: '连升', value: `${levels} 级`, tone: levels > 0 ? 'up' : undefined });

  const stones = num(pick(settle, 'spirit_stone', 'stones', 'stone_gain'));
  if (stones !== null && stones !== 0) lines.push({ label: '灵石', value: fmtDelta(stones), tone: stones > 0 ? 'up' : 'down' });

  const hp = num(pick(settle, 'hp_gain', 'hp_delta', 'hp_change'));
  if (hp !== null && hp !== 0) lines.push({ label: '气血', value: fmtDelta(hp), tone: hp > 0 ? 'up' : 'down' });

  const mp = num(pick(settle, 'mp_gain', 'mp_delta'));
  if (mp !== null && mp !== 0) lines.push({ label: '灵力', value: fmtDelta(mp), tone: mp > 0 ? 'up' : 'down' });

  const overflow = num(pick(settle, 'exp_overflow', 'overflow'));
  if (overflow !== null && overflow > 0) lines.push({ label: '溢出', value: fmt(overflow), tone: 'up' });

  arr(pick(settle, 'milestones', 'important')).slice(0, 3).forEach((item) => {
    const title = typeof item === 'string' ? item : pick(item, 'title', 'text', 'detail');
    if (title) lines.push({ label: '里程碑', value: String(title), tone: 'up' });
  });

  const drops = arr(pick(settle, 'drops', 'items', 'rewards'));
  drops.forEach((drop) => {
    if (typeof drop === 'string') {
      lines.push({ label: '所得', value: drop, tone: 'up' });
      return;
    }
    const name = pick(drop, 'name', 'item_name', 'item_id', 'label');
    const qty = num(pick(drop, 'qty', 'count', 'amount'), null);
    if (name) lines.push({ label: '所得', value: qty ? `${name} ×${qty}` : String(name), tone: 'up' });
  });

  return lines;
}

/** 把结算行渲染进容器（无内容时显示空态文案） */
export function renderSettle(container, data, emptyText = '此行事了，无甚可说。') {
  if (!container) return;
  clear(container);
  const lines = settleLines(data);
  const milestones = arr(pick(data, 'milestones', 'result.milestones', 'settle.milestones')).slice(0, 3);

  if (!lines.length && !milestones.length) {
    container.appendChild(el('p', { class: 'empty', text: emptyText }));
    return;
  }
  const wrap = el('div', { class: 'settle' }, lines.map((line) => el('div', { class: 'settle__line' }, [
    el('span', { class: 'muted', text: line.label }),
    el('b', { class: line.tone === 'up' ? 'up' : line.tone === 'down' ? 'down' : '', text: line.value }),
  ])));
  container.appendChild(wrap);

  if (milestones.length) {
    container.appendChild(el('ul', { class: 'tasklist' }, milestones.map((item) => el('li', null, [
      el('span', { class: 'tasklist__title', text: String(pick(item, 'title', 'text', 'detail') || item) }),
      el('span', { class: 'badge badge--ready', text: '里程碑' }),
    ]))));
  }
}

/* ============================================================
   效果清单（事件 / 战斗 / 突破 / 兑换共用）
   ============================================================ */

/** 十项基础属性（与 panes/character.js 的 BASIC_LABELS 一致；mnd 即道心） */
const ATTR_LABELS = {
  str: '力量',
  con: '体魄',
  spr: '灵力',
  sen: '神识',
  bon: '根骨',
  wis: '悟性',
  agi: '身法',
  luk: '气运',
  mnd: '道心',
  karma: '因果',
  dao_heart: '道心',
};

/** 角色状态（与 core/ui.js 的 statusBadge 一致） */
const STATUS_LABELS = {
  normal: '无恙',
  injured: '负伤',
  qi_deviation: '走火入魔',
  dead: '已坐化',
  deceased: '已坐化',
};

/** 效果类型 → 中文名 */
const TYPE_LABELS = {
  cultivation: '修为',
  exp: '修为',
  spirit_stone: '灵石',
  stones: '灵石',
  karma: '因果',
  dao_heart: '道心',
  contribution: '贡献',
  hp_pct: '气血',
  mp_pct: '灵力',
  attr: '属性',
  status: '状态',
  favor: '好感',
  technique: '功法',
  buff: '增益',
  time: '耗时',
  time_days: '耗时',
  time_cost_days: '耗时',
};

/** 只做记账、绝不展示的字段（chance_bp 尤其不得展示，设计 §3.5） */
const INTERNAL_KEYS = new Set([
  'success', 'chance_bp', 'effects', 'flags', 'unhandled', 'rng_counter', 'level', 'levels_gained',
]);

/** 内部英文标识（用于兜底：宁可不显示，也不把引擎键名抛给玩家） */
const isInternalKey = (token) => /^[\x20-\x7e]*$/.test(String(token));

function dictOf(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}

/** 把「数组 / 对象 / 单值」统一成 [键, 值] 列表 */
function pairsOf(value) {
  if (value === undefined || value === null) return [];
  if (Array.isArray(value)) return value.map((item) => [null, item]);
  const map = dictOf(value);
  if (map) return Object.keys(map).map((key) => [key, map[key]]);
  return [[null, value]];
}

/** 键 → 中文；未知英文键返回空串 */
function labelOf(raw, labels) {
  const key = raw === undefined || raw === null ? '' : String(raw);
  if (!key) return '';
  if (labels && labels[key]) return labels[key];
  return isInternalKey(key) ? '' : key;
}

/**
 * 效果清单（已译成中文）。
 * 支持 [{label,value}]、[{type,value,key}]、{attr:{...}}、EffectOutcome 字典四种形态。
 */
export function effectLines(effects) {
  const list = [];
  if (!effects) return list;

  const pushNum = (label, value, suffix = '') => {
    if (!label) return;
    const number = num(value, null);
    if (number === null) {
      const text = value === undefined || value === null ? '' : String(value);
      if (text) list.push({ label: String(label), text, tone: undefined });
      return;
    }
    if (number === 0) return;
    list.push({
      label: String(label),
      text: `${fmtDelta(number)}${suffix}`,
      tone: number > 0 ? 'up' : 'down',
    });
  };

  const pushText = (label, text, tone) => {
    if (!label || text === undefined || text === null || text === '') return;
    list.push({ label: String(label), text: String(text), tone });
  };

  /** 处理 {type, key, value} 形态 */
  const consumeTyped = (node) => {
    const type = String(pick(node, 'type') || '');
    const base = TYPE_LABELS[type] || labelOf(type, null);
    const rawKey = pick(node, 'key', 'item_id', 'tech_id', 'npc_id', 'status');
    const amount = pick(node, 'value', 'delta', 'amount', 'qty');

    if (type === 'attr') {
      pushNum(labelOf(rawKey, ATTR_LABELS) || '属性', amount);
      return true;
    }
    if (type === 'status') {
      pushText('状态', labelOf(rawKey, STATUS_LABELS));
      return true;
    }
    if (type === 'item' || type === 'item_loss') {
      const name = String(rawKey || '物品');
      pushText(`${type === 'item' ? '所得' : '失去'}·${name}`,
        amount === undefined || amount === null ? '' : `×${fmt(amount)}`,
        type === 'item' ? 'up' : 'down');
      return true;
    }
    if (type === 'technique') {
      pushText('习得功法', String(rawKey || ''));
      return true;
    }
    if (type === 'flag') return true; // 内部标记，不展示
    if (base) {
      pushNum(base, amount);
      return true;
    }
    return false;
  };

  /** 处理 EffectOutcome 风格的整个字典 */
  const consumeDict = (data) => {
    Object.keys(data).forEach((key) => {
      if (INTERNAL_KEYS.has(key)) return;
      const value = data[key];

      if (key === 'attr') {
        pairsOf(value).forEach(([attrKey, delta]) => {
          const label = labelOf(attrKey === null ? pick(delta || {}, 'key') : attrKey, ATTR_LABELS);
          pushNum(label, attrKey === null ? pick(delta || {}, 'value', 'delta') : delta);
        });
        return;
      }
      if (key === 'status' || key === 'statuses') {
        pairsOf(value).forEach(([, item]) => {
          const raw = typeof item === 'string' ? item : pick(item || {}, 'key', 'status');
          pushText('状态', labelOf(raw, STATUS_LABELS));
        });
        return;
      }
      if (key === 'items_gained' || key === 'items_lost' || key === 'item' || key === 'item_loss') {
        const gained = key === 'items_gained' || key === 'item';
        pairsOf(value).forEach(([itemId, qty]) => {
          const name = itemId === null ? pick(qty || {}, 'item_id', 'name') : itemId;
          const count = itemId === null ? pick(qty || {}, 'qty', 'count') : qty;
          pushText(`${gained ? '所得' : '失去'}·${name || '物品'}`,
            count === undefined || count === null ? '' : `×${fmt(count)}`,
            gained ? 'up' : 'down');
        });
        return;
      }
      if (key === 'techniques' || key === 'technique') {
        pairsOf(value).forEach(([, techId]) => {
          const id = typeof techId === 'string' ? techId : pick(techId || {}, 'tech_id', 'name');
          pushText('习得功法', String(id || ''));
        });
        return;
      }
      if (key === 'favors' || key === 'favor') {
        pairsOf(value).forEach(([npcId, delta]) => {
          pushNum(`好感·${npcId === null ? pick(delta || {}, 'npc_id') : npcId}`,
            npcId === null ? pick(delta || {}, 'value') : delta);
        });
        return;
      }
      if (key === 'buffs' || key === 'buff') {
        pairsOf(value).forEach(([, buff]) => {
          const amount = num(pick(buff || {}, 'value'), null);
          const days = num(pick(buff || {}, 'duration_days'), null);
          const parts = [];
          if (amount !== null && amount !== 0) parts.push(fmtDelta(amount));
          if (days) parts.push(`${fmt(days)} 日`);
          pushText('增益', parts.join(' · '), 'up');
        });
        return;
      }
      if (key === 'hp_pct' || key === 'mp_pct') {
        pushNum(key === 'hp_pct' ? '气血' : '灵力', value, '%');
        return;
      }
      if (key === 'time' || key === 'time_days' || key === 'time_cost_days') {
        pushText('耗时', `${fmt(value)} 日`, 'down');
        return;
      }
      const base = TYPE_LABELS[key];
      if (base) {
        pushNum(base, value);
        return;
      }
      // 未知键：中文自定义键原样展示，英文内部键不露出
      if (!isInternalKey(key)) pushNum(key, value);
    });
  };

  const consume = (node) => {
    if (node === null || node === undefined) return;
    if (Array.isArray(node)) {
      node.forEach(consume);
      return;
    }
    if (typeof node !== 'object') {
      pushText('所得', node, 'up');
      return;
    }
    if (pick(node, 'type')) {
      consumeTyped(node);
      return;
    }
    const direct = pick(node, 'label', 'text', 'name');
    if (direct !== undefined && !pick(node, 'key')) {
      pushNum(direct, pick(node, 'value', 'delta', 'amount', 'qty'));
      return;
    }
    consumeDict(node);
  };

  consume(effects);
  return list;
}

/** 渲染已译好的效果行（服务端 effect_lines 直接走这里） */
export function renderDeltaLines(container, lines) {
  if (!container) return;
  clear(container);
  const rows = arr(lines).filter((line) => line && (line.label !== undefined || line.text !== undefined));
  if (!rows.length) {
    append(container, el('li', null, [el('span', { class: 'muted', text: '无增无减' })]));
    return;
  }
  rows.forEach((line) => {
    const tone = line.tone === 'up' ? 'up' : line.tone === 'down' ? 'down' : '';
    container.appendChild(el('li', null, [
      el('span', { text: String(line.label === undefined ? '' : line.label) }),
      el('b', { class: tone, text: String(line.text === undefined || line.text === null ? '—' : line.text) }),
    ]));
  });
}

/** 渲染效果清单 */
export function renderDeltas(container, effects) {
  renderDeltaLines(container, effectLines(effects));
}

/** 取状态里的「待决事件」/「战斗」，用于浮层同步 */
export function pendingForOverlay() {
  const view = store.view;
  return { event: view.pendingEvent, combat: view.combat, combatActive: view.combatActive };
}

/** 供各 pane 末尾调用：把最新状态里的可执行提示补到 hint 上 */
export function blockReason(view) {
  if (view.pendingEvent) return '先处理眼前之事：有一桩因果未了。';
  if (view.combatActive) return '先处理眼前之事：战斗未止。';
  if (view.status === 'dead' || view.status === 'deceased') return '此身已寂，唯余读档。';
  return '';
}
