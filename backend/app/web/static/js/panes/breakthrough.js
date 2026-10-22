/**
 * panes/breakthrough.js · 突破弹窗
 *
 * 逐项展示七类加成与最终成功率（clamp 5%–95%，5 连败则必成），
 * 并把三档失败惩罚预告写清楚，再给「开始突破」。
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, el, fill, fmt, fmtPct, toast, toastError, openLayer, closeLayer, withBusy,
} from '../core/ui.js';
import { refreshAndSync, renderDeltas } from '../core/actions.js';
import router from '../core/router.js';

const ui = { preview: null, pills: [], selected: new Set(), submitting: false, done: false };

const DEFAULT_PENALTY = [
  { level: 'light', text: '轻伤（成功率 ≥60% 失败）：修为 −10%，气血 −30%' },
  { level: 'medium', text: '中伤（30%–60% 失败）：修为 −20%，气血 −60%，心境裂隙' },
  { level: 'heavy', text: '重伤（<30% 失败）：修为 −35%，气血 −90%，道心 −5，或走火入魔' },
];

function itemsOf(preview) {
  const raw = pick(preview, 'items', 'details', 'breakdown', 'bonus_items');
  return arr(raw).map((item, index) => {
    if (typeof item === 'string') return { name: item, value: null, note: '' };
    const label = pick(item, 'name', 'label', 'key') || `第 ${index + 1} 项`;
    const bp = num(pick(item, 'value_bp', 'bp', 'delta_bp'), null);
    const value = num(pick(item, 'value', 'delta', 'pct'), null);
    return {
      name: String(label),
      bp,
      value,
      note: pick(item, 'note', 'desc', 'description', 'text') || '',
    };
  });
}

function pillsOf(preview) {
  const raw = pick(preview, 'pills', 'prep_pills', 'available_pills', 'items_pill');
  return arr(raw).map((item) => {
    if (typeof item === 'string') return { id: item, name: item, qty: null };
    return {
      id: pick(item, 'item_id', 'id', 'key'),
      name: pick(item, 'name', 'label', 'display_name') || '丹药',
      qty: num(pick(item, 'qty', 'count'), null),
      selected: pick(item, 'selected') === true,
    };
  });
}

function prepPayload() {
  return {
    pill: ui.selected.size > 0,
    pill_ids: Array.from(ui.selected),
    guardian: false,
    omen: false,
    land: false,
  };
}

function renderPreview(root, preview) {
  ui.preview = preview;
  const view = store.view;

  const title = qs('#bt-title', root);
  if (title) title.textContent = `冲关 · ${pick(preview, 'realm_name', 'current.realm_name') || view.realmName || '此境'}`;

  const chain = qs('#bt-chain', root);
  if (chain) {
    const from = pick(preview, 'from_text', 'current.text', 'current.level')
      ?? (view.level === null ? '—' : `Lv.${view.level}`);
    const to = pick(preview, 'to_text', 'target.text', 'target.level', 'next_level');
    chain.textContent = `当前 ${from}${to === undefined || to === null ? '' : ` → 目标 ${to}`}`;
  }

  const finalNode = qs('#bt-final', root);
  const finalBp = num(pick(preview, 'final_bp', 'success_bp', 'rate_bp'), null);
  const finalPct = num(pick(preview, 'final_pct', 'success_rate', 'rate'), null);
  const guaranteed = pick(preview, 'guaranteed', 'sure') === true;
  if (finalNode) {
    if (guaranteed) finalNode.textContent = '100%';
    else if (finalBp !== null) finalNode.textContent = fmtPct(finalBp / 10000);
    else if (finalPct !== null) finalNode.textContent = finalPct <= 1 ? fmtPct(finalPct) : `${fmt(finalPct)}%`;
    else finalNode.textContent = '—';
  }
  const note = qs('#bt-final-note', root);
  if (note) {
    if (guaranteed) note.textContent = '心魔已破，此关必过。';
    else {
      const streak = num(pick(preview, 'fail_streak'), null);
      const prepSum = num(pick(preview, 'prep_sum_bp'), null);
      const bits = [];
      if (streak !== null && streak > 0) bits.push(`连败 ${streak} 次，已补 ${Math.min(30, streak * 15)}%`);
      if (prepSum !== null) bits.push(`准备加成 ${fmtPct(prepSum / 10000)}（上限 30%）`);
      bits.push('成功率钳在 5%–95% 之间');
      note.textContent = bits.join(' · ');
    }
  }

  const body = qs('#bt-items', root);
  if (body) {
    const items = itemsOf(preview);
    if (!items.length) {
      fill(body, el('tr', null, [el('td', { colspan: '3', class: 'empty', text: '服务端未返回明细' })]));
    } else {
      fill(body, items.map((item) => {
        let valueText = '—';
        if (item.bp !== null) valueText = `${item.bp >= 0 ? '+' : ''}${fmtPct(item.bp / 10000, 2)}`;
        else if (item.value !== null) valueText = item.value <= 1 ? `${item.value >= 0 ? '+' : ''}${fmtPct(item.value, 2)}` : fmt(item.value);
        return el('tr', null, [
          el('th', { scope: 'row', text: item.name }),
          el('td', { class: 'num', text: valueText }),
          el('td', { class: 'muted', text: item.note || '—' }),
        ]);
      }));
    }
  }

  const penaltyList = qs('#bt-penalty', root);
  if (penaltyList) {
    const penalty = arr(pick(preview, 'penalty', 'penalties', 'fail_penalty'));
    const lines = penalty.length
      ? penalty.map((item) => (typeof item === 'string'
        ? { level: 'light', text: item }
        : { level: pick(item, 'level', 'tier') || 'light', text: pick(item, 'text', 'desc', 'description') || '' }))
      : DEFAULT_PENALTY;
    fill(penaltyList, lines.map((line) => el('li', { dataset: { level: String(line.level) } }, [
      el('span', { text: line.text }),
    ])));
  }

  renderPills(root, preview);

  const blockNode = qs('#bt-block', root);
  const startBtn = qs('#bt-start', root);
  const canStart = pick(preview, 'can_breakthrough', 'allowed');
  const blockReason = pick(preview, 'block_reason', 'reason');
  if (blockNode) blockNode.textContent = blockReason ? String(blockReason) : '';
  if (startBtn) {
    startBtn.disabled = canStart === false;
    startBtn.title = blockReason ? String(blockReason) : '';
  }
}

function renderPills(root, preview) {
  const list = qs('#bt-pills', root);
  if (!list) return;
  const pills = pillsOf(preview);
  const available = pills.filter((pill) => pill.qty === null || pill.qty > 0);
  if (!pills.length) {
    fill(list, el('li', { class: 'empty', text: '无丹可服；空手冲关亦是一途。' }));
    return;
  }

  fill(list, available.map((pill) => {
    const id = String(pill.id || pill.name);
    if (pill.selected && !ui.selected.has(id)) ui.selected.add(id);
    const checkbox = el('input', {
      type: 'checkbox',
      id: `pill-${id}`,
      checked: ui.selected.has(id),
      onchange: (event) => {
        if (event.target.checked) ui.selected.add(id);
        else ui.selected.delete(id);
        refreshPreview(root);
      },
    });
    return el('li', { class: 'pillitem' }, [
      checkbox,
      el('label', { for: `pill-${id}`, text: String(pill.name) }),
      el('span', { text: pill.qty === null ? '' : `×${fmt(pill.qty)}` }),
    ]);
  }));
}

async function refreshPreview(root) {
  try {
    const preview = await api.breakthroughPreview(prepPayload());
    renderPreview(root, preview);
  } catch (err) {
    toastError(err);
  }
}

async function open(root) {
  ui.done = false;
  ui.submitting = false;
  ui.selected = new Set();
  const result = qs('#bt-result');
  if (result) {
    result.hidden = true;
    result.classList.remove('btresult--fail');
  }
  openLayer('layer-breakthrough');
  const startBtn = qs('#bt-start', root);
  if (startBtn && !startBtn.dataset.bound) {
    startBtn.dataset.bound = '1';
    startBtn.addEventListener('click', (event) => start(root, event.currentTarget));
  }
  const closeBtn = qs('#bt-close', root);
  if (closeBtn && !closeBtn.dataset.bound) {
    closeBtn.dataset.bound = '1';
    closeBtn.addEventListener('click', () => closeLayer('layer-breakthrough'));
  }
  const body = qs('#bt-items');
  if (body) fill(body, el('tr', null, [el('td', { colspan: '3', class: 'empty', text: '正在推演成功率…' })]));

  try {
    const preview = await api.breakthroughPreview(prepPayload());
    renderPreview(root, preview);
  } catch (err) {
    toastError(err);
    if (body) fill(body, el('tr', null, [el('td', { colspan: '3', class: 'empty', text: `未能预览：${err.message}` })]));
  }
}

async function start(root, button) {
  if (ui.submitting) return;
  ui.submitting = true;
  await withBusy(button, async () => {
    try {
      const result = await api.breakthrough({
        prep: prepPayload(),
        expected_version: store.view.version,
      });
      const success = pick(result, 'success', 'passed', 'ok');
      const resultBox = qs('#bt-result', root);
      if (resultBox) {
        resultBox.hidden = false;
        resultBox.classList.toggle('btresult--fail', success === false);
        fill(resultBox, [
          el('p', { class: 'eventresult__title', text: success === false ? '冲关失败，道基受创' : '冲关成功，境界已进' }),
          el('ul', { class: 'deltalist', id: 'bt-result-deltas' }),
          pick(result, 'text', 'message', 'desc')
            ? el('p', { class: 'hint', text: String(pick(result, 'text', 'message', 'desc')) })
            : null,
        ]);
        renderDeltas(qs('#bt-result-deltas', root), pick(result, 'effects', 'changes', 'penalty_applied'));
      }
      ui.done = true;
      toast(success === false ? '此关未过，且养伤再战' : '境界已进', success === false ? 'warn' : 'info');
      await refreshAndSync();
      if (success !== false) {
        window.setTimeout(() => closeLayer('layer-breakthrough'), 600);
      }
    } catch (err) {
      toastError(err);
      if (err.code === '4003') router.go('creation');
      if (err.code === '4009') toast('突破有冷却：上次失败未满 30 日', 'warn');
      if (err.code === '4008') toast('寿元不足十年，气血枯败，禁不起冲关', 'warn');
      if (err.code === '4007') toast('修为不足，先修满本级', 'warn');
    }
  }, { label: '冲关…' });
  ui.submitting = false;
}

export default {
  name: 'breakthrough',

  open,

  async enter(root) {
    const closeBtn = qs('#bt-close', root);
    if (closeBtn && !closeBtn.dataset.bound) {
      closeBtn.dataset.bound = '1';
      closeBtn.addEventListener('click', () => closeLayer('layer-breakthrough'));
    }
    const startBtn = qs('#bt-start', root);
    if (startBtn && !startBtn.dataset.bound) {
      startBtn.dataset.bound = '1';
      startBtn.addEventListener('click', (event) => start(root, event.currentTarget));
    }
  },
};
