/**
 * panes/sect.js · 宗门 / 方向 / 散修
 *
 * 一处身份，三条去处：
 * - 未择路：七选一（6 宗门 + 散修），逐条显示可判定的入宗条件；
 * - 已入宗：方向树（贡献点亮）、宗门任务接取/完成、贡献兑换；
 * - 散修：奇遇（可能产 pending_event）、贡献转换（一次性 50%）。
 */

import { api } from '../core/api.js';
import { store, pick, num, arr } from '../core/state.js';
import {
  qs, el, fill, fmt, toast, toastError, withBusy, renderLoading, renderError,
} from '../core/ui.js';
import { refreshAndSync } from '../core/actions.js';
import router from '../core/router.js';

const ui = { data: null, role: 'none' };

function detectRole(data) {
  const pathId = String(pick(data, 'path_id', 'identity.path_id', 'character.path_id') || store.view.pathId || '');
  const role = pick(data, 'role', 'identity.role');
  if (role === 'sect' || role === 'sansxiu') return role;
  if (pathId === 'sansxiu') return 'sansxiu';
  const sect = pick(data, 'sect', 'sect_name');
  if (sect) return 'sect';
  if (pathId && pathId !== 'none') return 'sect';
  return 'none';
}

function requirementLines(raw) {
  if (!raw) return [];
  if (typeof raw === 'string') return [{ text: raw, met: null }];
  if (Array.isArray(raw)) {
    return raw.map((item) => {
      if (typeof item === 'string') return { text: item, met: null };
      return {
        text: pick(item, 'text', 'desc', 'label', 'name') || String(item),
        met: pick(item, 'met', 'satisfied', 'ok', 'passed'),
      };
    });
  }
  if (typeof raw === 'object') {
    if (Array.isArray(raw.items)) return requirementLines(raw.items);
    const lines = [];
    if (raw.text) lines.push({ text: String(raw.text), met: pick(raw, 'met') });
    Object.keys(raw).forEach((key) => {
      const value = raw[key];
      if (key === 'text' || key === 'met' || key === 'items') return;
      const labels = {
        min_level: '境界不低于 Lv.', trial_win_streak: '试剑连胜 ', attrs: '属性要求 ',
        materials_any: '需灵药 ', materials_qty: '份', karma_max: '因果须不高于 ',
      };
      if (typeof value === 'number' || typeof value === 'string') {
        const label = labels[key];
        if (key === 'min_level') lines.push({ text: `境界不低于 Lv.${value}`, met: null });
        else if (key === 'trial_win_streak') lines.push({ text: `试剑连胜 ${value} 场`, met: null });
        else if (key === 'karma_max') lines.push({ text: `因果不高于 ${value}`, met: null });
        else if (label) lines.push({ text: `${label}${value}`, met: null });
        else lines.push({ text: `${key}：${value}`, met: null });
      } else if (value && typeof value === 'object') {
        Object.keys(value).forEach((attrKey) => {
          lines.push({ text: `属性 ${attrKey} 需 ≥ ${value[attrKey]}`, met: null });
        });
      }
    });
    return lines;
  }
  return [];
}

function renderIdentity(root) {
  const data = ui.data || {};
  const roleText = { sect: '宗门弟子', sansxiu: '散修', none: '未择路' }[ui.role] || '未择路';
  const sectName = pick(data, 'sect.name', 'sect_name', 'sect.display_name') || (ui.role === 'sansxiu' ? '无门无派' : '—');
  const contribution = pick(data, 'contribution', 'sect.contribution', 'member.contribution');
  const direction = pick(data, 'direction_name', 'direction.name', 'character.direction_id');

  const map = {
    role: roleText,
    sect: sectName,
    contribution: contribution === undefined || contribution === null ? '—' : fmt(contribution),
    direction: direction === undefined || direction === null || direction === '' ? '未定' : String(direction),
  };
  Object.keys(map).forEach((key) => {
    const node = qs(`[data-id="${key}"]`, root);
    if (node) node.textContent = map[key];
  });

  const hint = qs('#sect-identity-hint', root);
  if (hint) {
    if (ui.role === 'none') hint.textContent = '辟府之后方可择门；六宗各设门槛，散修无门无派。';
    else if (ui.role === 'sansxiu') hint.textContent = '散修无方向加成；贡献存于独立钱包，入宗时折半转换，且不可回退。';
    else hint.textContent = pick(data, 'hint', 'message') || '方向节点按累计贡献点亮；宗门任务每游戏年限次。';
  }

  const sub = qs('#sect-sub', root);
  if (sub) {
    sub.textContent = ui.role === 'none'
      ? '辟府之后，方可择门；一步踏错，再无退路。'
      : `${roleText} · ${sectName}`;
  }
}

function renderPaths(root) {
  const card = qs('#sect-paths', root);
  if (!card) return;
  const data = ui.data || {};
  const list = qs('#sect-path-list', root);
  const joinable = arr(pick(data, 'joinable', 'sects', 'options'));

  card.hidden = ui.role !== 'none';
  if (!list) return;

  if (!joinable.length) {
    fill(list, el('li', { class: 'empty', text: '尚无可入之门；或境界未至，或服务端未返回候选。' }));
    return;
  }

  fill(list, joinable.map((sect) => {
    const id = pick(sect, 'id', 'sect_id', 'key');
    const requirements = requirementLines(pick(sect, 'requirements', 'join_requirements', 'conds'));
    const canJoin = pick(sect, 'can_join', 'joinable', 'available');
    const reason = pick(sect, 'reason', 'lock_reason', 'missing_text');
    const isSansxiu = String(id) === 'sansxiu';

    return el('li', { class: 'pathcard', dataset: { id: String(id || '') } }, [
      el('div', { class: 'pathcard__head' }, [
        el('p', { class: 'pathcard__name', text: String(pick(sect, 'name', 'display_name') || (isSansxiu ? '散修 · 僻行独修' : '未名宗门')) }),
        el('span', {
          class: `badge${canJoin === false ? ' badge--locked' : ' badge--active'}`,
          text: canJoin === false ? '条件未达' : '可入',
        }),
      ]),
      pick(sect, 'desc', 'description')
        ? el('p', { class: 'questitem__desc', text: String(pick(sect, 'desc', 'description')) })
        : null,
      requirements.length
        ? el('ul', { class: 'reqlist' }, requirements.map((line) => el('li', {
            class: line.met === true ? 'is-met' : line.met === false ? 'is-missing' : '',
            text: line.text,
          })))
        : null,
      el('div', { class: 'questitem__foot' }, [
        el('p', { class: 'muted', text: reason ? String(reason) : (canJoin === false ? '条件未达，缺口见上。' : '择定之后不可回退。') }),
        el('button', {
          class: 'btn btn--primary btn--sm',
          type: 'button',
          text: isSansxiu ? '此为散修' : '拜入此门',
          disabled: canJoin === false,
          onclick: (event) => join(root, sect, event.currentTarget),
        }),
      ]),
    ]);
  }));
}

function renderDirection(root) {
  const card = qs('#sect-direction', root);
  if (!card) return;
  const data = ui.data || {};
  const tree = qs('#direction-tree', root);
  const directions = arr(pick(data, 'directions', 'direction_tree', 'paths'));

  card.hidden = ui.role !== 'sect';
  if (!tree) return;

  if (!directions.length) {
    fill(tree, el('p', { class: 'empty', text: '方向树未载；请先入宗或择定方向。' }));
    return;
  }

  fill(tree, directions.map((direction) => {
    const layers = arr(pick(direction, 'layers', 'nodes', 'tiers'));
    const currentDirection = pick(data, 'character.direction_id', 'direction_id', 'direction.id');
    const isCurrent = String(pick(direction, 'id')) === String(currentDirection);
    return el('section', { class: 'tree__dir', dataset: { dir: String(pick(direction, 'id') || '') } }, [
      el('div', { class: 'tree__dirhead' }, [
        el('p', { class: 'tree__dirname', text: String(pick(direction, 'name', 'display_name') || '未名方向') }),
        isCurrent
          ? el('span', { class: 'badge badge--ready', text: '所修方向' })
          : el('button', {
              class: 'btn btn--ghost btn--sm',
              type: 'button',
              text: '定为此方向',
              onclick: (event) => setDirection(root, direction, event.currentTarget),
            }),
      ]),
      el('div', { class: 'tree__layers' }, layers.map((layer) => {
        const nodes = arr(pick(layer, 'nodes', 'items', 'children'));
        return el('div', { class: 'tree__layer' }, [
          el('p', { class: 'tree__layerlabel', text: `第 ${pick(layer, 'layer', 'tier', 'level') || '?'} 层` }),
          el('div', { class: 'nodes' }, nodes.map((node) => {
            const unlocked = pick(node, 'unlocked', 'activated', 'is_open') === true;
            const need = num(pick(node, 'required_total_contribution', 'required_contribution', 'need', 'cost'), null);
            return el('div', { class: `node${unlocked ? ' is-open' : ''}` }, [
              el('span', { class: 'node__name', text: String(pick(node, 'name', 'display_name') || '未名节点') }),
              el('span', { class: 'node__meta', text: unlocked ? '已点亮' : (need === null ? '未点亮' : `需贡献 ${fmt(need)}`) }),
              pick(node, 'desc', 'effects_text')
                ? el('span', { class: 'node__meta', text: String(pick(node, 'desc', 'effects_text')) })
                : null,
            ]);
          })),
        ]);
      })),
    ]);
  }));

  const meta = qs('#direction-meta', root);
  if (meta) meta.textContent = pick(data, 'direction_name', 'direction.name') || '未定方向';
}

function renderSansxiu(root) {
  const card = qs('#sect-sansxiu', root);
  if (!card) return;
  const data = ui.data || {};
  card.hidden = ui.role !== 'sansxiu';

  const walletNode = qs('#sansxiu-wallet', root);
  const wallet = pick(data, 'sansxiu.general_contribution', 'wallet.general_contribution', 'general_contribution');
  if (walletNode) walletNode.textContent = wallet === undefined || wallet === null ? '贡献钱包 —' : `散修贡献 ${fmt(wallet)}`;

  const hint = qs('#sansxiu-hint', root);
  const converted = pick(data, 'sansxiu.converted', 'converted') === true;
  if (hint) {
    hint.textContent = converted
      ? '贡献已转换过一次，不可再转；身份不可回退。'
      : '奇遇产出散修独家功法；贡献转换只能一次，且不可回退。';
  }
  const convertBtn = qs('#sansxiu-convert', root);
  if (convertBtn) convertBtn.disabled = converted;
}

function renderSectQuests(root) {
  const card = qs('#sect-quests', root);
  if (!card) return;
  card.hidden = ui.role !== 'sect';
  const data = ui.data || {};
  const body = qs('#sect-quest-body', root);
  const meta = qs('#sect-quest-meta', root);
  const active = pick(data, 'sect_quest', 'active_sect_quest', 'active_quest');

  if (meta) {
    const remainingNode = pick(data, 'sect_quest_remaining', 'quests_remaining');
    meta.textContent = remainingNode === undefined || remainingNode === null
      ? '每游戏年限次'
      : `本年可接 ${fmt(remainingNode)} 次`;
  }
  if (!body) return;

  if (!active) {
    fill(body, el('p', { class: 'questcard--empty', text: '当前无在办事务。可接一桩，贡献与灵石同至。' }));
    return;
  }

  const objectives = arr(pick(active, 'objectives', 'targets'));
  const title = pick(active, 'title', 'name', 'quest_name') || '宗门事务';
  const status = pick(active, 'status') || 'active';
  const rewards = arr(pick(active, 'rewards', 'reward'));

  fill(body, [
    el('div', { class: 'questitem__head' }, [
      el('p', { class: 'questitem__title', text: String(title) }),
      el('span', { class: `badge${status === 'ready' || status === 'completed' ? ' badge--ready' : ' badge--active'}`, text: String(pick(active, 'status_text') || status) }),
    ]),
    pick(active, 'desc', 'description')
      ? el('p', { class: 'questitem__desc', text: String(pick(active, 'desc', 'description')) })
      : null,
    objectives.length
      ? el('ul', { class: 'objectives' }, objectives.map((item) => el('li', null, [
          el('span', { text: String(pick(item, 'desc', 'text', 'label', 'type') || '目标') }),
          el('span', { class: 'num', text: pick(item, 'current') !== undefined
            ? `${fmt(pick(item, 'current'))} / ${fmt(pick(item, 'target', 'count'))}`
            : '' }),
        ])))
      : null,
    rewards.length
      ? el('div', { class: 'rewards' }, rewards.map((item) => el('span', {
          class: 'badge',
          text: typeof item === 'string' ? item : String(pick(item, 'label', 'name', 'item_id', 'tech_id') || '赏'),
        })))
      : null,
    el('div', { class: 'actionbar' }, [
      el('button', {
        class: 'btn btn--primary btn--sm',
        type: 'button',
        text: '交差',
        onclick: (event) => completeSectQuest(root, active, event.currentTarget),
      }),
    ]),
  ]);
}

function renderExchange(root) {
  const card = qs('#sect-exchange', root);
  if (!card) return;
  card.hidden = ui.role !== 'sect';
  const data = ui.data || {};
  const list = qs('#exchange-list', root);
  const items = arr(pick(data, 'exchange', 'exchanges', 'shop', 'exchange_items'));
  const meta = qs('#exchange-meta', root);

  if (meta) {
    const contribution = pick(data, 'contribution', 'sect.contribution', 'member.contribution');
    meta.textContent = contribution === undefined || contribution === null ? '贡献 —' : `可用贡献 ${fmt(contribution)}`;
  }
  if (!list) return;

  if (!items.length) {
    fill(list, el('li', { class: 'empty', text: '本宗暂无物可换。' }));
    return;
  }

  fill(list, items.map((item) => {
    const price = num(pick(item, 'price', 'contribution_price', 'cost', 'contribution'), null);
    const canExchange = pick(item, 'can_exchange', 'available', 'affordable');
    const reason = pick(item, 'reason', 'lock_reason');
    const type = pick(item, 'item_type', 'kind', 'type') || 'technique';
    const id = pick(item, 'item_id', 'tech_id', 'id');

    return el('li', { class: 'exchangeitem' }, [
      el('div', { class: 'exchangeitem__main' }, [
        el('span', { class: 'exchangeitem__name', text: String(pick(item, 'name', 'label', 'display_name') || id || '无名之物') }),
        el('span', {
          class: 'exchangeitem__meta',
          text: `${price === null ? '价 —' : `贡献 ${fmt(price)}`}${pick(item, 'tier_name') ? ` · ${pick(item, 'tier_name')}` : ''}`,
        }),
      ]),
      el('span', { class: 'techitem__side' }, [
        reason ? el('span', { class: 'badge badge--locked', text: String(reason) }) : null,
        el('button', {
          class: 'btn btn--ghost btn--sm',
          type: 'button',
          text: '兑换',
          disabled: canExchange === false,
          onclick: (event) => exchange(root, item, type, id, event.currentTarget),
        }),
      ]),
    ]);
  }));
}

/* ---------------- 写操作 ---------------- */

async function join(root, sect, button) {
  const id = pick(sect, 'id', 'sect_id', 'key');
  await withBusy(button, async () => {
    try {
      await api.sectJoin(id);
      toast(String(id) === 'sansxiu' ? '已择散修之路，再无退路' : '已拜入门下', 'info');
      await refreshAndSync();
      await load(root);
    } catch (err) {
      toastError(err);
      if (err.code === '4024') toast('身份不可回退：已入宗者不能改投散修', 'warn');
    }
  }, { label: '行礼…' });
}

async function setDirection(root, direction, button) {
  const id = pick(direction, 'id', 'direction_id');
  await withBusy(button, async () => {
    try {
      await api.sectDirection(id);
      toast('方向已定，方向树开启', 'info');
      await refreshAndSync();
      await load(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '定方向…' });
}

async function takeQuest(root, level, button) {
  await withBusy(button, async () => {
    try {
      await api.sectQuestTake(level);
      toast('已接下一桩宗门事务', 'info');
      await refreshAndSync();
      await load(root);
    } catch (err) {
      toastError(err);
      if (err.code === '4003') router.go('creation');
    }
  }, { label: '领命…' });
}

async function completeSectQuest(root, quest, button) {
  const id = pick(quest, 'quest_id', 'id');
  await withBusy(button, async () => {
    try {
      await api.sectQuestComplete(id);
      toast('交差已毕，贡献与赏赐同至', 'info');
      await refreshAndSync();
      await load(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '交差…' });
}

async function exchange(root, item, type, id, button) {
  await withBusy(button, async () => {
    try {
      await api.sectExchange(type, id);
      toast(`已换得《${pick(item, 'name', 'label', 'display_name') || id}》`, 'info');
      await refreshAndSync();
      await load(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '兑换…' });
}

async function encounter(root, button) {
  await withBusy(button, async () => {
    try {
      const result = await api.sansxiuEncounter();
      const tech = pick(result, 'technique_name', 'technique_id', 'reward.technique');
      toast(tech ? `奇遇所得：${tech}` : '一场奇遇，已入因果', 'info');
      await refreshAndSync();
      await load(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '远行…' });
}

async function convert(root, button) {
  await withBusy(button, async () => {
    try {
      const result = await api.sansxiuConvert();
      const after = pick(result, 'contribution', 'converted', 'sect_contribution');
      toast(after === undefined ? '贡献已折半转入宗门' : `贡献折半转入：${fmt(after)}`, 'info');
      await refreshAndSync();
      await load(root);
    } catch (err) {
      toastError(err);
    }
  }, { label: '转换…' });
}

async function load(root) {
  const list = qs('#sect-path-list', root);
  if (list) renderLoading(list, '正在叩问门径…');
  try {
    ui.data = await api.sect();
    ui.role = detectRole(ui.data);
    renderIdentity(root);
    renderPaths(root);
    renderDirection(root);
    renderSansxiu(root);
    renderSectQuests(root);
    renderExchange(root);
  } catch (err) {
    if (err.code === '4003') {
      router.go('creation');
      return;
    }
    toastError(err);
    if (list) renderError(list, err, () => load(root));
    const identity = qs('#sect-identity', root);
    if (identity) {
      const roleNode = qs('[data-id="role"]', identity);
      if (roleNode) roleNode.textContent = '未知';
    }
  }
}

export default {
  name: 'sect',

  async enter(root) {
    if (!store.hasCharacter) {
      router.go('creation');
      return;
    }
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      root.addEventListener('click', (event) => {
        const takeBtn = event.target.closest('[data-sq]');
        if (takeBtn && !takeBtn.disabled) takeQuest(root, takeBtn.dataset.sq, takeBtn);
        if (event.target.closest('#sansxiu-encounter')) encounter(root, event.target.closest('#sansxiu-encounter'));
        if (event.target.closest('#sansxiu-convert')) convert(root, event.target.closest('#sansxiu-convert'));
      });
    }
    await load(root);
  },
};
