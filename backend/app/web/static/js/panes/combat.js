/**
 * panes/combat.js · 战斗（全屏浮层）
 *
 * 一局 30 回合：普攻/术法/防御/道具/逃跑。
 * 会话由服务端持久化，刷新后 GET /combat 原样恢复，不重 roll。
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, el, fill, fmt, toast, toastError, openLayer, closeLayer, withBusy, floatDamage, setBar,
} from '../core/ui.js';
import { refreshAndSync, renderDeltas } from '../core/actions.js';
import router from '../core/router.js';

const ui = { session: null, busy: false, ended: false };

function unwrap(data) {
  if (!data) return null;
  const session = pick(data, 'session', 'combat', 'state') || data;
  if (!session || typeof session !== 'object') return null;
  return { session, envelope: data };
}

function renderFighter(root, side, data) {
  const isPlayer = side === 'player';
  const nameNode = qs(isPlayer ? '#combat-player-name' : '#combat-enemy-name', root);
  if (nameNode) {
    nameNode.textContent = String(pick(data, 'name', 'display_name') || (isPlayer ? '我' : '敌'));
  }
  const hp = num(pick(data, 'hp'), null);
  const hpMax = num(pick(data, 'hp_max', 'max_hp'), null);
  const mp = num(pick(data, 'mp'), null);
  const mpMax = num(pick(data, 'mp_max', 'max_mp'), null);
  const level = num(pick(data, 'level'), null);
  const namePrefix = level === null ? '' : `Lv.${level} `;
  if (nameNode) nameNode.textContent = `${namePrefix}${String(pick(data, 'name', 'display_name') || (isPlayer ? '我' : '敌'))}`;

  setBar(qs(`[data-bar="${isPlayer ? 'php' : 'ehp'}"]`, root), hp !== null && hpMax ? hp / hpMax : null,
    hp === null ? '—' : `${fmt(hp)} / ${fmt(hpMax)}`);
  setBar(qs(`[data-bar="${isPlayer ? 'pmp' : 'emp'}"]`, root), mp !== null && mpMax ? mp / mpMax : null,
    mp === null ? '—' : `${fmt(mp)} / ${fmt(mpMax)}`);

  const statusNode = qs(isPlayer ? '#combat-player-status' : '#combat-enemy-status', root);
  if (statusNode) {
    const bits = [];
    const status = pick(data, 'status', 'state');
    if (status && status !== 'normal') bits.push(String(status));
    const buffs = arr(pick(data, 'buffs', 'effects'));
    buffs.slice(0, 2).forEach((buff) => {
      const label = typeof buff === 'string' ? buff : pick(buff, 'name', 'label', 'key');
      if (label) bits.push(String(label));
    });
    statusNode.textContent = bits.join(' · ');
  }
}

function logLine(item) {
  if (typeof item === 'string') return el('li', { text: item });
  const tone = pick(item, 'tone', 'side', 'kind');
  return el('li', { dataset: { tone: tone ? String(tone) : 'neutral' }, text: String(pick(item, 'text', 'message', 'desc') || '—') });
}

function renderSession(root, payload) {
  const unpacked = unwrap(payload);
  if (!unpacked) {
    renderEmptyState(root);
    return;
  }
  ui.session = unpacked.session;
  const session = ui.session;
  const status = String(pick(session, 'status') || 'active');
  const turn = num(pick(session, 'turn', 'round'), null);
  const maxTurn = num(pick(session, 'max_turn', 'turn_limit'), 30);

  renderFighter(root, 'player', pick(session, 'player', 'self', 'me') || {});
  renderFighter(root, 'enemy', pick(session, 'enemy', 'foe') || {});

  const roundNode = qs('#combat-round', root);
  if (roundNode) roundNode.textContent = turn === null ? `回合 — / ${maxTurn}` : `回合 ${turn} / ${maxTurn}`;

  const hintNode = qs('#combat-hint', root);
  if (hintNode) {
    const fleeBlocked = pick(session, 'flee_blocked_reason', 'can_flee_reason');
    hintNode.textContent = status === 'active'
      ? (fleeBlocked ? String(fleeBlocked) : `敌：${String(pick(session, 'enemy.name', 'enemy_name') || '不明')}`)
      : '战已止。';
  }

  const log = arr(pick(session, 'log', 'logs', 'battle_log', 'turns')).slice(-20);
  const logRoot = qs('#combat-log', root);
  if (logRoot) {
    if (!log.length) fill(logRoot, el('li', { class: 'empty', text: '尚未交锋。' }));
    else fill(logRoot, log.map(logLine));
    logRoot.scrollTop = logRoot.scrollHeight;
  }

  const floats = arr(pick(unpacked.envelope, 'floats', 'damage', 'popups')).concat(arr(pick(session, 'floats')));
  floats.slice(0, 6).forEach((item) => {
    const kind = pick(item, 'kind', 'type');
    const crit = pick(item, 'crit', 'critical') === true || kind === 'crit';
    const dodge = pick(item, 'dodge', 'missed') === true || kind === 'dodge';
    const heal = kind === 'heal' || num(pick(item, 'value'), 0) > 0 && pick(item, 'heal') === true;
    const value = num(pick(item, 'value', 'amount', 'damage'), null);
    const side = pick(item, 'side', 'target') === 'player' ? 'player' : 'enemy';
    const label = dodge ? '闪' : (value === null ? String(pick(item, 'label', 'text') || '') : fmt(value));
    if (label) {
      floatDamage(qs('#float-layer', root), {
        label,
        kind: dodge ? 'dodge' : crit ? 'crit' : heal ? 'heal' : 'normal',
        side,
      });
    }
  });

  const actions = qs('#combat-actions', root);
  if (actions) {
    const fleeBlocked = Boolean(pick(session, 'flee_blocked', 'flee_forbidden')) || Boolean(pick(session, 'flee_blocked_reason'));
    Array.from(actions.querySelectorAll('button.cbattle')).forEach((button) => {
      const isFlee = button.dataset.caction === 'flee';
      button.disabled = status !== 'active' || (isFlee && fleeBlocked);
      if (isFlee && fleeBlocked) button.title = String(pick(session, 'flee_blocked_reason') || '退路已断');
    });
  }

  const resultBox = qs('#combat-result', root);
  const finished = status !== 'active';
  ui.ended = finished;
  if (resultBox) {
    resultBox.hidden = !finished;
    if (finished) {
      const titles = {
        victory: '胜',
        defeat: '败',
        escaped: '已脱身',
        timeout: '三十回合，未分胜负',
        draw: '和局',
      };
      const titleNode = qs('#combat-result-title', root);
      if (titleNode) titleNode.textContent = titles[status] || '战已止';
      renderDeltas(qs('#combat-result-deltas', root), pick(unpacked.envelope, 'rewards', 'effects', 'deltas')
        || pick(session, 'rewards', 'effects'));
    }
  }
}

function renderEmptyState(root) {
  ui.session = null;
  ui.ended = false;
  renderFighter(root, 'player', { name: '我', hp: store.view.hp, hp_max: store.view.hpMax, mp: store.view.mp, mp_max: store.view.mpMax });
  renderFighter(root, 'enemy', { name: '无', hp: 0, hp_max: 0, mp: 0, mp_max: 0 });
  const roundNode = qs('#combat-round', root);
  if (roundNode) roundNode.textContent = '此刻无战事';
  const hintNode = qs('#combat-hint', root);
  if (hintNode) hintNode.textContent = '去历练或切磋，方可遇敌。';
  const logRoot = qs('#combat-log', root);
  if (logRoot) fill(logRoot, el('li', { class: 'empty', text: '四下无事，唯风过野。' }));
  const actions = qs('#combat-actions', root);
  if (actions) {
    Array.from(actions.querySelectorAll('button.cbattle')).forEach((button) => { button.disabled = true; });
  }
  const resultBox = qs('#combat-result', root);
  if (resultBox) {
    resultBox.hidden = false;
    const titleNode = qs('#combat-result-title', root);
    if (titleNode) titleNode.textContent = '无战';
    const deltas = qs('#combat-result-deltas', root);
    if (deltas) fill(deltas, []);
    const cont = qs('#combat-continue', root);
    if (cont) cont.hidden = false;
  }
}

function pickerOf(session, type) {
  const player = pick(session, 'player', 'self') || {};
  if (type === 'skill') return arr(pick(player, 'skills', 'techniques')).map((skill) => ({
    id: pick(skill, 'skill_id', 'id', 'tech_id'),
    label: pick(skill, 'name', 'label') || '术法',
    meta: num(pick(skill, 'mp_cost'), null) === null ? '' : `灵力 ${fmt(pick(skill, 'mp_cost'))}`,
  }));
  return arr(pick(player, 'items', 'usable_items')).map((item) => ({
    id: pick(item, 'item_id', 'id'),
    label: pick(item, 'name', 'label') || '道具',
    meta: num(pick(item, 'qty', 'count'), null) === null ? '' : `×${fmt(pick(item, 'qty'))}`,
  }));
}

function showPicker(root, type) {
  const box = qs('#combat-picker', root);
  const list = qs('#combat-picker-list', root);
  const title = qs('#combat-picker-title', root);
  if (!box || !list) return;
  const options = pickerOf(ui.session, type);
  if (!options.length) {
    toast(type === 'skill' ? '未学术法，先以普攻应敌' : '囊中无可用之物', 'warn');
    return;
  }
  if (title) title.textContent = type === 'skill' ? '择一术法' : '取一物';
  fill(list, options.map((option) => el('li', null, [
    el('button', {
      type: 'button',
      onclick: () => {
        box.hidden = true;
        submit(root, { action_type: type, [type === 'skill' ? 'skill_id' : 'item_id']: option.id });
      },
    }, [
      el('span', { text: String(option.label) }),
      option.meta ? el('span', { class: 'muted', text: ` ${option.meta}` }) : null,
    ]),
  ])));
  box.hidden = false;
}

async function submit(root, payload) {
  if (ui.busy) return;
  if (!ui.session || ui.ended) {
    toast('战已止，先收势', 'warn');
    return;
  }
  ui.busy = true;
  const actions = qs('#combat-actions', root);
  const button = actions ? actions.querySelector(`[data-caction="${payload.action_type}"]`) : null;

  await withBusy(button, async () => {
    try {
      const data = await api.combatAction(payload);
      renderSession(root, data);
      if (ui.ended) {
        await store.refresh({ force: true, silent: true });
      }
    } catch (err) {
      toastError(err);
      if (err.code === '4014') {
        ui.ended = true;
        await refreshAndSync();
      }
      if (err.code === '4003') router.go('creation');
    }
  }, { label: '交锋…', group: actions });
  ui.busy = false;
}

function bind(root) {
  if (!root || root.dataset.bound) return;
  root.dataset.bound = '1';

  qs('#combat-actions', root)?.addEventListener('click', (event) => {
    const button = event.target.closest('button.cbattle');
    if (!button || button.disabled) return;
    const action = button.dataset.caction;
    if (action === 'skill' || action === 'item') {
      showPicker(root, action);
      return;
    }
    submit(root, { action_type: action });
  });

  qs('#combat-picker-cancel', root)?.addEventListener('click', () => {
    const box = qs('#combat-picker', root);
    if (box) box.hidden = true;
  });

  qs('#combat-continue', root)?.addEventListener('click', () => finish(root));
}

async function open(root) {
  if (!store.hasCharacter) return;
  bind(root);
  openLayer('layer-combat');
  const logRoot = qs('#combat-log', root);
  if (logRoot) fill(logRoot, el('li', { class: 'empty', text: '正在寻敌…' }));
  try {
    const data = await api.combat();
    if (!data || (data.session === null || data.status === null) && data.status !== 'active' && !pick(data, 'player')) {
      renderEmptyState(root);
      return;
    }
    renderSession(root, data);
  } catch (err) {
    if (err.code === '4012' || err.code === '404') {
      renderEmptyState(root);
      return;
    }
    toastError(err);
    if (logRoot) fill(logRoot, el('li', { class: 'empty', text: `未能开战：${err.message}` }));
  }
}

async function finish(root) {
  closeLayer('layer-combat');
  ui.session = null;
  ui.ended = false;
  await refreshAndSync();
}

export default {
  name: 'combat',

  open,

  async enter(root) {
    bind(root);
    if (store.view.combatActive) await open(root);
    else renderEmptyState(root);
  },
};
