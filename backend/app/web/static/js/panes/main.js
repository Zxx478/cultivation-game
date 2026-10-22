/**
 * panes/main.js · 洞府
 *
 * 展示修为/气血/灵力/寿元与灵石，提供五类行动与持续闭关。
 * 所有数值来自 /state；行动结果来自 /action。
 */

import { store, pick, arr, num } from '../core/state.js';
import { performAction, refreshAndSync, renderSettle, blockReason } from '../core/actions.js';
import {
  qs, qsa, el, fill, fmt, fmtPct, fmtYears, setBar, toast, toastError, withBusy,
} from '../core/ui.js';
import router from '../core/router.js';

const ACTION_HINTS = {
  seclusion: '闭关三十日，修为渐长；寿元亦随之而去。',
  adventure: '历练十五日，或遇敌、或得物；不保证活着回来。',
  gather: '采药十日，拾些草木换灵石。',
  spar: '切磋三日，与人试手，胜负皆有所得。',
  meditate: '打坐十日，回两成气血，不动修为。',
};

function renderBars(root) {
  const view = store.view;

  setBar(qs('[data-bar="exp"]', root), view.expPct, view.expNeed
    ? `${fmt(view.cultivation)} / ${fmt(view.expNeed)}（${fmtPct(view.expPct)}）`
    : '—');

  setBar(qs('[data-bar="hp"]', root), view.hpMax ? view.hp / view.hpMax : null,
    view.hpMax ? `${fmt(view.hp)} / ${fmt(view.hpMax)}` : '—');

  setBar(qs('[data-bar="mp"]', root), view.mpMax ? view.mp / view.mpMax : null,
    view.mpMax ? `${fmt(view.mp)} / ${fmt(view.mpMax)}` : '—');

  setBar(qs('[data-bar="life"]', root), view.lifePct,
    view.remainingYears === null ? '—' : fmtYears(view.remainingYears), { warn: true });

  const nameNode = qs('[data-cc="name"]', root);
  if (nameNode) nameNode.textContent = view.name;

  const statMap = {
    stone: fmt(view.spiritStone),
    karma: fmt(view.karma),
    dao_heart: view.daoHeart === null ? '—' : `${fmt(view.daoHeart)} / 100`,
    foundation: view.foundation ? String(view.foundation) : '未定',
  };
  Object.keys(statMap).forEach((key) => {
    const node = qs(`[data-vital="${key}"]`, root);
    if (node) node.textContent = statMap[key];
  });
}

function renderHead(root) {
  const view = store.view;
  const sub = qs('#main-sub', root);
  if (sub) {
    const parts = [];
    if (view.realmName) parts.push(view.realmName);
    if (view.regionName) parts.push(`身在 ${view.regionName}`);
    parts.push('一年三百六十日，过一日少一日');
    sub.textContent = parts.join(' · ');
  }

  const regionNode = qs('#action-region', root);
  if (regionNode) {
    const bits = [];
    if (view.regionName) bits.push(view.regionName);
    if (view.regionEnv !== null) bits.push(`环境 ×${view.regionEnv}`);
    bits.push(view.closedDoorTimes === null ? '还能闭关 —' : `还能闭关 ${fmt(view.closedDoorTimes)} 次`);
    regionNode.textContent = bits.join(' · ');
  }

  const hint = qs('#action-hint', root);
  const block = blockReason(view);
  if (hint) hint.textContent = block || ACTION_HINTS.seclusion;

  const actionsGroup = qs('#actionbar', root);
  qsa('button.action', actionsGroup || root).forEach((button) => {
    const blocked = Boolean(block) || view.status === 'dead' || view.status === 'deceased';
    button.disabled = blocked;
    button.setAttribute('aria-disabled', blocked ? 'true' : 'false');
    if (blocked) button.title = block || '此身已寂，不可再行';
    else button.removeAttribute('title');
  });

  const repeat = qs('#repeat-count', root);
  if (repeat) repeat.disabled = Boolean(block);

  const breakBtn = qs('#main-breakthrough', root);
  if (breakBtn) {
    breakBtn.hidden = !view.canBreakthrough;
    if (view.canBreakthrough) {
      breakBtn.textContent = view.realmName ? `冲击下一境（${view.realmName}）` : '冲击下一境';
    }
  }

  const prologueCard = qs('#main-prologue', root);
  if (prologueCard) {
    const text = view.prologue || pick(view.raw, 'guide.prologue', 'prologue_text');
    prologueCard.hidden = !text;
    if (text) {
      const node = qs('#main-prologue-text', root);
      if (node) node.textContent = String(text);
    }
  }
}

function renderQuests(root) {
  const list = qs('#main-questlist', root);
  if (!list) return;
  const view = store.view;
  const mainQuest = view.mainQuest;
  const lines = [];

  if (mainQuest && typeof mainQuest === 'object') {
    lines.push({
      title: pick(mainQuest, 'title', 'name') || '当前主线',
      meta: pick(mainQuest, 'desc', 'objective_text', 'progress_text') || '—',
      badge: pick(mainQuest, 'status') || 'active',
      href: '#/quests',
    });
  }

  arr(pick(view.raw, 'quests', 'main_quests')).slice(0, 3).forEach((quest) => {
    const status = pick(quest, 'status');
    if (status !== 'ready' && status !== 'active') return;
    lines.push({
      title: pick(quest, 'title', 'name') || '未名之事',
      meta: pick(quest, 'desc', 'progress_text') || '—',
      badge: status,
      href: '#/quests',
    });
  });

  if (!lines.length) {
    fill(list, el('li', null, [
      el('span', { class: 'tasklist__title muted', text: '暂无眼前之事，且自修行。' }),
    ]));
    return;
  }

  fill(list, lines.map((line) => el('li', null, [
    el('div', null, [
      el('p', { class: 'tasklist__title', text: String(line.title) }),
      el('p', { class: 'tasklist__meta', text: String(line.meta) }),
    ]),
    el('a', { class: 'btn btn--ghost btn--sm', href: line.href, text: '查看' }),
  ])));
}

function showResult(root, data) {
  const card = qs('#main-result', root);
  const body = qs('#main-result-body', root);
  if (!card || !body) return;
  card.hidden = false;
  const head = qs('.card__title', card);
  if (head) head.textContent = '结算';
  renderSettle(body, data);
}

async function runAction(root, button) {
  const kind = button.dataset.action;
  const useRepeat = button.dataset.repeat === '1';
  const repeatNode = qs('#repeat-count', root);
  const repeat = useRepeat && repeatNode ? Number(repeatNode.value) : null;
  const group = qs('#actionbar', root);

  await withBusy(button, async () => {
    try {
      const data = await performAction(kind, { repeat, expectedVersion: store.view.version });
      showResult(root, data);
      const lines = [];
      if (data && typeof data === 'object') {
        const days = num(pick(data, 'total_days', 'settle.total_days', 'days'));
        const exp = num(pick(data, 'total_exp', 'settle.total_exp', 'settle.exp'));
        if (repeat) lines.push(`连闭关 ${repeat} 次`);
        if (days !== null) lines.push(`${fmt(days)} 日`);
        if (exp !== null) lines.push(`修为 +${fmt(exp)}`);
      }
      toast(lines.length ? lines.join('，') : '行动已结算', 'info');
      await refreshAndSync();
      renderBars(root);
      renderHead(root);
    } catch (err) {
      toastError(err);
      if (err.code === '4003') router.go('creation');
    }
  }, { label: '结算中…', group });
}

export default {
  name: 'main',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }

    renderHead(root);
    renderBars(root);
    renderQuests(root);

    const actionbar = qs('#actionbar', root);
    if (actionbar && !actionbar.dataset.bound) {
      actionbar.dataset.bound = '1';
      actionbar.addEventListener('click', (event) => {
        const button = event.target.closest('button.action');
        if (!button || button.disabled) return;
        runAction(root, button);
      });
    }

    const breakBtn = qs('#main-breakthrough', root);
    if (breakBtn && !breakBtn.dataset.bound) {
      breakBtn.dataset.bound = '1';
      breakBtn.addEventListener('click', () => {
        document.dispatchEvent(new CustomEvent('game:open-breakthrough'));
      });
    }
  },
};
