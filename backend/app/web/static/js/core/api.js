/**
 * core/api.js · 唯一网络出口
 *
 * 约定（设计 §5）：所有接口前缀 /api/v1，统一响应 {ok, data, error:{code,message,detail}}。
 * 前端不计算数值，只把 data 交给 pane 渲染。
 * 失败一律抛 ApiError，由调用方 toast 或渲染错误态。
 */

const BASE = '/api/v1';

/** 全局钩子：无角色（4003）时由 app.js 注册，用于切回创角。 */
export const apiEvents = {
  onNoCharacter: null,
  onConflict: null,
};

export class ApiError extends Error {
  /**
   * @param {string} code 业务码（4001–4025 / 5000 / NETWORK / BAD_JSON / HTTP 码）
   * @param {string} message 可直接展示的中文文案（后端已含缺口数值）
   * @param {*} detail 附加信息
   * @param {number} http HTTP 状态
   */
  constructor(code, message, detail = null, http = 0) {
    super(message);
    this.name = 'ApiError';
    this.code = String(code == null ? 'unknown' : code);
    this.detail = detail;
    this.http = http;
  }

  /** 版本冲突（存档已在别处变更） */
  get isVersionConflict() {
    return this.code === '4004';
  }
}

function buildQuery(query) {
  const usp = new URLSearchParams();
  Object.keys(query || {}).forEach((key) => {
    const value = query[key];
    if (value === undefined || value === null || value === '') return;
    usp.set(key, String(value));
  });
  const text = usp.toString();
  return text ? `?${text}` : '';
}

/**
 * 底层请求。
 * @param {string} path 形如 '/state'（自动拼 /api/v1）
 * @param {{method?:string, body?:*, query?:object, raw?:boolean}} options
 */
export async function request(path, options = {}) {
  const { method = 'GET', body, query, raw = false } = options;
  const url = `${BASE}${path}${buildQuery(query)}`;
  const headers = { Accept: 'application/json' };
  let payloadBody;
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payloadBody = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: payloadBody,
      credentials: 'same-origin',
      cache: 'no-store',
    });
  } catch (err) {
    throw new ApiError('NETWORK', '与道基失联，请检查服务是否在运行', null, 0);
  }

  const text = await response.text().catch(() => '');
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (err) {
      payload = null;
    }
  }

  if (payload === null) {
    if (response.status === 404) {
      throw new ApiError('404', '此路未通（接口尚未就绪）', null, 404);
    }
    if (!response.ok) {
      throw new ApiError(`HTTP${response.status}`, `道基震荡（HTTP ${response.status}）`, null, response.status);
    }
    throw new ApiError('BAD_JSON', '天机错乱：响应不是可解析的 JSON', null, response.status);
  }

  const failed = payload.ok === false || (payload.ok === undefined && payload.error);
  if (failed) {
    const info = payload.error || {};
    const error = new ApiError(
      info.code == null ? response.status : info.code,
      info.message || '道基有误，且看明细',
      info.detail == null ? null : info.detail,
      response.status,
    );
    if (error.code === '4003' && typeof apiEvents.onNoCharacter === 'function') {
      apiEvents.onNoCharacter();
    }
    if (error.code === '4004' && typeof apiEvents.onConflict === 'function') {
      apiEvents.onConflict(error);
    }
    throw error;
  }

  if (!response.ok) {
    throw new ApiError(String(response.status), `道基震荡（HTTP ${response.status}）`, null, response.status);
  }

  if (raw) return payload;
  return payload.data === undefined ? payload : payload.data;
}

/* ============================================================
   接口包装 · 严格对应设计 §5 清单
   ============================================================ */
export const api = {
  /* 首屏 / 创角 */
  state: () => request('/state'),
  meta: () => request('/meta'),
  reroll: () => request('/bootstrap/reroll', { method: 'POST', body: {} }),
  candidate: () => request('/bootstrap/candidate'),
  bootstrap: (payload) => request('/bootstrap', { method: 'POST', body: payload }),

  /* 行动 / 突破 / 事件 */
  action: (payload) => request('/action', { method: 'POST', body: payload }),
  breakthroughPreview: (prep) => request('/breakthrough/preview', { method: 'POST', body: { prep: prep || {} } }),
  breakthrough: (payload) => request('/breakthrough', { method: 'POST', body: payload }),
  eventChoose: (instanceId, optionId) =>
    request('/event/choose', { method: 'POST', body: { instance_id: instanceId, option_id: optionId } }),

  /* 战斗 */
  combat: () => request('/combat'),
  combatAction: (payload) => request('/combat/action', { method: 'POST', body: payload }),

  /* 角色 / 功法 / 背包 */
  character: () => request('/character'),
  techniques: () => request('/techniques'),
  techniqueLearn: (techId) => request('/techniques/learn', { method: 'POST', body: { tech_id: techId } }),
  techniqueMeditate: (techId) => request('/techniques/meditate', { method: 'POST', body: { tech_id: techId } }),
  techniqueEquip: (techId, slot) => request('/techniques/equip', { method: 'POST', body: { tech_id: techId, slot } }),
  techniqueUnequip: (techId, slot) =>
    request('/techniques/unequip', { method: 'POST', body: { tech_id: techId, slot } }),

  inventory: (tab) => request('/inventory', { query: tab && tab !== 'all' ? { tab } : undefined }),
  itemUse: (itemId, qty = 1) => request('/inventory/use', { method: 'POST', body: { item_id: itemId, qty } }),
  itemSell: (itemId, qty = 1) => request('/inventory/sell', { method: 'POST', body: { item_id: itemId, qty } }),
  itemEquip: (instanceId) => request('/inventory/equip', { method: 'POST', body: { instance_id: instanceId } }),
  itemUnequip: (instanceId) => request('/inventory/unequip', { method: 'POST', body: { instance_id: instanceId } }),
  itemEnhance: (instanceId) => request('/inventory/enhance', { method: 'POST', body: { instance_id: instanceId } }),

  /* 任务 / 地图 */
  quests: () => request('/quests'),
  claimQuest: (questId) => request('/quests/claim', { method: 'POST', body: { quest_id: questId } }),
  map: () => request('/map'),
  travel: (regionId) => request('/map/travel', { method: 'POST', body: { region_id: regionId } }),

  /* 宗门 / 散修 */
  sect: () => request('/sect'),
  sectJoin: (sectId) => request('/sect/join', { method: 'POST', body: { sect_id: sectId } }),
  sectDirection: (directionId) => request('/sect/direction', { method: 'POST', body: { direction_id: directionId } }),
  sectQuestTake: (level) => request('/sect/quest/take', { method: 'POST', body: { level } }),
  sectQuestComplete: (questId) => request('/sect/quest/complete', { method: 'POST', body: { quest_id: questId } }),
  sectExchange: (itemType, itemId) =>
    request('/sect/exchange', { method: 'POST', body: { item_type: itemType, item_id: itemId } }),
  sansxiuEncounter: () => request('/sect/sansxiu/encounter', { method: 'POST', body: {} }),
  sansxiuConvert: () => request('/sect/sansxiu/convert', { method: 'POST', body: {} }),

  /* 禁地 */
  forbidden: () => request('/forbidden'),
  forbiddenEnter: (landId) => request('/forbidden/enter', { method: 'POST', body: { land_id: landId } }),

  /* 存档 */
  saves: () => request('/saves'),
  saveSlot: (slotId, expectedVersion) =>
    request('/saves/save', { method: 'POST', body: { slot_id: slotId, expected_version: expectedVersion } }),
  loadSlot: (slotId) => request('/saves/load', { method: 'POST', body: { slot_id: slotId } }),
  exportSlot: (slotId) => request('/saves/export', { method: 'POST', body: { slot_id: slotId } }),
  importSave: (payload) => request('/saves/import', { method: 'POST', body: { payload } }),
  deleteSlot: (slotId) => request('/saves/delete', { method: 'POST', body: { slot_id: slotId } }),

  /* 日志 */
  logs: () => request('/logs'),
};

export default api;
