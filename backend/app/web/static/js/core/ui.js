/**
 * core/ui.js · 安全渲染与通用组件
 *
 * 硬约束：全部节点用 createElement/textContent 构建，本模块不提供 innerHTML 通道。
 * 用户输入（道号等）只能经 textContent / node.value 写入。
 */

import { ApiError } from './api.js';
import { store } from './state.js';

/* ============================================================
   1. DOM 构建
   ============================================================ */

/**
 * 创建元素。
 * @param {string} tag
 * @param {object|null} attrs 支持 class / text / dataset / style(对象) / on* / aria-*
 * @param {Array<Node|string|null>|Node|string} children
 */
export function el(tag, attrs = null, children = null) {
  const node = document.createElement(tag);
  if (attrs) {
    Object.keys(attrs).forEach((key) => {
      const value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = String(value);
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
      else if (key === 'value') node.value = value;
      else if (key === 'checked') node.checked = Boolean(value);
      else if (key === 'disabled') node.disabled = Boolean(value);
      else if (key === 'hidden') node.hidden = Boolean(value);
      else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else node.setAttribute(key, String(value));
    });
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  if (children === null || children === undefined) return parent;
  if (Array.isArray(children)) {
    children.forEach((child) => append(parent, child));
    return parent;
  }
  if (children instanceof Node) parent.appendChild(children);
  else parent.appendChild(document.createTextNode(String(children)));
  return parent;
}

export function text(value) {
  return document.createTextNode(value === null || value === undefined ? '' : String(value));
}

export function clear(node) {
  if (!node) return node;
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function qs(selector, root = document) {
  return root ? root.querySelector(selector) : null;
}

export function qsa(selector, root = document) {
  return root ? Array.from(root.querySelectorAll(selector)) : [];
}

export function fill(container, children) {
  clear(container);
  append(container, children);
  return container;
}

/* ============================================================
   2. 格式化（只做展示，不做数值推算）
   ============================================================ */

export function fmt(value, fallback = '—') {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return parsed.toLocaleString('zh-CN');
}

export function fmtPct(ratio, digits = 1, fallback = '—') {
  if (ratio === null || ratio === undefined || !Number.isFinite(Number(ratio))) return fallback;
  const value = Number(ratio) * 100;
  return `${value.toFixed(digits)}%`;
}

export function fmtYears(years, fallback = '—') {
  if (years === null || years === undefined || !Number.isFinite(Number(years))) return fallback;
  const value = Number(years);
  return `${value >= 10 ? value.toFixed(1) : value.toFixed(2)} 年`;
}

export function fmtTime(stamp) {
  if (stamp === null || stamp === undefined || stamp === '') return '';
  let date;
  if (typeof stamp === 'number') date = new Date(stamp < 1e12 ? stamp * 1000 : stamp);
  else date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fmtDelta(value, suffix = '') {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const number = Number(value);
  const sign = number > 0 ? '+' : '';
  return `${sign}${fmt(number)}${suffix}`;
}

/* ============================================================
   3. 状态条 / 数字动画
   ============================================================ */

const BAR_DURATION = 400;

/**
 * 设置状态条。
 * @param {HTMLElement} root 含 [data-fill] / [data-value] 的 .bar
 * @param {number|null} ratio 0–1
 * @param {string} valueText 右侧文字
 * @param {{warn?:boolean, flash?:boolean}} options
 */
export function setBar(root, ratio, valueText, options = {}) {
  if (!root) return;
  const fillNode = qs('[data-fill]', root);
  const valueNode = qs('[data-value]', root);
  const safe = ratio === null || ratio === undefined ? 0 : Math.max(0, Math.min(1, Number(ratio)));
  if (fillNode) {
    const target = `${(safe * 100).toFixed(2)}%`;
    if (store.prefs.motion === false) fillNode.style.transition = 'none';
    else fillNode.style.transition = '';
    fillNode.style.width = target;
  }
  if (valueNode) valueNode.textContent = valueText === undefined || valueText === null ? '—' : String(valueText);
  root.classList.toggle('is-warn', Boolean(options.warn) && safe < 0.2);
  if (options.flash) {
    root.classList.remove('is-flash');
    // 强制重排以便重复播放
    void root.offsetWidth;
    root.classList.add('is-flash');
  }
}

/** 数字滚动（prefers-reduced-motion / data-motion=off 时直接赋值） */
export function animateNumber(node, from, to, duration = 600) {
  if (!node) return;
  const start = Number(from);
  const end = Number(to);
  if (!Number.isFinite(end)) {
    node.textContent = '—';
    return;
  }
  if (store.prefs.motion === false || !Number.isFinite(start) || Math.abs(end - start) < 1e-6) {
    node.textContent = fmt(end);
    return;
  }
  const began = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - began) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    node.textContent = fmt(start + (end - start) * eased);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ============================================================
   4. 内容占位（加载 / 空 / 错误）
   ============================================================ */

export function renderLoading(container, label = '正在推演…') {
  return fill(container, el('p', { class: 'empty', text: label }));
}

export function renderEmpty(container, label) {
  return fill(container, el('p', { class: 'empty', text: label }));
}

/**
 * 错误态：必须给出可执行原因，并带「重试」。
 */
export function renderError(container, error, onRetry) {
  const message = error && error.message ? error.message : '道基有误，且待重试';
  const code = error && error.code ? `（${error.code}）` : '';
  return fill(container, [
    el('div', { class: 'empty' }, [
      el('p', { text: `${message}${code}` }),
      onRetry
        ? el('button', { class: 'btn btn--ghost btn--sm', type: 'button', onclick: onRetry, text: '重试' })
        : null,
    ]),
  ]);
}

/* ============================================================
   5. Toast
   ============================================================ */

const TOAST_LIMIT = 4;

/**
 * @param {string} message 文案（后端已含缺口数值，如「灵石不足（需 5,000，现有 1,200）」）
 * @param {'info'|'error'|'warn'} type
 * @param {{action?:{label:string, run:Function}, timeout?:number}} options
 */
export function toast(message, type = 'info', options = {}) {
  const root = document.getElementById('toasts');
  if (!root) return null;

  const close = () => {
    if (node.parentNode) node.parentNode.removeChild(node);
  };

  const node = el('div', { class: `toast${type === 'error' ? ' toast--error' : type === 'warn' ? ' toast--warn' : ''}` }, [
    el('p', { class: 'toast__msg', text: message || '天机无言' }),
    options.action
      ? el('button', {
          class: 'toast__action',
          type: 'button',
          text: options.action.label,
          onclick: () => {
            close();
            try {
              options.action.run();
            } catch (err) {
              console.error(err);
            }
          },
        })
      : null,
  ]);

  root.appendChild(node);
  while (root.children.length > TOAST_LIMIT) root.removeChild(root.firstElementChild);

  const timeout = options.timeout === undefined ? 3200 : options.timeout;
  if (timeout > 0) window.setTimeout(close, timeout);
  return node;
}

/**
 * 统一错误提示：409 版本冲突额外提供「重新载入」。
 */
export function toastError(error, options = {}) {
  const err = error instanceof ApiError ? error : new ApiError('unknown', (error && error.message) || '未知之错');
  if (options.silent) return;

  if (err.code === '4003') {
    toast('尚未有角色，先去立身', 'warn', { action: { label: '去创角', run: () => store.markNoCharacter() } });
    return;
  }
  if (err.code === '4004') {
    toast(`存档已在别处变更：${err.message}`, 'error', {
      timeout: 6000,
      action: {
        label: '重新载入',
        run: async () => {
          try {
            await store.refresh({ force: true });
            toast('已载入最新状态', 'info');
          } catch (refreshError) {
            toastError(refreshError);
          }
        },
      },
    });
    return;
  }
  if (err.code === '4010') {
    toast('眼前之事已了，刷新一下天机', 'warn', {
      action: {
        label: '刷新',
        run: () => store.refresh({ force: true }).catch(toastError),
      },
    });
    return;
  }
  toast(err.message, 'error');
}

/* ============================================================
   6. 写操作防双击
   ============================================================ */

/**
 * 让按钮进入「结算中…」，完成后恢复。
 * @param {HTMLButtonElement} button
 * @param {() => Promise<*>} task
 * @param {{label?:string, group?:HTMLElement}} options
 */
export async function withBusy(button, task, options = {}) {
  const label = options.label || '结算中…';
  const siblings = options.group
    ? qsa('button, a.btn', options.group)
    : button
      ? [button]
      : [];

  const snapshot = siblings.map((node) => ({
    node,
    text: node.dataset.labelSnapshot === undefined ? node.textContent : node.dataset.labelSnapshot,
    disabled: node.disabled,
    ariaDisabled: node.getAttribute('aria-disabled'),
  }));

  siblings.forEach((node) => {
    if (!node.dataset.labelSnapshot) node.dataset.labelSnapshot = node.textContent;
    node.disabled = true;
    node.setAttribute('aria-disabled', 'true');
  });
  if (button) {
    button.classList.add('is-busy');
    button.textContent = label;
  }

  try {
    return await task();
  } finally {
    snapshot.forEach((item) => {
      item.node.disabled = item.disabled;
      if (item.ariaDisabled === null) item.node.removeAttribute('aria-disabled');
      else item.node.setAttribute('aria-disabled', item.ariaDisabled);
      item.node.textContent = item.text;
      delete item.node.dataset.labelSnapshot;
    });
    if (button) button.classList.remove('is-busy');
  }
}

/* ============================================================
   7. 浮层（弹窗 / 战斗）
   ============================================================ */

const layerStack = [];

export function openLayer(id, options = {}) {
  const node = document.getElementById(id);
  if (!node) return null;
  node.hidden = false;
  if (!layerStack.includes(id)) layerStack.push(id);
  document.body.style.overflow = 'hidden';
  if (options.scrim) {
    const scrim = document.getElementById('scrim');
    if (scrim) scrim.hidden = false;
  }
  const focusTarget = qs('[autofocus]', node) || qs('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])', node);
  if (focusTarget && typeof focusTarget.focus === 'function') {
    window.setTimeout(() => focusTarget.focus({ preventScroll: true }), 30);
  }
  document.dispatchEvent(new CustomEvent('layer:open', { detail: { id } }));
  return node;
}

export function closeLayer(id) {
  const node = document.getElementById(id);
  if (node) node.hidden = true;
  const index = layerStack.indexOf(id);
  if (index >= 0) layerStack.splice(index, 1);
  if (!layerStack.length) {
    document.body.style.overflow = '';
    const scrim = document.getElementById('scrim');
    if (scrim) scrim.hidden = true;
  }
  document.dispatchEvent(new CustomEvent('layer:close', { detail: { id } }));
}

export function isLayerOpen(id) {
  const node = document.getElementById(id);
  return Boolean(node && !node.hidden);
}

export function topLayer() {
  return layerStack.length ? layerStack[layerStack.length - 1] : null;
}

/** Esc：关掉最上层「可关」浮层；返回是否消费了事件。 */
export function closeTopLayer(options = {}) {
  const id = topLayer();
  if (!id) return false;
  if (options.skip && options.skip.includes(id)) return false;
  closeLayer(id);
  return true;
}

/* ============================================================
   8. 战斗飘字
   ============================================================ */

/**
 * @param {HTMLElement} layer 绝对定位容器（.float-layer）
 * @param {{label:string, kind?:'crit'|'dodge'|'heal'|'normal', side?:'player'|'enemy'}} options
 */
export function floatDamage(layer, options = {}) {
  if (!layer) return null;
  const kind = options.kind || 'normal';
  const node = el('span', {
    class: `float${kind === 'crit' ? ' float--crit' : kind === 'dodge' ? ' float--dodge' : kind === 'heal' ? ' float--heal' : ''}`,
    text: options.label || '',
  });
  const leftRatio = options.side === 'player' ? 0.22 : 0.78;
  const jitter = (Math.random() - 0.5) * 0.1;
  node.style.left = `${(leftRatio + jitter) * 100}%`;
  node.style.top = `${34 + Math.random() * 18}%`;
  layer.appendChild(node);
  const life = store.prefs.motion === false ? 600 : 1500;
  window.setTimeout(() => {
    if (node.parentNode) node.parentNode.removeChild(node);
  }, life);
  return node;
}

/* ============================================================
   9. 小工具
   ============================================================ */

/** 风险标签：只展示性质，不展示成功率（设计 §3.5） */
export function riskTag(choice) {
  const raw = String(choice.risk || choice.risk_label || '').toLowerCase();
  const map = {
    danger: ['危险', 'risk'],
    hard: ['硬闯', 'risk'],
    cost: ['消耗', 'risk risk--cost'],
    resource: ['消耗', 'risk risk--cost'],
    karma: ['因果', 'risk risk--karma'],
    trap: ['陷阱', 'risk'],
    check: ['需判定', 'risk risk--cost'],
  };
  const found = map[raw];
  if (found) return el('span', { class: found[1], text: found[0] });
  if (choice.risk_label) return el('span', { class: 'risk', text: String(choice.risk_label) });
  return null;
}

export function statusBadge(status) {
  const map = {
    locked: ['未解锁', 'badge badge--locked'],
    active: ['进行中', 'badge badge--active'],
    ready: ['可领取', 'badge badge--ready'],
    completed: ['已完成', 'badge badge--completed'],
    normal: ['无恙', 'badge badge--active'],
    injured: ['负伤', 'badge badge--ready'],
    qi_deviation: ['走火入魔', 'badge badge--danger'],
    dead: ['已坐化', 'badge badge--danger'],
    deceased: ['已坐化', 'badge badge--danger'],
  };
  const entry = map[status] || [status || '—', 'badge'];
  return el('span', { class: entry[1], text: entry[0] });
}

export function debounce(fn, wait = 220) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), wait);
  };
}

/** 让节点短暂抖动（失败反馈）；动效关闭时不抖 */
export function shake(node) {
  if (!node || store.prefs.motion === false) return;
  node.classList.remove('is-shake');
  void node.offsetWidth;
  node.classList.add('is-shake');
  window.setTimeout(() => node.classList.remove('is-shake'), 320);
}
