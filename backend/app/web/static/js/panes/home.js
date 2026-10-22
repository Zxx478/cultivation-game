/**
 * panes/home.js · 游戏首页
 *
 * 职责：标题/玩法说明/开始游戏/存档选择。
 * 流程：首页 →（开始游戏）创角 →（创角提交）主界面；
 *       也可直接读取手动/自动存档进入主界面。
 */
import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import { qs, el, fill, fmt, toast, toastError } from '../core/ui.js';
import router from '../core/router.js';

const ACTION_LABELS = {
  seclusion: '闭关修炼',
  adventure: '外出冒险',
  gather: '采药',
  spar: '切磋',
  meditate: '打坐疗伤',
  breakthrough: '突破冲关',
};

const ui = { loading: false, meta: null };

function renderActions(root) {
  const start = qs('#home-start', root);
  const cont = qs('#home-continue', root);
  const hint = qs('#home-hint', root);
  const has = store.hasCharacter;

  if (start) {
    start.textContent = has ? '重开一世' : '开始游戏';
    start.disabled = ui.loading;
  }
  if (cont) {
    cont.hidden = !has;
    cont.disabled = ui.loading;
    cont.textContent = has && store.view.name ? `继续 · ${store.view.name}` : '继续修行';
  }
  if (hint) {
    hint.textContent = has
      ? `当前角色：${store.view.name || '无名'} · ${store.view.realmName || ''} · 第 ${store.view.gameYear ?? '—'} 年`
      : '尚未有修行记录；点击「开始游戏」进入创角。';
  }
}

function renderTime(root) {
  const body = qs('#home-time-body', root);
  if (!body) return;
  const view = store.view;
  const base = view.actionBaseDays || (ui.meta && ui.meta.action_base_days) || {};
  const now = view.actionDays || {};
  const rows = Object.keys(ACTION_LABELS).map((kind) => ({
    kind,
    label: ACTION_LABELS[kind],
    base: num(base[kind], null),
    now: num(now[kind], num(base[kind], null)),
  }));
  if (!rows.length) {
    fill(body, el('tr', null, [el('td', { colspan: 3, class: 'muted', text: '载入中…' })]));
    return;
  }
  fill(body, rows.map((row) => el('tr', null, [
    el('th', { scope: 'row', text: row.label }),
    el('td', { class: 'num', text: row.base === null ? '—' : `${fmt(row.base)} 日` }),
    el('td', { class: 'num', text: row.now === null ? '—' : `${fmt(row.now)} 日` }),
  ])));
}

function slotLabel(slot) {
  const name = pick(slot, 'name', 'char_name') || '无名';
  const level = num(pick(slot, 'level'), null);
  const version = num(pick(slot, 'version'), null);
  const bits = [name];
  if (level !== null) bits.push(`Lv.${fmt(level)}`);
  if (version !== null) bits.push(`v${fmt(version)}`);
  return bits.join(' · ');
}

function renderSaves(root, slots) {
  const list = qs('#home-save-list', root);
  const empty = qs('#home-save-empty', root);
  if (!list) return;
  const rows = arr(slots).filter((slot) => pick(slot, 'exists', 'has_save') === true || pick(slot, 'char_id'));
  if (!rows.length) {
    fill(list, null);
    if (empty) empty.textContent = '暂无存档。首次创角后会自动写入自动槽。';
    return;
  }
  if (empty) empty.textContent = '';
  fill(list, rows.map((slot) => {
    const slotId = num(pick(slot, 'slot_id', 'id'), null);
    const isAuto = slotId === 0;
    return el('li', { class: 'savecard' }, [
      el('div', { class: 'savecard__main' }, [
        el('p', { class: 'savecard__title', text: isAuto ? '自动存档' : `手动槽 ${slotId}` }),
        el('p', { class: 'savecard__meta', text: slotLabel(slot) }),
      ]),
      el('button', {
        class: 'btn btn--ghost btn--sm',
        type: 'button',
        text: '读取',
        dataset: { slot: String(slotId ?? '') },
      }),
    ]);
  }));
}

async function loadMeta() {
  if (ui.meta !== null) return ui.meta;
  try {
    ui.meta = await api.meta();
  } catch (err) {
    ui.meta = null;
  }
  const version = qs('#home-version');
  if (version) {
    const v = ui.meta && ui.meta.content_version;
    version.textContent = v ? `v${v} · MVP（锻血—辟府）` : 'v1.5 · MVP（锻血—辟府）';
  }
  return ui.meta;
}

async function loadSaves(root) {
  try {
    const data = await api.saves();
    renderSaves(root, pick(data, 'slots', 'list') || []);
  } catch (err) {
    toastError(err);
    const list = qs('#home-save-list', root);
    if (list) fill(list, el('li', { class: 'empty', text: '存档读取失败。' }));
  }
}

async function loadSlot(root, slotId, button) {
  if (slotId === null) return;
  await withBusy(button, async () => {
    try {
      await api.loadSlot(slotId);
      await store.refresh({ force: true });
      toast('已读取存档', 'info');
      router.go('main');
    } catch (err) {
      toastError(err);
      await loadSaves(root);
    }
  }, { label: '读取…' });
}

async function withBusy(button, task, options = {}) {
  if (!button) return task();
  const old = button.textContent;
  const label = options.label || '…';
  button.disabled = true;
  button.textContent = label;
  try {
    return await task();
  } finally {
    button.disabled = false;
    button.textContent = old;
  }
}

export default {
  name: 'home',

  async enter(root) {
    await loadMeta();
    renderActions(root);
    renderTime(root);
    await loadSaves(root);

    const start = qs('#home-start', root);
    if (start && !start.dataset.bound) {
      start.dataset.bound = '1';
      start.addEventListener('click', () => router.go('creation'));
    }
    const cont = qs('#home-continue', root);
    if (cont && !cont.dataset.bound) {
      cont.dataset.bound = '1';
      cont.addEventListener('click', () => router.go('main'));
    }
    const theme = qs('#home-theme', root);
    if (theme && !theme.dataset.bound) {
      theme.dataset.bound = '1';
      const sync = () => {
        theme.textContent = store.prefs.theme === 'paper' ? '夜色' : '宣纸';
      };
      sync();
      theme.addEventListener('click', () => {
        store.toggleTheme();
        sync();
        const chrome = qs('#theme-toggle');
        if (chrome) {
          chrome.textContent = store.prefs.theme === 'paper' ? '夜色' : '宣纸';
          chrome.setAttribute('aria-pressed', store.prefs.theme === 'paper' ? 'true' : 'false');
        }
      });
    }

    const list = qs('#home-save-list', root);
    if (list && !list.dataset.bound) {
      list.dataset.bound = '1';
      list.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-slot]');
        if (!button || button.disabled) return;
        loadSlot(root, num(button.dataset.slot, null), button);
      });
    }
  },

  leave() {
    ui.loading = false;
  },
};
