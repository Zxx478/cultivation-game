/**
 * panes/saves.js · 存档（3 手动 + 1 自动；保存/读取/导出/导入/删除）
 *
 * 自动槽（slot 0）只读：玩家侧 save/delete 均由服务端拒绝（4019）。
 * 409 版本冲突由 core/ui.js 的 toastError 统一给出「重新载入」。
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, el, fill, fmt, fmtTime, toast, toastError, withBusy, renderLoading, renderError,
} from '../core/ui.js';
import router from '../core/router.js';

const SLOT_IDS = [0, 1, 2, 3];
const ui = { slots: [], currentVersion: null };

function slotsFromData(data) {
  const raw = arr(pick(data, 'slots', 'list', 'save_slots'));
  const byId = new Map();
  raw.forEach((slot) => {
    const id = num(pick(slot, 'slot_id', 'id', 'slot'), null);
    if (id !== null) byId.set(id, slot);
  });
  return SLOT_IDS.map((id) => byId.get(id) || { slot_id: id, empty: true });
}

function slotLabel(slot) {
  const id = num(pick(slot, 'slot_id'), 0);
  return id === 0 ? '自动存档' : `存档 ${id}`;
}

function slotExists(slot) {
  const exists = pick(slot, 'exists', 'occupied');
  if (exists !== undefined) return Boolean(exists);
  return Boolean(pick(slot, 'created_at', 'snapshot', 'char_name', 'name'));
}

function renderSlots(root) {
  const list = qs('#save-slots', root);
  if (!list) return;
  const version = ui.currentVersion;

  if (!ui.slots.length) {
    fill(list, el('li', { class: 'empty', text: '暂无槽位。' }));
    return;
  }

  fill(list, ui.slots.map((slot) => {
    const id = num(pick(slot, 'slot_id'), 0);
    const exists = slotExists(slot);
    const isAuto = id === 0;
    const created = pick(slot, 'created_at', 'saved_at', 'time');
    const metaBits = [
      exists ? `道号 ${pick(slot, 'char_name', 'name') || '—'}` : '空槽',
      num(pick(slot, 'level'), null) !== null ? `Lv.${fmt(pick(slot, 'level'))}` : null,
      num(pick(slot, 'age_days'), null) !== null ? `第 ${fmt(pick(slot, 'age_days'))} 日` : null,
      pick(slot, 'content_version') ? `内容 ${pick(slot, 'content_version')}` : null,
      created ? fmtTime(created) : null,
    ].filter(Boolean);

    const actions = [];
    if (isAuto) {
      actions.push(el('span', { class: 'badge badge--ready', text: '只读' }));
    } else {
      actions.push(el('button', {
        class: 'btn btn--ghost btn--sm',
        type: 'button',
        text: exists ? '覆盖保存' : '保存',
        onclick: (event) => save(root, id, event.currentTarget),
      }));
    }
    actions.push(el('button', {
      class: 'btn btn--ghost btn--sm',
      type: 'button',
      text: '读取',
      disabled: !exists,
      onclick: (event) => loadSlot(root, id, event.currentTarget),
    }));
    actions.push(el('button', {
      class: 'btn btn--ghost btn--sm',
      type: 'button',
      text: '导出',
      disabled: !exists,
      onclick: (event) => exportSlot(root, id, event.currentTarget),
    }));
    if (!isAuto) {
      actions.push(el('button', {
        class: 'btn btn--danger btn--sm',
        type: 'button',
        text: '删除',
        disabled: !exists,
        onclick: (event) => removeSlot(root, id, event.currentTarget),
      }));
    }

    return el('li', { class: `slotrow${isAuto ? ' slotrow--auto' : ''}${exists ? '' : ' slotrow--empty'}`, dataset: { slot: String(id) } }, [
      el('div', { class: 'slotrow__head' }, [
        el('p', { class: 'slotrow__name', text: slotLabel(slot) }),
        el('span', {
          class: `badge${isAuto ? ' badge--ready' : exists ? ' badge--active' : ' badge--locked'}`,
          text: isAuto ? '系统写入' : exists ? '已有档' : '空',
        }),
      ]),
      el('p', { class: 'slotrow__meta', text: metaBits.join(' · ') }),
      el('div', { class: 'slotrow__actions' }, actions),
    ]);
  }));

  const meta = qs('#saves-meta', root);
  if (meta) {
    meta.textContent = version === null ? '版本未知' : `当前版本 ${version}`;
  }
}

async function loadData(root) {
  const list = qs('#save-slots', root);
  renderLoading(list, '正在翻检卷宗…');
  try {
    const data = await api.saves();
    ui.slots = slotsFromData(data);
    ui.currentVersion = num(pick(data, 'current_version', 'version'), store.view.version);
    renderSlots(root);
  } catch (err) {
    if (err.code === '4003') {
      router.go('creation');
      return;
    }
    toastError(err);
    renderError(list, err, () => loadData(root));
  }
}

async function save(root, slotId, button) {
  const version = ui.currentVersion !== null ? ui.currentVersion : store.view.version;
  await withBusy(button, async () => {
    try {
      await api.saveSlot(slotId, version);
      toast(`已写入存档 ${slotId}`, 'info');
      await store.refresh({ force: true, silent: true });
      await loadData(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '落笔…' });
}

async function loadSlot(root, slotId, button) {
  await withBusy(button, async () => {
    try {
      await api.loadSlot(slotId);
      toast(`已读回存档 ${slotId}`, 'info');
      await store.refresh({ force: true });
      await loadData(root);
      router.go('main');
    } catch (err) {
      toastError(err);
    }
  }, { label: '读取…' });
}

async function exportSlot(root, slotId, button) {
  await withBusy(button, async () => {
    try {
      const data = await api.exportSlot(slotId);
      const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
      const textarea = qs('#import-text', root);
      if (textarea) textarea.value = text;
      try {
        const blob = new Blob([text], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = el('a', { href: url, download: `huangtianji-save-${slotId}.json` });
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        window.setTimeout(() => URL.revokeObjectURL(url), 2000);
      } catch (downloadError) {
        /* 下载被拦时，文本已放入输入框可手动复制 */
      }
      toast(`存档 ${slotId} 已导出（文本已填入下方输入框）`, 'info', { timeout: 4200 });
    } catch (err) {
      toastError(err);
    }
  }, { label: '导出…' });
}

async function removeSlot(root, slotId, button) {
  await withBusy(button, async () => {
    try {
      await api.deleteSlot(slotId);
      toast(`已抹去存档 ${slotId}`, 'info');
      await loadData(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '删除…' });
}

async function importSave(root, button) {
  const textarea = qs('#import-text', root);
  const hint = qs('#import-hint', root);
  const fileInput = qs('#import-file', root);
  const text = textarea ? textarea.value.trim() : '';

  if (!text) {
    if (hint) hint.textContent = '先把导出的文本粘进来（或选择文件）。';
    toast('导入内容为空', 'warn');
    return;
  }

  let payload = text;
  try {
    payload = JSON.parse(text);
  } catch (err) {
    payload = text;
  }

  await withBusy(button, async () => {
    try {
      await api.importSave(payload);
      toast('档案已入卷宗，新建槽位', 'info');
      if (textarea) textarea.value = '';
      if (fileInput) fileInput.value = '';
      await store.refresh({ force: true, silent: true });
      await loadData(root);
    } catch (err) {
      toastError(err);
      if (hint) hint.textContent = `导入被拒：${err.message}（原存档未受影响）`;
    }
  }, { label: '校验…' });
}

export default {
  name: 'saves',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      qs('#import-submit', root)?.addEventListener('click', (event) => importSave(root, event.currentTarget));
      qs('#import-clear', root)?.addEventListener('click', () => {
        const textarea = qs('#import-text', root);
        const fileInput = qs('#import-file', root);
        if (textarea) textarea.value = '';
        if (fileInput) fileInput.value = '';
      });
      const fileInput = qs('#import-file', root);
      if (fileInput) {
        fileInput.addEventListener('change', () => {
          const file = fileInput.files && fileInput.files[0];
          if (!file) return;
          const reader = new FileReader();
          reader.onload = () => {
            const textarea = qs('#import-text', root);
            if (textarea) textarea.value = String(reader.result || '');
            toast('文件已读入，确认后导入', 'info');
          };
          reader.onerror = () => toast('文件读取失败', 'error');
          reader.readAsText(file);
        });
      }
    }
    await loadData(root);
  },
};
