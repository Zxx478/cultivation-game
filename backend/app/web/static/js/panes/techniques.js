/**
 * panes/techniques.js · 功法（已装备 1+3+3+3 / 功法库 / 学习·参悟·装备）
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, qsa, el, fill, fmt, fmtPct, setBar, toast, toastError, withBusy, renderLoading, renderError,
} from '../core/ui.js';
import router from '../core/router.js';

const SLOTS = ['heart', 'atk1', 'atk2', 'atk3', 'def1', 'def2', 'def3', 'sup1', 'sup2', 'sup3'];
const SLOT_LABELS = {
  heart: '心法', atk1: '攻一', atk2: '攻二', atk3: '攻三',
  def1: '防一', def2: '防二', def3: '防三',
  sup1: '辅一', sup2: '辅二', sup3: '辅三',
};
const TIER_LABELS = { fan: '凡', ling: '灵', bao: '宝', sheng: '圣', di: '帝' };
const SUB_LABELS = { xia: '下', zhong: '中', shang: '上' };
const TYPE_LABELS = { heart: '心法', attack: '攻法', defense: '防法', support: '辅法' };

const ui = { tier: 'all', learned: 'all', selected: null, data: null };

function tierOf(tech) {
  const tier = String(pick(tech, 'tier', 'grade') || 'fan').toLowerCase();
  return TIER_LABELS[tier] ? tier : 'fan';
}

function tierText(tech) {
  const tier = tierOf(tech);
  const sub = String(pick(tech, 'sub_tier', 'sub') || '').toLowerCase();
  return `${TIER_LABELS[tier]}${SUB_LABELS[sub] || ''}` || '凡';
}

function typeText(tech) {
  const type = String(pick(tech, 'type', 'kind') || '').toLowerCase();
  return TYPE_LABELS[type] || String(pick(tech, 'type_name') || '—');
}

function normalizeEquipped(raw, library) {
  const map = {};
  if (raw && !Array.isArray(raw) && typeof raw === 'object') {
    SLOTS.forEach((slot) => {
      const hit = raw[slot];
      if (hit) map[slot] = hit;
    });
    ['attack', 'defense', 'support'].forEach((group) => {
      const prefix = { attack: 'atk', defense: 'def', support: 'sup' }[group];
      arr(raw[group]).forEach((tech, index) => {
        if (index < 3) map[`${prefix}${index + 1}`] = tech;
      });
    });
  }
  arr(raw).forEach((tech) => {
    const slot = pick(tech, 'equipped_slot', 'slot');
    if (slot && SLOTS.includes(slot) && !map[slot]) map[slot] = tech;
  });
  library.forEach((tech) => {
    const slot = pick(tech, 'equipped_slot', 'slot');
    if (slot && SLOTS.includes(slot) && !map[slot]) map[slot] = tech;
  });
  return map;
}

function techName(tech) {
  return String(pick(tech, 'name', 'tech_name', 'display_name', 'title', 'id') || '未名功法');
}

function techId(tech) {
  return pick(tech, 'tech_id', 'id', 'key');
}

function isLearned(tech) {
  const flag = pick(tech, 'learned', 'owned', 'studied', 'is_learned');
  if (flag !== undefined) return Boolean(flag);
  return num(pick(tech, 'layer'), 0) > 0;
}

function findFreeSlot(type, equipped) {
  const prefix = { heart: 'heart', attack: 'atk', defense: 'def', support: 'sup' }[type] || 'atk';
  if (prefix === 'heart') return equipped.heart ? null : 'heart';
  for (let index = 1; index <= 3; index += 1) {
    if (!equipped[`${prefix}${index}`]) return `${prefix}${index}`;
  }
  return `${prefix}1`;
}

function renderEquipped(root) {
  const equipped = normalizeEquipped(pick(ui.data, 'equipped', 'slots'), arr(pick(ui.data, 'library', 'list', 'techniques', 'items')));
  let count = 0;
  SLOTS.forEach((slot) => {
    const node = qs(`[data-slot="${slot}"]`, root);
    if (!node) return;
    const tech = equipped[slot];
    const nameNode = qs('[data-slot-name]', node);
    node.classList.toggle('is-filled', Boolean(tech));
    node.classList.toggle('is-active', Boolean(ui.selected) && tech && techId(tech) === techId(ui.selected));
    if (tech) {
      count += 1;
      if (nameNode) nameNode.textContent = techName(tech);
      node.dataset.tech = String(techId(tech) || '');
      node.dataset.tier = tierOf(tech);
      node.title = `${techName(tech)} · ${tierText(tech)}${num(pick(tech, 'layer')) !== null ? ` · 第 ${num(pick(tech, 'layer'))} 层` : ''}`;
    } else {
      if (nameNode) nameNode.textContent = '空';
      delete node.dataset.tech;
      delete node.dataset.tier;
      node.title = `${SLOT_LABELS[slot]}：尚未装备`;
    }
  });

  const counter = qs('#tech-equipped-count', root);
  if (counter) counter.textContent = `已装备 ${count} / 10`;
}

function passesFilters(tech) {
  if (ui.tier !== 'all' && tierOf(tech) !== ui.tier) return false;
  if (ui.learned === 'yes' && !isLearned(tech)) return false;
  if (ui.learned === 'no' && isLearned(tech)) return false;
  return true;
}

function renderLibrary(root) {
  const list = qs('#tech-list', root);
  const counter = qs('#tech-count', root);
  if (!list) return;
  const library = arr(pick(ui.data, 'library', 'list', 'techniques', 'items'));
  const shown = library.filter(passesFilters);

  if (counter) counter.textContent = `${shown.length} / ${library.length} 部`;
  if (!library.length) {
    fill(list, el('li', { class: 'empty', text: '功法库尚空；先入宗门或以贡献换取。' }));
    return;
  }
  if (!shown.length) {
    fill(list, el('li', { class: 'empty', text: '此筛选下无功法。' }));
    return;
  }

  fill(list, shown.map((tech) => {
    const id = techId(tech);
    const learned = isLearned(tech);
    const equippedSlot = pick(tech, 'equipped_slot', 'slot');
    return el('li', {
      class: 'techitem',
      dataset: { tier: tierOf(tech), id: String(id || '') },
    }, [
      el('button', {
        class: 'techitem__main',
        type: 'button',
        onclick: () => selectTech(root, tech),
        style: { textAlign: 'left', background: 'transparent', border: '0', padding: '0' },
      }, [
        el('span', { class: 'techitem__name', text: techName(tech) }),
        el('span', {
          class: 'techitem__meta',
          text: `${tierText(tech)} · ${typeText(tech)}${num(pick(tech, 'layer')) !== null ? ` · 第 ${num(pick(tech, 'layer'))} 层` : ''}${equippedSlot ? ' · 已装备' : ''}`,
        }),
      ]),
      el('span', { class: 'techitem__side' }, [
        learned ? null : el('button', {
          class: 'btn btn--ghost btn--sm',
          type: 'button',
          text: '学习',
          onclick: (event) => learn(root, tech, event.currentTarget),
          disabled: pick(tech, 'can_learn') === false,
          title: pick(tech, 'learn_reason', 'reason') || '',
        }),
        learned ? el('button', {
          class: 'btn btn--ghost btn--sm',
          type: 'button',
          text: '参悟',
          onclick: (event) => meditate(root, tech, event.currentTarget),
          disabled: pick(tech, 'can_meditate') === false,
        }) : null,
        learned && !equippedSlot ? el('button', {
          class: 'btn btn--ghost btn--sm',
          type: 'button',
          text: '装备',
          onclick: (event) => equip(root, tech, event.currentTarget),
        }) : null,
        learned && equippedSlot ? el('button', {
          class: 'btn btn--danger btn--sm',
          type: 'button',
          text: '卸下',
          onclick: (event) => unequip(root, tech, equippedSlot, event.currentTarget),
        }) : null,
      ]),
    ]);
  }));
}

function selectTech(root, tech) {
  ui.selected = tech;
  const card = qs('#tech-detail', root);
  if (!card) return;
  card.hidden = false;
  const title = qs('#tech-detail-title', root);
  if (title) title.textContent = techName(tech);
  const tier = qs('#tech-detail-tier', root);
  if (tier) tier.textContent = `${tierText(tech)} · ${typeText(tech)}`;

  const desc = qs('#tech-detail-desc', root);
  if (desc) desc.textContent = pick(tech, 'desc', 'description', 'flavor', 'note') || '此功法来历不明。';

  const layer = num(pick(tech, 'layer'), 0);
  const maxLayer = num(pick(tech, 'max_layer'), 9);
  const sense = num(pick(tech, 'sense'), null);
  const senseNeed = num(pick(tech, 'sense_need', 'sense_required', 'need_sense'), null);
  const ratio = sense !== null && senseNeed ? Math.min(1, sense / senseNeed) : null;
  setBar(qs('[data-bar="sense"]', root), ratio,
    sense === null ? '—' : `${fmt(sense)} / ${fmt(senseNeed)}${ratio !== null ? `（${fmtPct(ratio)}）` : ''}`);

  const stats = qs('#tech-detail-stats', root);
  if (stats) {
    const items = [
      ['层数', `${layer} / ${maxLayer}`],
      ['类型', typeText(tech)],
      ['品阶', tierText(tech)],
      ['所需境界', pick(tech, 'required_realm_name', 'required_realm_index', 'required_realm') ?? '—'],
      ['来源', pick(tech, 'source_text', 'source') ?? '—'],
      ['学习状态', isLearned(tech) ? '已学' : '未学'],
    ];
    fill(stats, items.map(([label, value]) => el('div', { class: 'statgrid__item' }, [
      el('dt', { text: label }),
      el('dd', { text: String(value === null || value === undefined ? '—' : value) }),
    ])));
  }

  const actions = qs('#tech-detail-actions', root);
  if (actions) {
    const equippedSlot = pick(tech, 'equipped_slot', 'slot');
    const buttons = [];
    if (!isLearned(tech)) {
      buttons.push(el('button', {
        class: 'btn btn--primary btn--sm',
        type: 'button',
        text: '学习此功法',
        onclick: (event) => learn(root, tech, event.currentTarget),
      }));
    } else {
      buttons.push(el('button', {
        class: 'btn btn--ghost btn--sm',
        type: 'button',
        text: '参悟一层',
        onclick: (event) => meditate(root, tech, event.currentTarget),
      }));
      if (equippedSlot) {
        buttons.push(el('button', {
          class: 'btn btn--danger btn--sm',
          type: 'button',
          text: '卸下',
          onclick: (event) => unequip(root, tech, equippedSlot, event.currentTarget),
        }));
      } else {
        buttons.push(el('button', {
          class: 'btn btn--primary btn--sm',
          type: 'button',
          text: '装备',
          onclick: (event) => equip(root, tech, event.currentTarget),
        }));
      }
    }
    fill(actions, buttons);
  }
  renderEquipped(root);
}

async function afterWrite(root, message, button) {
  if (message) toast(message, 'info');
  await withBusy(button || null, async () => {
    try {
      ui.data = await api.techniques();
      renderEquipped(root);
      renderLibrary(root);
      if (ui.selected) {
        const id = techId(ui.selected);
        const fresh = arr(pick(ui.data, 'library', 'list', 'techniques', 'items')).find((item) => techId(item) === id);
        if (fresh) selectTech(root, fresh);
      }
      await store.refresh({ force: true, silent: true });
    } catch (err) {
      toastError(err);
    }
  }, { label: '…' });
}

async function learn(root, tech, button) {
  await withBusy(button, async () => {
    try {
      const result = await api.techniqueLearn(techId(tech));
      const ok = pick(result, 'success', 'learned', 'ok');
      toast(ok === false ? (pick(result, 'message', 'reason') || '学习未成') : `已学《${techName(tech)}》`, ok === false ? 'warn' : 'info');
      ui.data = await api.techniques();
      renderEquipped(root);
      renderLibrary(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '推演…' });
}

async function meditate(root, tech, button) {
  await withBusy(button, async () => {
    try {
      const result = await api.techniqueMeditate(techId(tech));
      const layerUp = pick(result, 'level_up', 'layer_up', 'upgraded');
      toast(layerUp ? `《${techName(tech)}》更进一层` : `参悟 +${fmt(pick(result, 'sense_gain', 'gain', 'sense') ?? '—')}`, 'info');
      ui.data = await api.techniques();
      renderEquipped(root);
      renderLibrary(root);
      const id = techId(tech);
      const fresh = arr(pick(ui.data, 'library', 'list', 'techniques', 'items')).find((item) => techId(item) === id);
      if (fresh) selectTech(root, fresh);
    } catch (err) {
      toastError(err);
    }
  }, { label: '参悟…' });
}

async function equip(root, tech, button) {
  const equipped = normalizeEquipped(pick(ui.data, 'equipped', 'slots'), []);
  const slot = findFreeSlot(String(pick(tech, 'type', 'kind') || 'attack').toLowerCase(), equipped);
  if (!slot) {
    toast('心法槽已满，需先卸下一部', 'warn');
    return;
  }
  await withBusy(button, async () => {
    try {
      await api.techniqueEquip(techId(tech), slot);
      toast(`《${techName(tech)}》已入${SLOT_LABELS[slot]}`, 'info');
      ui.data = await api.techniques();
      renderEquipped(root);
      renderLibrary(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '装备…' });
}

async function unequip(root, tech, slot, button) {
  await withBusy(button, async () => {
    try {
      await api.techniqueUnequip(techId(tech), slot);
      toast(`已卸下《${techName(tech)}》`, 'info');
      ui.data = await api.techniques();
      renderEquipped(root);
      renderLibrary(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '卸下…' });
}

async function load(root) {
  const list = qs('#tech-list', root);
  renderLoading(list, '正在翻检功法…');
  try {
    ui.data = await api.techniques();
    renderEquipped(root);
    renderLibrary(root);
    const equipped = normalizeEquipped(pick(ui.data, 'equipped', 'slots'), []);
    const first = arr(pick(ui.data, 'library', 'list', 'techniques', 'items'))[0];
    const initial = SLOTS.map((slot) => equipped[slot]).find(Boolean) || first;
    if (initial) selectTech(root, initial);
  } catch (err) {
    if (err.code === '4003') {
      router.go('creation');
      return;
    }
    toastError(err);
    renderError(list, err, () => load(root));
  }
}

export default {
  name: 'techniques',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      qsa('.filters [data-tier]', root).forEach((button) => {
        button.addEventListener('click', () => {
          ui.tier = button.dataset.tier;
          qsa('.filters [data-tier]', root).forEach((node) => node.classList.toggle('is-active', node === button));
          renderLibrary(root);
        });
      });
      qsa('.filters [data-learned]', root).forEach((button) => {
        button.addEventListener('click', () => {
          ui.learned = button.dataset.learned;
          qsa('.filters [data-learned]', root).forEach((node) => node.classList.toggle('is-active', node === button));
          renderLibrary(root);
        });
      });
      qsa('.slot', root).forEach((slotNode) => {
        slotNode.addEventListener('click', () => {
          const id = slotNode.dataset.tech;
          if (!id) return;
          const library = arr(pick(ui.data, 'library', 'list', 'techniques', 'items'));
          const equipped = normalizeEquipped(pick(ui.data, 'equipped', 'slots'), []);
          const hit = SLOTS.map((slot) => equipped[slot]).find((tech) => tech && String(techId(tech)) === id)
            || library.find((tech) => String(techId(tech)) === id);
          if (hit) selectTech(root, hit);
        });
      });
    }
    await load(root);
  },
};
