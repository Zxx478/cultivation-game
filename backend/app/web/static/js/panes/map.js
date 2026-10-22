/**
 * panes/map.js · 地图（20 区域，未解锁灰显；前往耗时与环境系数）
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, el, fill, fmt, toast, toastError, withBusy, renderLoading, renderError,
} from '../core/ui.js';
import { refreshAndSync } from '../core/actions.js';
import router from '../core/router.js';

const ui = { data: null, regions: [] };

function regionName(region) {
  return String(pick(region, 'name', 'display_name', 'region_name') || pick(region, 'id', 'region_id') || '未名之地');
}

function regionId(region) {
  return pick(region, 'region_id', 'id', 'key');
}

function isEnabled(region) {
  const enabled = pick(region, 'enabled', 'open', 'available');
  if (enabled !== undefined) return Boolean(enabled);
  const unlocked = pick(region, 'unlocked', 'is_unlocked');
  return unlocked === undefined ? true : Boolean(unlocked);
}

function lockReason(region) {
  const reason = pick(region, 'lock_reason', 'reason', 'unlock_reason', 'block_reason');
  if (reason) return String(reason);
  if (isEnabled(region)) return '';
  const levelMin = num(pick(region, 'level_min', 'min_level'), null);
  if (levelMin !== null) return `需 Lv.${levelMin} 方可踏足`;
  return '尚未开放';
}

function materialsText(region) {
  const raw = pick(region, 'materials', 'material_names');
  const note = pick(region, 'material_note');
  if (Array.isArray(raw) && raw.length) {
    return raw.map((item) => (typeof item === 'string' ? item : pick(item, 'name', 'label', 'item_id'))).filter(Boolean).join('、');
  }
  if (typeof raw === 'string' && raw) return raw;
  return note ? String(note) : '—';
}

function renderCurrent(root) {
  const currentId = pick(ui.data, 'current.region_id', 'current.id', 'current_region_id', 'region_id') || store.view.regionId;
  const current = ui.regions.find((region) => String(regionId(region)) === String(currentId)) || pick(ui.data, 'current', null);

  const nameNode = qs('#map-current', root);
  if (nameNode) nameNode.textContent = current ? regionName(current) : (store.view.regionName || '—');

  const envNode = qs('#map-current-env', root);
  if (envNode) {
    const env = num(pick(current, 'env_coef', 'coef'), store.view.regionEnv);
    const label = pick(current, 'env_label');
    envNode.textContent = env === null ? '—' : `×${env}${label ? `（${label}）` : ''}`;
  }

  const levelNode = qs('#map-current-level', root);
  if (levelNode) {
    const min = num(pick(current, 'level_min'), null);
    const max = num(pick(current, 'level_max'), null);
    levelNode.textContent = min === null && max === null ? '—' : `Lv.${min ?? '?'} – ${max ?? '?'}`;
  }

  const matsNode = qs('#map-current-mats', root);
  if (matsNode) matsNode.textContent = current ? materialsText(current) : '—';

  const meta = qs('#map-current-meta', root);
  if (meta) {
    const bits = [];
    if (pick(current, 'desc', 'description')) bits.push(String(pick(current, 'desc', 'description')));
    if (num(pick(current, 'travel_days'), null) !== null) bits.push(`此处可久居`);
    meta.textContent = bits.join(' ');
  }
}

function renderGrid(root) {
  const grid = qs('#map-grid', root);
  const counter = qs('#map-count', root);
  if (!grid) return;
  const currentId = pick(ui.data, 'current.region_id', 'current.id', 'current_region_id', 'region_id') || store.view.regionId;

  if (counter) {
    const open = ui.regions.filter(isEnabled).length;
    counter.textContent = `${ui.regions.length} 处 · 已开 ${open} 处`;
  }

  if (!ui.regions.length) {
    fill(grid, el('li', { class: 'empty', text: '舆图未载。' }));
    return;
  }

  fill(grid, ui.regions.map((region) => {
    const id = regionId(region);
    const isCurrent = String(id) === String(currentId);
    const enabled = isEnabled(region);
    const levelMin = num(pick(region, 'level_min'), null);
    const levelMax = num(pick(region, 'level_max'), null);
    const env = num(pick(region, 'env_coef', 'coef'), null);
    const travelDays = num(pick(region, 'travel_days', 'days'), null);

    return el('li', { class: `region${isCurrent ? ' is-current' : ''}${enabled ? '' : ' is-locked'}`, dataset: { id: String(id || '') } }, [
      el('div', { class: 'region__head' }, [
        el('p', { class: 'region__name', text: regionName(region) }),
        isCurrent
          ? el('span', { class: 'badge badge--active', text: '此处' })
          : el('span', { class: `badge${enabled ? '' : ' badge--locked'}`, text: enabled ? '可往' : '未开' }),
      ]),
      el('div', { class: 'region__meta' }, [
        levelMin !== null || levelMax !== null
          ? el('span', { text: `Lv.${levelMin ?? '?'}–${levelMax ?? '?'}` })
          : null,
        env !== null ? el('span', { text: `环境 ×${env}${pick(region, 'env_label') ? `（${pick(region, 'env_label')}）` : ''}` }) : null,
        travelDays !== null ? el('span', { text: `耗时 ${fmt(travelDays)} 日` }) : null,
      ]),
      el('p', { class: 'region__note', text: `特产：${materialsText(region)}` }),
      el('p', { class: 'region__note', text: pick(region, 'desc', 'description') || (enabled ? '无甚可说。' : lockReason(region)) }),
      el('div', { class: 'region__foot' }, [
        el('button', {
          class: `btn btn--sm ${isCurrent ? 'btn--ghost' : 'btn--primary'}`,
          type: 'button',
          text: isCurrent ? '已在此处' : (enabled ? '前往' : '无法前往'),
          disabled: isCurrent || !enabled,
          title: enabled ? '' : lockReason(region),
          onclick: (event) => travel(root, region, event.currentTarget),
        }),
      ]),
    ]);
  }));
}

async function travel(root, region, button) {
  await withBusy(button, async () => {
    try {
      const result = await api.travel(regionId(region));
      const days = num(pick(result, 'days', 'travel_days', 'time_cost_days'), null);
      toast(days === null ? `已至${regionName(region)}` : `行 ${fmt(days)} 日，已至${regionName(region)}`, 'info');
      await refreshAndSync();
      await load(root);
    } catch (err) {
      toastError(err);
      if (err.code === '4003') router.go('creation');
    }
  }, { label: '赶路…' });
}

async function load(root) {
  const grid = qs('#map-grid', root);
  renderLoading(grid, '正在展开舆图…');
  try {
    ui.data = await api.map();
    ui.regions = arr(pick(ui.data, 'regions', 'list', 'items', 'areas'));
    renderCurrent(root);
    renderGrid(root);
  } catch (err) {
    if (err.code === '4003') {
      router.go('creation');
      return;
    }
    toastError(err);
    renderError(grid, err, () => load(root));
  }
}

export default {
  name: 'map',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    await load(root);
  },
};
