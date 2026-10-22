/**
 * panes/character.js · 角色（10 基础 + 13 衍生 + 战力）
 *
 * 数据源 GET /character；数值一律来自服务端，前端只做标签映射与单位格式化。
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, el, fill, fmt, fmtPct, renderLoading, renderError, toastError,
} from '../core/ui.js';
import router from '../core/router.js';

const BASIC_LABELS = {
  str: '力量',
  con: '体魄',
  spr: '灵力',
  sen: '神识',
  bon: '根骨',
  wis: '悟性',
  agi: '身法',
  luk: '气运',
  karma: '因果',
  dao_heart: '道心',
};

const DERIVED_LABELS = {
  hp: '气血上限',
  hp_max: '气血上限',
  mp: '灵力上限',
  mp_max: '灵力上限',
  def: '防御',
  defence: '防御',
  damage_reduction: '减伤',
  reduce: '减伤',
  penetration: '破防',
  pen: '破防',
  crit: '暴击率',
  crit_rate: '暴击率',
  crit_damage: '暴击伤害',
  crit_dmg: '暴击伤害',
  hit: '命中',
  accuracy: '命中',
  dodge: '闪避',
  initiative: '先手',
  speed: '身法速度',
  exp_rate: '修为增速',
  cultivation_rate: '修为增速',
  power: '战力',
  cp: '战力',
};

const PERCENT_KEYS = new Set([
  'damage_reduction', 'reduce', 'crit', 'crit_rate', 'crit_damage', 'crit_dmg', 'hit', 'accuracy', 'dodge',
]);

const TIPS = {
  str: '力量：决定近身伤害与破防。',
  con: '体魄：决定气血上限与减伤。',
  spr: '灵力：决定术法威力与灵力上限。',
  sen: '神识：决定术法命中、部分宗门条件。',
  bon: '根骨：决定突破成功率与寿元根基。',
  wis: '悟性：决定修为增速，天授难改。',
  agi: '身法：决定先手与闪避。',
  luk: '气运：影响事件判定与掉落，五点天定。',
  karma: '因果：善恶之数，影响事件走向。',
  dao_heart: '道心：心境系数据此换算，走火入魔会削它。',
};

function normalizeList(raw, fallbackLabels) {
  if (!raw) return [];
  if (Array.isArray(raw)) {
    return raw.map((item, index) => {
      if (item === null || item === undefined) return null;
      if (typeof item === 'number' || typeof item === 'string') {
        const key = Object.keys(fallbackLabels)[index];
        return { key, label: fallbackLabels[key] || key || '—', value: item };
      }
      const key = pick(item, 'key', 'id', 'code') || Object.keys(fallbackLabels)[index];
      return {
        key,
        label: pick(item, 'label', 'name', 'display_name') || fallbackLabels[key] || key || '—',
        value: pick(item, 'value', 'total', 'final', 'current'),
        base: pick(item, 'base', 'base_value'),
        delta: pick(item, 'delta', 'change', 'diff'),
        desc: pick(item, 'desc', 'description', 'formula', 'note'),
      };
    }).filter(Boolean);
  }
  if (typeof raw === 'object') {
    return Object.keys(raw).map((key) => {
      const value = raw[key];
      if (value !== null && typeof value === 'object') {
        return {
          key,
          label: pick(value, 'label', 'name') || fallbackLabels[key] || key,
          value: pick(value, 'value', 'total', 'final', 'current'),
          base: pick(value, 'base', 'base_value'),
          delta: pick(value, 'delta', 'change'),
          desc: pick(value, 'desc', 'description', 'formula', 'note'),
        };
      }
      return { key, label: fallbackLabels[key] || key, value };
    });
  }
  return [];
}

function formatValue(key, value) {
  if (value === null || value === undefined || value === '') return '—';
  const label = String(key || '');
  if (PERCENT_KEYS.has(label)) {
    const number = num(value, null);
    if (number === null) return String(value);
    return number <= 1 ? fmtPct(number, 2) : `${fmt(number)}%`;
  }
  if (value && typeof value === 'object') {
    return fmt(pick(value, 'value', 'total', 'current'));
  }
  return fmt(value);
}

function renderHero(root, data) {
  const view = store.view;
  const cp = pick(data, 'cp', 'combat_power', 'power', 'battle_power', 'cp_total');
  const map = {
    'char-realm': pick(data, 'realm_display', 'realm_name', 'realm') || view.realmName || '—',
    'char-cp': cp === undefined ? '—' : fmt(cp),
    'char-foundation': pick(data, 'foundation') || view.foundation || '未定',
    'char-physique': pick(data, 'physique_name', 'physique') || view.physiqueName || '—',
    'char-talent': (() => {
      const talent = num(pick(data, 'talent'), view.talent);
      return talent === null ? '—' : `${talent} 品`;
    })(),
    'char-personality': pick(data, 'personality_name', 'personality') || view.personalityName || '—',
  };
  Object.keys(map).forEach((id) => {
    const node = qs(`#${id}`, root);
    if (node) node.textContent = String(map[id]);
  });

  const points = qs('#char-points', root);
  if (points) {
    const free = num(pick(data, 'free_points', 'points_left', 'unspent'), null);
    points.textContent = free === null ? '' : `未分配 ${free} 点`;
  }
}

function rowsFor(list, basic) {
  return list.map((item) => {
    const nameCell = el('th', { scope: 'row' }, [
      el('span', {
        class: 'attrname',
        tabindex: '0',
        'data-tip': item.desc || TIPS[item.key] || '',
        text: item.label,
      }),
    ]);
    const valueCell = el('td', { class: 'num', text: formatValue(item.key, item.value) });

    let deltaCell;
    if (basic) {
      const delta = num(item.delta, null);
      const base = num(item.base, null);
      const text = delta !== null && delta !== 0
        ? `${delta > 0 ? '+' : ''}${fmt(delta)}`
        : base !== null && num(item.value, null) !== null
          ? `基准 ${fmt(base)}`
          : '—';
      deltaCell = el('td', {
        class: `num ${delta !== null && delta !== 0 ? (delta > 0 ? 'delta-up' : 'delta-down') : 'muted'}`,
        text,
      });
    } else {
      deltaCell = el('td', { class: 'muted', text: item.desc || '—' });
    }

    return el('tr', { 'data-key': item.key || '' }, basic
      ? [nameCell, valueCell, deltaCell]
      : [nameCell, valueCell, deltaCell]);
  });
}

async function load(root) {
  renderHero(root, null);
  const basicBody = qs('#attr-basic', root);
  const derivedBody = qs('#attr-derived', root);
  const hint = qs('#character-hint', root);
  if (basicBody) renderLoading(basicBody, '正在推演戏骨…');
  if (derivedBody) renderLoading(derivedBody, '正在推演…');

  try {
    const data = await api.character();
    renderHero(root, data);

    const basicRaw = pick(data, 'basic', 'basic_attrs', 'base_attrs', 'attrs');
    const derivedRaw = pick(data, 'derived', 'derived_attrs', 'sub_attrs', 'secondary');
    const basic = normalizeList(basicRaw, BASIC_LABELS);
    const derived = normalizeList(derivedRaw, DERIVED_LABELS);

    if (basicBody) {
      if (!basic.length) fill(basicBody, el('tr', null, [el('td', { colspan: '3', class: 'empty', text: '服务端未返回基础属性' })]));
      else fill(basicBody, rowsFor(basic, true));
    }

    if (derivedBody) {
      if (!derived.length) fill(derivedBody, el('tr', null, [el('td', { colspan: '3', class: 'empty', text: '服务端未返回衍生属性' })]));
      else fill(derivedBody, rowsFor(derived, false));
    }

    if (hint) {
      hint.textContent = `衍生 ${derived.length} 项 · 基础 ${basic.length} 项；悬停属性名可见计算来历。`;
    }
  } catch (err) {
    if (err.code === '4003') {
      router.go('creation');
      return;
    }
    toastError(err);
    if (basicBody) {
      renderError(basicBody, err, () => load(root));
    }
    if (derivedBody) renderError(derivedBody, err, () => load(root));
    if (hint) hint.textContent = '属性未能载入：' + (err.message || '接口未就绪');
  }
}

export default {
  name: 'character',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    await load(root);
  },
};
