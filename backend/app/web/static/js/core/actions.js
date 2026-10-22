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

/**
 * 效果清单（事件/战斗/兑换共用）。
 * 支持 [{label,value}]、[{type,value,key}]、{attr:{...}} 三种形态。
 */
export function effectLines(effects) {
  const list = [];
  if (!effects) return list;

  const push = (label, value) => {
    if (label === null || label === undefined) return;
    const number = num(value, null);
    const tone = number === null ? undefined : number > 0 ? 'up' : number < 0 ? 'down' : undefined;
    list.push({
      label: String(label),
      text: number === null ? String(value === undefined ? '—' : value) : fmtDelta(number),
      tone,
    });
  };

  const typeLabels = {
    cultivation: '修为',
    spirit_stone: '灵石',
    item: '物品',
    item_loss: '失去',
    karma: '因果',
    hp_pct: '气血',
    mp_pct: '灵力',
    attr: '属性',
    dao_heart: '道心',
    status: '状态',
    favor: '好感',
    technique: '功法',
    flag: '因果标记',
    buff: '增益',
    contribution: '贡献',
    time: '耗时',
    time_days: '耗时',
  };

  const consume = (item) => {
    if (item === null || item === undefined) return;
    if (typeof item === 'string' || typeof item === 'number') {
      list.push({ label: '所得', text: String(item), tone: undefined });
      return;
    }
    if (Array.isArray(item)) {
      item.forEach(consume);
      return;
    }
    const label = pick(item, 'label', 'name', 'text', 'desc');
    const value = pick(item, 'value', 'delta', 'amount', 'qty');
    if (label !== undefined) {
      push(label, value === undefined ? pick(item, 'result', 'effect') : value);
      return;
    }
    const type = pick(item, 'type');
    if (type) {
      const name = pick(item, 'key', 'item_id', 'tech_id', 'npc_id') || '';
      const value2 = pick(item, 'value', 'qty', 'duration_days');
      push(`${typeLabels[type] || type}${name ? `·${name}` : ''}`, value2);
      return;
    }
    Object.keys(item).forEach((key) => {
      const value3 = item[key];
      if (value3 && typeof value3 === 'object') {
        push(key, pick(value3, 'value', 'delta', 'amount'));
      } else {
        push(key, value3);
      }
    });
  };

  consume(effects);
  return list;
}

/** 渲染效果清单 */
export function renderDeltas(container, effects) {
  if (!container) return;
  clear(container);
  const lines = effectLines(effects);
  if (!lines.length) {
    append(container, el('li', null, [el('span', { class: 'muted', text: '无增无减' })]));
    return;
  }
  lines.forEach((line) => {
    container.appendChild(el('li', null, [
      el('span', { text: line.label }),
      el('b', { class: line.tone === 'up' ? 'up' : line.tone === 'down' ? 'down' : '', text: line.text }),
    ]));
  });
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
