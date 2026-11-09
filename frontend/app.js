/* 遥感船舶检测与人机协同复核 — 前端
 *
 * 数据全部经由 window.API（api/client.js）访问，UI 不直接读 SCENE。
 * 后端就绪后把 config.js 的 apiMode 改成 'http' 即可对接，界面代码无需改动。
 *
 * 坐标契约与 algorithm/ship_detector.py 一致：
 *   polygon = 4×2 像素坐标，原点左上，顺时针，起点为最小 (y, x) 顶点
 */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const round2 = v => Math.round(v * 100) / 100;
const clone = o => JSON.parse(JSON.stringify(o));

const G = window.GEOM;
const CFG = window.APP_CONFIG;
const SCENE = window.SCENE;          // 仅用于影像尺寸等静态元信息与演示提示
const TILE_VIEW = window.TileView;   // A 方大图复核核心：后端显示瓦片层

const IMG_W = SCENE.image.width;
const IMG_H = SCENE.image.height;
const TILES = SCENE.tiling.tiles;
const COLORS = {
  pending: '#8fe9ff', confirmed: '#5ff3e4', modified: '#2f5bff',
  deleted: '#d92d50', ai: '#d8ff4a', region: '#2f5bff'
};
const STATE_LABEL = { pending: '未检查', confirmed: '已确认', modified: '已修改', deleted: '已删除' };

/* --------------------------------------------------------------------- state */

const state = {
  user: null,
  project: null,
  view: 'project',

  image: null,
  task: null,
  images: [],            // 项目影像列表（服务端），刷新后据此恢复
  tasks: [],             // 项目任务列表（服务端）
  result: null,          // ship-detection/0.2 结果（只读）
  versions: [],          // 版本列表（不含 detections）
  draft: [],             // 服务端草稿
  serverDraft: [],       // 最近一次已知的服务端状态，用于增量同步
  baseline: [],          // v0 模型原始结果
  viewingVersion: null,  // 历史版本（含 detections，只读）

  review: { items: [], sampling: [] },
  region: [],
  regionClosed: false,
  stats: null,

  selection: null,
  listFilter: 'all',
  tool: 'select',
  statTool: 'region',
  showTiles: false,
  showLabels: true,

  upload: { status: 'idle', pct: 0, received: 0, total: 24, stage: '', name: '', id: null, error: null, file: null },
  ai: { status: 'idle' },
  exportJob: null,

  undo: [],
  redo: [],
  dirty: false,
  busy: false
};

const activeDetections = () => state.viewingVersion ? state.viewingVersion.detections : state.draft;
const selected = () => activeDetections().find(d => d.id === state.selection) || null;

/** 切片布局与检测参数优先取服务端结果，未创建任务时回退到演示数据 */
const tilesNow = () => (state.result && state.result.tiling ? state.result.tiling.tiles : TILES);
const paramsNow = () => (state.result && state.result.parameters ? state.result.parameters : SCENE.parameters);

function counts(list) {
  const c = { total: list.length, pending: 0, confirmed: 0, modified: 0, deleted: 0, added: 0 };
  for (const d of list) {
    c[d.review_state] = (c[d.review_state] || 0) + 1;
    if (d.origin !== 'model') c.added += 1;
  }
  return c;
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .3s'; }, 2600);
  setTimeout(() => el.remove(), 3000);
}

function toastError(err) {
  const code = err && err.code ? err.code : 'ERROR';
  const msg = err && err.message ? err.message : String(err);
  toast(`${code}：${msg}`, 'err');
}

/** 统一的异步动作包装：捕获 ApiError，避免到处写 try/catch */
async function action(fn) {
  try {
    return await fn();
  } catch (err) {
    if (err && err.status === 401) { doLogout(true); return; }
    toastError(err);
    if (state.view === 'workbench' && managed()) updateDirty();
    return null;
  }
}

/* --------------------------------------------------------------------- image */

const IMG = new Image();
/* mock 后端生成标注图时需要访问影像位图；http 模式下由服务端渲染，无需此钩子 */
CFG.getImage = () => IMG;
IMG.onload = () => { if (state.user) redraw(); };
IMG.src = 'assets/harbor.png';

/* ------------------------------------------------------------------ viewport */

class Viewport {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.mode = opts.mode || 'edit';
    this.scale = 0.3; this.ox = 0; this.oy = 0;
    // 每个视口有自己的原图尺寸：工作台跟随真实影像，统计页仍用演示场景
    this.worldW = IMG_W; this.worldH = IMG_H;
    this.tileLayer = null;
    this.drag = null; this.needFit = true;
    this.onDraw = opts.onDraw || (() => {});
    this.bind();
  }
  resize() {
    const r = this.canvas.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    this.w = r.width; this.h = r.height;
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(r.width * dpr);
    this.canvas.height = Math.round(r.height * dpr);
    this.dpr = dpr;
    return true;
  }
  fit() {
    if (!this.w) return;
    this.scale = Math.min(this.w / this.worldW, this.h / this.worldH) * 0.96;
    this.ox = (this.w - this.worldW * this.scale) / 2;
    this.oy = (this.h - this.worldH * this.scale) / 2;
    this.needFit = false;
  }
  zoomAt(sx, sy, factor) {
    const ns = clamp(this.scale * factor, 0.05, 12);
    const k = ns / this.scale;
    this.ox = sx - (sx - this.ox) * k;
    this.oy = sy - (sy - this.oy) * k;
    this.scale = ns;
  }
  w2s(x, y) { return [x * this.scale + this.ox, y * this.scale + this.oy]; }
  s2w(x, y) { return [(x - this.ox) / this.scale, (y - this.oy) / this.scale]; }
  local(ev) {
    const r = this.canvas.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  }
  bind() {
    const c = this.canvas;
    c.addEventListener('wheel', ev => {
      ev.preventDefault();
      const [x, y] = this.local(ev);
      this.zoomAt(x, y, Math.exp(-ev.deltaY * 0.0016));
      this.draw();
      if (this.onZoom) this.onZoom(this.scale);
    }, { passive: false });

    c.addEventListener('pointerdown', ev => {
      try { c.setPointerCapture(ev.pointerId); } catch (_) { /* synthetic */ }
      const [sx, sy] = this.local(ev);
      this.drag = { sx, sy, world: this.s2w(sx, sy), moved: false, button: ev.button };
      this.onPointerDown && this.onPointerDown(this.drag.world, [sx, sy], ev);
    });

    c.addEventListener('pointermove', ev => {
      const [sx, sy] = this.local(ev);
      const world = this.s2w(sx, sy);
      if (!this.drag) return;
      if (Math.hypot(sx - this.drag.sx, sy - this.drag.sy) > 3) this.drag.moved = true;
      if (this.drag.pan) {
        this.ox += sx - this.drag.sx;
        this.oy += sy - this.drag.sy;
        this.drag.sx = sx; this.drag.sy = sy;
        this.draw();
        return;
      }
      this.onPointerMove && this.onPointerMove(world, [sx, sy], ev);
    });

    const end = ev => {
      const [sx, sy] = this.local(ev);
      const world = this.s2w(sx, sy);
      const drag = this.drag;
      this.drag = null;
      if (!drag || drag.pan) return;
      this.onPointerUp && this.onPointerUp(world, [sx, sy], drag);
    };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', () => { this.drag = null; });
    c.addEventListener('contextmenu', ev => ev.preventDefault());
  }
  draw() {
    if (!this.w) return;
    const { ctx } = this;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    if (this.tileLayer) {
      // 后端显示瓦片：预览垫底，视口内按层取块，坐标仍是原图像素
      this.tileLayer.draw(ctx, this);
    } else if (IMG.complete && IMG.naturalWidth) {
      ctx.imageSmoothingEnabled = this.scale < 1;
      ctx.drawImage(IMG, this.ox, this.oy, this.worldW * this.scale, this.worldH * this.scale);
      // 被检测影像与海面背景之间留一条细边，明确“影像”和“四周”的边界
      ctx.strokeStyle = 'rgba(95, 243, 228, .42)';
      ctx.lineWidth = 1;
      ctx.strokeRect(this.ox - .5, this.oy - .5, this.worldW * this.scale + 1, this.worldH * this.scale + 1);
    }
    this.onDraw(ctx, this);
  }
}

function redraw() {
  if (WB.vp && state.view === 'workbench') WB.vp.draw();
  if (ST.vp && state.view === 'stats') ST.vp.draw();
}

/* --------------------------------------------------------------- tile layer */

/** 后端私有影像要带会话令牌，不能直接用 <img src> 取。 */
async function authImage(url) {
  const target = TILE_VIEW.authenticatedImageUrl(url, API.baseUrl, location.href);
  const headers = {};
  const token = window.AuthSession ? AuthSession.token() : (localStorage.getItem('dsh.ship.token') || '');
  if (token) headers.Authorization = 'Bearer ' + token;
  return fetch(target, { headers, credentials: 'same-origin', redirect: 'error' })
    .then(res => {
      if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + url);
      return res.blob();
    })
    .then(blob => new Promise((resolve, reject) => {
      const objectUrl = URL.createObjectURL(blob);
      const image = new Image();
      image.decoding = 'async';
      image.onload = () => { URL.revokeObjectURL(objectUrl); resolve(image); };
      image.onerror = () => { URL.revokeObjectURL(objectUrl); reject(new Error('影像解码失败')); };
      image.src = objectUrl;
    }));
}

/** manifest 给的是 /tiles/...，业务 API 挂在 /api/v1 下。 */
function resolveTileTemplate(template) {
  if (!template) return '';
  if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(template)) return template;
  const base = (CFG.baseUrl || '').replace(/\/$/, '');
  if (template.indexOf('/api/') === 0) return base + template;
  return (API.baseUrl || base + (CFG.apiPrefix || '/api/v1')) + template;
}

function scheduleWorkbenchDraw() {
  if (WB.tileFrame) return;
  WB.tileFrame = requestAnimationFrame(() => {
    WB.tileFrame = 0;
    if (state.view === 'workbench' && WB.vp) WB.vp.draw();
  });
}

/** 任务对应的影像摘要：state.image 是首选，取详情失败时退回项目影像列表。 */
function workbenchImageInfo() {
  const wanted = state.task && state.task.image_id;
  if (wanted) {
    if (state.image && state.image.id === wanted) return state.image;
    const fromList = state.images.find(item => item.id === wanted);
    if (fromList) return fromList;
  }
  return state.image;
}

/**
 * 工作台换图：把原图尺寸交给视口，并为真实后端挂上瓦片层。
 * 瓦片/预览都是异步的，先用预览垫底；失败时保留预览，不把演示影像当真实影像。
 */
function prepareWorkbenchImage() {
  const info = workbenchImageInfo();
  if (!info || !WB.vp) return;
  const width = Number(info.width);
  const height = Number(info.height);
  const vp = WB.vp;
  if (width > 0 && height > 0 && (vp.worldW !== width || vp.worldH !== height)) {
    vp.worldW = width;
    vp.worldH = height;
    vp.needFit = true;
  }
  if (API.mode !== 'http' || !TILE_VIEW || !info.id) return;

  const imageKey = info.id + '@' + (info.sha256 || '');
  if (WB.imageKey === imageKey) return;
  vp.needFit = true;
  WB.imageKey = imageKey;
  const token = ++WB.imageToken;

  if (!WB.tileLayer) {
    WB.tileLayer = new TILE_VIEW.TileLayer({
      requestLimit: 16,
      loadTile: ({ url }) => authImage(url),
      onUpdate: scheduleWorkbenchDraw
    });
  }
  vp.tileLayer = WB.tileLayer;
  WB.tileLayer.reset();

  authImage(API.baseUrl + '/previews/' + encodeURIComponent(info.id))
    .then(image => {
      if (token !== WB.imageToken) return;
      WB.tileLayer.setPreview(image);
      scheduleWorkbenchDraw();
    })
    .catch(error => {
      if (token !== WB.imageToken) return;
      console.warn('工作台预览加载失败', error);
      scheduleWorkbenchDraw();
    });

  API.getTileManifest(info.id)
    .then(manifest => {
      if (token !== WB.imageToken || !manifest) return;
      WB.tileLayer.configure(manifest, { urlTemplate: resolveTileTemplate(manifest.url_template) });
      scheduleWorkbenchDraw();
    })
    .catch(error => {
      if (token !== WB.imageToken) return;
      console.warn('工作台瓦片清单加载失败', error);
      scheduleWorkbenchDraw();
    });
}

/* ------------------------------------------------------------------ workbench */

const WB = {
  vp: null, drag: null, gesture: null, before: null,
  tileLayer: null, imageKey: null, imageToken: 0, tileFrame: 0
};

function initWorkbench() {
  WB.vp = new Viewport($('#wb-canvas'), { mode: 'edit' });
  WB.vp.onDraw = drawWorkbench;
  WB.vp.onZoom = s => { $('#wb-zoom').textContent = Math.round(s * 100) + '%'; };
  WB.vp.onPointerDown = wbDown;
  WB.vp.onPointerMove = wbMove;
  WB.vp.onPointerUp = wbUp;

  $('#btn-fit').onclick = () => { WB.vp.resize(); WB.vp.fit(); WB.vp.draw(); };
  $('#btn-one').onclick = () => {
    const vp = WB.vp;
    const c = vp.s2w(vp.w / 2, vp.h / 2);
    vp.scale = 1; vp.ox = vp.w / 2 - c[0]; vp.oy = vp.h / 2 - c[1];
    vp.draw(); $('#wb-zoom').textContent = '100%';
  };
  $('#btn-tiles').onclick = e => {
    state.showTiles = !state.showTiles;
    e.currentTarget.classList.toggle('on', state.showTiles);
    WB.vp.draw();
  };
  $('#btn-labels').onclick = e => {
    state.showLabels = !state.showLabels;
    e.currentTarget.classList.toggle('on', state.showLabels);
    WB.vp.draw();
  };
  $$('#tool-group .tool').forEach(b => {
    b.onclick = () => {
      state.tool = b.dataset.tool;
      $$('#tool-group .tool').forEach(x => x.classList.toggle('on', x === b));
      $('#wb-hint').textContent = {
        select: '滚轮缩放 · 拖动平移 · 点击选择',
        add: '在空白处拖动创建新框',
        ai: '点击漏检位置，由 AI 生成候选轮廓'
      }[state.tool];
      WB.vp.canvas.style.cursor = state.tool === 'select' ? 'default' : 'crosshair';
      state.ai = { status: 'idle' };
      WB.vp.draw();
    };
  });

  $('#ai-refine').onclick = () => {
    const box = selected();
    if (box && managed() && writable() && workflow.ai?.available)
      aiRequest({ engine: 'sam2', mode: 'refine', box: promptBounds(box.polygon) }, box.id);
  };
  $('#btn-confirm').onclick = () => patchSelected({ review_state: 'confirmed' }, '已确认');
  $('#btn-delete').onclick = () => patchSelected({ review_state: 'deleted' }, '已标记删除');
  $('#btn-undo').onclick = undo;
  $('#btn-redo').onclick = redo;
  $('#btn-save').onclick = saveVersion;
  $('#btn-exit-readonly').onclick = () => {
    if (managed() && workflow.task.status !== 'active') return;
    state.viewingVersion = null;
    state.selection = null;
    refreshWorkbench();
    toast('已返回当前工作副本');
  };

  $$('#list-filters button').forEach(b => {
    b.onclick = () => {
      state.listFilter = b.dataset.f;
      $$('#list-filters button').forEach(x => x.classList.toggle('on', x === b));
      renderList();
    };
  });
}

function patchSelected(patch, label) {
  const d = selected();
  if (!d) return toast('请先选择目标', 'err');
  if (!writable()) return toast('历史版本只读，无法修改', 'err');
  snapshotLocal();
  return action(async () => {
    const updated = await API.patchDetection(state.task.id, d.id, patch);
    Object.assign(d, updated);
    state.dirty = true;
    syncServerDraft();
    refreshWorkbench();
    if (label) toast(`${label} #${d.id}`, 'ok');
  });
}

function snapshotLocal() {
  state.before = clone(state.draft);
  state.undo.push(state.before);
  if (state.undo.length > 50) state.undo.shift();
  state.redo.length = 0;
}

/** 记录服务端已知状态，供 undo/redo 后的增量同步使用 */
function syncServerDraft() {
  state.serverDraft = clone(state.draft);
}

/** 把本地草稿与服务端已知状态的差异推给后端（用于撤销/重做） */
async function pushDraftDiff() {
  const server = new Map(state.serverDraft.map(d => [d.id, d]));
  const jobs = [];
  const target = new Set(state.draft.map(d => d.id));
  for (const old of server.values()) {
    if (!target.has(old.id) && old.origin !== 'model' && old.review_state !== 'deleted')
      jobs.push(API.patchDetection(state.task.id, old.id, { review_state: 'deleted' }));
  }
  for (const d of state.draft) {
    const old = server.get(d.id);
    if (!old) {
      jobs.push(API.addDetection(state.task.id, {
        polygon: d.polygon, review_state: d.review_state, origin: d.origin, note: d.note
      }));
      continue;
    }
    const moved = old.polygon.some((p, i) => p[0] !== d.polygon[i][0] || p[1] !== d.polygon[i][1]);
    if (moved || old.review_state !== d.review_state || (old.note || '') !== (d.note || '')) {
      jobs.push(API.patchDetection(state.task.id, d.id, {
        polygon: moved ? d.polygon : undefined,
        review_state: d.review_state,
        note: d.note ?? ''
      }));
    }
  }
  await Promise.all(jobs);
  const fresh = await API.getDraft(state.task.id);
  state.draft = fresh.detections;
  syncServerDraft();
  refreshWorkbench();
}

function undo() {
  if (!writable()) return toast('历史版本只读', 'err');
  if (!state.undo.length) return toast('没有可撤销的操作');
  state.redo.push(clone(state.draft));
  state.draft = state.undo.pop();
  state.dirty = true;
  if (!state.draft.some(d => d.id === state.selection)) state.selection = null;
  refreshWorkbench();
  action(pushDraftDiff);
}

function redo() {
  if (!writable()) return toast('历史版本只读', 'err');
  if (!state.redo.length) return toast('没有可重做的操作');
  state.undo.push(clone(state.draft));
  state.draft = state.redo.pop();
  state.dirty = true;
  refreshWorkbench();
  action(pushDraftDiff);
}

function handlesFor(d) {
  const b = G.fromPolygon(d.polygon);
  const pts = G.cornersOf(b);
  const c = Math.cos(b.theta), s = Math.sin(b.theta);
  const rot = [b.cx + s * (b.h / 2 + 26 / WB.vp.scale), b.cy - c * (b.h / 2 + 26 / WB.vp.scale)];
  return { corners: pts, rotate: rot, box: b };
}

function wbDown(world, [sx, sy], ev) {
  const vp = WB.vp;
  if (ev.button === 1 || ev.button === 2) { vp.drag.pan = true; return; }
  if (!writable()) {
    const hit = activeDetections().slice().reverse().find(d => G.pointInPolygon(world, d.polygon));
    state.selection = hit?.id ?? null;
    vp.drag.pan = true;
    refreshWorkbench();
    return;
  }

  if (state.tool === 'ai') {
    if (!managed()) aiRequest({ point: world, mode: 'point' });
    else if (workflow.ai?.available && !['requesting','preview'].includes(state.ai.status))
      WB.drag = { kind: 'ai-box', start: world };
    return;
  }
  if (state.tool === 'add') { WB.drag = { kind: 'create', start: world }; return; }

  const d = selected();
  if (d) {
    const h = handlesFor(d);
    const near = pt => {
      const p = vp.w2s(pt[0], pt[1]);
      return Math.hypot(p[0] - sx, p[1] - sy) < 10;
    };
    if (near(h.rotate)) {
      WB.drag = {
        kind: 'rotate', id: d.id,
        startAngle: Math.atan2(world[1] - h.box.cy, world[0] - h.box.cx),
        theta0: h.box.theta
      };
      snapshotLocal();
      return;
    }
    for (let i = 0; i < 4; i++) {
      if (near(h.corners[i])) {
        WB.drag = { kind: 'resize', id: d.id, fixed: h.corners[(i + 2) % 4] };
        snapshotLocal();
        return;
      }
    }
  }

  const list = activeDetections();
  for (let i = list.length - 1; i >= 0; i--) {
    if (G.pointInPolygon(world, list[i].polygon)) {
      state.selection = list[i].id;
      WB.drag = { kind: 'move', id: list[i].id, last: world };
      snapshotLocal();
      refreshWorkbench();
      return;
    }
  }
  state.selection = null;
  vp.drag.pan = true;
  refreshWorkbench();
}

function wbMove(world) {
  const dr = WB.drag;
  if (!dr) {
    if (WB.vp.drag && WB.vp.drag.moved === false) WB.vp.drag.pan = true;
    return;
  }
  const d = dr.id != null ? state.draft.find(x => x.id === dr.id) : null;

  if (dr.kind === 'move' && d) {
    const b = G.fromPolygon(d.polygon);
    b.cx += world[0] - dr.last[0];
    b.cy += world[1] - dr.last[1];
    d.polygon = G.toPolygon(b);
    if (d.review_state === 'pending') d.review_state = 'modified';
    dr.last = world;
    state.dirty = true;
    WB.vp.draw(); renderDetail();
  } else if (dr.kind === 'rotate' && d) {
    const b = G.fromPolygon(d.polygon);
    const ang = Math.atan2(world[1] - b.cy, world[0] - b.cx);
    b.theta = dr.theta0 + (ang - dr.startAngle);
    d.polygon = G.toPolygon(b);
    if (d.review_state === 'pending') d.review_state = 'modified';
    state.dirty = true;
    WB.vp.draw(); renderDetail();
  } else if (dr.kind === 'resize' && d) {
    const b = G.fromPolygon(d.polygon);
    const u = [Math.cos(b.theta), Math.sin(b.theta)];
    const v = [-Math.sin(b.theta), Math.cos(b.theta)];
    const rel = [world[0] - dr.fixed[0], world[1] - dr.fixed[1]];
    const lx = rel[0] * u[0] + rel[1] * u[1];
    const ly = rel[0] * v[0] + rel[1] * v[1];
    b.w = Math.max(8, Math.abs(lx));
    b.h = Math.max(5, Math.abs(ly));
    b.cx = dr.fixed[0] + u[0] * (lx / 2) + v[0] * (ly / 2);
    b.cy = dr.fixed[1] + u[1] * (lx / 2) + v[1] * (ly / 2);
    d.polygon = G.toPolygon(b);
    if (d.review_state === 'pending') d.review_state = 'modified';
    state.dirty = true;
    WB.vp.draw(); renderDetail();
  } else if (dr.kind === 'create' || dr.kind === 'ai-box') {
    WB.vp.draw();
    const ctx = WB.vp.ctx;
    const a = WB.vp.w2s(dr.start[0], dr.start[1]);
    const c = WB.vp.w2s(world[0], world[1]);
    ctx.save();
    ctx.strokeStyle = COLORS.modified; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]);
    ctx.strokeRect(Math.min(a[0], c[0]), Math.min(a[1], c[1]), Math.abs(c[0] - a[0]), Math.abs(c[1] - a[1]));
    ctx.restore();
  }
}

function wbUp(world) {
  const dr = WB.drag;
  WB.drag = null;
  if (!dr) return;

  if (dr.kind === 'ai-box') {
    const box = promptBounds([dr.start, world]);
    if (box[2] - box[0] >= 5 && box[3] - box[1] >= 5)
      aiRequest({ engine: 'sam2', mode: 'box', box });
    else { WB.vp.draw(); toast('请拖出包含目标的粗框'); }
    return;
  }
  if (dr.kind === 'create') {
    const [x0, y0] = dr.start;
    const w = Math.abs(world[0] - x0), h = Math.abs(world[1] - y0);
    if (w < 6 || h < 5) { WB.vp.draw(); return; }
    const box = { cx: (x0 + world[0]) / 2, cy: (y0 + world[1]) / 2, w, h, theta: 0 };
    snapshotLocal();
    action(async () => {
      const d = await API.addDetection(state.task.id, {
        polygon: G.toPolygon(box), review_state: 'modified', origin: 'manual'
      });
      state.draft.push(d);
      state.selection = d.id;
      state.dirty = true;
      syncServerDraft();
      refreshWorkbench();
      toast('已新增手动标注 #' + d.id, 'ok');
    });
    return;
  }

  // 移动/旋转/缩放结束：把几何提交给后端
  const d = state.draft.find(x => x.id === dr.id);
  if (!d) return;
  action(async () => {
    const updated = await API.patchDetection(state.task.id, d.id, {
      polygon: d.polygon, review_state: d.review_state
    });
    Object.assign(d, updated);
    syncServerDraft();
    refreshWorkbench();
  });
}

function drawWorkbench(ctx, vp) {
  const list = activeDetections();

  if (state.showTiles) {
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,.28)';
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 5]);
    ctx.font = '10px monospace';
    ctx.fillStyle = 'rgba(255,255,255,.62)';
    for (const t of tilesNow()) {
      const [x, y] = vp.w2s(t.x, t.y);
      ctx.strokeRect(x, y, t.width * vp.scale, t.height * vp.scale);
      ctx.fillText('T' + t.id, x + 4, y + 12);
    }
    ctx.restore();
  }

  for (const d of list) {
    const sp = d.polygon.map(p => vp.w2s(p[0], p[1]));
    const sel = d.id === state.selection;
    const col = COLORS[d.review_state] || COLORS.pending;
    ctx.beginPath();
    ctx.moveTo(sp[0][0], sp[0][1]);
    for (let i = 1; i < sp.length; i++) ctx.lineTo(sp[i][0], sp[i][1]);
    ctx.closePath();
    ctx.lineWidth = sel ? 2.4 : 1.5;
    ctx.strokeStyle = col;
    if (d.review_state === 'deleted') ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);
    if (sel) { ctx.fillStyle = 'rgba(47,91,255,.14)'; ctx.fill(); }
    else if (d.review_state === 'pending' && vp.scale > 0.22) { ctx.fillStyle = 'rgba(184,184,184,.06)'; ctx.fill(); }

    if (state.showLabels && vp.scale > 0.16) {
      ctx.font = '600 11px "Segoe UI", sans-serif';
      const label = '#' + d.id;
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(8,13,18,.82)';
      ctx.fillRect(sp[0][0] - 1, sp[0][1] - 14, tw + 6, 14);
      ctx.fillStyle = col;
      ctx.fillText(label, sp[0][0] + 2, sp[0][1] - 3);
    }
  }

  if (state.ai.status === 'preview' && state.ai.polygon) {
    const sp = state.ai.polygon.map(p => vp.w2s(p[0], p[1]));
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(sp[0][0], sp[0][1]);
    for (let i = 1; i < sp.length; i++) ctx.lineTo(sp[i][0], sp[i][1]);
    ctx.closePath();
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 2;
    ctx.strokeStyle = COLORS.ai;
    ctx.fillStyle = 'rgba(184,184,184,.14)';
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  const d = selected();
  if (d && writable()) {
    const h = handlesFor(d);
    const c = vp.w2s(h.box.cx, h.box.cy);
    const r = vp.w2s(h.rotate[0], h.rotate[1]);
    ctx.save();
    ctx.strokeStyle = '#2f5bff';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(c[0], c[1]); ctx.lineTo(r[0], r[1]); ctx.stroke();
    for (const pt of h.corners) {
      const [x, y] = vp.w2s(pt[0], pt[1]);
      ctx.fillStyle = '#0b1219';
      ctx.strokeStyle = '#2f5bff';
      ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.rect(x - 4, y - 4, 8, 8); ctx.fill(); ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(r[0], r[1], 5.5, 0, Math.PI * 2);
    ctx.fillStyle = '#2f5bff'; ctx.fill();
    ctx.restore();
  }

  if (state.ai.status === 'requesting') {
    ctx.save();
    ctx.font = '12px "Segoe UI", sans-serif';
    ctx.fillStyle = '#b8b8b8';
    ctx.fillText('AI 候选生成中…', 16, 24);
    ctx.restore();
  }
  if (state.geometryPreview) {
    ctx.save();
    ctx.beginPath();
    state.geometryPreview.forEach(([x,y], index) => {
      const [sx,sy] = vp.w2s(x,y);
      if (index === 0) ctx.moveTo(sx,sy); else ctx.lineTo(sx,sy);
    });
    ctx.closePath();
    ctx.setLineDash([6,4]);
    ctx.strokeStyle = '#f5d36f';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }

}

function refreshWorkbench() {
  renderList();
  renderDetail();
  updateDirty();
  if (WB.vp) WB.vp.draw();
}

function updateDirty() {
  renderAnnotationToolbar();
  const el = $('#wb-dirty');
  const banner = $('#wb-banner');
  if (state.viewingVersion) {
    el.textContent = '历史版本只读';
    el.className = 'tag';
    banner.classList.remove('hidden');
    $('#wb-banner-id').textContent = state.viewingVersion.id;
    return;
  }
  banner.classList.add('hidden');
  el.textContent = managed()
    ? API.hasDraftFailure(state.task.id) ? '草稿保存失败' : '草稿随编辑保存'
    : state.dirty ? '有未保存修改' : '无未保存修改';
  el.className = 'tag ' + (state.dirty ? 'pending' : '');
}

function renderList() {
  const host = $('#det-list');
  const list = activeDetections();
  const f = state.listFilter;
  const rows = list.filter(d => {
    if (f === 'all') return true;
    if (f === 'low') return d.confidence != null && d.confidence < 0.45 && (!managed() || d.review_state === 'pending');
    return d.review_state === f;
  }).sort((a, b) => a.id - b.id);

  $('#list-count').textContent = rows.length + ' / ' + list.length;
  host.innerHTML = rows.map(d => `
    <div class="det-item ${d.id === state.selection ? 'sel' : ''}" data-id="${d.id}">
      <span class="sw" style="background:${COLORS[d.review_state]}"></span>
      <span class="id">#${d.id}</span>
      <span class="muted">${d.origin === 'ai' ? 'AI' : d.origin === 'manual' ? '手动' : '模型'}</span>
      <span class="cf">${d.confidence == null ? '—' : d.confidence.toFixed(2)}</span>
    </div>`).join('') || '<div class="empty">该筛选下没有目标</div>';

  host.querySelectorAll('.det-item').forEach(el => {
    el.onclick = () => {
      state.selection = Number(el.dataset.id);
      refreshWorkbench();
      centerOn(state.selection);
    };
  });
}

function centerOn(id) {
  const d = activeDetections().find(x => x.id === id);
  if (!d || !WB.vp.w) return;
  const vp = WB.vp;
  const c = G.centroid(d.polygon);
  vp.ox = vp.w / 2 - c[0] * vp.scale;
  vp.oy = vp.h / 2 - c[1] * vp.scale;
  vp.draw();
}

function reasonTagsFor(id) {
  const item = state.review.items.find(x => x.detection_id === id);
  if (!item) return '';
  return item.reasons.map(r =>
    `<span class="tag" title="${r.code}">${r.message}</span>`).join('');
}

function renderDetail() {
  state.geometryPreview = null;
  if (state.ai.status === 'preview') {
    showAiDialog(state.ai.candidate, state.ai.replaceId);
    return;
  }
  const host = $('#wb-detail');
  const d = selected();
  const tagEl = $('#detail-origin');
  if (!d) {
    host.innerHTML = '<div class="empty">未选择目标<br><span class="dim">在画布或列表中选择一个检测框</span></div>';
    tagEl.style.display = 'none';
    return;
  }
  tagEl.style.display = '';
  tagEl.textContent = d.origin === 'ai' ? 'AI 辅助' : d.origin === 'manual' ? '手动新增' : '模型输出';
  tagEl.className = 'tag ' + (d.origin === 'ai' ? 'ai' : 'model');

  const tile = tilesNow().find(t => t.id === d.source_tile_id);
  const box = G.fromPolygon(d.polygon);
  const item = state.review.items.find(x => x.detection_id === d.id);
  const dup = item && item.reasons.find(r => r.code === 'DUPLICATE');

  host.innerHTML = `
    <dl>
      <dt>编号</dt><dd>#${d.id}</dd>
      <dt>状态</dt><dd><span class="tag ${d.review_state}">${STATE_LABEL[d.review_state]}</span></dd>
      <dt>置信度</dt><dd>${d.confidence == null ? '— <span class="dim">(人工/AI 候选不伪造置信度)</span>' : d.confidence.toFixed(4)}</dd>
      <dt>来源切片</dt><dd>${tile ? 'T' + tile.id + ' (x=' + tile.x + ', y=' + tile.y + ')' : '—'}</dd>
      <dt>中心</dt><dd>${box.cx.toFixed(1)}, ${box.cy.toFixed(1)}</dd>
      <dt>长 / 宽</dt><dd>${box.w.toFixed(1)} × ${box.h.toFixed(1)} px</dd>
      <dt>角度</dt><dd>${(box.theta * 180 / Math.PI).toFixed(1)}°</dd>
      <dt>多边形</dt><dd style="font-size:10.5px;line-height:1.6">${d.polygon.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('<br>')}</dd>
    </dl>
    ${item ? `<div style="margin-bottom:10px"><span class="dim" style="font-size:11.5px">疑难原因</span>
      <div class="row" style="margin-top:5px;gap:5px">${reasonTagsFor(d.id)}</div>
      ${dup ? `<div class="dim" style="font-size:11px;margin-top:6px">与 #${dup.partner_id} 多边形 IoU ${dup.iou.toFixed(3)}，低于 merge_iou ${paramsNow().merge_iou}，未被 NMS 合并</div>` : ''}
    </div>` : ''}
    ${writable() ? `<form id="det-geometry" class="annotation-geometry">
      ${[['cx','中心 X',box.cx],['cy','中心 Y',box.cy],['w','长度',box.w],
          ['h','宽度',box.h],['theta','角度 °',box.theta * 180 / Math.PI]].map(([key,label,value]) =>
        `<label>${label}<input type="number" name="${key}" step="any" value="${value.toFixed(2)}" required></label>`).join('')}
      <button class="btn sm">应用数值</button>
    </form>` : ''}
    <label class="dim" style="font-size:11.5px">备注</label>
    <textarea id="det-note" placeholder="记录复核判断依据…" ${!writable() ? 'disabled' : ''}>${escapeWorkflow(d.note)}</textarea>
    <div class="row" style="margin-top:8px">
      <button class="btn sm" id="btn-note-save" ${!writable() ? 'disabled' : ''}>保存备注</button>
      <button class="btn sm" id="btn-locate">定位</button>
    </div>`;

  const geometryForm = $('#det-geometry');
  if (geometryForm) geometryForm.onsubmit = event => {
    event.preventDefault();
    const data = new FormData(geometryForm);
    const [cx,cy,w,h,theta] = ['cx','cy','w','h','theta'].map(key => Number(data.get(key)));
    const shape = { cx, cy, w, h, theta: theta * Math.PI / 180 };
    const polygon = G.toPolygon(shape);
    const width = state.image?.width || IMG_W, height = state.image?.height || IMG_H;
    if (![cx,cy,w,h,theta].every(Number.isFinite) || w <= 0 || h <= 0 ||
        polygon.some(([x,y]) => x < 0 || y < 0 || x > width || y > height))
      return toast('旋转框须有正宽高且位于原图范围内', 'err');
    patchSelected({ polygon, review_state: 'modified' }, '已应用数值');
  };
  if (geometryForm) geometryForm.oninput = () => {
    const data = new FormData(geometryForm);
    const [cx,cy,w,h,theta] = ['cx','cy','w','h','theta'].map(key => Number(data.get(key)));
    state.geometryPreview = w > 0 && h > 0 && [cx,cy,w,h,theta].every(Number.isFinite)
      ? G.toPolygon({cx,cy,w,h,theta:theta * Math.PI / 180}) : null;
    WB.vp.draw();
  };
  const noteBtn = $('#btn-note-save');
  if (noteBtn) noteBtn.onclick = () => patchSelected({ note: $('#det-note').value }, '备注已保存');
  $('#btn-locate').onclick = () => centerOn(d.id);
}

/* --------------------------------------------------------------------- upload */

const UPLOAD_STEPS = [
  { id: 'transport', label: '分片传输', note: '按 1 MiB 分片上传，可暂停与续传' },
  { id: 'merge', label: '合并与完整性校验', note: '核对预期长度与最终校验和' },
  { id: 'inspect', label: '图像验证', note: '真实解码、格式签名、像素上限、EXIF' },
  { id: 'preview', label: '生成预览', note: '生成缩略图与瓦片，进入可分析状态' }
];

function renderUpload() {
  const u = state.upload;
  const idx = UPLOAD_STEPS.findIndex(s => s.id === u.stage);
  $('#upload-steps').innerHTML = UPLOAD_STEPS.map((s, i) => {
    let cls = '';
    if (u.status === 'ready' || (idx >= 0 && i < idx)) cls = 'done';
    else if (idx === i) cls = 'active';
    return `<div class="step ${cls}">
      <div class="bullet">${cls === 'done' ? '✓' : i + 1}</div>
      <div class="txt">${s.label}<small>${s.note}</small></div>
    </div>`;
  }).join('');

  $('#upload-bar').style.width = u.pct + '%';
  $('#upload-pct').textContent = Math.round(u.pct) + '%';
  $('#upload-chunk').textContent = u.total ? `分片 ${u.received}/${u.total}` : '';
  $('#upload-meta').textContent = u.status === 'ready'
    ? `${state.image ? state.image.file_name : 'harbor.png'} · ${IMG_W}×${IMG_H} · RGB`
    : (u.name || '');

  $('#btn-pause').disabled = !(u.status === 'transport' || u.status === 'paused');
  $('#btn-abort').disabled = !(u.status === 'transport' || u.status === 'paused');
  $('#btn-pause').textContent = u.status === 'paused' ? '继续传输' : '暂停传输';
  $('#btn-create-task').disabled = !(state.image && state.image.status === 'ready' && !state.task);
  $('#upload-block').textContent = state.image
    ? (state.task ? '已创建检测任务，可进入任务页' : '影像已通过验证，可以创建检测任务')
    : '未就绪影像不能创建检测任务';
}

function resetUpload() {
  state.upload = { status: 'idle', pct: 0, received: 0, total: 24, stage: '', name: '', id: null, error: null, file: null };
  renderUpload();
}

async function startUpload(name, file) {
  const u = state.upload;
  if (u.status === 'transport' || u.status === 'verifying') return;

  await action(async () => {
    const created = await API.createUpload(state.project.id, { file_name: name, size: file ? file.size : null });
    Object.assign(u, {
      id: created.id, status: 'transport', stage: 'transport',
      received: created.chunks_received, total: created.chunks_total,
      pct: created.pct, name, file: file || null, error: null
    });
    renderUpload();
    await runChunks();
  });
}

async function runChunks() {
  const u = state.upload;
  const size = CFG.chunkSize || 1048576;
  while (u.received < u.total) {
    if (u.status === 'paused') return;
    const index = u.received;
    const bytes = u.file
      ? u.file.slice(index * size, (index + 1) * size)
      : new Uint8Array(Math.min(size, 1024));
    try {
      const r = await API.putChunk(u.id, index, bytes);
      u.received = r.chunks_received;
      u.pct = r.pct;
      renderUpload();
    } catch (err) {
      u.status = 'failed';
      u.error = err.code;
      renderUpload();
      toastError(err);
      return;
    }
  }
  await finishUpload();
}

/** 真实文件用 WebCrypto 计算整体校验和；演示影像直接使用已知哈希 */
async function sha256OfFile(file) {
  if (!file) return SCENE.image.sha256;
  if (!globalThis.crypto || !crypto.subtle) return null;
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

async function finishUpload() {
  const u = state.upload;
  u.status = 'verifying';
  u.stage = 'merge';
  renderUpload();
  await action(async () => {
    const sha256 = await sha256OfFile(u.file);
    await API.completeUpload(u.id, { sha256, size: u.file ? u.file.size : null });
    await pollUpload(u.id);
  });
}

async function pollUpload(uploadId) {
  const u = state.upload;
  for (let i = 0; i < 120; i++) {
    const snap = await API.getUpload(uploadId);
    u.stage = snap.stage;
    u.status = snap.status === 'verifying' ? 'verifying' : snap.status;
    renderUpload();
    if (snap.status === 'ready') {
      state.image = await API.getImage(snap.image_id);
      u.status = 'ready';
      u.stage = 'ready';
      u.pct = 100;
      renderUpload();
      await loadLibrary();      // 新影像立刻出现在项目影像列表里
      toast('影像已通过验证，可以创建检测任务', 'ok');
      return;
    }
    if (snap.status === 'failed') {
      toast('上传校验失败：' + (snap.error || '未知原因'), 'err');
      renderUpload();
      return;
    }
    await sleep(CFG.pollIntervalMs || 250);
  }
}

function bindUpload() {
  $('#btn-pick-demo').onclick = () => { resetUpload(); startUpload('harbor.png', null); };
  $('#dropzone').onclick = ev => {
    if (ev.target.id !== 'btn-pick-demo') $('#file-input').click();
  };
  $('#file-input').onchange = ev => {
    const f = ev.target.files[0];
    if (f) { resetUpload(); startUpload(f.name, f); }
  };
  const dz = $('#dropzone');
  ['dragenter', 'dragover'].forEach(t => dz.addEventListener(t, e => { e.preventDefault(); dz.classList.add('hot'); }));
  ['dragleave', 'drop'].forEach(t => dz.addEventListener(t, e => { e.preventDefault(); dz.classList.remove('hot'); }));
  dz.addEventListener('drop', e => {
    const f = e.dataTransfer.files[0];
    if (f) { resetUpload(); startUpload(f.name, f); }
  });
  $('#btn-pause').onclick = () => {
    const u = state.upload;
    if (u.status === 'transport') {
      u.status = 'paused';
      toast('传输已暂停，服务端已保留分片进度');
    } else if (u.status === 'paused') {
      u.status = 'transport';
      renderUpload();
      toast(`已从第 ${u.received + 1} 个分片继续`);
      action(runChunks);
      return;
    }
    renderUpload();
  };
  $('#btn-abort').onclick = () => {
    state.upload.status = 'paused';
    toast('网络中断，已保留服务端分片进度', 'err');
    renderUpload();
  };
  $('#btn-create-task').onclick = () => createTask();
}

/* ----------------------------------------------------------------------- task */

async function createTask() {
  if (!state.image) return toast('请先上传影像', 'err');
  if (state.task && ['running', 'merging'].includes(state.task.status))
    return toast('任务正在运行，请等待完成或取消', 'err');
  await action(async () => {
    state.task = await API.createTask(state.project.id, {
      image_id: state.image.id,
      parameters: SCENE.parameters
    });
    switchView('task');
    renderTask();
    pollTask();
  });
}

async function pollTask() {
  const id = state.task.id;
  for (let i = 0; i < 600; i++) {
    await sleep(CFG.pollIntervalMs || 250);
    if (!state.task || state.task.id !== id) return;
    let snap;
    try {
      snap = await API.getTask(id);
    } catch (err) {
      toastError(err);
      return;
    }
    state.task = snap;
    renderTask();
    if (snap.status === 'done') {
      await loadResult();
      toast('检测完成，最终数量已发布', 'ok');
      return;
    }
    if (snap.status === 'cancelled' || snap.status === 'failed') return;
  }
}

async function loadResult() {
  state.result = await API.getTaskResult(state.task.id);
  state.versions = await API.listVersions(state.task.id);
  const draft = await API.getDraft(state.task.id);
  state.draft = draft.detections;
  syncServerDraft();
  const v0 = await API.getVersion('v0', state.task.id);
  state.baseline = v0.detections;
  state.dirty = false;
  state.undo = []; state.redo = [];
  await loadReview();
  renderTask();
  renderHistory();
  renderExport();
  refreshWorkbench();
}

async function loadReview() {
  if (!state.task) return;
  state.review = await API.getReviewQueue(state.task.id);
}

function renderTask() {
  const t = state.task;
  const label = {
    queued: '排队中', running: '运行中', merging: '合并中',
    done: '已完成', cancelled: '已取消', failed: '失败'
  }[t ? t.status : ''] || '未创建';

  const el = $('#task-state');
  el.textContent = label;
  el.className = 'tag ' + (t && t.status === 'done' ? 'confirmed'
    : t && (t.status === 'running' || t.status === 'merging') ? 'pending'
    : t && t.status === 'cancelled' ? 'deleted' : '');

  const tiles = tilesNow();
  $('#task-tiles').textContent = (t ? t.tiles_done : 0) + '/' + (t ? t.tiles_total : tiles.length);
  $('#task-temp').textContent = t && t.temp_count ? t.temp_count : '—';
  $('#task-final').textContent = t && t.final_count != null ? t.final_count : '—';
  $('#task-attempt').textContent = '第 ' + (t ? t.attempt : 1) + ' 次尝试';
  $('#task-bar').style.width = ((t ? t.tiles_done : 0) / (t ? t.tiles_total : tiles.length)) * 100 + '%';

  $('#tiles-grid').innerHTML = tiles.map((tile, i) => {
    let cls = '';
    if (t && i < t.tiles_done) cls = 'done';
    else if (t && i === t.tiles_done && t.status === 'running') cls = 'now';
    return `<div class="tile-cell ${cls}">T${tile.id}</div>`;
  }).join('');

  $('#btn-run-task').disabled = !state.image || (t && ['running', 'merging'].includes(t.status));
  $('#btn-cancel-task').disabled = !t || t.status !== 'running';
  $('#btn-retry-task').disabled = !t || !['cancelled', 'failed'].includes(t.status);
  $('#btn-goto-wb').disabled = !t || t.status !== 'done';

  $('#task-log').innerHTML = t
    ? `<div>任务 ${t.id} · ${label} · 尝试 ${t.attempt}</div>
       <div>已完成切片 ${t.tiles_done}/${t.tiles_total}，临时候选 ${t.temp_count}</div>
       ${t.final_count != null ? `<div>合并抑制后发布最终数量 ${t.final_count}</div>` : '<div class="dim">最终数量在合并完成后发布</div>'}`
    : '等待创建任务…';
}

function bindTask() {
  $('#btn-run-task').onclick = () => createTask();
  $('#btn-cancel-task').onclick = () => action(async () => {
    state.task = await API.cancelTask(state.task.id);
    renderTask();
    toast('已请求取消，等待可终止边界');
  });
  $('#btn-retry-task').onclick = () => action(async () => {
    state.task = await API.retryTask(state.task.id);
    renderTask();
    pollTask();
  });
  $('#btn-goto-wb').onclick = async () => {
    switchView('workbench');
    await sleep(40);
    WB.vp.resize(); WB.vp.fit(); WB.vp.draw();
  };
}

/* ---------------------------------------------------------------------- stats */

const ST = { vp: null };

function initStats() {
  ST.vp = new Viewport($('#st-canvas'), { mode: 'region' });
  ST.vp.onDraw = drawStats;
  ST.vp.onPointerDown = (world) => {
    if (state.statTool === 'pan') { ST.vp.drag.pan = true; return; }
    if (state.regionClosed) return;
    state.region.push([world[0], world[1]]);
    renderStats();
  };
  ST.vp.canvas.addEventListener('dblclick', () => {
    if (state.region.length > 2) { state.regionClosed = true; commitRegion(); }
  });
  $('#btn-region-clear').onclick = () => {
    state.region = []; state.regionClosed = false; state.stats = null; renderStats();
  };
  $('#btn-region-close').onclick = () => {
    if (state.region.length > 2) { state.regionClosed = true; commitRegion(); }
  };
  $$('[data-stat-tool]').forEach(b => {
    b.onclick = () => {
      state.statTool = b.dataset.statTool;
      $$('[data-stat-tool]').forEach(x => x.classList.toggle('on', x === b));
    };
  });
}

function commitRegion() {
  if (!state.task) return toast('还没有检测任务', 'err');
  action(async () => {
    state.stats = await API.createRegion(state.task.id, {
      polygon: state.region, name: '关注区域'
    });
    renderStats();
  });
}

function drawStats(ctx, vp) {
  for (const d of activeDetections()) {
    const sp = d.polygon.map(p => vp.w2s(p[0], p[1]));
    ctx.beginPath();
    ctx.moveTo(sp[0][0], sp[0][1]);
    for (let i = 1; i < sp.length; i++) ctx.lineTo(sp[i][0], sp[i][1]);
    ctx.closePath();
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = d.review_state === 'deleted' ? 'rgba(217,45,80,.55)' : COLORS[d.review_state];
    ctx.stroke();
    if (d.review_state !== 'deleted') {
      const c = vp.w2s(...G.centroid(d.polygon));
      ctx.fillStyle = 'rgba(47,91,255,.55)';
      ctx.fillRect(c[0] - 1.5, c[1] - 1.5, 3, 3);
    }
  }
  if (state.region.length) {
    const sp = state.region.map(p => vp.w2s(p[0], p[1]));
    ctx.beginPath();
    ctx.moveTo(sp[0][0], sp[0][1]);
    for (let i = 1; i < sp.length; i++) ctx.lineTo(sp[i][0], sp[i][1]);
    if (state.regionClosed) ctx.closePath();
    ctx.lineWidth = 1.8;
    ctx.strokeStyle = COLORS.region;
    ctx.stroke();
    if (state.regionClosed) { ctx.fillStyle = 'rgba(47,91,255,.14)'; ctx.fill(); }
    for (const [x, y] of sp) {
      ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = '#2f5bff'; ctx.fill();
    }
  }
}

function renderStats() {
  const list = activeDetections();
  const c = counts(list);
  const baseline = state.baseline.length;
  $('#st-global').innerHTML = `
    <tr><td>未检查</td><td class="num">${c.pending}</td></tr>
    <tr><td>已确认</td><td class="num">${c.confirmed}</td></tr>
    <tr><td>已修改</td><td class="num">${c.modified}</td></tr>
    <tr><td>已删除</td><td class="num">${c.deleted}</td></tr>
    <tr><td><b>当前有效</b></td><td class="num"><b>${c.total - c.deleted}</b></td></tr>
    <tr><td class="dim">模型候选（v0）</td><td class="num dim">${baseline}</td></tr>`;

  const ready = state.regionClosed && state.region.length > 2 && state.stats;
  $('#st-empty').classList.toggle('hidden', !!ready);
  $('#st-result').classList.toggle('hidden', !ready);

  if (ready) {
    const s = state.stats.counts;
    $('#st-rows').innerHTML = `
      <tr><td>模型原始结果</td><td class="num">${s.model}</td></tr>
      <tr><td>当前有效</td><td class="num">${s.effective}</td></tr>
      <tr><td>人工删除</td><td class="num">${s.deleted}</td></tr>
      <tr><td>人工/AI 新增</td><td class="num">${s.added}</td></tr>
      <tr><td>修订变化</td><td class="num">${s.delta >= 0 ? '+' : ''}${s.delta}</td></tr>
      <tr><td class="dim">区域面积</td><td class="num dim">${state.stats.area_px2.toLocaleString()} px²</td></tr>`;
  }
  if (ST.vp) ST.vp.draw();
}

/* -------------------------------------------------------------------- history */

async function saveVersion() {
  if (!writable()) return toast('历史版本只读', 'err');
  if (!state.dirty) return toast('没有需要保存的修改');
  await action(async () => {
    const v = await API.commitVersion(state.task.id, { label: '人工修订' });
    state.versions = await API.listVersions(state.task.id);
    state.draft = v.detections;
    syncServerDraft();
    state.dirty = false;
    await loadReview();
    renderHistory();
    renderExport();
    refreshWorkbench();
    toast('已保存为版本 ' + v.id, 'ok');
  });
}

function renderHistory() {
  const host = $('#ver-list');
  if (!state.versions.length) {
    host.innerHTML = '<div class="empty">还没有检测结果</div>';
    return;
  }
  host.innerHTML = state.versions.slice().reverse().map(v => {
    const c = v.counts;
    return `<div class="ver ${state.viewingVersion && state.viewingVersion.id === v.id ? 'on' : ''}" data-id="${v.id}">
      <span class="vid">${v.id}</span>
      <div style="flex:1">
        <div>${v.label} ${v.readonly ? '<span class="tag model">不可变</span>' : '<span class="tag modified">人工</span>'}</div>
        <div class="meta">${v.author} · ${new Date(v.created_at).toLocaleString('zh-CN')} · 有效 ${c.total - c.deleted} 个目标</div>
      </div>
      <button class="btn sm" data-view-ver="${v.id}">查看</button>
      ${v.id === 'v0' ? '' : `<button class="btn sm" data-restore="${v.id}">恢复</button>`}
    </div>`;
  }).join('');

  $$('[data-view-ver]').forEach(b => {
    b.onclick = ev => {
      ev.stopPropagation();
      action(async () => {
        state.viewingVersion = await API.getVersion(b.dataset.viewVer, state.task.id);
        switchView('workbench');
        await sleep(40);
        WB.vp.resize(); WB.vp.fit(); WB.vp.draw();
        refreshWorkbench();
        toast('正在查看 ' + state.viewingVersion.id + '（只读）');
      });
    };
  });
  $$('[data-restore]').forEach(b => {
    b.onclick = ev => {
      ev.stopPropagation();
      action(async () => {
        const draft = await API.resetDraft(state.task.id, b.dataset.restore);
        state.draft = draft.detections;
        syncServerDraft();
        state.viewingVersion = null;
        state.dirty = true;
        await loadReview();
        refreshWorkbench();
        toast('已将 ' + b.dataset.restore + ' 恢复为工作副本，保存后成为新版本', 'ok');
      });
    };
  });

  const active = state.viewingVersion || state.versions[state.versions.length - 1];
  $('#diff-ver').textContent = active.id;
  const s = active.summary;
  $('#diff-rows').innerHTML = s ? `
    <tr><td>新增目标</td><td class="num">+${s.added}</td></tr>
    <tr><td>删除目标</td><td class="num">−${s.deleted}</td></tr>
    <tr><td>几何修改</td><td class="num">${s.changed}</td></tr>
    <tr><td>新确认</td><td class="num">${s.confirmed}</td></tr>`
    : '<tr><td colspan="2" class="dim">模型原始结果，无人工变更</td></tr>';
}

/* --------------------------------------------------------------------- export */

function renderExport() {
  const sel = $('#ex-version');
  const list = managed()
    ? state.versions.filter(version => workflow.task.status === 'signed' && version.id === workflow.task.version_id)
    : state.versions;
  sel.innerHTML = list.slice().reverse()
    .map(v => `<option value="${v.id}">${v.id} · ${v.label}</option>`).join('')
    || '<option value="">（暂无版本）</option>';
  const c = counts(state.draft);
  const p = paramsNow();
  const m = (state.result && state.result.model) || SCENE.model;
  const img = state.image || SCENE.image;
  $('#ex-preview').innerHTML = `
    <dt>影像</dt><dd>${img.file_name} · ${img.width} × ${img.height}</dd>
    <dt>模型</dt><dd>${m.weights_file} · 权重 ${String(m.weights_sha256 || '').slice(0, 8)}…</dd>
    <dt>参数</dt><dd>conf ${p.conf} · imgsz ${p.imgsz} · tile ${p.tile_size}/${p.stride} · merge_iou ${p.merge_iou}</dd>
    <dt>版本</dt><dd>${list.length ? list[list.length - 1].id : '—'}</dd>
    <dt>有效目标</dt><dd>${c.total - c.deleted}</dd>
    <dt>模型候选</dt><dd>${state.baseline.length}</dd>
    <dt>坐标口径</dt><dd>原图像素 · 左上原点 · 顺时针</dd>`;
  $('#btn-export').disabled = !list.length;
}

async function doExport() {
  const vid = $('#ex-version').value;
  if (!vid || !state.task) return toast('请先完成任务并选择版本', 'err');
  $('#ex-status').textContent = '生成中…';
  await action(async () => {
    let job = await API.createExport(vid, { formats: ['csv', 'json', 'png'] }, state.task.id);
    for (let i = 0; i < 100 && job.status !== 'done'; i++) {
      await sleep(CFG.pollIntervalMs || 250);
      job = await API.getExport(job.id);
    }
    if (job.status !== 'done') { $('#ex-status').textContent = '导出超时'; return; }
    let png = false;
    for (const f of job.files) {
      if (!f.url) continue;
      const a = document.createElement('a');
      const objectUrl = API.mode === 'http' ? URL.createObjectURL(await API.downloadExport(job.id, f.name)) : null;
      a.href = objectUrl || f.url;
      a.download = f.name;
      a.click();
      if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      if (f.format === 'png') png = true;
    }
    $('#ex-status').textContent = png
      ? `已生成 ${vid} 的 CSV / JSON / PNG`
      : `已生成 ${vid} 的 CSV / JSON（标注图需通过 http 访问）`;
    toast('导出完成', 'ok');
  });
}

/* ------------------------------------------------------------------------ AI */

function promptBounds(points) {
  const [x0,y0,x1,y1] = G.bbox(points);
  const width = state.image?.width || IMG_W, height = state.image?.height || IMG_H;
  return [Math.max(0,x0), Math.max(0,y0), Math.min(width,x1), Math.min(height,y1)];
}

async function aiRequest(payload, replaceId = null) {
  if (!state.image || !writable()) return;
  const taskId = state.task?.id;
  state.ai = { status: 'requesting' };
  updateDirty();
  WB.vp.draw();
  try {
    const cand = await API.requestAiCandidate(state.image.id, { ...payload, task_id: taskId });
    if (state.task?.id !== taskId || state.view !== 'workbench') return;
    state.ai = { status: 'preview', polygon: cand.polygon, candidate: cand, replaceId };
    WB.vp.draw();
    showAiDialog(cand, replaceId);
    refreshAiAvailability();
  } catch (err) {
    if (state.task?.id !== taskId || state.view !== 'workbench') return;
    state.ai = { status: 'failed' };
    WB.vp.draw();
    if (managed()) refreshAiAvailability();
    toastError(err);
  }
}

function showAiDialog(cand, replaceId = null) {
  const taskId = state.task.id;
  const host = $('#wb-detail');
  host.innerHTML = `
    <div class="row" style="margin-bottom:10px">
      <span class="tag ai">AI 候选</span>
      <span class="dim" style="font-size:11.5px">尚未保存</span>
    </div>
    <dl>
      <dt>候选编号</dt><dd>${cand.id}</dd>
      <dt>候选来源</dt><dd>${cand.source}</dd>
      <dt>尺寸</dt><dd>${round2(cand.length)} × ${round2(cand.width)} px</dd>
      <dt>状态</dt><dd>待人工确认</dd>
    </dl>
    <div class="note" style="margin-bottom:12px">
      候选未确认前不进入修订、统计与导出；确认后记录 AI 辅助来源，且不伪造检测置信度。
    </div>
    <div class="row">
      <button class="btn primary sm" id="ai-accept">${replaceId == null ? '新增候选框' : '替换选中框'}</button>
      <button class="btn sm" id="ai-cancel">取消</button>
    </div>`;

  const applyCandidate = replaceId => action(async () => {
    if (state.task.id !== taskId || !writable()) return;
    snapshotLocal();
    const d = await API.acceptAiCandidate(cand.id, taskId, replaceId);
    if (state.task.id !== taskId) return;
    if (replaceId == null) state.draft.push(d);
    else state.draft = state.draft.map(box => box.id === d.id ? d : box);
    state.selection = d.id;
    state.dirty = true;
    syncServerDraft();
    state.ai = { status: 'idle' };
    await loadReview();
    refreshWorkbench();
    toast('AI 候选已接受并计入修订', 'ok');
  });

  $('#ai-accept').onclick = () => applyCandidate(replaceId);
  $('#ai-cancel').onclick = () => action(async () => {
    await API.cancelAiCandidate(cand.id);
    state.ai = { status: 'idle' };
    refreshWorkbench();
    toast('已取消候选，正式结果未改变');
  });
}

/* -------------------------------------------------------------------- review */

function renderReview() {
  const items = state.review.items || [];
  $('#q-count').textContent = items.length;
  $('#q-list').innerHTML = items.map((x, i) => {
    const d = activeDetections().find(y => y.id === x.detection_id);
    if (!d || d.review_state === 'deleted') return '';
    return `<div class="q-item" data-id="${d.id}">
      <span class="rank">${String(i + 1).padStart(2, '0')}</span>
      <div style="flex:1">
        <div class="row" style="gap:8px">
          <b>#${d.id}</b>
          <span class="muted" style="font-size:12px">置信度 ${d.confidence == null ? '—' : d.confidence.toFixed(2)}</span>
          <span class="tag ${d.review_state}">${STATE_LABEL[d.review_state]}</span>
          <span class="spacer"></span>
          <span class="dim mono" style="font-size:11px">切片 T${d.source_tile_id} · 分数 ${x.score}</span>
        </div>
        <div class="why">${x.reasons.map(r => `<span class="tag" title="${r.code}">${r.message}</span>`).join('')}</div>
      </div>
    </div>`;
  }).join('') || '<div class="empty">没有需要优先复核的目标</div>';

  $$('#q-list .q-item').forEach(el => {
    el.onclick = () => {
      state.selection = Number(el.dataset.id);
      switchView('workbench');
      setTimeout(() => centerOn(state.selection), 60);
    };
  });

  const sampling = state.review.sampling || [];
  $('#q-samples').innerHTML = sampling.length
    ? sampling.map(s => `x=${s.x}–${s.x + s.width}, y=${s.y}–${s.y + s.height}`).join('<br>')
    : '所有区块均有检测，建议人工抽查密集区域';
}

/* ------------------------------------------------------------------ projects */

/* --------------------------------------------------------------- library */

const TASK_STATUS_LABEL = {
  queued: '排队中', running: '运行中', merging: '合并中',
  done: '已完成', cancelled: '已取消', failed: '失败'
};

/** 顶部徽章如实反映数据来源：真实后端还是浏览器内模拟 */
function renderModeBadge() {
  const el = $('#mode-badge');
  if (!el) return;
  if (API.mode === 'http') {
    el.textContent = '真实后端';
    el.className = 'badge confirmed';
    el.title = '接口 ' + API.baseUrl;
  } else {
    el.textContent = '演示数据';
    el.className = 'badge demo';
    el.title = '当前为浏览器内模拟后端；把 config.js 的 apiMode 改成 http 可接真实服务';
  }
}

/** 拉取项目的影像与任务列表（03 §2.1/2.2：刷新或重新进入后要能找回已有数据） */
async function loadLibrary() {
  if (!state.project || !state.project.id) return;
  try {
    const [images, tasks] = await Promise.all([
      API.listImages(state.project.id),
      API.listTasks(state.project.id, { limit: 20 })
    ]);
    state.images = images.items || [];
    state.tasks = tasks.items || [];
  } catch (err) {
    state.images = [];
    state.tasks = [];
    if (API.mode === 'http') toastError(err);
  }
  renderProject();
}

/** 打开一幅影像：优先载入已完成任务，其次接上运行中的任务 */
async function openImage(imageId) {
  await action(async () => {
    workflow.task = null;
    state.viewingVersion = null;
    state.image = await API.getImage(imageId);
    const mine = state.tasks.filter(t => t.image_id === imageId);
    const done = mine.find(t => t.status === 'done');
    const running = mine.find(t => ['queued', 'running', 'merging'].includes(t.status));
    if (done) {
      if (API.mode === 'http') {
        await loadAnnotations();
        const item = workflow.items.find(entry => entry.task_id === done.id);
        if (item) return enterAnnotation(item);
      }
      await loadTaskContext(done.id);

      switchView('workbench');
      toast('已载入该影像最近的完成结果', 'ok');
      return;
    }
    if (running) {
      state.task = await API.getTask(running.id);
      switchView('task');
      renderTask();
      pollTask();
      return;
    }
    state.task = null;
    switchView('task');
    renderTask();
    toast('这幅影像还没有检测任务，可以直接创建', 'ok');
  });
}

/** 打开一个已有任务 */
async function loadTaskContext(taskId, item = null) {
  const task = await API.getTask(taskId);
  const image = await API.getImage(task.image_id);
  workflow.task = item;
  state.task = task;
  state.image = image;
  state.viewingVersion = null;
  state.selection = null;
  state.ai = { status: 'idle' };
  if (task.status === 'done') await loadResult();
}

async function openTask(taskId) {
  await action(async () => {
    if (API.mode === 'http') {
      await loadAnnotations();
      const item = workflow.items.find(entry => entry.task_id === taskId);
      if (item) return enterAnnotation(item);
    }
    await loadTaskContext(taskId);
    if (state.task.status === 'done') {
      switchView('workbench');
      return;
    }
    switchView('task');
    renderTask();
    if (['queued', 'running', 'merging'].includes(state.task.status)) pollTask();
  });
}

function renderLibrary() {
  const imgBox = $('#pj-image-list');
  if (imgBox) {
    imgBox.innerHTML = state.images.length
      ? state.images.map(i => `
        <div class="lib-row" data-image="${i.id}" role="button" tabindex="0">
          <span class="lib-name">${escapeWorkflow(i.file_name)}</span>
          <span class="dim">${i.width} × ${i.height}</span>
          <span class="dim">${i.bytes ? (i.bytes / 1048576).toFixed(2) + ' MB' : '—'}</span>
        </div>`).join('')
      : '<div class="dim" style="padding:6px 0">还没有影像，先去「上传」导入。</div>';
    imgBox.querySelectorAll('[data-image]').forEach(el => {
      el.onclick = () => openImage(el.dataset.image);
    });
  }

  const taskBox = $('#pj-task-list');
  if (taskBox) {
    taskBox.innerHTML = state.tasks.length
      ? state.tasks.map(t => `
        <div class="lib-row" data-task="${t.id}" role="button" tabindex="0">
          <span class="lib-name">${escapeWorkflow(t.image?.file_name || t.image_id)}</span>
          <span class="tag ${t.status === 'done' ? 'confirmed' : ''}">${TASK_STATUS_LABEL[t.status] || t.status}</span>
          <span class="dim">${t.tiles_done}/${t.tiles_total} 片 · ${t.final_count == null ? '—' : t.final_count} 框</span>
        </div>`).join('')
      : '<div class="dim" style="padding:6px 0">还没有检测任务。</div>';
    taskBox.querySelectorAll('[data-task]').forEach(el => {
      el.onclick = () => openTask(el.dataset.task);
    });
  }
}

function renderProject() {
  const p = state.project;
  if (!p) return;
  $('#pj-name').textContent = p.name;
  $('#pj-role').textContent = state.user.role;
  $('#pj-task').textContent = state.task
    ? (TASK_STATUS_LABEL[state.task.status] || state.task.status)
    : '未创建';

  const current = state.image || state.images[0] || null;
  const imagesEl = $('#pj-images');
  if (imagesEl) {
    imagesEl.textContent = state.images.length
      ? `${state.images.length} 幅` + (current ? `（${current.file_name}，${current.width} × ${current.height}）` : '')
      : '尚无影像';
  }
  $('#pj-sha').textContent = current && current.sha256 ? current.sha256.slice(0, 16) + '…' : '—';
  $('#pj-count').textContent = state.baseline.length;
  $('#pj-cand').textContent = state.result ? state.result.tiling.candidate_count : 0;
  $('#pj-tiles').textContent = state.result ? tilesNow().length : 0;
  $('#pj-members').innerHTML = p.members.map(m => `
    <div class="member">
      <div class="avatar">${escapeWorkflow(m.name.slice(0, 1).toUpperCase())}</div>
      <div style="flex:1">${escapeWorkflow(m.name)}${m.name === state.user.name ? ' <span class="tag confirmed">你</span>' : ''}
        <div class="dim" style="font-size:11.5px">${m.role}</div></div>
      <span class="tag">${m.role === '项目负责人' ? '管理' : '读写'}</span>
    </div>`).join('');
  renderLibrary();
}

/* ---------------------------------------------------------------------- nav */

function switchView(name) {
  state.view = name;
  const contextual = managed() && ['stats','history','export','review'].includes(name);
  $('#annotation-back').hidden = !contextual;
  $$('.view').forEach(v => v.classList.toggle('on', v.id === 'view-' + name));
  $$('#rail button').forEach(b => b.classList.toggle('on', b.dataset.view === name));

  if (name === 'workbench') {
    if (!WB.vp) initWorkbench();
    prepareWorkbenchImage();
    requestAnimationFrame(() => {
      if (WB.vp.resize() && WB.vp.needFit) WB.vp.fit();
      WB.vp.draw();
      renderList(); renderDetail(); updateDirty();
      $('#wb-zoom').textContent = Math.round(WB.vp.scale * 100) + '%';
    });
  } else if (name === 'stats') {
    if (!ST.vp) initStats();
    requestAnimationFrame(() => {
      if (ST.vp.resize() && ST.vp.needFit) ST.vp.fit();
      renderStats();
    });
  } else if (name === 'project') renderProject();
  else if (name === 'upload') renderUpload();
  else if (name === 'task') renderTask();
  else if (name === 'review') renderReview();
  else if (name === 'history') renderHistory();
  else if (name === 'export') renderExport();
}

/* --------------------------------------------------------------------- auth */

/* 独立登录页（login.html）成功后会写入 AuthSession；
 * 工作台启动时据此恢复用户与项目，不再要求二次登录。 */
async function restoreSession() {
  const session = window.AuthSession && AuthSession.read();
  if (!session) return false;

  // mock 适配器把会话放在内存里；刷新后先用已保存的令牌把它接回来。
  if (API.mode === 'mock' && typeof API.resumeSession === 'function') {
    try {
      await API.resumeSession(session.token, session.user.project.id);
    } catch (_) {
      AuthSession.clear();
      return false;
    }
  }

  let project = null;
  try {
    project = await API.getProject(session.user.project.id);
  } catch (e) {
    // mock 适配器有自己独立的演示会话，不能用它的 401 否定真实登录态
    if (API.mode === 'http' && e && (e.status === 401 || e.status === 403)) {
      AuthSession.clear();
      return false;
    }
    // 网络抖动时先用会话里的项目信息渲染，后续请求再重试
  }

  state.user = session.user;
  state.project = (project && project.id)
    ? project
    : Object.assign({ members: [] }, session.user.project || { id: '', name: '—' });
  if (!Array.isArray(state.project.members)) state.project.members = [];

  const pj = state.user.project || state.project;
  $('#ctx-user').textContent = state.user.name;
  $('#ctx-role').textContent = state.user.role;
  $('#ctx-project').textContent = pj.name || pj.id || '—';
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
  renderModeBadge();
  await loadLibrary();
  if (API.mode === 'http') {
    $$('#rail [data-annotation]').forEach(button => {
      button.hidden = lead() ? !['manage','approval','tools'].includes(button.dataset.annotation)
        : !['hall','mine'].includes(button.dataset.annotation);
    });
    $$('#rail [data-view]').forEach(button => {
      button.hidden = !lead() || !['project','upload'].includes(button.dataset.view);
    });
    $('#rail .rail-link').hidden = true;
    await annotationPage(lead() ? 'manage' : 'hall');
  } else switchView('project');
  renderUpload();
  renderTask();
  renderExport();
  return true;
}

async function doLogin(username, password, projectId) {
  await action(async () => {
    const r = await API.login(username, password, projectId);
    const project = await API.getProject(r.user.project.id);
    state.user = r.user;
    state.project = project;

    // 登录一次后，跨页面 / 刷新不再要求重登（7 天）。
    if (window.AuthSession) {
      try {
        AuthSession.save(
          r.token || ('mock-token-' + r.user.id),
          r.user,
          true,
          7 * 24 * 60 * 60 * 1000
        );
      } catch (_) { /* 隐私模式禁用存储时仍可本次使用 */ }
    }

    $('#ctx-user').textContent = state.user.name;
    $('#ctx-role').textContent = state.user.role;
    $('#ctx-project').textContent = state.user.project.name;
    $('#login').classList.add('hidden');
    $('#app').classList.remove('hidden');
    switchView('project');
    renderProject();
    renderUpload();
    renderTask();
    renderExport();
    renderModeBadge();
    await loadLibrary();
    toast(`已登录：${state.user.name}（${state.user.role}）`, 'ok');
  });
}

async function doLogout(silent) {
  try { await API.logout(); } catch (_) { /* ignore */ }
  if (window.AuthSession) AuthSession.clear();
  state.user = null;
  state.project = null;
  state.task = null;
  workflow.task = null;
  state.viewingVersion = null;
  state.draft = [];
  state.versions = [];
  state.image = null;
  resetUpload();

  // 真实后端模式：退出后回到独立登录页；mock 模式留在本页显示登录门。
  if (API.mode === 'http' && window.AuthSession) {
    const target = AuthSession.loginUrl();
    location.replace(target + (target.includes('?') ? '&' : '?') + 'logout=1');
    return;
  }

  $('#app').classList.add('hidden');
  $('#login').classList.remove('hidden');
  if (!silent) toast('已退出登录');
}

/* --------------------------------------------------------------------- init */

function bindGlobal() {
  $('#annotation-back').onclick = () => switchView('workbench');
  $$('#rail [data-annotation]').forEach(button => {
    button.onclick = () => annotationPage(button.dataset.annotation);
  });
  $$('#rail button[data-view]').forEach(b => b.onclick = () => switchView(b.dataset.view));
  $$('[data-goto]').forEach(b => b.onclick = () => switchView(b.dataset.goto));
  $('#btn-q-refresh').onclick = () => action(async () => {
    await loadReview();
    renderReview();
    toast('已按当前结果重新排序');
  });
  $('#btn-export').onclick = doExport;
  $('#btn-logout').onclick = () => doLogout(false);

  // 会话在任意请求中被撤销（401）时，统一回到登录页
  window.addEventListener('auth:expired', () => {
    if (API.mode === 'http' && window.AuthSession) {
      location.replace(AuthSession.loginUrl());
    }
  });

  window.addEventListener('resize', () => {
    // 视图尺寸变化后重新适配整幅影像，避免窗口放大后影像仍停在手机端的小比例
    if (WB.vp && state.view === 'workbench') { WB.vp.resize(); WB.vp.fit(); WB.vp.draw(); }
    if (ST.vp && state.view === 'stats') { ST.vp.resize(); ST.vp.fit(); ST.vp.draw(); }
  });

  window.addEventListener('keydown', ev => {
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName)) return;
    if (state.view !== 'workbench') return;
    if (ev.key === 'Delete' || ev.key === 'Backspace') {
      ev.preventDefault();
      $('#btn-delete').click();
    } else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'z') {
      ev.preventDefault();
      ev.shiftKey ? redo() : undo();
    } else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'y') {
      ev.preventDefault();
      redo();
    } else if (ev.key === 'Escape') {
      state.selection = null;
      state.ai = { status: 'idle' };
      refreshWorkbench();
    }
  });

  $('#login-form').onsubmit = ev => {
    ev.preventDefault();
    doLogin($('#login-user').value, $('#login-pass').value, $('#login-project').value);
  };
}

async function boot() {
  bindGlobal();
  bindUpload();
  bindTask();
  renderUpload();
  renderTask();
  renderExport();
  renderModeBadge();
  const mode = API.mode === 'mock' ? 'mock（浏览器内模拟）' : API.baseUrl;
  $('#ctx-api').textContent = mode;
  $('#ctx-api').title = API.mode === 'mock'
    ? '改 config.js 的 apiMode 为 http 即可对接真实后端'
    : API.baseUrl;

  if (window.AuthSession) {
    const hasSession = !!AuthSession.read();
    if (hasSession) $('#login').classList.add('hidden');

    if (API.mode === 'http' || hasSession) {
      const ok = await restoreSession();
      if (!ok && API.mode === 'http') {
        const here = (location.pathname.split('/').pop() || 'index.html') + location.search;
        location.replace(AuthSession.loginUrl(here));
        return;
      }
      if (!ok) $('#login').classList.remove('hidden');
    }
  }
}

boot();
