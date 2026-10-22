/**
 * panes/event.js · 事件弹窗
 *
 * 只展示性质（危险/消耗/因果），不展示具体成功率（设计 §3.5）。
 * 选择提交后展示判定结果与逐项 Δ；刷新页面会由 /state 的 pending_event 重新打开。
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, el, fill, fmt, toast, toastError, riskTag, openLayer, closeLayer, isLayerOpen, withBusy,
} from '../core/ui.js';
import { refreshAndSync, renderDeltas, renderDeltaLines } from '../core/actions.js';

const ui = {
  pending: null,
  chosen: false,
  result: null,
  instanceId: null,
  dismissedId: null,
  finishing: false,
};

/** 取事件实例 id（不同接口字段名不同，统一成一个字符串键） */
function instanceKey(data) {
  if (!data) return '';
  const ev = pick(data, 'event', 'detail', 'payload') || data;
  const raw = pick(data, 'instance_id', 'instanceId') ?? pick(ev, 'instance_id');
  return raw === undefined || raw === null ? '' : String(raw);
}

function currentEvent() {
  const pending = ui.pending || store.view.pendingEvent;
  if (!pending) return null;
  const ev = pick(pending, 'event', 'detail', 'payload') || pending;
  return {
    instanceId: pick(pending, 'instance_id', 'instanceId') ?? pick(ev, 'instance_id'),
    title: pick(ev, 'title', 'name') || '一桩因果',
    body: pick(ev, 'body', 'text', 'desc', 'description', 'content') || '',
    choices: arr(pick(ev, 'choices', 'options', 'items')),
    raw: pending,
  };
}

function setResultVisible(visible, isSuccess) {
  const box = qs('#event-result');
  if (!box) return;
  box.hidden = !visible;
  box.classList.toggle('eventresult--fail', visible && !isSuccess);
  const cont = qs('#event-continue');
  if (cont) cont.hidden = !visible;
  const hint = qs('#event-hint');
  if (hint) hint.textContent = visible ? '按空格或「继续」收束此事。' : '按 1–4 选择；Esc 收起，稍后再来。';
}

function renderChoices(eventData) {
  const list = qs('#event-choices');
  if (!list) return;
  const choices = eventData.choices;

  if (!choices.length) {
    fill(list, el('li', { class: 'empty', text: '此局无选项；按「继续」了结。' }));
    setResultVisible(true, true);
    const cont = qs('#event-continue');
    if (cont) cont.hidden = false;
    const box = qs('#event-result');
    if (box) {
      box.hidden = false;
      const title = qs('#event-result-title');
      if (title) title.textContent = '此事已了';
    }
    return;
  }

  fill(list, choices.map((choice, index) => {
    const optionId = pick(choice, 'id', 'option_id', 'key');
    return el('li', null, [
      el('button', {
        class: 'choice',
        type: 'button',
        dataset: { option: String(optionId || index + 1) },
        onclick: (event) => choose(optionId, event.currentTarget),
      }, [
        el('span', { class: 'choice__key', text: String(index + 1) }),
        el('span', { class: 'choice__main' }, [
          el('span', { class: 'choice__label', text: String(pick(choice, 'label', 'text', 'title', 'desc') || `选项 ${index + 1}`) }),
          el('span', { class: 'choice__tags' }, [
            riskTag(choice),
            num(pick(choice, 'time_cost_days', 'time_days', 'days'), null) !== null
              ? el('span', { class: 'badge', text: `耗时 ${fmt(pick(choice, 'time_cost_days', 'time_days', 'days'))} 日` })
              : null,
            pick(choice, 'hint', 'note')
              ? el('span', { class: 'badge', text: String(pick(choice, 'hint', 'note')) })
              : null,
          ]),
        ]),
      ]),
    ]);
  }));
}

/**
 * 绑定「关闭 / 继续」。
 *
 * 事件层不是路由（router.ROUTES 里没有 event），外壳只会调 open()，
 * 所以这里必须在 open() 里绑定一次；只在 enter() 里绑会导致按钮永久失联。
 */
function bindControls(root = document) {
  const closeBtn = qs('#event-close', root);
  if (closeBtn && !closeBtn.dataset.bound) {
    closeBtn.dataset.bound = '1';
    closeBtn.addEventListener('click', () => dismiss());
  }
  const cont = qs('#event-continue', root);
  if (cont && !cont.dataset.bound) {
    cont.dataset.bound = '1';
    cont.addEventListener('click', () => finish());
  }
}

export function open(eventData) {
  const event = eventData ? currentEventFrom(eventData) : currentEvent();
  if (!event) {
    toast('眼前无待决之事', 'warn');
    return;
  }
  ui.pending = eventData || store.view.pendingEvent;
  ui.instanceId = event.instanceId;
  ui.chosen = false;
  ui.result = null;
  ui.finishing = false;
  ui.dismissedId = null;

  const title = qs('#event-title');
  if (title) title.textContent = event.title;
  const body = qs('#event-body');
  if (body) body.textContent = event.body || '四下无声，唯余风过。';
  const kicker = qs('#event-kicker');
  if (kicker) kicker.textContent = '奇遇';

  const box = qs('#event-result');
  if (box) {
    box.hidden = true;
    box.classList.remove('eventresult--fail');
  }
  const deltaList = qs('#event-result-deltas');
  if (deltaList) fill(deltaList, []);
  const timeNode = qs('#event-result-time');
  if (timeNode) timeNode.textContent = '';
  const cont = qs('#event-continue');
  if (cont) cont.hidden = true;

  if (event.choices.length) setResultVisible(false, true);
  renderChoices(event);
  bindControls();
  openLayer('layer-event');
}

function currentEventFrom(data) {
  const ev = pick(data, 'event', 'detail', 'payload') || data;
  return {
    instanceId: pick(data, 'instance_id', 'instanceId') ?? pick(ev, 'instance_id'),
    title: pick(ev, 'title', 'name') || '一桩因果',
    body: pick(ev, 'body', 'text', 'desc', 'description', 'content') || '',
    choices: arr(pick(ev, 'choices', 'options', 'items')),
    raw: data,
  };
}

async function choose(optionId, button) {
  if (ui.chosen) return;
  const instanceId = ui.instanceId || pick(ui.pending, 'instance_id', 'instanceId');
  if (instanceId === undefined || instanceId === null) {
    toast('缺少事件凭据，先刷新天机', 'warn');
    return;
  }

  await withBusy(button, async () => {
    try {
      const result = await api.eventChoose(instanceId, optionId);
      ui.chosen = true;
      ui.result = result;

      const ok = pick(result, 'success', 'passed', 'check_success', 'check.success');
      const text = pick(result, 'text', 'message', 'outcome', 'result_text', 'desc');
      const titleNode = qs('#event-result-title');
      if (titleNode) {
        titleNode.textContent = text
          ? String(text)
          : ok === false ? '所求未成' : ok === true ? '如你所愿' : '此局已定';
      }

      // 展示优先级：服务端译好的中文行 → deltas.effects → 原始 effects
      // （deltas 扁平字典里混着 success/chance_bp，直接渲染会把内部键名抛给玩家）
      const lines = arr(pick(result, 'effect_lines', 'effectLines', 'delta_lines'));
      const effects = pick(result, 'effects', 'deltas.effects', 'applied', 'changes')
        || (ok === false ? pick(result, 'fail_effects') : pick(result, 'success_effects'));
      const deltaBox = qs('#event-result-deltas');
      if (lines.length) renderDeltaLines(deltaBox, lines);
      else renderDeltas(deltaBox, effects);

      const time = num(pick(result, 'time_cost_days', 'time_days', 'days'), null);
      const timeNode = qs('#event-result-time');
      if (timeNode) {
        const bits = [];
        if (time !== null) bits.push(`耗时 ${fmt(time)} 日`);
        const extra = pick(result, 'time_text', 'note');
        if (extra) bits.push(String(extra));
        timeNode.textContent = bits.join(' · ');
      }

      // 选项已定，禁用余下按钮
      Array.from((qs('#event-choices') || {}).children || []).forEach((item) => {
        const choiceButton = item.querySelector ? item.querySelector('button.choice') : null;
        if (choiceButton) choiceButton.disabled = true;
      });

      setResultVisible(true, ok !== false);
      const cont = qs('#event-continue');
      if (cont) cont.hidden = false;
      if (cont) cont.focus();
    } catch (err) {
      toastError(err);
    }
  }, { label: '推演…' });
}

/**
 * 收起此页（× / Esc）。
 *
 * 没结算就只是「暂时收起」：服务端仍留着待决事件，外壳会亮出「续」入口把人请回来；
 * 已结算则刷新 /state，事件在存档里真正消失。
 */
export function dismiss() {
  const settled = ui.chosen;
  const instanceId = instanceKey(ui.pending || store.view.pendingEvent);
  if (isLayerOpen('layer-event')) closeLayer('layer-event');
  ui.pending = null;
  ui.chosen = false;
  ui.result = null;
  ui.dismissedId = settled ? null : (instanceId || null);

  if (settled) {
    refreshAndSync();
    return;
  }
  // 「续」入口由外壳按状态渲染，这里只通知它同步一次
  document.dispatchEvent(new CustomEvent('game:sync'));
}

/** 此实例是否被手动收起过（外壳据此不再强行弹回） */
export function isDismissed(pending) {
  if (ui.dismissedId === null) return false;
  return instanceKey(pending) === ui.dismissedId;
}

async function finish() {
  if (ui.finishing) return;
  ui.finishing = true;
  try {
    if (!ui.chosen) {
      dismiss();
      return;
    }
    closeLayer('layer-event');
    ui.pending = null;
    ui.chosen = false;
    ui.result = null;
    ui.dismissedId = null;
    await refreshAndSync();
  } finally {
    ui.finishing = false;
  }
}

function handleKey(event) {
  if (!isLayerOpen('layer-event')) return false;
  if (event.target && /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName)) return false;

  const key = event.key;
  if (key === 'Escape' || key === 'Esc') {
    event.preventDefault();
    dismiss();
    return true;
  }
  if (key === ' ' || key === 'Spacebar' || key === 'Enter') {
    if (ui.chosen || !ui.result) {
      const cont = qs('#event-continue');
      if (cont && !cont.hidden) {
        event.preventDefault();
        finish();
        return true;
      }
    }
    return false;
  }

  if (!ui.chosen && /^[1-4]$/.test(key)) {
    const buttons = Array.from((qs('#event-choices') || {}).querySelectorAll?.('button.choice') || []);
    const target = buttons[Number(key) - 1];
    if (target && !target.disabled) {
      event.preventDefault();
      target.click();
      return true;
    }
  }
  return false;
}

export default {
  name: 'event',

  open,

  dismiss,

  isDismissed,

  handleKey,

  enter(root) {
    bindControls(root);
    const pending = store.view.pendingEvent;
    if (pending) open(pending);
  },
};
