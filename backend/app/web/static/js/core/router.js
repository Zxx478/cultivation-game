/**
 * core/router.js · hash 路由 → pane
 *
 * 12 个主界面各自有 DOM 容器（[data-pane]）。战斗/事件/突破是浮层，
 * 不占路由（由状态驱动开关），因此不在此表内。
 */

import { store } from './state.js';

/** 可在主内容区内切换的界面（顺序即导航顺序；home 为首页，不占导航） */
export const ROUTES = [
  'home',
  'main',
  'character',
  'cultivation',
  'techniques',
  'inventory',
  'quests',
  'map',
  'sect',
  'forbidden',
  'saves',
  'logs',
];

const CREATION = 'creation';
const HOME = 'home';

let registry = {};
let current = null;
let token = 0;
let afterRender = null;

function readHash() {
  const raw = (window.location.hash || '').replace(/^#\/?/, '').trim();
  const name = raw.split('?')[0].split('&')[0];
  return name || '';
}

function isKnown(name) {
  return name === CREATION || ROUTES.includes(name);
}

export const router = {
  ROUTES,

  register(panes) {
    registry = panes || {};
  },

  /** 每次渲染完成后回调（用于更新 aria / 滚动位置等） */
  afterRender(fn) {
    afterRender = fn;
  },

  isRoute(name) {
    return ROUTES.includes(name);
  },

  get current() {
    return current;
  },

  start() {
    window.addEventListener('hashchange', () => {
      this.resolve();
    });
    this.resolve(true);
  },

  go(name, options = {}) {
    if (!isKnown(name)) {
      this.render(name, options);
      return;
    }
    const hash = `#/${name}`;
    if (window.location.hash === hash) {
      this.render(name, options);
      return;
    }
    if (options.replace) {
      window.location.replace(hash);
    } else {
      window.location.hash = hash;
    }
  },

  /** 重新进入当前 pane（写操作成功后刷新视图用） */
  reload() {
    if (current) this.render(current, { keepScroll: true });
  },

  async resolve(initial = false) {
    let name = readHash();
    if (!name || !isKnown(name)) {
      name = HOME;
    }
    // 无角色时，除「首页 / 创角」外一律回首页（由首页决定开始或读档）
    if (!store.hasCharacter && name !== HOME && name !== CREATION) name = HOME;
    // 有角色也允许显式进入创角（首页「重开一世」）
    await this.render(name, { initial });
  },

  async render(name, options = {}) {
    const target = isKnown(name) ? name : 'main';
    const pane = registry[target];
    const main = document.getElementById('main');
    if (!main) return;

    if (!pane) {
      console.error(`[router] 未注册的 pane: ${target}`);
      return;
    }

    const myToken = ++token;
    const previous = current;

    if (previous && previous !== target && registry[previous] && typeof registry[previous].leave === 'function') {
      try {
        registry[previous].leave();
      } catch (err) {
        console.error('[router] leave 失败', err);
      }
    }

    // 主内容区内只显示一个 pane；浮层（combat/event/breakthrough）不在 #main 内
    main.querySelectorAll(':scope > [data-pane]').forEach((node) => {
      node.hidden = node.dataset.pane !== target;
    });

    current = target;
    store.setLastPane(target);

    document.querySelectorAll('[data-nav]').forEach((item) => {
      const active = item.dataset.nav === target;
      item.classList.toggle('is-active', active);
      if (active) item.setAttribute('aria-current', 'page');
      else item.removeAttribute('aria-current');
    });
    document.querySelectorAll('[data-tab]').forEach((item) => {
      item.classList.toggle('is-active', item.dataset.tab === target);
    });

    const titles = {
      creation: '立身 · 荒天纪',
      main: '洞府 · 荒天纪',
      character: '角色 · 荒天纪',
      cultivation: '修炼 · 荒天纪',
      techniques: '功法 · 荒天纪',
      inventory: '背包 · 荒天纪',
      quests: '任务 · 荒天纪',
      map: '地图 · 荒天纪',
      sect: '宗门 · 荒天纪',
      forbidden: '禁地 · 荒天纪',
      saves: '存档 · 荒天纪',
      logs: '日志 · 荒天纪',
    };
    document.title = titles[target] || '荒天纪';

    const element = document.querySelector(`[data-pane="${target}"]`);
    if (element && typeof pane.enter === 'function') {
      try {
        await pane.enter(element, { name: target, initial: Boolean(options.initial) });
      } catch (err) {
        console.error('[router] enter 失败', err);
      }
    }
    if (myToken !== token) return;

    if (!options.keepScroll) {
      window.scrollTo({ top: 0, behavior: 'auto' });
    }
    if (typeof afterRender === 'function') afterRender(target, element);
  },
};

export default router;
