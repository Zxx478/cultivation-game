/**
 * panes/logs.js · 日志（事件流 / 里程碑 / 战斗 / 任务）
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, qsa, el, fill, fmt, fmtTime, toastError, renderLoading, renderError,
} from '../core/ui.js';
import router from '../core/router.js';

const KIND_LABELS = { event: '事件', milestone: '里程碑', combat: '战斗', quest: '任务', system: '天机' };
const ui = { kind: 'all', entries: [] };

function normalize(data) {
  const events = arr(pick(data, 'events', 'event_log', 'logs'));
  const milestones = arr(pick(data, 'milestones', 'milestone_log'));
  const merged = [];

  events.forEach((item) => {
    if (typeof item === 'string') {
      merged.push({ kind: 'event', text: item, time: null });
      return;
    }
    merged.push({
      kind: String(pick(item, 'kind', 'type', 'category') || 'event').toLowerCase(),
      text: pick(item, 'text', 'message', 'detail', 'payload_text', 'title') || '—',
      time: pick(item, 'created_at', 'time', 'at', 'age_days'),
      icon: pick(item, 'icon'),
    });
  });

  milestones.forEach((item) => {
    if (typeof item === 'string') {
      merged.push({ kind: 'milestone', text: item, time: null });
      return;
    }
    merged.push({
      kind: 'milestone',
      text: pick(item, 'title', 'text', 'detail') || '里程碑',
      detail: pick(item, 'detail', 'desc'),
      time: pick(item, 'created_at', 'time', 'at', 'age_days'),
    });
  });

  return merged.sort((a, b) => {
    const left = num(a.time, null);
    const right = num(b.time, null);
    if (left === null || right === null) return 0;
    return right - left;
  });
}

function renderList(root) {
  const list = qs('#logs-list', root);
  const counter = qs('#logs-count', root);
  if (!list) return;
  const shown = ui.entries.filter((entry) => ui.kind === 'all' || entry.kind === ui.kind);

  if (counter) counter.textContent = `${shown.length} 条`;
  if (!shown.length) {
    fill(list, el('li', { class: 'empty', text: ui.kind === 'all' ? '天机尚无记载。' : '此类暂无记载。' }));
    return;
  }

  fill(list, shown.slice(0, 200).map((entry) => el('li', { class: `logitem logitem--${entry.kind}` }, [
    el('span', { class: 'logitem__dot' }),
    el('div', null, [
      el('p', { class: 'logitem__text', text: `${KIND_LABELS[entry.kind] ? `［${KIND_LABELS[entry.kind]}］` : ''}${entry.text}` }),
      entry.detail ? el('p', { class: 'logitem__text muted', text: String(entry.detail) }) : null,
      entry.time === null || entry.time === undefined
        ? null
        : el('span', { class: 'logitem__time', text: typeof entry.time === 'number' && entry.time < 1e9 ? `第 ${fmt(entry.time)} 日` : fmtTime(entry.time) }),
    ]),
  ])));
}

async function load(root) {
  const list = qs('#logs-list', root);
  renderLoading(list, '正在翻阅天机…');
  try {
    const data = await api.logs();
    ui.entries = normalize(data);
    renderList(root);
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
  name: 'logs',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      qsa('[data-kind]', root).forEach((button) => {
        button.addEventListener('click', () => {
          ui.kind = button.dataset.kind;
          qsa('[data-kind]', root).forEach((node) => node.classList.toggle('is-active', node === button));
          renderList(root);
        });
      });
    }
    await load(root);
  },
};
