/**
 * panes/forbidden.js · 禁地（2 处启用 + 8 处未开）
 *
 * 进入前置由服务端裁决（仅散修 / 年次数 / 冷却 / 贡献）；
 * 前端只把 land 上的 enabled / lock_reason / 余量原样展示，并给出可执行原因。
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, el, fill, fmt, toast, toastError, withBusy, renderLoading, renderError,
} from '../core/ui.js';
import { refreshAndSync } from '../core/actions.js';
import router from '../core/router.js';

const ui = { lands: [] };

function landId(land) {
  return pick(land, 'land_id', 'id', 'key');
}

function landName(land) {
  return String(pick(land, 'name', 'display_name', 'title') || landId(land) || '未名死地');
}

function enabledOf(land) {
  const enabled = pick(land, 'enabled', 'open', 'available');
  if (enabled !== undefined) return Boolean(enabled);
  const unlocked = pick(land, 'unlocked');
  return unlocked === undefined ? false : Boolean(unlocked);
}

function blockReason(land) {
  const reason = pick(land, 'lock_reason', 'reason', 'block_reason', 'requirement_text');
  if (reason) return String(reason);
  if (!enabledOf(land)) return '尚在封禁，非此世可入';
  const left = num(pick(land, 'year_entries_left', 'remaining_entries'), null);
  if (left !== null && left <= 0) return '本年次数已尽（每年至多三次）';
  const cooldown = num(pick(land, 'cooldown_left_days', 'cooldown_days_left'), null);
  if (cooldown !== null && cooldown > 0) return `余烬未散，${fmt(cooldown)} 日后可再入`;
  return '';
}

function rewardTags(land) {
  const raw = pick(land, 'first_clear_rewards', 'rewards', 'first_rewards');
  return arr(raw).map((item) => {
    if (typeof item === 'string') return item;
    const name = pick(item, 'name', 'label', 'tech_id', 'item_id', 'type');
    const qty = num(pick(item, 'qty', 'value', 'count'), null);
    return name ? (qty ? `${name} ×${fmt(qty)}` : String(name)) : '赏';
  }).filter(Boolean);
}

function renderLand(root, land, sealed) {
  const id = landId(land);
  const enabled = enabledOf(land);
  const reason = blockReason(land);
  const yearUsed = num(pick(land, 'year_entries', 'entries_this_year', 'used_entries'), null);
  const yearLimit = num(pick(land, 'year_limit', 'entries_per_year', 'max_entries'), 3);
  const contribution = num(pick(land, 'entry_contribution', 'contribution_cost', 'cost_contribution'), null);
  const days = num(pick(land, 'entry_days', 'enter_days'), null);
  const boss = pick(land, 'boss_name', 'boss.name', 'boss_id');
  const cleared = pick(land, 'cleared', 'first_cleared') === true;
  const rewards = rewardTags(land);

  return el('li', { class: 'land', dataset: { id: String(id || '') } }, [
    el('div', { class: 'land__head' }, [
      el('p', { class: 'land__name', text: landName(land) }),
      el('span', {
        class: `badge${sealed || !enabled ? ' badge--locked' : cleared ? ' badge--completed' : ' badge--active'}`,
        text: sealed || !enabled ? '封禁' : cleared ? '已破' : '可入',
      }),
    ]),
    pick(land, 'desc', 'description')
      ? el('p', { class: 'land__desc', text: String(pick(land, 'desc', 'description')) })
      : null,
    el('div', { class: 'land__meta' }, [
      pick(land, 'tier_name', 'tier') ? el('span', { class: 'badge', text: String(pick(land, 'tier_name', 'tier')) }) : null,
      num(pick(land, 'required_realm_index'), null) !== null
        ? el('span', { class: 'badge', text: `境界 ${fmt(pick(land, 'required_realm_index'))}` })
        : null,
      el('span', { class: 'badge', text: `本年 ${yearUsed === null ? '—' : fmt(yearUsed)} / ${fmt(yearLimit)}` }),
      contribution !== null ? el('span', { class: 'badge badge--ready', text: `贡献 ${fmt(contribution)}` }) : null,
      days !== null ? el('span', { class: 'badge', text: `耗时 ${fmt(days)} 日` }) : null,
      boss ? el('span', { class: 'badge badge--danger', text: `守关：${boss}` }) : null,
    ]),
    rewards.length
      ? el('div', { class: 'rewards' }, rewards.slice(0, 5).map((tag) => el('span', { class: 'badge badge--ready', text: tag })))
      : null,
    el('div', { class: 'land__foot' }, [
      el('p', { class: 'muted', text: sealed ? '此世不入，留待后卷。' : (reason || (cleared ? '非首通只余灵石。' : '首通必得功法。')) }),
      sealed
        ? el('span', { class: 'badge badge--locked', text: '未开放' })
        : el('button', {
            class: 'btn btn--danger btn--sm',
            type: 'button',
            text: '踏入',
            disabled: !enabled || Boolean(reason),
            title: reason || '',
            onclick: (event) => enter(root, land, event.currentTarget),
          }),
    ]),
  ]);
}

async function enter(root, land, button) {
  await withBusy(button, async () => {
    try {
      const result = await api.forbiddenEnter(landId(land));
      const days = num(pick(result, 'days', 'entry_days', 'time_cost_days'), null);
      toast(days === null ? `已入${landName(land)}` : `行 ${fmt(days)} 日，已入${landName(land)}`, 'info');
      await refreshAndSync();
    } catch (err) {
      toastError(err);
      if (err.code === '4003') router.go('creation');
    }
  }, { label: '入内…' });
}

async function load(root) {
  const open = qs('#forbidden-open', root);
  const sealed = qs('#forbidden-sealed', root);
  if (open) renderLoading(open, '正在探路…');
  if (sealed) renderLoading(sealed, '…');
  try {
    const data = await api.forbidden();
    ui.lands = arr(pick(data, 'lands', 'list', 'items', 'forbidden_lands'));
    const enabled = ui.lands.filter(enabledOf);
    const closed = ui.lands.filter((land) => !enabledOf(land));

    if (open) {
      fill(open, enabled.length
        ? enabled.map((land) => renderLand(root, land, false))
        : el('li', { class: 'empty', text: '此地暂无可入之门。' }));
    }
    if (sealed) {
      fill(sealed, closed.length
        ? closed.map((land) => renderLand(root, land, true))
        : el('li', { class: 'empty', text: '无封禁之地。' }));
    }

    const meta = qs('#forbidden-meta', root);
    if (meta) meta.textContent = `${ui.lands.length} 处 · 已开 ${enabled.length} 处`;
    const sub = qs('#forbidden-sub', root);
    if (sub) {
      sub.textContent = `十处死地，仅 ${enabled.length || 0} 处可入；散修方得踏足。`;
    }
  } catch (err) {
    if (err.code === '4003') {
      router.go('creation');
      return;
    }
    toastError(err);
    if (open) renderError(open, err, () => load(root));
    if (sealed) fill(sealed, []);
  }
}

export default {
  name: 'forbidden',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    await load(root);
  },
};
