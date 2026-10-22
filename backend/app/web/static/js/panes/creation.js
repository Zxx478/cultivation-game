/**
 * panes/creation.js · 创角（掷骨 → 五点先天 → 三路径告知）
 *
 * 数值权威在服务端：体质/资质/性格/悟性由 /bootstrap/reroll 掷出，
 * 前端只负责展示与采集「5 点、单项 ≤3」的加点。
 */

import { api } from '../core/api.js';
import { store, pick, num } from '../core/state.js';
import { qs, qsa, el, fill, toast, toastError, withBusy } from '../core/ui.js';
import router from '../core/router.js';

const POINT_KEYS = ['str', 'con', 'spr', 'sen', 'bon', 'wis', 'agi', 'dao_heart'];
const POINT_LABELS = {
  str: '力量',
  con: '体魄',
  spr: '灵力',
  sen: '神识',
  bon: '根骨',
  wis: '悟性',
  agi: '身法',
  dao_heart: '道心',
};

const state = {
  candidates: null,
  remaining: 3,
  allocations: Object.fromEntries(POINT_KEYS.map((key) => [key, 0])),
  step: 1,
  submitting: false,
  bound: false,
};

function totalAllocated() {
  return POINT_KEYS.reduce((sum, key) => sum + (state.allocations[key] || 0), 0);
}

function baseAttrs() {
  const raw = (state.candidates && (state.candidates.base_attrs || state.candidates.attrs || state.candidates.init_attrs)) || null;
  const flat = num(state.candidates && state.candidates.init_attr_base, 10);
  const result = {};
  POINT_KEYS.forEach((key) => {
    const value = raw && typeof raw === 'object' ? num(pick(raw, key), null) : null;
    result[key] = value === null ? flat : value;
  });
  return result;
}

/** 兼容扁平与嵌套两种 reroll 返回结构 */
function readCandidates(data) {
  const source = data || {};
  const physiqueRaw = pick(source, 'physique', 'physique_id') || {};
  const personalityRaw = pick(source, 'personality', 'personality_id') || {};
  const physique = typeof physiqueRaw === 'string'
    ? { id: physiqueRaw, name: pick(source, 'physique_name') || physiqueRaw }
    : physiqueRaw;
  const personality = typeof personalityRaw === 'string'
    ? { id: personalityRaw, name: pick(source, 'personality_name') || personalityRaw }
    : personalityRaw;

  return {
    physique: {
      id: pick(physique, 'id', 'physique_id') || '',
      name: pick(physique, 'name', 'display_name') || '未知之体',
      desc: pick(physique, 'desc', 'description', 'special_desc') || '',
      base: pick(physique, 'base_attrs', 'init_attr_base', 'init_attr_base') || pick(source, 'base_attrs') || null,
      cultivation: pick(physique, 'cultivation_bp') || null,
    },
    talent: num(pick(source, 'talent', 'talent_grade', 'physique.talent'), 5),
    personality: {
      id: pick(personality, 'id') || 'zhongyong',
      name: pick(personality, 'name', 'display_name') || '中庸',
      desc: pick(personality, 'desc', 'description') || '',
    },
    wis: num(pick(source, 'wis', 'wisdom'), 20),
    options: Array.isArray(source.personalities) ? source.personalities : [],
    remaining: num(pick(source, 'remaining', 'reroll_left', 'remaining_rerolls', 'left'), null),
  };
}

function updatePointsUi(root) {
  const total = totalAllocated();
  const left = 5 - total;
  const leftNode = qs('#points-left', root);
  if (leftNode) leftNode.textContent = String(left);
  const bases = baseAttrs();

  qsa('.points__row', root).forEach((row) => {
    const key = row.dataset.key;
    const alloc = state.allocations[key] || 0;
    const baseNode = qs('[data-base]', row);
    const valNode = qs('[data-val]', row);
    const plus = qs('[data-step="1"]', row);
    const minus = qs('[data-step="-1"]', row);
    if (baseNode) baseNode.textContent = String(bases[key] === undefined ? '—' : bases[key]);
    if (valNode) {
      valNode.textContent = alloc > 0 ? `+${alloc}` : '0';
      valNode.classList.toggle('is-plus', alloc > 0);
    }
    if (plus) plus.disabled = left <= 0 || alloc >= 3;
    if (minus) minus.disabled = alloc <= 0;
  });

  const hint = qs('.points .hint', root);
  if (hint) {
    hint.textContent = left === 0
      ? '五点已尽，可行下一步。'
      : `尚有 ${left} 点未分；单项至多 +3。`;
  }
}

function renderCandidates(root) {
  const data = state.candidates;
  if (!data) return;
  const physiqueNode = qs('#roll-physique', root);
  if (physiqueNode) physiqueNode.textContent = data.physique.name;
  const physiqueDesc = qs('#roll-physique-desc', root);
  if (physiqueDesc) physiqueDesc.textContent = data.physique.desc || '—';

  const talentNode = qs('#roll-talent', root);
  if (talentNode) talentNode.textContent = data.talent === null ? '—' : `${data.talent} 品`;

  const personalityNode = qs('#roll-personality', root);
  if (personalityNode) personalityNode.textContent = data.personality.name;
  const personalityDesc = qs('#roll-personality-desc', root);
  if (personalityDesc) personalityDesc.textContent = data.personality.desc || '—';

  const wisNode = qs('#roll-wis', root);
  if (wisNode) wisNode.textContent = String(data.wis);

  const leftNode = qs('#reroll-left', root);
  if (leftNode) leftNode.textContent = String(state.remaining);
  const rerollBtn = qs('#reroll', root);
  if (rerollBtn) rerollBtn.disabled = state.remaining <= 0 || state.submitting;

  const select = qs('#f-personality', root);
  if (select) {
    const options = data.options.length
      ? data.options.map((item) => ({
          id: pick(item, 'id') || '',
          name: pick(item, 'name', 'display_name') || pick(item, 'id') || '—',
        }))
      : [{ id: data.personality.id, name: data.personality.name }];
    fill(select, options.map((item) => el('option', { value: item.id, text: item.name })));
    select.value = data.personality.id;
  }
  updatePointsUi(root);
}

function switchStep(root, step) {
  state.step = step;
  qsa('.creation__step', root).forEach((node) => {
    node.hidden = Number(node.dataset.step) !== step;
  });
  if (step === 2) renderSummary(root);
  const heading = qs('.pane__title', root);
  if (heading) heading.focus?.();
}

function renderSummary(root) {
  const data = state.candidates || {};
  const nameInput = qs('#f-name', root);
  const genderInput = qs('input[name="gender"]:checked', root);
  const genderLabel = { male: '男', female: '女', unknown: '其他' }[genderInput ? genderInput.value : 'male'] || '—';
  const personalitySelect = qs('#f-personality', root);
  const personalityName = personalitySelect && personalitySelect.selectedOptions.length
    ? personalitySelect.selectedOptions[0].textContent
    : (data.personality && data.personality.name) || '—';
  const bases = baseAttrs();
  const allocText = POINT_KEYS.filter((key) => state.allocations[key] > 0)
    .map((key) => `${POINT_LABELS[key]} +${state.allocations[key]}`)
    .join('、') || '未分配';

  const rows = [
    ['道号', nameInput ? nameInput.value.trim() : '—'],
    ['性别', genderLabel],
    ['体质', (data.physique && data.physique.name) || '—'],
    ['资质', data.talent === null || data.talent === undefined ? '—' : `${data.talent} 品`],
    ['性格', personalityName],
    ['悟性', data.wis === undefined ? '—' : String(data.wis)],
    ['先天', allocText],
    ['力量', String(bases.str ?? '—')],
  ];
  fill(qs('#creation-summary', root), rows.map(([label, value]) => el('div', null, [
    el('dt', { text: label }),
    el('dd', { text: value }),
  ])));
}

function collectPayload(root) {
  const nameInput = qs('#f-name', root);
  const genderInput = qs('input[name="gender"]:checked', root);
  const personalitySelect = qs('#f-personality', root);
  return {
    name: nameInput ? nameInput.value.trim() : '',
    gender: genderInput ? genderInput.value : 'male',
    personality: personalitySelect ? personalitySelect.value : (state.candidates?.personality?.id || 'zhongyong'),
    physique: state.candidates?.physique?.id || '',
    talent: state.candidates?.talent ?? 5,
    wis: state.candidates?.wis ?? 20,
    points: { ...state.allocations },
  };
}

function validate(root) {
  const errorNode = qs('#creation-error', root);
  const nameInput = qs('#f-name', root);
  const name = nameInput ? nameInput.value.trim() : '';
  const nameError = qs('#f-name-error', root);

  const fail = (message, target) => {
    if (errorNode) {
      errorNode.hidden = false;
      errorNode.textContent = message;
    }
    if (target) {
      target.classList.add('is-shake');
      window.setTimeout(() => target.classList.remove('is-shake'), 320);
      target.focus();
    }
    return false;
  };

  if (!name) {
    if (nameError) {
      nameError.hidden = false;
      nameError.textContent = '道号不可为空';
    }
    return fail('道号不可为空。', nameInput);
  }
  if (Array.from(name).length > 16) {
    if (nameError) {
      nameError.hidden = false;
      nameError.textContent = '道号至多十六字';
    }
    return fail('道号至多十六字。', nameInput);
  }
  if (nameError) nameError.hidden = true;

  const total = totalAllocated();
  if (total !== 5) {
    return fail(total < 5 ? `先天五点尚未分尽（尚余 ${5 - total} 点）。` : '先天至多五点，不可多取。', qs('.points', root));
  }
  const over = POINT_KEYS.find((key) => state.allocations[key] > 3);
  if (over) return fail(`单项加点至多三点（${POINT_LABELS[over]}已超）。`, qs('.points', root));

  if (errorNode) errorNode.hidden = true;
  return true;
}

async function doReroll(root, button) {
  await withBusy(button, async () => {
    try {
      const data = await api.reroll();
      state.candidates = readCandidates(data);
      const remaining = state.candidates.remaining;
      state.remaining = remaining === null ? Math.max(0, state.remaining - 1) : remaining;
      POINT_KEYS.forEach((key) => { state.allocations[key] = 0; });
      renderCandidates(root);
      const hint = qs('#roll-hint', root);
      if (hint) hint.textContent = state.remaining > 0 ? `重掷已用，余 ${state.remaining} 次。` : '三次已尽，此即天定。';
      toast('骨血已定', 'info', { timeout: 1600 });
    } catch (err) {
      if (err.code === '4002') {
        state.remaining = 0;
        const btn = qs('#reroll', root);
        if (btn) btn.disabled = true;
        const leftNode = qs('#reroll-left', root);
        if (leftNode) leftNode.textContent = '0';
        toast('重掷三次已尽，天不予改', 'warn');
        return;
      }
      toastError(err);
    }
  }, { label: '掷骨…' });
}

async function doSubmit(root, button) {
  if (state.submitting) return;
  if (!validate(root)) return;
  state.submitting = true;
  const payload = collectPayload(root);

  await withBusy(button, async () => {
    try {
      await api.bootstrap(payload);
      toast('此身已立，前路自去', 'info');
      try {
        await store.refresh({ force: true });
      } catch (err) {
        if (err.code === '4003') {
          toast('立身未成，且再试一次', 'error');
          return;
        }
        throw err;
      }
      router.go('main');
    } catch (err) {
      toastError(err);
      const errorNode = qs('#creation-error', root);
      if (errorNode) {
        errorNode.hidden = false;
        errorNode.textContent = err.message || '立身失败';
      }
      if (err.code === '4001' && errorNode) errorNode.textContent = `加点非法：${err.message}`;
    }
  }, { label: '入世…' });
  state.submitting = false;
}

export default {
  name: 'creation',

  async enter(root) {
    state.submitting = false;
    const restart = qs('#creation-restart', root);
    if (restart) restart.hidden = !store.hasCharacter;

    if (!state.bound) {
      state.bound = true;

      root.addEventListener('click', (event) => {
        const stepBtn = event.target.closest('[data-step]');
        if (stepBtn && stepBtn.classList.contains('stepper')) {
          const row = stepBtn.closest('.points__row');
          if (!row) return;
          const key = row.dataset.key;
          const delta = Number(stepBtn.dataset.step);
          const current = state.allocations[key] || 0;
          const next = current + delta;
          const total = totalAllocated();
          if (next < 0 || next > 3) return;
          if (delta > 0 && total >= 5) {
            toast('先天五点已尽', 'warn', { timeout: 1800 });
            return;
          }
          state.allocations[key] = next;
          updatePointsUi(root);
          const errorNode = qs('#creation-error', root);
          if (errorNode) errorNode.hidden = true;
        }
      });

      qs('#reroll', root)?.addEventListener('click', (event) => doReroll(root, event.currentTarget));
      qs('#creation-next', root)?.addEventListener('click', () => {
        if (validate(root)) switchStep(root, 2);
      });
      qs('#creation-back', root)?.addEventListener('click', () => switchStep(root, 1));
      qs('#creation-submit', root)?.addEventListener('click', (event) => doSubmit(root, event.currentTarget));

      qs('#f-name', root)?.addEventListener('input', () => {
        const errorNode = qs('#f-name-error', root);
        if (errorNode) errorNode.hidden = true;
      });
    }

    switchStep(root, 1);

    if (!state.candidates) {
      const hint = qs('#roll-hint', root);
      if (hint) hint.textContent = '正在掷骨…';
      try {
        const data = await api.candidate();
        state.candidates = readCandidates(data);
        const remaining = state.candidates.remaining;
        state.remaining = remaining === null ? 2 : remaining;
        renderCandidates(root);
        if (hint) hint.textContent = '此为当前候选；重掷至多三次。';
      } catch (err) {
        if (hint) hint.textContent = '未能掷骨：' + (err.message || '接口未就绪') + '。可稍后重试。';
        toastError(err);
      }
    } else {
      renderCandidates(root);
    }
    updatePointsUi(root);
  },
};
