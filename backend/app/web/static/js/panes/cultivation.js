/**
 * panes/cultivation.js · 修炼
 *
 * 展示单次闭关收益、修为增速（ExpRate）逐层明细、本境进程；
 * 收益与增速全部取自 /state 返回，前端不做乘算。
 */

import { store, pick, num } from '../core/state.js';
import { performAction, refreshAndSync, renderSettle, blockReason } from '../core/actions.js';
import {
  qs, el, fill, fmt, fmtPct, setBar, toast, toastError, withBusy, renderEmpty,
} from '../core/ui.js';
import router from '../core/router.js';

/** 状态订阅句柄：leave 时解绑 */
let unsubscribeState = null;
/** 结算进行中：按钮被 withBusy 临时置灰，此时不让订阅回调把按钮重新放开 */
let settling = false;

const LAYER_NAMES = {
  base: '悟性基数',
  wis: '悟性',
  realm: '境界',
  technique: '功法',
  physique: '体质',
  region: '环境',
  env: '环境',
  mood: '心境',
  status: '状态',
  direction: '方向',
  global: '全局倍率',
  global_bp: '全局倍率',
};

function formatCoef(value) {
  const number = num(value, null);
  if (number === null) return value === undefined ? '—' : String(value);
  if (number > 1000) return `×${(number / 10000).toFixed(2)}`;
  if (number > 10) return `×${(number / 100).toFixed(2)}`;
  return `×${number.toFixed(number % 1 === 0 ? 0 : 3)}`;
}

function collectLayers(view) {
  const source = view.expRate;
  if (!source) return [];
  const list = pick(source, 'layers', 'items', 'details', 'detail');
  if (Array.isArray(list)) {
    return list.map((item, index) => ({
      name: pick(item, 'name', 'label', 'key') || `第 ${index + 1} 层`,
      coef: pick(item, 'coef', 'value', 'factor', 'bp', 'coefficient'),
      note: pick(item, 'note', 'desc', 'description', 'formula') || '',
    }));
  }
  if (typeof source === 'object') {
    return Object.keys(source)
      .filter((key) => !['total', 'exp_rate', 'rate', 'layers', 'items', 'details', 'detail', 'final'].includes(key))
      .map((key) => ({
        name: LAYER_NAMES[key] || key,
        coef: source[key] && typeof source[key] === 'object'
          ? pick(source[key], 'coef', 'value', 'factor')
          : source[key],
        note: source[key] && typeof source[key] === 'object'
          ? pick(source[key], 'note', 'desc') || ''
          : '',
      }));
  }
  return [];
}

function gainOf(view) {
  const source = view.gainPreview;
  if (source === null || source === undefined) return { exp: null, pct: null, days: view.seclusionDays };
  if (typeof source === 'number' || typeof source === 'string') {
    return { exp: num(source, null), pct: null, days: view.seclusionDays };
  }
  const node = pick(source, 'seclusion', 'close_door', 'cultivate') || source;
  return {
    exp: num(pick(node, 'exp', 'gain', 'cultivation', 'value')),
    pct: num(pick(node, 'pct', 'percent', 'ratio', 'level_pct')),
    days: num(pick(node, 'days', 'time_cost_days'), view.seclusionDays),
  };
}

function renderGain(root) {
  const view = store.view;
  const gain = gainOf(view);
  const gainNode = qs('#cult-gain', root);
  const metaNode = qs('#cult-gain-meta', root);
  const hint = qs('#cult-hint', root);

  if (gainNode) gainNode.textContent = gain.exp === null ? '—' : `+${fmt(gain.exp)}`;
  if (metaNode) {
    const bits = [];
    if (gain.days !== null) bits.push(`闭关 ${fmt(gain.days)} 日`);
    if (gain.pct !== null) bits.push(`本级 ${fmtPct(gain.pct)}`);
    if (view.expNeed !== null) bits.push(`本级需 ${fmt(view.expNeed)} 修为`);
    metaNode.textContent = bits.length ? bits.join(' · ') : '服务端未返回单次收益，可直接闭关结算查看。';
  }

  const block = blockReason(view);
  if (hint) hint.textContent = block || '中途遇事即止，余下次数作废；寿元不足者不可闭关。';

  const group = qs('.actionbar', root);
  if (group) {
    Array.from(group.querySelectorAll('button.action')).forEach((button) => {
      const blocked = Boolean(block) || view.status === 'dead' || view.status === 'deceased';
      button.disabled = blocked;
      button.setAttribute('aria-disabled', blocked ? 'true' : 'false');
      if (blocked) button.title = block || '此身已寂';
      else button.removeAttribute('title');
    });
  }
  const repeat = qs('#cult-repeat', root);
  if (repeat) repeat.disabled = Boolean(block);

  const breakBtn = qs('#cult-breakthrough', root);
  if (breakBtn) {
    breakBtn.hidden = !view.canBreakthrough;
    if (view.canBreakthrough) breakBtn.textContent = view.reason || '冲击下一境';
  }
}

function renderRate(root) {
  const view = store.view;
  const body = qs('#cult-rate-body', root);
  const total = qs('#cult-rate-total', root);
  const layers = collectLayers(view);

  if (total) {
    const rate = num(pick(view.expRate, 'total', 'exp_rate', 'rate', 'final'), null);
    total.textContent = rate === null ? '—' : `合计 ${fmt(rate)} 修为 / 日`;
  }

  if (!body) return;
  if (!layers.length) {
    renderEmpty(body, '服务端尚未返回增速明细；请先闭关一次或稍后刷新。');
    const cell = body.firstElementChild;
    if (cell && cell.tagName === 'P') {
      const tr = el('tr', null, [el('td', { colspan: '3' }, [cell])]);
      fill(body, tr);
    }
    return;
  }

  fill(body, layers.map((layer) => el('tr', null, [
    el('th', { scope: 'row', text: String(layer.name) }),
    el('td', { class: 'num', text: formatCoef(layer.coef) }),
    el('td', { class: 'muted', text: layer.note ? String(layer.note) : '—' }),
  ])));
}

function renderProgress(root) {
  const view = store.view;
  setBar(qs('[data-bar="exp2"]', root), view.expPct, view.expNeed
    ? `${fmt(view.cultivation)} / ${fmt(view.expNeed)}（${fmtPct(view.expPct)}）`
    : '—');

  const remaining = view.remainingActions || {};
  const realmTimes = num(pick(remaining, 'realm', 'realm_remaining', 'realm_times'), null);
  const levelTimes = num(pick(remaining, 'level', 'level_remaining', 'level_times'), null);

  const realmNode = qs('#cult-remain-realm', root);
  if (realmNode) realmNode.textContent = realmTimes === null ? '—' : `${fmt(realmTimes)} 次`;
  const levelNode = qs('#cult-remain-level', root);
  if (levelNode) levelNode.textContent = levelTimes === null ? '—' : `${fmt(levelTimes)} 次`;
  const lifeNode = qs('#cult-remain-life', root);
  if (lifeNode) lifeNode.textContent = view.closedDoorTimes === null ? '—' : `${fmt(view.closedDoorTimes)} 次`;
}

function ensureResultCard(root) {
  let card = qs('#cult-result', root);
  if (card) return card;
  card = el('div', { class: 'card', id: 'cult-result', hidden: true }, [
    el('header', { class: 'card__head' }, [el('h3', { class: 'card__title', text: '结算' })]),
    el('div', { class: 'card__body', id: 'cult-result-body' }),
  ]);
  const body = qs('.pane__body', root);
  if (body) body.appendChild(card);
  return card;
}

async function runAction(root, button) {
  const kind = button.dataset.action || 'seclusion';
  const useRepeat = button.dataset.repeat === '1';
  const repeatNode = qs('#cult-repeat', root);
  const repeat = useRepeat && repeatNode ? Number(repeatNode.value) : null;
  const group = qs('.actionbar', root);
  const card = ensureResultCard(root);

  await withBusy(button, async () => {
    try {
      settling = true;
      const data = await performAction(kind, { repeat, expectedVersion: store.view.version });
      card.hidden = false;
      renderSettle(qs('#cult-result-body', root), data);
      await refreshAndSync();
      renderRate(root);
    } catch (err) {
      toastError(err);
      if (err.code === '4003') router.go('creation');
      if (err.code === '4006' || err.code === '4008') toast('寿元不足，修行已至尽头', 'warn');
    } finally {
      settling = false;
      renderGain(root);
      renderProgress(root);
    }
  }, { label: '结算中…', group });
}

export default {
  name: 'cultivation',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    renderGain(root);
    renderRate(root);
    renderProgress(root);

    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      root.addEventListener('click', (event) => {
        const button = event.target.closest('button.action');
        if (button && !button.disabled) runAction(root, button);
        if (event.target.closest('#cult-breakthrough')) {
          document.dispatchEvent(new CustomEvent('game:open-breakthrough'));
        }
      });
    }

    // 突破后本境档位变了，这里的「闭关 N 日」也要跟着变
    if (typeof unsubscribeState === 'function') unsubscribeState();
    unsubscribeState = store.subscribe((view, reason) => {
      if (reason !== 'state' || root.hidden || settling) return;
      renderGain(root);
      renderProgress(root);
    });
  },

  leave() {
    if (typeof unsubscribeState === 'function') {
      unsubscribeState();
      unsubscribeState = null;
    }
  },
};
