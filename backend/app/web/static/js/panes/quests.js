/**
 * panes/quests.js · 主线（19 条 / 卷一·卷二进度 / 领取奖励）
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, qsa, el, fill, fmt, setBar, statusBadge, toast, toastError, withBusy, renderLoading, renderError,
} from '../core/ui.js';
import router from '../core/router.js';

const STATUS_ORDER = { active: 0, ready: 1, locked: 2, completed: 3 };
const REWARD_LABELS = {
  exp: '修为', cultivation: '修为', stones: '灵石', spirit_stone: '灵石',
  items: '物品', equipments: '装备', techniques: '功法', attrs: '属性',
  contribution: '贡献', flags: '标记',
};

const ui = { status: 'all', data: null, quests: [] };

function questTitle(quest) {
  return String(pick(quest, 'title', 'name', 'display_name') || '未名之事');
}

function questId(quest) {
  return pick(quest, 'quest_id', 'id', 'key');
}

function statusOf(quest) {
  const raw = String(pick(quest, 'status', 'state') || 'locked').toLowerCase();
  return ['locked', 'active', 'ready', 'completed'].includes(raw) ? raw : 'locked';
}

function normalizeObjectives(quest) {
  const raw = pick(quest, 'objectives', 'targets', 'goals');
  return arr(raw).map((item) => {
    if (typeof item === 'string') return { text: item, current: null, target: null, done: null };
    return {
      text: pick(item, 'desc', 'text', 'label', 'title', 'type') || '目标',
      current: num(pick(item, 'current', 'progress', 'value'), null),
      target: num(pick(item, 'target', 'count', 'need', 'required'), null),
      done: pick(item, 'done', 'completed', 'met'),
    };
  });
}

function rewardTags(quest) {
  const rewards = pick(quest, 'rewards', 'reward');
  const tags = [];
  if (!rewards) return tags;
  if (Array.isArray(rewards)) {
    rewards.forEach((item) => {
      if (typeof item === 'string') {
        tags.push(item);
        return;
      }
      const label = pick(item, 'label', 'name', 'text');
      if (label) {
        const qty = num(pick(item, 'qty', 'value', 'count'), null);
        tags.push(qty ? `${label} ×${fmt(qty)}` : String(label));
        return;
      }
      Object.keys(item).forEach((key) => {
        const value = item[key];
        const name = REWARD_LABELS[key] || key;
        if (value && typeof value === 'object') {
          const qty = num(pick(value, 'qty', 'value', 'count'), null);
          if (qty !== null) tags.push(`${name} ${fmt(qty)}`);
        } else if (typeof value === 'number') {
          tags.push(`${name} ${fmt(value)}`);
        } else if (typeof value === 'string') {
          tags.push(`${name}：${value}`);
        }
      });
    });
    return tags;
  }
  if (typeof rewards === 'object') {
    Object.keys(rewards).forEach((key) => {
      const value = rewards[key];
      const name = REWARD_LABELS[key] || key;
      if (typeof value === 'number' && value !== 0) tags.push(`${name} ${fmt(value)}`);
      else if (typeof value === 'string' && value) tags.push(`${name}：${value}`);
      else if (Array.isArray(value)) value.forEach((entry) => {
        const label = typeof entry === 'string' ? entry : pick(entry, 'name', 'item_id', 'tech_id', 'label');
        if (label) tags.push(`${name}：${label}`);
      });
    });
  }
  return tags;
}

function renderVolumes(root) {
  const completed = { 1: 0, 2: 0 };
  const totals = { 1: 0, 2: 0 };
  ui.quests.forEach((quest) => {
    const volume = num(pick(quest, 'volume', 'vol'), 1);
    const key = volume === 2 ? 2 : 1;
    totals[key] += 1;
    if (statusOf(quest) === 'completed') completed[key] += 1;
  });

  const serverVolumes = pick(ui.data, 'volumes', 'volume_progress');
  [1, 2].forEach((volume) => {
    const server = Array.isArray(serverVolumes)
      ? serverVolumes.find((item) => num(pick(item, 'volume', 'index')) === volume)
      : pick(serverVolumes, String(volume));
    const done = num(pick(server, 'completed', 'done'), completed[volume]);
    const total = num(pick(server, 'total'), totals[volume]);
    setBar(qs(`[data-bar="vol${volume}"]`, root), total ? done / total : 0, `${done} / ${total}`);
    const counter = qs(`[data-vol-count="${volume}"]`, root);
    if (counter) counter.textContent = total ? `已完成 ${done} 段，余 ${total - done} 段` : '暂无此卷任务';
  });

  const totalNode = qs('#quests-count', root);
  if (totalNode) totalNode.textContent = `${ui.quests.length} 条`;
}

function renderList(root) {
  const list = qs('#quest-list', root);
  if (!list) return;
  const shown = ui.quests.filter((quest) => ui.status === 'all' || statusOf(quest) === ui.status);

  if (!ui.quests.length) {
    fill(list, el('li', { class: 'empty', text: '主线尚未载入。' }));
    return;
  }
  if (!shown.length) {
    fill(list, el('li', { class: 'empty', text: '此筛选下无任务。' }));
    return;
  }

  fill(list, shown.map((quest) => {
    const status = statusOf(quest);
    const objectives = normalizeObjectives(quest);
    const rewards = rewardTags(quest);
    const id = questId(quest);

    return el('li', { class: 'questitem', dataset: { status, id: String(id || '') } }, [
      el('div', { class: 'questitem__head' }, [
        el('p', { class: 'questitem__title', text: questTitle(quest) }),
        statusBadge(status),
      ]),
      pick(quest, 'desc', 'description', 'objective_text')
        ? el('p', { class: 'questitem__desc', text: String(pick(quest, 'desc', 'description', 'objective_text')) })
        : null,
      objectives.length
        ? el('ul', { class: 'objectives' }, objectives.map((objective) => {
            const progress = objective.current !== null && objective.target !== null
              ? `${fmt(objective.current)} / ${fmt(objective.target)}`
              : objective.current !== null ? fmt(objective.current) : '';
            const done = objective.done === true
              || (objective.current !== null && objective.target !== null && objective.current >= objective.target);
            return el('li', { class: done ? 'is-done' : '' }, [
              el('span', { text: objective.text }),
              el('span', { class: 'num', text: progress }),
            ]);
          }))
        : null,
      rewards.length
        ? el('div', { class: 'rewards' }, rewards.slice(0, 6).map((tag) => el('span', { class: 'badge', text: tag })))
        : null,
      el('div', { class: 'questitem__foot' }, [
        el('p', { class: 'muted', text: pick(quest, 'region_name', 'region', 'hint') || '' }),
        status === 'ready'
          ? el('button', {
              class: 'btn btn--primary btn--sm',
              type: 'button',
              text: '领取奖励',
              onclick: (event) => claim(root, quest, event.currentTarget),
            })
          : el('span', { class: 'muted', text: status === 'completed' ? '已了' : status === 'locked' ? '尚未开启' : '进行中' }),
      ]),
    ]);
  }));
}

async function claim(root, quest, button) {
  await withBusy(button, async () => {
    try {
      const result = await api.claimQuest(questId(quest));
      const rewards = pick(result, 'rewards', 'granted');
      const labels = rewardTags({ rewards });
      toast(labels.length ? `已领：${labels.slice(0, 3).join('、')}` : `已领《${questTitle(quest)}》之赏`, 'info');
      await load(root);
      await store.refresh({ force: true, silent: true });
    } catch (err) {
      toastError(err);
      if (err.code === '4003') router.go('creation');
    }
  }, { label: '领取…' });
}

async function load(root) {
  const list = qs('#quest-list', root);
  renderLoading(list, '正在翻检因果…');
  try {
    ui.data = await api.quests();
    ui.quests = arr(pick(ui.data, 'quests', 'list', 'items', 'main_quests')).map((quest) => ({ ...quest }));
    ui.quests.sort((a, b) => (num(pick(a, 'order'), 99) - num(pick(b, 'order'), 99))
      || (STATUS_ORDER[statusOf(a)] - STATUS_ORDER[statusOf(b)]));
    renderVolumes(root);
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
  name: 'quests',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      qsa('[data-status]', root).forEach((button) => {
        button.addEventListener('click', () => {
          ui.status = button.dataset.status;
          qsa('[data-status]', root).forEach((node) => node.classList.toggle('is-active', node === button));
          renderList(root);
        });
      });
    }
    await load(root);
  },
};
