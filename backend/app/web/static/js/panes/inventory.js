/**
 * panes/inventory.js · 背包（四页签 / 格子 / 使用·装备·出售·强化）
 *
 * 页签切换走 GET /inventory?tab=…（对齐 R63）；写操作后重取列表。
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, qsa, el, fill, fmt, toast, toastError, withBusy, renderLoading, renderError,
} from '../core/ui.js';
import router from '../core/router.js';

const TABS = ['pill', 'material', 'equipment', 'consumable'];
const KIND_LABELS = { pill: '丹药', material: '材料', equipment: '装备', consumable: '杂物' };

const ui = { tab: 'pill', data: null, items: [], selected: null };

function kindOf(item) {
  const kind = String(pick(item, 'kind', 'item_type', 'type') || '').toLowerCase();
  if (TABS.includes(kind)) return kind;
  if (kind === 'equip' || pick(item, 'slot', 'instance_id')) return 'equipment';
  return 'material';
}

function itemName(item) {
  return String(pick(item, 'name', 'item_name', 'display_name', 'title', 'item_id') || '无名之物');
}

function itemKey(item) {
  return String(pick(item, 'item_id', 'id', 'instance_id', 'key') || '');
}

function normalizeItems(data) {
  const raw = pick(data, 'items', 'list', 'inventory', 'entries');
  return arr(raw).map((item) => {
    if (item === null || item === undefined) return null;
    if (typeof item === 'string') return { item_id: item, name: item, kind: 'material', qty: 1 };
    return {
      ...item,
      kind: kindOf(item),
      name: itemName(item),
      key: itemKey(item),
      qty: num(pick(item, 'qty', 'count', 'amount', 'stack_count'), null),
    };
  }).filter(Boolean);
}

function capacityOf(data) {
  const cap = num(pick(data, 'slots', 'capacity', 'bag_slots', 'max_slots', 'slot_count'), null);
  const used = num(pick(data, 'used', 'used_slots', 'occupied'), null);
  return { cap, used };
}

function renderTabs(root) {
  const counts = {};
  TABS.forEach((tab) => { counts[tab] = 0; });
  ui.items.forEach((item) => {
    if (counts[item.kind] !== undefined) counts[item.kind] += 1;
  });
  TABS.forEach((tab) => {
    const badge = qs(`[data-tab-count="${tab}"]`, root);
    if (badge) badge.textContent = String(counts[tab]);
    const button = qs(`[data-tab="${tab}"]`, root);
    if (button) {
      const active = tab === ui.tab;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
  });
}

function renderCells(root) {
  const container = qs('#inv-cells', root);
  if (!container) return;
  const { cap, used } = capacityOf(ui.data);
  const shown = ui.items.filter((item) => item.kind === ui.tab);
  const total = cap || Math.max(shown.length, used || 0, 1);

  const cells = shown.map((item) => {
    const active = ui.selected && ui.selected.key === item.key && ui.selected.kind === item.kind;
    return el('button', {
      class: `cell${active ? ' is-active' : ''}`,
      type: 'button',
      dataset: { key: item.key, kind: item.kind },
      onclick: () => {
        ui.selected = item;
        renderCells(root);
        renderDetail(root);
      },
    }, [
      el('span', { class: 'cell__tag', text: KIND_LABELS[item.kind] || '物' }),
      el('span', { class: 'cell__name', text: item.name }),
      item.qty !== null && item.qty > 1 ? el('span', { class: 'cell__qty', text: `×${fmt(item.qty)}` }) : null,
      pick(item, 'enhance') ? el('span', { class: 'cell__tag', text: `+${num(pick(item, 'enhance'), 0)}` }) : null,
    ]);
  });

  for (let index = shown.length; index < total; index += 1) {
    cells.push(el('li', { class: 'cell cell--empty' }, [
      el('span', { class: 'cell__tag', text: '空' }),
    ]));
  }

  fill(container, cells);
  const sub = qs('#inventory-sub', root);
  if (sub) {
    const capText = cap ? `${used === null ? shown.length : used} / ${cap} 格` : `${shown.length} 件`;
    sub.textContent = `${capText} · ${KIND_LABELS[ui.tab] || ''}页`;
  }
  const hint = qs('#inv-hint', root);
  if (hint) {
    hint.textContent = cap && used !== null && used >= cap
      ? '行囊已满：再拾取将被拒（409 / 4015），先卖些无用之物。'
      : '格子上限随储物戒变化；满包时拾取会被拒绝。';
  }
}

function renderDetail(root) {
  const card = qs('#inv-detail', root);
  if (!card) return;
  const item = ui.selected;
  if (!item) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const title = qs('#inv-detail-title', root);
  if (title) title.textContent = item.name;
  const meta = qs('#inv-detail-meta', root);
  if (meta) {
    const bits = [KIND_LABELS[item.kind] || '物'];
    if (item.qty !== null) bits.push(`×${fmt(item.qty)}`);
    if (num(pick(item, 'price'), null) !== null) bits.push(`价 ${fmt(pick(item, 'price'))} 灵石`);
    meta.textContent = bits.join(' · ');
  }
  const desc = qs('#inv-detail-desc', root);
  if (desc) desc.textContent = pick(item, 'desc', 'description', 'flavor', 'effect_text') || '无说明。';

  const stats = qs('#inv-detail-stats', root);
  if (stats) {
    const reinforce = num(pick(item, 'enhance', 'refine', 'plus'), null);
    const rows = [
      ['堆叠上限', pick(item, 'stack', 'stack_max') ?? '—'],
      ['强化', reinforce === null ? '—' : `+${reinforce} / 5`],
      ['部位', pick(item, 'slot_name', 'slot') ?? '—'],
      ['品质', pick(item, 'rarity_name', 'rarity', 'tier') ?? '—'],
    ];
    fill(stats, rows.map(([label, value]) => el('div', { class: 'statgrid__item' }, [
      el('dt', { text: label }),
      el('dd', { text: String(value) }),
    ])));
  }

  const actions = qs('#inv-detail-actions', root);
  if (!actions) return;
  const buttons = [];
  const canUse = pick(item, 'can_use') !== false && (item.kind === 'pill' || item.kind === 'consumable');
  const isEquip = item.kind === 'equipment';
  const equipped = pick(item, 'equipped') === true;

  if (canUse) {
    buttons.push(el('button', {
      class: 'btn btn--primary btn--sm',
      type: 'button',
      text: '使用',
      onclick: (event) => useItem(root, item, event.currentTarget),
    }));
  }
  if (isEquip) {
    buttons.push(el('button', {
      class: 'btn btn--primary btn--sm',
      type: 'button',
      text: equipped ? '卸下' : '装备',
      onclick: (event) => toggleEquip(root, item, equipped, event.currentTarget),
    }));
    buttons.push(el('button', {
      class: 'btn btn--ghost btn--sm',
      type: 'button',
      text: '强化',
      disabled: num(pick(item, 'enhance'), 0) >= 5,
      title: num(pick(item, 'enhance'), 0) >= 5 ? '已至 +5 上限' : '',
      onclick: (event) => enhance(root, item, event.currentTarget),
    }));
  }
  buttons.push(el('button', {
    class: 'btn btn--danger btn--sm',
    type: 'button',
    text: '出售一件',
    onclick: (event) => sell(root, item, event.currentTarget),
  }));

  fill(actions, buttons);
}

async function reload(root, keepSelection = true) {
  ui.data = await api.inventory(ui.tab);
  const fetched = normalizeItems(ui.data);
  // 接口可能按页签过滤：只替换当前页签的数据，其它页签保留上次结果
  const others = ui.items.filter((item) => item.kind !== ui.tab);
  ui.items = [...others, ...fetched];
  renderTabs(root);
  renderCells(root);
  if (keepSelection && ui.selected) {
    const hit = ui.items.find((item) => item.key === ui.selected.key && item.kind === ui.selected.kind);
    ui.selected = hit || null;
  }
  renderDetail(root);
}

async function useItem(root, item, button) {
  await withBusy(button, async () => {
    try {
      const result = await api.itemUse(item.key, 1);
      const gain = pick(result, 'cultivation', 'gain', 'hp_gain', 'effect');
      toast(gain ? `服下${item.name}，${gain}` : `已用${item.name}`, 'info');
      await reload(root, false);
      ui.selected = null;
      renderDetail(root);
      await store.refresh({ force: true, silent: true });
    } catch (err) {
      toastError(err);
    }
  }, { label: '服用…' });
}

async function sell(root, item, button) {
  await withBusy(button, async () => {
    try {
      await api.itemSell(item.key, 1);
      toast(`已售出 1 件${item.name}`, 'info');
      await reload(root, false);
      ui.selected = null;
      renderDetail(root);
      await store.refresh({ force: true, silent: true });
    } catch (err) {
      toastError(err);
    }
  }, { label: '交易…' });
}

async function toggleEquip(root, item, equipped, button) {
  const instanceId = pick(item, 'instance_id', 'id');
  await withBusy(button, async () => {
    try {
      if (equipped) await api.itemUnequip(instanceId);
      else await api.itemEquip(instanceId);
      toast(equipped ? `已卸下${item.name}` : `已装备${item.name}`, 'info');
      await reload(root);
      await store.refresh({ force: true, silent: true });
    } catch (err) {
      toastError(err);
    }
  }, { label: equipped ? '卸下…' : '装备…' });
}

async function enhance(root, item, button) {
  const instanceId = pick(item, 'instance_id', 'id');
  await withBusy(button, async () => {
    try {
      const result = await api.itemEnhance(instanceId);
      const level = num(pick(result, 'enhance', 'level'), null);
      toast(level === null ? '强化已毕' : `强化至 +${level}`, 'info');
      await reload(root);
      await store.refresh({ force: true, silent: true });
    } catch (err) {
      toastError(err);
    }
  }, { label: '锻造…' });
}

async function load(root) {
  const container = qs('#inv-cells', root);
  renderLoading(container, '正在清点行囊…');
  try {
    ui.data = await api.inventory(ui.tab);
    ui.items = normalizeItems(ui.data);
    renderTabs(root);
    renderCells(root);
    renderDetail(root);
    // 首屏再取一次全量，只为四页签角标（服务端若忽略 tab 参数则等价于同一次请求）
    try {
      const full = await api.inventory();
      const merged = new Map();
      [...normalizeItems(full), ...ui.items].forEach((item) => {
        merged.set(`${item.kind}:${item.key}`, item);
      });
      ui.items = Array.from(merged.values());
      renderTabs(root);
      renderCells(root);
    } catch (err) {
      /* 角标失败不影响主流程 */
    }
  } catch (err) {
    if (err.code === '4003') {
      router.go('creation');
      return;
    }
    toastError(err);
    renderError(container, err, () => load(root));
  }
}

export default {
  name: 'inventory',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      qsa('[data-tab]', root).forEach((button) => {
        button.addEventListener('click', async () => {
          ui.tab = button.dataset.tab;
          ui.selected = null;
          renderDetail(root);
          await load(root);
        });
      });
    }
    await load(root);
  },
};
