/**
 * panes/main.js · 洞府
 *
 * 展示修为/气血/灵力/寿元与灵石，提供五类行动与持续闭关。
 * 所有数值来自 /state；行动结果来自 /action。
 *
 * 「今日行事」的耗时不是写死的常量：
 *   实际日数 = 基础日数 × 本境档位 ÷ 档位基准(30)
 * 由服务端按角色当前境界查表算好后随 state 下发（action_days / time_tier），
 * 本文件只负责显示。境界一变，store 广播 'state' → 订阅回调重绘 → 按钮上的日数立刻跟上。
 */

import { store, pick, arr, num } from '../core/state.js';
import { performAction, refreshAndSync, renderSettle, blockReason } from '../core/actions.js';
import {
  qs, qsa, el, fill, fmt, fmtPct, fmtYears, setBar, toast, toastError, withBusy,
} from '../core/ui.js';
import router from '../core/router.js';

/** 五类行事的名称与所得（耗时一律来自服务端，此处绝不写日数） */
const ACTIONS = [
  { kind: 'seclusion', name: '闭关', yield: '修为渐长，寿元随之而去' },
  { kind: 'adventure', name: '历练', yield: '或遇敌、或得物，不保证活着回来' },
  { kind: 'gather', name: '采药', yield: '拾些草木，换些灵石' },
  { kind: 'spar', name: '切磋', yield: '与人试手，胜负皆有所得' },
  { kind: 'meditate', name: '打坐', yield: '回两成气血，不动修为' },
];

/** 状态订阅句柄：leave 时解绑，避免离开洞府后还在后台重绘 */
let unsubscribeState = null;
/** 结算进行中：按钮子节点被 withBusy 临时摘下，重绘写不进去，故推迟到收尾统一重绘 */
let settling = false;

/* ---------- 时间口径：全部读服务端下发的字段 ---------- */

/** 某一类行事在本境的耗时（日）；未下发返回 null */
function dayCost(view, kind) {
  const map = view.actionDays;
  if (!map || typeof map !== 'object') return null;
  return num(map[kind]);
}

function dayText(days) {
  return days === null ? '—' : `${fmt(days)} 日`;
}

/** 超过一年（360 日）时补一个年数，免得后期只看到一串零 */
function daySpan(days) {
  if (days === null || days < 360) return '';
  const years = days / 360;
  const rounded = years >= 10 ? Math.round(years) : Math.round(years * 10) / 10;
  return `约 ${fmt(rounded)} 年`;
}

/** 「持续闭关」当前选中的次数 */
function repeatTimes(root) {
  const node = qs('#repeat-count', root);
  const value = node ? Number(node.value) : NaN;
  return Number.isFinite(value) && value > 1 ? Math.floor(value) : 1;
}

/* ============================================================
   渲染
   ============================================================ */

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

/** 面板头部 + 按钮可用性（时间文字不在这里，见 renderActions） */
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
    if (view.gameYear !== null) bits.push(`第 ${fmt(view.gameYear)} 年`);
    regionNode.textContent = bits.length ? bits.join(' · ') : '—';
  }

  const block = blockReason(view);
  const hint = qs('#action-hint', root);
  if (hint) {
    const once = dayCost(view, 'seclusion');
    hint.textContent = block
      || (once === null
        ? '闭关耗日，寿元随之而去。慎重。'
        : `本境闭关一次 ${dayText(once)}，寿元随之而去。境界提升后，所有行事耗时按新档位重算。`);
  }

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
      const cost = dayCost(view, 'breakthrough');
      const label = view.realmName ? `冲击下一境（${view.realmName}）` : '冲击下一境';
      breakBtn.textContent = cost === null ? label : `${label} · ${dayText(cost)}`;
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

/** 档位条 + 按钮上的耗时 + 细目表：境界一变，这一整块跟着变 */
function renderActions(root) {
  const view = store.view;

  // 1) 每个按钮上的实际耗时
  qsa('[data-cost]', root).forEach((node) => {
    const kind = node.dataset.cost;
    if (kind === 'repeat') {
      const once = dayCost(view, 'seclusion');
      node.textContent = once === null ? '—' : dayText(once * repeatTimes(root));
      return;
    }
    node.textContent = dayText(dayCost(view, kind));
  });

  // 2) 本境档位（序列：1 个月 / 3 个月 / … / 1 万年）
  const tierNode = qs('#action-tier', root);
  if (tierNode) tierNode.textContent = view.timeTierLabel || '—';

  const tierDaysNode = qs('#action-tier-days', root);
  if (tierDaysNode) {
    tierDaysNode.textContent = view.timeTierDays === null
      ? '一档 —'
      : `一档 ${dayText(view.timeTierDays)}`;
  }

  const leftNode = qs('#action-left', root);
  if (leftNode) {
    if (view.closedDoorTimes === null) {
      leftNode.textContent = '还能闭关 — 次';
      leftNode.classList.remove('actionrule__left--warn');
    } else {
      leftNode.textContent = `还能闭关 ${fmt(view.closedDoorTimes)} 次`;
      leftNode.classList.toggle('actionrule__left--warn', view.closedDoorTimes < 1);
    }
  }

  // 3) 规则说明：把「怎么算出来的」写清楚，玩家不用猜
  const lawNode = qs('#action-law', root);
  if (lawNode) {
    const tierDays = view.timeTierDays;
    if (tierDays === null) {
      lawNode.textContent = '本境档位未下发，暂按基础日数计。';
    } else {
      const ratio = Math.round((tierDays / 30) * 100) / 100;
      lawNode.textContent = [
        `耗时 = 基础日数 × 本境档位 ${fmt(tierDays)} 日 ÷ 30`,
        `本境倍率 ×${ratio}`,
        '境界提升后自动重算',
      ].join('；') + '。';
    }
  }

  // 4) 细目：逐条列出耗时与所得
  renderActionList(root);
}

function renderActionList(root) {
  const list = qs('#action-list', root);
  if (!list) return;
  const view = store.view;

  const rows = ACTIONS.map((item) => ({ ...item, days: dayCost(view, item.kind) }));
  // 冲境同样按档位缩放，但它不是可反复点的行事，故只进细目、不占按钮
  rows.push({
    kind: 'breakthrough',
    name: '冲境',
    days: dayCost(view, 'breakthrough'),
    yield: '需境界圆满方可为之',
  });

  fill(list, rows.map((row) => {
    const span = daySpan(row.days);
    return el('li', { class: 'actionlist__item' }, [
      el('span', { class: 'actionlist__name', text: row.name }),
      el('b', { class: 'actionlist__days', text: dayText(row.days) }),
      el('span', { class: 'actionlist__note', text: span ? `${row.yield} · ${span}` : row.yield }),
    ]);
  }));
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

/** 一次重绘所有与「时间」有关的区块 */
function renderAll(root) {
  renderBars(root);
  renderHead(root);
  renderActions(root);
}

/* ============================================================
   交互
   ============================================================ */

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
  const repeat = useRepeat ? repeatTimes(root) : null;
  const group = qs('#actionbar', root);

  settling = true;
  try {
    await withBusy(button, async () => {
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
      // 耗时与余日变了，刷新状态；订阅回调此时被 settling 挡住，收尾统一重绘
      await refreshAndSync();
    }, { label: '结算中…', group });
  } catch (err) {
    toastError(err);
    if (err.code === '4003') router.go('creation');
  } finally {
    settling = false;
    renderAll(root);
  }
}

function bindInteractions(root) {
  const actionbar = qs('#actionbar', root);
  if (actionbar && !actionbar.dataset.bound) {
    actionbar.dataset.bound = '1';
    actionbar.addEventListener('click', (event) => {
      const button = event.target.closest('button.action');
      if (!button || button.disabled) return;
      runAction(root, button);
    });
  }

  // 换连闭关次数 → 合计日数立刻重算
  const repeatNode = qs('#repeat-count', root);
  if (repeatNode && !repeatNode.dataset.bound) {
    repeatNode.dataset.bound = '1';
    repeatNode.addEventListener('change', () => renderActions(root));
  }

  const breakBtn = qs('#main-breakthrough', root);
  if (breakBtn && !breakBtn.dataset.bound) {
    breakBtn.dataset.bound = '1';
    breakBtn.addEventListener('click', () => {
      document.dispatchEvent(new CustomEvent('game:open-breakthrough'));
    });
  }
}

export default {
  name: 'main',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }

    renderAll(root);
    renderQuests(root);
    bindInteractions(root);

    // 境界变化 → 服务端重算档位 → store 广播 → 这里重绘，面板自动跟上新日数
    if (typeof unsubscribeState === 'function') unsubscribeState();
    unsubscribeState = store.subscribe((view, reason) => {
      if (reason !== 'state') return;
      if (root.hidden) return;
      if (settling) return;
      renderAll(root);
      renderQuests(root);
    });
  },

  leave() {
    if (typeof unsubscribeState === 'function') {
      unsubscribeState();
      unsubscribeState = null;
    }
  },
};
