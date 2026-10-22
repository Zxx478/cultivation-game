/**
 * core/app.js · 应用入口
 *
 * 启动顺序：应用 UI 偏好 → 注册 pane → 取 /state → 决定「创角 / 洞府」→ 起路由 → 同步浮层。
 * 刷新恢复：/state 里带 pending_event 或 combat.status=active 时，自动重开对应浮层。
 */

import { store, applyPrefs } from './state.js';
import { apiEvents } from './api.js';
import router from './router.js';
import {
  qs, qsa, el, fill, fmt, fmtYears, fmtPct, setBar, toast, toastError,
  openLayer, closeLayer, isLayerOpen,
} from './ui.js';

import creationPane from '../panes/creation.js';
import homePane from '../panes/home.js';
import mainPane from '../panes/main.js';
import characterPane from '../panes/character.js';
import cultivationPane from '../panes/cultivation.js';
import techniquesPane from '../panes/techniques.js';
import inventoryPane from '../panes/inventory.js';
import questsPane from '../panes/quests.js';
import mapPane from '../panes/map.js';
import sectPane from '../panes/sect.js';
import forbiddenPane from '../panes/forbidden.js';
import savesPane from '../panes/saves.js';
import logsPane from '../panes/logs.js';
import combatPane from '../panes/combat.js';
import eventPane from '../panes/event.js';
import breakthroughPane from '../panes/breakthrough.js';

const panes = {
  home: homePane,
  creation: creationPane,
  main: mainPane,
  character: characterPane,
  cultivation: cultivationPane,
  techniques: techniquesPane,
  inventory: inventoryPane,
  quests: questsPane,
  map: mapPane,
  sect: sectPane,
  forbidden: forbiddenPane,
  saves: savesPane,
  logs: logsPane,
  combat: combatPane,
  event: eventPane,
  breakthrough: breakthroughPane,
};

/* ============================================================
   外壳渲染
   ============================================================ */

function renderChrome() {
  const view = store.view;
  const route = router.current || '';
  const stage = route === 'home' ? 'home'
    : route === 'creation' ? 'creation'
      : (store.hasCharacter ? 'playing' : 'creation');
  document.body.dataset.stage = stage;

  const stats = {
    name: view.name,
    realm: view.realmName || '—',
    year: view.gameYear === null ? '—' : `第 ${fmt(view.gameYear)} 年`,
    life: view.remainingYears === null ? '—' : `余 ${fmtYears(view.remainingYears)}`,
    stone: fmt(view.spiritStone),
  };
  Object.keys(stats).forEach((key) => {
    const node = qs(`[data-stat="${key}"]`);
    if (node) node.textContent = stats[key];
  });

  const charMap = {
    name: view.name,
    realm: view.realmName || '—',
    hp: view.hpMax ? `${fmt(view.hp)} / ${fmt(view.hpMax)}` : '—',
    mp: view.mpMax ? `${fmt(view.mp)} / ${fmt(view.mpMax)}` : '—',
    life: view.remainingYears === null ? '—' : fmtYears(view.remainingYears),
    stone: fmt(view.spiritStone),
    region: view.regionName || '—',
  };
  Object.keys(charMap).forEach((key) => {
    const node = qs(`[data-cc="${key}"]`);
    if (node) node.textContent = charMap[key];
  });

  setBar(qs('.charcard [data-bar="exp"]'), view.expPct, view.expNeed
    ? `${fmt(view.cultivation)} / ${fmt(view.expNeed)}`
    : fmtPct(view.expPct));

  renderLogRail();
}

function renderLogRail() {
  const list = qs('#log-list');
  const counter = qs('#log-count');
  if (!list) return;
  const view = store.view;
  const entries = [];

  // logs 为「新→旧」，取前 40 条最新（不要用 slice(-40)，那是取最旧）
  view.logs.slice(0, 40).forEach((item) => {
    if (typeof item === 'string') entries.push({ kind: 'event', text: item });
    else entries.push({
      kind: String((item && (item.kind || item.type)) || 'event').toLowerCase(),
      text: (item && (item.text || item.message || item.title)) || '—',
      time: item && (item.created_at || item.time),
    });
  });
  view.milestones.slice(-10).forEach((item) => {
    if (typeof item === 'string') entries.push({ kind: 'milestone', text: item });
    else entries.push({
      kind: 'milestone',
      text: (item && (item.title || item.text)) || '里程碑',
      time: item && (item.created_at || item.time),
    });
  });

  const recent = entries.slice(-50);
  if (counter) counter.textContent = `${recent.length} 条`;
  if (!recent.length) {
    fill(list, el('li', { class: 'empty', text: '天机尚无记载。' }));
    return;
  }

  fill(list, recent.slice().reverse().map((entry) => el('li', { class: `logitem logitem--${entry.kind}` }, [
    el('span', { class: 'logitem__dot' }),
    el('p', { class: 'logitem__text', text: String(entry.text) }),
  ])));
}

/* ============================================================
   浮层同步
   ============================================================ */

function syncOverlays() {
  if (!store.hasCharacter) return;
  const view = store.view;

  if (view.pendingEvent && !isLayerOpen('layer-event')) {
    const root = qs('[data-pane="event"]');
    if (root) eventPane.open(view.pendingEvent);
    return;
  }
  if (!view.pendingEvent && isLayerOpen('layer-event')) {
    // 事件已在别处结算（如读档），收起旧弹窗
    closeLayer('layer-event');
  }

  if (view.combatActive && !isLayerOpen('layer-event') && !isLayerOpen('layer-combat')) {
    const root = qs('[data-pane="combat"]');
    if (root) combatPane.open(root);
  }
}

/* ============================================================
   外壳交互
   ============================================================ */

function toggleTheme(button) {
  const theme = store.toggleTheme();
  if (button) {
    button.textContent = theme === 'paper' ? '夜色' : '宣纸';
    button.setAttribute('aria-pressed', theme === 'paper' ? 'true' : 'false');
  }
  toast(theme === 'paper' ? '已换宣纸' : '已归夜色', 'info', { timeout: 1400 });
}

function toggleMotion(button) {
  const on = store.toggleMotion();
  if (button) {
    button.textContent = on ? '动效：开' : '动效：关';
    button.setAttribute('aria-pressed', on ? 'true' : 'false');
  }
}

function bindChrome() {
  const themeButton = qs('#theme-toggle');
  if (themeButton) {
    themeButton.textContent = store.prefs.theme === 'paper' ? '夜色' : '宣纸';
    themeButton.setAttribute('aria-pressed', store.prefs.theme === 'paper' ? 'true' : 'false');
    themeButton.addEventListener('click', () => toggleTheme(themeButton));
  }

  const motionButton = qs('#motion-toggle');
  if (motionButton) {
    motionButton.textContent = store.prefs.motion ? '动效：开' : '动效：关';
    motionButton.setAttribute('aria-pressed', store.prefs.motion ? 'true' : 'false');
    motionButton.addEventListener('click', () => toggleMotion(motionButton));
  }

  const rail = qs('#lograil');
  const railToggle = qs('#lograil-toggle');
  const railClose = qs('#lograil-close');
  const setRail = (open) => {
    if (rail) rail.classList.toggle('is-open', open);
    if (railToggle) railToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  };
  railToggle?.addEventListener('click', () => setRail(!(rail && rail.classList.contains('is-open'))));
  railClose?.addEventListener('click', () => setRail(false));

  qs('#tab-more')?.addEventListener('click', () => openLayer('layer-more', { scrim: true }));
  const sheet = qs('#layer-more');
  sheet?.addEventListener('click', (event) => {
    const closer = event.target.closest('[data-close]');
    if (closer) closeLayer(closer.dataset.close);
    else if (event.target === sheet) closeLayer('layer-more');
  });
  qs('#scrim')?.addEventListener('click', () => closeLayer('layer-more'));

  qs('#more-combat')?.addEventListener('click', () => {
    closeLayer('layer-more');
    openCombat();
  });

  document.querySelectorAll('[data-nav="combat"]').forEach((button) => {
    button.addEventListener('click', () => openCombat());
  });

  document.addEventListener('game:sync', syncOverlays);

  document.addEventListener('game:open-breakthrough', () => {
    const root = qs('[data-pane="breakthrough"]');
    if (root) breakthroughPane.open(root);
  });

  document.addEventListener('layer:close', (event) => {
    if (event.detail && event.detail.id === 'layer-more') {
      const scrim = qs('#scrim');
      if (scrim) scrim.hidden = true;
    }
  });

  apiEvents.onConflict = () => {
    setRail(false);
  };
}

function openCombat() {
  const root = qs('[data-pane="combat"]');
  if (!root) return;
  combatPane.enter(root);
  combatPane.open(root).catch((err) => toastError(err));
}

function bindKeyboard() {
  document.addEventListener('keydown', (event) => {
    const target = event.target;
    const tag = target && target.tagName ? target.tagName : '';
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (target && target.isContentEditable)) return;

    if (event.key === 'Escape') {
      if (isLayerOpen('layer-more')) {
        closeLayer('layer-more');
        return;
      }
      if (isLayerOpen('layer-event')) {
        closeLayer('layer-event');
        return;
      }
      if (isLayerOpen('layer-breakthrough')) {
        closeLayer('layer-breakthrough');
        return;
      }
      if (isLayerOpen('layer-combat')) {
        const resultBox = qs('#combat-result');
        if (resultBox && !resultBox.hidden) {
          closeLayer('layer-combat');
        } else {
          toast('战未止，不能退', 'warn', { timeout: 1800 });
        }
      }
      return;
    }

    if (eventPane.handleKey(event)) return;

    if (event.key === ' ' && isLayerOpen('layer-breakthrough')) {
      const result = qs('#bt-result');
      if (result && !result.hidden) {
        event.preventDefault();
        closeLayer('layer-breakthrough');
      }
    }
  });
}

/* ============================================================
   启动
   ============================================================ */

function showBootError(error) {
  const boot = qs('#boot');
  const tip = qs('#boot-tip');
  const box = qs('#boot-error');
  const text = qs('#boot-errtext');
  if (boot) boot.hidden = false;
  if (tip) tip.hidden = true;
  if (box) box.hidden = false;
  if (text) {
    text.textContent = `未能连接道基：${error && error.message ? error.message : '未知之错'}。请确认服务已启动，再重试。`;
  }
}

async function boot() {
  applyPrefs(store.prefs);
  router.register(panes);
  router.afterRender(() => {
    const rail = qs('#lograil');
    if (rail && window.innerWidth <= 1280) rail.classList.remove('is-open');
    renderChrome();
  });

  bindChrome();
  bindKeyboard();

  store.subscribe((view, reason) => {
    renderChrome();
    if (reason === 'no-character') {
      document.getElementById('app').hidden = false;
      qs('#boot').hidden = true;
      document.body.dataset.stage = 'home';
      router.go('home', { replace: true });
    }
  });

  const bootNode = qs('#boot');
  const retry = qs('#boot-retry');
  if (retry) {
    retry.addEventListener('click', async () => {
      if (bootNode) bootNode.hidden = false;
      const box = qs('#boot-error');
      if (box) box.hidden = true;
      const tip = qs('#boot-tip');
      if (tip) {
        tip.hidden = false;
        tip.textContent = '正在推演天机…';
      }
      try {
        await store.refresh({ force: true });
      } catch (err) {
        showBootError(err);
        return;
      }
      startShell();
    });
  }

  try {
    await store.refresh();
  } catch (err) {
    if (err.code !== '4003') {
      showBootError(err);
      console.error('[boot] 状态载入失败', err);
      return;
    }
  }
  startShell();
}

function startShell() {
  const app = qs('#app');
  if (app) app.hidden = false;
  const bootNode = qs('#boot');
  if (bootNode) bootNode.hidden = true;
  document.body.dataset.stage = 'home';
  renderChrome();
  router.start();
  window.setTimeout(syncOverlays, 200);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
