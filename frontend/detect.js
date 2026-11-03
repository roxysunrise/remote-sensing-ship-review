/* 影像检测与标注工作台 —— 界面功能层
 *
 * 职责：
 *   1. 载入图片 / 视频，统一到同一块画布上缩放、平移、逐帧浏览
 *   2. 画框 / 多边形 / 移动 / 旋转 / 缩放 / 顶点编辑，并实时给出原图像素坐标
 *   3. 调用 window.DetectAPI 完成整帧检测与 AI 辅助分割，候选经人工采纳后落为标注
 *
 * 本文件不直接 fetch、不直接跑模型；所有网络与推理细节都在 api/detect-api.js。
 */
'use strict';

(function () {
  const $ = sel => document.querySelector(sel);
  const $$ = sel => Array.from(document.querySelectorAll(sel));
  const CFG = window.DETECT_CONFIG || {};
  const VIDEO_CFG = CFG.video || {};
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const fmt = (n, d = 1) => (Number.isFinite(Number(n)) ? Number(n).toFixed(d) : '—');
  const pad = (n, l = 2) => String(n).padStart(l, '0');
  const normAngleDeg = a => {
    let d = a % 180;
    if (d > 90) d -= 180;
    if (d <= -90) d += 180;
    return d;
  };
  const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const COLORS = {
    model: '#4a9eff',
    manual: '#38b2a3',
    ai: '#a274ff',
    held: '#e0a13a'
  };
  const ORIGIN_LABEL = { model: '模型', manual: '手工', ai: 'AI 辅助' };

  /* ------------------------------------------------------------------ state */

  const S = {
    media: null,
    sessionId: null,
    sessionError: null,
    view: { scale: 1, ox: 0, oy: 0, w: 0, h: 0, dpr: 1, fitted: false },
    tool: 'select',
    filter: 'all',
    annotations: [],
    detections: new Map(),
    selectedId: null,
    hoverId: null,
    draft: null,
    polyDraft: [],
    pointer: null,
    ai: {
      status: 'idle', roi: null, candidates: [], active: 0,
      meta: null, error: null, prompt: '船舶', lastReq: null
    },
    busy: null,
    auto: false,
    lastAutoAt: 0,
    lastListAt: 0,
    lastTimeAt: 0,
    lastFrameIndex: -1,
    show: { labels: false, coords: false, hold: true },
    lastDetect: null,
    drag: null,
    space: false,
    seq: { a: 0 },
    seekDragging: false
  };

  let canvas, ctx, mediaImage = null, mediaVideo = null;
  const els = {};

  /* ------------------------------------------------------------------ boot */

  document.addEventListener('DOMContentLoaded', boot);

  function boot() {
    cacheEls();
    initCanvas();
    initApiMode();
    bindMedia();
    bindTools();
    bindStage();
    bindVideo();
    bindAi();
    bindDetail();
    bindKeyboard();
    renderAll();
    requestAnimationFrame(loop);

    if (location.protocol === 'file:') {
      toast('请通过 http 打开页面：file:// 会污染画布，检测与分割无法读取像素', 'err');
    }

    const params = new URLSearchParams(location.search);
    if (params.get('empty') === '1') {
      els.empty.classList.remove('hidden');
    } else {
      loadDemoImage();
    }
  }

  function cacheEls() {
    const ids = [
      'dt-media', 'dt-res', 'dt-frame', 'dt-api', 'dt-mode-badge',
      'dt-drop', 'dt-pick', 'dt-demo-img', 'dt-demo-video', 'dt-file', 'dt-meta',
      'dt-count', 'dt-tabs', 'dt-list', 'dt-export',
      'dt-tools', 'dt-detect', 'dt-auto', 'dt-clear', 'dt-fit', 'dt-one',
      'dt-labels', 'dt-coords', 'dt-hold',
      'dt-stage', 'dt-canvas', 'dt-video', 'dt-cursor', 'dt-zoom', 'dt-hint',
      'dt-drawinfo', 'dt-busy', 'dt-busy-text', 'dt-empty',
      'dt-vbar', 'dt-play', 'dt-prev', 'dt-next', 'dt-time', 'dt-seek',
      'dt-markers', 'dt-speed', 'dt-fps', 'dt-auto-state',
      'dt-sel-tag', 'dt-detail', 'dt-ai-mode', 'dt-ai', 'dt-toasts'
    ];
    for (const id of ids) {
      els[id.replace(/^dt-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = document.getElementById(id);
    }
    els.canvas = els.canvas || document.getElementById('dt-canvas');
    els.video = els.video || document.getElementById('dt-video');
  }

  function initApiMode() {
    const params = new URLSearchParams(location.search);
    const requested = params.get('api');
    if (requested === 'http' || requested === 'mock') {
      try { window.DetectAPI.setMode(requested); } catch (err) { toast(err.message, 'err'); }
    }
    els.modeBadge.title = 'mock 为浏览器内启发式；http 走 detect-config.js 中的真实服务';
  }

  /* ------------------------------------------------------------------ canvas */

  function initCanvas() {
    canvas = els.canvas;
    mediaVideo = els.video;
    ctx = canvas.getContext('2d');
    canvas.classList.add('selecting');
    const ro = new ResizeObserver(() => { resizeCanvas(); draw(); });
    ro.observe(els.stage);
    window.addEventListener('resize', () => { resizeCanvas(); draw(); });
    resizeCanvas();
  }

  function resizeCanvas() {
    const r = els.stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.round(r.width * dpr);
    const h = Math.round(r.height * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    S.view.w = r.width;
    S.view.h = r.height;
    S.view.dpr = dpr;
    if (!S.media) return;
    if (S.view.fitted) fitView();
    updateZoom();
  }

  function fitView() {
    if (!S.media || !S.view.w || !S.view.h) return;
    const s = Math.min(S.view.w / S.media.width, S.view.h / S.media.height) * 0.94;
    S.view.scale = s > 0 ? s : 1;
    S.view.ox = (S.view.w - S.media.width * S.view.scale) / 2;
    S.view.oy = (S.view.h - S.media.height * S.view.scale) / 2;
    S.view.fitted = true;
    updateZoom();
  }

  function oneToOne() {
    if (!S.media) return;
    S.view.scale = 1;
    S.view.ox = (S.view.w - S.media.width) / 2;
    S.view.oy = (S.view.h - S.media.height) / 2;
    S.view.fitted = false;
    updateZoom();
    draw();
  }

  function updateZoom() {
    if (els.zoom) els.zoom.textContent = S.media ? Math.round(S.view.scale * 100) + '%' : '—';
  }

  const toScreen = (x, y) => [x * S.view.scale + S.view.ox, y * S.view.scale + S.view.oy];
  const toWorld = (sx, sy) => [(sx - S.view.ox) / S.view.scale, (sy - S.view.oy) / S.view.scale];

  function localPoint(ev) {
    const r = canvas.getBoundingClientRect();
    return [ev.clientX - r.left, ev.clientY - r.top];
  }

  /* ------------------------------------------------------------------ media */

  function loadDemoImage() {
    loadImageSource('assets/harbor.png', 'harbor.png', { demo: true }).catch(err => {
      setBusy(null);
      els.empty.classList.remove('hidden');
      els.empty.innerHTML =
        '<div class="dt-empty-ico">▦</div>' +
        '<div>示例影像载入失败</div>' +
        '<div class="dim">' + esc(err.message) + '</div>' +
        '<div class="dim">请通过 http 打开页面，或从左侧选择本地图片</div>';
      toast('示例影像载入失败：' + err.message, 'err');
    });
  }

  async function loadImageSource(src, name, opts) {
    opts = opts || {};
    setBusy('load', '载入图片…');
    try {
      const img = await new Promise((resolve, reject) => {
        const im = new Image();
        im.decoding = 'async';
        im.onload = () => resolve(im);
        im.onerror = () => reject(new Error('图片解码失败'));
        im.src = src;
      });
      stopPlayback();
      releaseMediaUrl();
      mediaImage = img;
      mediaVideo.pause();
      mediaVideo.removeAttribute('src');
      try { mediaVideo.load(); } catch (_) { /* ignore */ }
      S.media = {
        kind: 'image',
        name: name || 'image',
        width: img.naturalWidth,
        height: img.naturalHeight,
        duration: 0,
        fps: 0,
        demo: !!opts.demo,
        ownedUrl: opts.owned ? src : null
      };
      resetMediaState();
      S.view.fitted = false;
      resizeCanvas();
      fitView();
      els.empty.classList.add('hidden');
      renderAll();
      await createSessionForMedia();
      toast('已载入图片 ' + S.media.name + ' · ' + S.media.width + ' × ' + S.media.height, 'ok');
    } finally {
      setBusy(null);
    }
  }

  async function loadVideoSource(src, name, opts) {
    opts = opts || {};
    setBusy('load', '载入视频…');
    try {
      stopPlayback();
      const v = mediaVideo;
      v.pause();
      v.removeAttribute('src');
      try { v.load(); } catch (_) { /* ignore */ }
      v.muted = true;
      v.src = src;
      await new Promise((resolve, reject) => {
        const ok = () => { cleanup(); resolve(); };
        const bad = () => { cleanup(); reject(new Error('视频无法解码')); };
        const cleanup = () => {
          v.removeEventListener('loadedmetadata', ok);
          v.removeEventListener('error', bad);
        };
        v.addEventListener('loadedmetadata', ok);
        v.addEventListener('error', bad);
      });
      await ensureDuration(v);
      if (!v.videoWidth || !v.videoHeight) throw new Error('视频没有有效画面尺寸');
      if (v.readyState < 2) {
        await new Promise(resolve => {
          const done = () => { v.removeEventListener('loadeddata', done); resolve(); };
          v.addEventListener('loadeddata', done);
          setTimeout(done, 800);
        });
      }
      releaseMediaUrl();
      mediaImage = null;
      S.media = {
        kind: 'video',
        name: name || 'video',
        width: v.videoWidth,
        height: v.videoHeight,
        duration: Number.isFinite(v.duration) ? v.duration * 1000 : 0,
        fps: Number(VIDEO_CFG.fpsFallback) || 30,
        demo: !!opts.demo,
        ownedUrl: opts.owned ? src : null
      };
      v.currentTime = 0;
      resetMediaState();
      S.view.fitted = false;
      resizeCanvas();
      fitView();
      els.empty.classList.add('hidden');
      renderAll();
      await createSessionForMedia();
      toast('已载入视频 ' + S.media.name + ' · ' + timeStr(S.media.duration), 'ok');
    } finally {
      setBusy(null);
    }
  }

  async function ensureDuration(v) {
    if (Number.isFinite(v.duration) && v.duration > 0) return;
    await new Promise(resolve => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        v.removeEventListener('timeupdate', onTime);
        try { v.currentTime = 0; } catch (_) { /* ignore */ }
        setTimeout(resolve, 60);
      };
      const onTime = () => {
        if (Number.isFinite(v.duration) && v.duration > 0) finish();
      };
      v.addEventListener('timeupdate', onTime);
      try { v.currentTime = 1e101; } catch (_) { finish(); }
      setTimeout(finish, 1600);
    });
  }

  function releaseMediaUrl() {
    if (S.media && S.media.ownedUrl) {
      try { URL.revokeObjectURL(S.media.ownedUrl); } catch (_) { /* ignore */ }
    }
  }

  function resetMediaState() {
    S.annotations = [];
    S.detections = new Map();
    S.selectedId = null;
    S.hoverId = null;
    S.draft = null;
    S.polyDraft = [];
    S.pointer = null;
    S.sessionId = null;
    S.sessionError = null;
    S.lastDetect = null;
    S.lastFrameIndex = -1;
    S.auto = false;
    S.lastAutoAt = 0;
    S.seq = { a: 0 };
    S.ai = {
      status: 'idle', roi: null, candidates: [], active: 0,
      meta: null, error: null, prompt: S.ai.prompt || '船舶', lastReq: null
    };
  }

  function stopPlayback() {
    if (mediaVideo && !mediaVideo.paused) mediaVideo.pause();
  }

  async function createSessionForMedia() {
    try {
      await ensureSession();
    } catch (err) {
      S.sessionError = err;
      toast('推理会话创建失败：' + err.message, 'err');
    }
  }

  async function ensureSession() {
    if (S.sessionId) return S.sessionId;
    if (!S.media) return null;
    const res = await window.DetectAPI.createSession({
      kind: S.media.kind,
      name: S.media.name,
      width: S.media.width,
      height: S.media.height,
      duration_ms: Math.round(S.media.duration || 0),
      fps: S.media.fps || null
    });
    S.sessionId = res.session_id || res.sessionId || res.id;
    return S.sessionId;
  }

  /* ------------------------------------------------------------------ time */

  function fps() {
    return (S.media && S.media.fps) || 30;
  }

  function currentTimeMs() {
    return S.media && S.media.kind === 'video' ? mediaVideo.currentTime * 1000 : 0;
  }

  function currentFrameIndex() {
    return S.media && S.media.kind === 'video' ? Math.round(mediaVideo.currentTime * fps()) : 0;
  }

  function frameTimeMs(index) {
    return (index / fps()) * 1000;
  }

  function timeStr(ms) {
    ms = Math.max(0, Number(ms) || 0);
    const total = ms / 1000;
    const m = Math.floor(total / 60);
    const s = Math.floor(total % 60);
    const milli = Math.floor(ms % 1000);
    return pad(m) + ':' + pad(s) + '.' + pad(milli, 3);
  }

  /* ------------------------------------------------------------------ items */

  function frameKey(index) {
    return S.media && S.media.kind === 'video' ? index : 0;
  }

  function displayItems() {
    if (!S.media) return [];
    const idx = currentFrameIndex();
    let dets = [];
    if (S.media.kind === 'image') {
      dets = (S.detections.get(0) || []).map(d => Object.assign({}, d, { held: false }));
    } else if (S.detections.has(idx)) {
      dets = (S.detections.get(idx) || []).map(d => Object.assign({}, d, { held: false }));
    } else if (S.show.hold) {
      let best = null;
      for (const key of S.detections.keys()) {
        if (key <= idx && (best === null || key > best)) best = key;
      }
      if (best !== null) {
        dets = (S.detections.get(best) || []).map(d =>
          Object.assign({}, d, { held: true, heldFrame: best, heldTime: frameTimeMs(best) }));
      }
    }
    const anns = S.annotations.filter(a => S.media.kind === 'image' || a.frameIndex === idx);
    return dets.concat(anns);
  }

  function storedItem(id) {
    if (!id) return null;
    const a = S.annotations.find(x => x.id === id);
    if (a) return { item: a, list: S.annotations };
    for (const arr of S.detections.values()) {
      const d = arr.find(x => x.id === id);
      if (d) return { item: d, list: arr };
    }
    return null;
  }

  function selectedStored() {
    const found = storedItem(S.selectedId);
    return found ? found.item : null;
  }

  function nextId(prefix) {
    S.seq[prefix] = (S.seq[prefix] || 0) + 1;
    return prefix + pad(S.seq[prefix], 2);
  }

  function clampPoint(p) {
    return [
      clamp(Number(p[0]) || 0, 0, S.media ? S.media.width : 0),
      clamp(Number(p[1]) || 0, 0, S.media ? S.media.height : 0)
    ];
  }

  function boxPolygon(cx, cy, w, h, angle) {
    return window.GEOM.toPolygon({
      cx, cy,
      w: Math.max(2, w),
      h: Math.max(2, h),
      theta: angle
    });
  }

  function boxParams(poly) {
    if (!poly || poly.length < 4) return { cx: 0, cy: 0, w: 0, h: 0, angle: 0 };
    let best = -1, bi = 0;
    for (let i = 0; i < poly.length; i++) {
      const a = poly[i], b = poly[(i + 1) % poly.length];
      const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (d > best) { best = d; bi = i; }
    }
    const p0 = poly[bi], p1 = poly[(bi + 1) % poly.length], p2 = poly[(bi + 2) % poly.length];
    return {
      cx: (p0[0] + p2[0]) / 2,
      cy: (p0[1] + p2[1]) / 2,
      w: Math.hypot(p1[0] - p0[0], p1[1] - p0[1]),
      h: Math.hypot(p2[0] - p1[0], p2[1] - p1[1]),
      angle: Math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    };
  }

  function getBox(item) {
    if (!item.box) item.box = boxParams(item.polygon);
    return item.box;
  }

  function setBox(item, b) {
    item.box = {
      cx: b.cx, cy: b.cy,
      w: Math.max(2, b.w), h: Math.max(2, b.h),
      angle: b.angle
    };
    item.polygon = boxPolygon(item.box.cx, item.box.cy, item.box.w, item.box.h, item.box.angle);
  }

  function orderPolygon(points) {
    const pts = points.map(p => [Number(p[0]), Number(p[1])]);
    let area = 0;
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i], q = pts[(i + 1) % pts.length];
      area += p[0] * q[1] - q[0] * p[1];
    }
    const ordered = area >= 0 ? pts.slice() : pts.slice().reverse();
    let first = 0;
    for (let i = 1; i < ordered.length; i++) {
      const d = ordered[i], f = ordered[first];
      if (d[1] < f[1] - 1e-9 || (Math.abs(d[1] - f[1]) <= 1e-9 && d[0] < f[0])) first = i;
    }
    return ordered.slice(first).concat(ordered.slice(0, first));
  }

  function itemColor(item) {
    return item.held ? COLORS.held : (COLORS[item.origin] || COLORS.manual);
  }

  function itemScore(item) {
    if (item.origin === 'model' && Number.isFinite(item.score)) return fmt(item.score, 2);
    if (item.origin === 'ai' && Number.isFinite(item.quality)) return 'Q ' + fmt(item.quality, 2);
    return '—';
  }

  /* ------------------------------------------------------------------ draw */

  function draw() {
    if (!ctx || !S.view.w) return;
    const dpr = S.view.dpr || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, S.view.w, S.view.h);
    if (!S.media) return;

    drawMedia();
    const items = displayItems();
    for (const item of items) drawItem(item);
    drawAiOverlay();
    drawDraft();
  }

  function drawMedia() {
    const m = S.media;
    const src = m.kind === 'image' ? mediaImage : mediaVideo;
    if (!src) return;
    if (m.kind === 'video' && mediaVideo.readyState < 2) return;
    const s = S.view.scale;
    const ox = S.view.ox, oy = S.view.oy;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    try {
      ctx.drawImage(src, ox, oy, m.width * s, m.height * s);
    } catch (_) { /* 解码中的视频帧 */ }
    ctx.strokeStyle = 'rgba(45,63,82,.85)';
    ctx.lineWidth = 1;
    ctx.strokeRect(ox - 0.5, oy - 0.5, m.width * s + 1, m.height * s + 1);
  }

  function pathPolygon(poly, close) {
    ctx.beginPath();
    poly.forEach((p, i) => {
      const q = toScreen(p[0], p[1]);
      if (i === 0) ctx.moveTo(q[0], q[1]);
      else ctx.lineTo(q[0], q[1]);
    });
    if (close !== false) ctx.closePath();
  }

  function drawItem(item) {
    if (!item.polygon || item.polygon.length < 3) return;
    const color = itemColor(item);
    const selected = item.id === S.selectedId;
    const hover = item.id === S.hoverId;
    const bb = window.GEOM.bbox(item.polygon);
    const tl = toScreen(bb[0], bb[1]);
    const br = toScreen(bb[2], bb[3]);
    if (br[0] < -20 || br[1] < -20 || tl[0] > S.view.w + 20 || tl[1] > S.view.h + 20) return;

    pathPolygon(item.polygon, true);
    ctx.save();
    ctx.globalAlpha = selected ? 0.2 : (hover ? 0.13 : 0.07);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.restore();

    ctx.save();
    ctx.lineJoin = 'round';
    ctx.strokeStyle = color;
    ctx.lineWidth = selected ? 2.4 : (hover ? 2 : 1.4);
    if (item.held) ctx.setLineDash([6, 4]);
    if (item.origin === 'ai' && !selected) ctx.setLineDash([5, 3]);
    ctx.stroke();
    ctx.restore();

    if (selected) drawHandles(item, color);
    if (S.show.labels || S.show.coords || selected || hover) drawLabel(item, color, bb);
  }

  function drawLabel(item, color, bb) {
    const tl = toScreen(bb[0], bb[1]);
    let text = '#' + item.id;
    if (S.show.labels) {
      text += ' ' + (item.label || 'ship');
      if (item.origin === 'model' && Number.isFinite(item.score)) text += ' ' + fmt(item.score, 2);
      if (item.origin === 'ai' && Number.isFinite(item.quality)) text += ' Q' + fmt(item.quality, 2);
      if (item.held) text += ' · 沿用';
    }
    ctx.save();
    ctx.font = '600 10.5px "Cascadia Mono", "JetBrains Mono", Consolas, monospace';
    const tw = ctx.measureText(text).width;
    const lines = [text];
    if (S.show.coords) {
      lines.push(fmt(bb[0], 0) + ',' + fmt(bb[1], 0) + ' → ' + fmt(bb[2], 0) + ',' + fmt(bb[3], 0));
    }
    let w = tw;
    if (S.show.coords) {
      ctx.font = '10px "Cascadia Mono", Consolas, monospace';
      w = Math.max(w, ctx.measureText(lines[1]).width);
      ctx.font = '600 10.5px "Cascadia Mono", "JetBrains Mono", Consolas, monospace';
    }
    const h = lines.length * 13 + 5;
    let lx = clamp(tl[0], 2, Math.max(2, S.view.w - w - 12));
    let ly = tl[1] - h - 4;
    if (ly < 2) ly = tl[1] + 4;
    ctx.fillStyle = 'rgba(9,15,21,.88)';
    ctx.strokeStyle = 'rgba(45,63,82,.9)';
    ctx.lineWidth = 1;
    roundRect(ctx, lx, ly, w + 10, h, 4);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    lines.forEach((line, i) => {
      if (i === 1) ctx.font = '10px "Cascadia Mono", Consolas, monospace';
      ctx.fillText(line, lx + 5, ly + 11 + i * 13);
    });
    ctx.restore();
  }

  function drawHandles(item, color) {
    const pts = item.polygon.map(p => toScreen(p[0], p[1]));
    ctx.save();
    ctx.lineWidth = 1;
    if (item.shape === 'box') {
      const r = rotateHandle(item);
      const top = topCenter(item);
      ctx.strokeStyle = 'rgba(217,228,238,.55)';
      ctx.beginPath();
      ctx.moveTo(top[0], top[1]);
      ctx.lineTo(r[0], r[1]);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(r[0], r[1], 4.5, 0, Math.PI * 2);
      ctx.fillStyle = '#d9e4ee';
      ctx.fill();
      ctx.strokeStyle = '#0a0f14';
      ctx.stroke();
    }
    for (const p of pts) {
      ctx.beginPath();
      ctx.rect(p[0] - 3.5, p[1] - 3.5, 7, 7);
      ctx.fillStyle = '#eaf3fb';
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.stroke();
    }
    ctx.restore();
  }

  function topCenter(item) {
    const b = getBox(item);
    const ux = Math.cos(b.angle), uy = Math.sin(b.angle);
    const vx = -uy, vy = ux;
    return toScreen(b.cx - vx * (b.h / 2), b.cy - vy * (b.h / 2));
  }

  function rotateHandle(item) {
    const b = getBox(item);
    const ux = Math.cos(b.angle), uy = Math.sin(b.angle);
    const vx = -uy, vy = ux;
    const d = b.h / 2 + 22 / S.view.scale;
    return toScreen(b.cx - vx * d, b.cy - vy * d);
  }

  function drawAiOverlay() {
    const ai = S.ai;
    if (ai.status === 'busy' && ai.roi) {
      const [x0, y0, x1, y1] = ai.roi;
      const a = toScreen(x0, y0), b = toScreen(x1, y1);
      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = COLORS.ai;
      ctx.lineWidth = 1.6;
      ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      ctx.restore();
    }
    if (ai.status !== 'preview') return;
    ai.candidates.forEach((cand, i) => {
      if (!cand.polygon || cand.polygon.length < 3) return;
      const active = i === ai.active;
      pathPolygon(cand.polygon, true);
      ctx.save();
      ctx.setLineDash(active ? [7, 4] : [4, 4]);
      ctx.strokeStyle = active ? '#c7a8ff' : 'rgba(162,116,255,.55)';
      ctx.lineWidth = active ? 2.2 : 1.2;
      ctx.stroke();
      if (active) {
        ctx.globalAlpha = 0.12;
        ctx.fillStyle = COLORS.ai;
        ctx.fill();
      }
      ctx.restore();
      const bb = window.GEOM.bbox(cand.polygon);
      const p = toScreen(bb[0], bb[1]);
      ctx.save();
      ctx.font = '600 10.5px "Cascadia Mono", Consolas, monospace';
      const text = 'AI 候选 ' + (i + 1) + ' · Q' + fmt(cand.quality, 2);
      const w = ctx.measureText(text).width;
      let lx = clamp(p[0], 2, Math.max(2, S.view.w - w - 12));
      let ly = p[1] - 18;
      if (ly < 2) ly = p[1] + 4;
      ctx.fillStyle = 'rgba(9,15,21,.9)';
      roundRect(ctx, lx, ly, w + 10, 17, 4);
      ctx.fill();
      ctx.fillStyle = active ? '#c7a8ff' : 'rgba(199,168,255,.7)';
      ctx.fillText(text, lx + 5, ly + 12);
      ctx.restore();
    });
  }

  function drawDraft() {
    if (S.draft) {
      const [x0, y0] = S.draft.start;
      const [x1, y1] = S.draft.current;
      const a = toScreen(Math.min(x0, x1), Math.min(y0, y1));
      const b = toScreen(Math.max(x0, x1), Math.max(y0, y1));
      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = S.draft.type === 'ai' ? COLORS.ai : COLORS.manual;
      ctx.lineWidth = 1.6;
      ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      ctx.globalAlpha = 0.08;
      ctx.fillStyle = S.draft.type === 'ai' ? COLORS.ai : COLORS.manual;
      ctx.fillRect(a[0], a[1], b[0] - a[0], b[1] - a[1]);
      ctx.restore();
    }

    if (S.tool === 'polygon' && S.polyDraft.length) {
      const pts = S.polyDraft.map(p => toScreen(p[0], p[1]));
      const pointer = S.pointer ? toScreen(S.pointer[0], S.pointer[1]) : null;
      ctx.save();
      ctx.beginPath();
      pts.forEach((p, i) => { if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); });
      if (pointer) ctx.lineTo(pointer[0], pointer[1]);
      ctx.strokeStyle = COLORS.manual;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      if (pts.length > 1) {
        ctx.closePath();
        ctx.globalAlpha = 0.07;
        ctx.fillStyle = COLORS.manual;
        ctx.fill();
      }
      ctx.restore();
      for (const p of pts) {
        ctx.beginPath();
        ctx.arc(p[0], p[1], 3.2, 0, Math.PI * 2);
        ctx.fillStyle = '#eaf3fb';
        ctx.fill();
        ctx.strokeStyle = COLORS.manual;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      if (pts.length >= 3) {
        ctx.beginPath();
        ctx.arc(pts[0][0], pts[0][1], 7, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(56,178,163,.7)';
        ctx.setLineDash([3, 3]);
        ctx.stroke();
      }
    }
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  /* ------------------------------------------------------------------ pointer */

  function bindStage() {
    canvas.addEventListener('pointerdown', onPointerDown);
    canvas.addEventListener('pointermove', onPointerMove);
    canvas.addEventListener('pointerup', onPointerUp);
    canvas.addEventListener('pointercancel', () => {
      S.drag = null;
      canvas.classList.remove('grabbing');
      draw();
    });
    canvas.addEventListener('dblclick', ev => {
      if (S.tool !== 'polygon') return;
      ev.preventDefault();
      if (S.polyDraft.length >= 2) {
        const a = S.polyDraft[S.polyDraft.length - 1];
        const b = S.polyDraft[S.polyDraft.length - 2];
        if (dist(a, b) < 6) S.polyDraft.pop();
      }
      finishPolygon();
    });
    canvas.addEventListener('contextmenu', ev => ev.preventDefault());
    canvas.addEventListener('wheel', ev => {
      ev.preventDefault();
      if (!S.media) return;
      const [sx, sy] = localPoint(ev);
      const factor = Math.exp(-ev.deltaY * 0.0014);
      const ns = clamp(S.view.scale * factor, 0.02, 24);
      const k = ns / S.view.scale;
      S.view.ox = sx - (sx - S.view.ox) * k;
      S.view.oy = sy - (sy - S.view.oy) * k;
      S.view.scale = ns;
      S.view.fitted = false;
      updateZoom();
      draw();
    }, { passive: false });
  }

  function onPointerDown(ev) {
    if (!S.media) return;
    // 标注 / 框选必须绑定稳定的一帧，交互开始即暂停
    if (S.media.kind === 'video' && !mediaVideo.paused) mediaVideo.pause();
    const [sx, sy] = localPoint(ev);
    const world = toWorld(sx, sy);
    S.pointer = world;
    const panButton = ev.button === 1 || ev.button === 2 || S.space || S.tool === 'pan';
    if (panButton) {
      try { canvas.setPointerCapture(ev.pointerId); } catch (_) { /* synthetic */ }
      S.drag = { mode: 'pan', sx, sy, ox: S.view.ox, oy: S.view.oy };
      canvas.classList.add('grabbing');
      return;
    }
    if (ev.button !== 0) return;
    try { canvas.setPointerCapture(ev.pointerId); } catch (_) { /* synthetic */ }

    if (S.tool === 'select') {
      const handle = handleAt(sx, sy);
      if (handle && S.selectedId) {
        const stored = storedItem(S.selectedId);
        if (stored) { startHandleDrag(handle, stored.item, world); return; }
      }
      const hit = hitTest(world);
      if (hit) {
        selectItem(hit.id);
        const stored = storedItem(hit.id);
        if (stored) {
          S.drag = {
            mode: 'move',
            id: hit.id,
            start: world,
            original: stored.item.polygon.map(p => [p[0], p[1]]),
            box: stored.item.box ? Object.assign({}, stored.item.box) : null
          };
        }
        return;
      }
      selectItem(null);
      S.drag = { mode: 'pan', sx, sy, ox: S.view.ox, oy: S.view.oy };
      canvas.classList.add('grabbing');
      return;
    }

    if (S.tool === 'box' || S.tool === 'ai') {
      S.drag = { mode: 'draw', kind: S.tool, start: world, current: world, shift: ev.shiftKey };
      S.draft = { type: S.tool, start: world, current: world };
      updateDrawInfo();
      draw();
      return;
    }

    if (S.tool === 'polygon') {
      S.drag = { mode: 'polyclick', sx, sy, moved: false };
    }
  }

  function onPointerMove(ev) {
    const [sx, sy] = localPoint(ev);
    const world = toWorld(sx, sy);
    S.pointer = world;
    updateCursor(world);

    if (!S.drag) {
      updateHover(world);
      return;
    }

    const drag = S.drag;
    if (drag.mode === 'pan') {
      S.view.ox = drag.ox + (sx - drag.sx);
      S.view.oy = drag.oy + (sy - drag.sy);
      S.view.fitted = false;
      draw();
      return;
    }
    if (drag.mode === 'move') {
      const dx = world[0] - drag.start[0];
      const dy = world[1] - drag.start[1];
      const stored = storedItem(drag.id);
      if (!stored) return;
      stored.item.polygon = drag.original.map(p => [p[0] + dx, p[1] + dy]);
      if (drag.box) {
        stored.item.box = Object.assign({}, drag.box, { cx: drag.box.cx + dx, cy: drag.box.cy + dy });
      }
      stored.item.edited = true;
      draw();
      return;
    }
    if (drag.mode === 'draw') {
      if (ev.shiftKey) drag.shift = true;
      drag.current = world;
      S.draft.current = world;
      updateDrawInfo();
      draw();
      return;
    }
    if (drag.mode === 'polyclick') {
      if (Math.hypot(sx - drag.sx, sy - drag.sy) > 4) drag.moved = true;
      draw();
      return;
    }
    if (drag.mode === 'handle') {
      applyHandleDrag(world);
      draw();
    }
  }

  function onPointerUp(ev) {
    const [sx, sy] = localPoint(ev);
    const world = toWorld(sx, sy);
    const drag = S.drag;
    S.drag = null;
    canvas.classList.remove('grabbing');
    if (!drag) return;

    if (drag.mode === 'draw') {
      S.draft = null;
      const [x0, y0] = drag.start;
      let x1 = world[0], y1 = world[1];
      if (drag.shift) {
        const s = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
        x1 = x0 + (x1 < x0 ? -s : s);
        y1 = y0 + (y1 < y0 ? -s : s);
      }
      const w = Math.abs(x1 - x0), h = Math.abs(y1 - y0);
      if (w < 5 || h < 5) {
        if (drag.kind === 'ai') runSegment({ point: world });
        updateDrawInfo();
        draw();
        return;
      }
      if (drag.kind === 'box') {
        createBox(x0, y0, x1, y1);
      } else {
        runSegment({
          roi: [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)]
        });
      }
      updateDrawInfo();
      return;
    }

    if (drag.mode === 'polyclick' && !drag.moved) {
      addPolyPoint(world);
    }
    if (drag.mode === 'move' || drag.mode === 'handle') {
      renderList();
      renderDetail();
      renderMarkers();
      draw();
    }
  }

  function updateCursor(world) {
    if (!els.cursor) return;
    els.cursor.textContent = 'x ' + fmt(world[0], 1) + ' · y ' + fmt(world[1], 1);
  }

  function updateDrawInfo() {
    if (!els.drawinfo) return;
    if (S.draft) {
      const [x0, y0] = S.draft.start;
      const [x1, y1] = S.draft.current;
      els.drawinfo.textContent =
        fmt(Math.min(x0, x1), 0) + ',' + fmt(Math.min(y0, y1), 0) + ' → ' +
        fmt(Math.max(x0, x1), 0) + ',' + fmt(Math.max(y0, y1), 0) + ' · ' +
        fmt(Math.abs(x1 - x0), 1) + ' × ' + fmt(Math.abs(y1 - y0), 1);
      return;
    }
    if (S.tool === 'polygon' && S.polyDraft.length) {
      els.drawinfo.textContent = '顶点 ' + S.polyDraft.length +
        (S.polyDraft.length >= 3 ? ' · 双击 / Enter 闭合' : ' · 继续单击');
      return;
    }
    els.drawinfo.textContent = '';
  }

  function updateHover(world) {
    if (S.tool !== 'select') {
      if (S.hoverId) { S.hoverId = null; draw(); }
      canvas.style.cursor = '';
      return;
    }
    const hit = hitTest(world);
    const id = hit ? hit.id : null;
    if (id !== S.hoverId) {
      S.hoverId = id;
      draw();
    }
    canvas.style.cursor = id ? 'move' : '';
  }

  function hitTest(world) {
    const items = displayItems();
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i];
      if (!it.polygon || it.polygon.length < 3) continue;
      const bb = window.GEOM.bbox(it.polygon);
      if (world[0] < bb[0] - 3 || world[0] > bb[2] + 3 || world[1] < bb[1] - 3 || world[1] > bb[3] + 3) continue;
      if (window.GEOM.pointInPolygon(world, it.polygon)) return it;
    }
    return null;
  }

  function handleAt(sx, sy) {
    const item = selectedStored();
    if (!item) return null;
    const pts = item.polygon.map(p => toScreen(p[0], p[1]));
    for (let i = 0; i < pts.length; i++) {
      if (Math.hypot(pts[i][0] - sx, pts[i][1] - sy) <= 8) return { type: 'vertex', index: i };
    }
    if (item.shape === 'box') {
      const r = rotateHandle(item);
      if (Math.hypot(r[0] - sx, r[1] - sy) <= 9) return { type: 'rotate' };
    }
    return null;
  }

  function startHandleDrag(handle, item, world) {
    if (handle.type === 'vertex' && item.shape === 'polygon') {
      S.drag = { mode: 'handle', kind: 'vertex', id: item.id, index: handle.index };
      return;
    }
    if (handle.type === 'vertex' && item.shape === 'box') {
      const b = getBox(item);
      const fixed = item.polygon[(handle.index + 2) % 4];
      S.drag = { mode: 'handle', kind: 'resize', id: item.id, fixed, angle: b.angle };
      return;
    }
    if (handle.type === 'rotate') {
      const b = getBox(item);
      S.drag = {
        mode: 'handle', kind: 'rotate', id: item.id,
        center: [b.cx, b.cy], w: b.w, h: b.h, angle0: b.angle,
        a0: Math.atan2(world[1] - b.cy, world[0] - b.cx)
      };
    }
  }

  function applyHandleDrag(world) {
    const drag = S.drag;
    if (!drag || drag.mode !== 'handle') return;
    const stored = storedItem(drag.id);
    if (!stored) return;
    const item = stored.item;

    if (drag.kind === 'vertex') {
      item.polygon[drag.index] = clampPoint(world);
      item.shape = 'polygon';
      item.box = null;
      item.edited = true;
      return;
    }
    if (drag.kind === 'resize') {
      const ux = Math.cos(drag.angle), uy = Math.sin(drag.angle);
      const vx = -uy, vy = ux;
      const rx = world[0] - drag.fixed[0];
      const ry = world[1] - drag.fixed[1];
      const uu = rx * ux + ry * uy;
      const vv = rx * vx + ry * vy;
      const w = Math.max(4, Math.abs(uu));
      const h = Math.max(4, Math.abs(vv));
      const su = uu < 0 ? -1 : 1;
      const sv = vv < 0 ? -1 : 1;
      const cx = drag.fixed[0] + ux * (su * w / 2) + vx * (sv * h / 2);
      const cy = drag.fixed[1] + uy * (su * w / 2) + vy * (sv * h / 2);
      setBox(item, { cx, cy, w, h, angle: drag.angle });
      item.edited = true;
      return;
    }
    if (drag.kind === 'rotate') {
      const a = Math.atan2(world[1] - drag.center[1], world[0] - drag.center[0]);
      let d = a - drag.a0;
      while (d > Math.PI) d -= Math.PI * 2;
      while (d < -Math.PI) d += Math.PI * 2;
      setBox(item, {
        cx: drag.center[0], cy: drag.center[1],
        w: drag.w, h: drag.h, angle: drag.angle0 + d
      });
      item.edited = true;
    }
  }

  /* ------------------------------------------------------------------ create */

  function createBox(x0, y0, x1, y1) {
    const x = Math.min(x0, x1), y = Math.min(y0, y1);
    const w = Math.abs(x1 - x0), h = Math.abs(y1 - y0);
    if (w < 4 || h < 4) return;
    const cx = x + w / 2, cy = y + h / 2;
    const item = {
      id: nextId('a'),
      label: 'ship',
      origin: 'manual',
      shape: 'box',
      polygon: boxPolygon(cx, cy, w, h, 0),
      box: { cx, cy, w, h, angle: 0 },
      score: null,
      frameIndex: currentFrameIndex(),
      edited: true,
      createdAt: Date.now()
    };
    S.annotations.push(item);
    selectItem(item.id);
    renderAll();
    toast('已创建标注 ' + item.id + ' · ' + fmt(w, 1) + ' × ' + fmt(h, 1) + ' px', 'ok');
  }

  function addPolyPoint(world) {
    const p = clampPoint(world);
    if (S.polyDraft.length >= 3 && dist(p, S.polyDraft[0]) < 9) {
      finishPolygon();
      return;
    }
    S.polyDraft.push(p);
    updateDrawInfo();
    draw();
  }

  function finishPolygon() {
    const pts = S.polyDraft.slice();
    if (pts.length >= 2 && dist(pts[0], pts[pts.length - 1]) < 4) pts.pop();
    if (pts.length < 3) {
      toast('多边形至少需要 3 个顶点', 'err');
      return;
    }
    const item = {
      id: nextId('a'),
      label: 'ship',
      origin: 'manual',
      shape: 'polygon',
      polygon: orderPolygon(pts),
      score: null,
      frameIndex: currentFrameIndex(),
      edited: true,
      createdAt: Date.now()
    };
    S.polyDraft = [];
    S.annotations.push(item);
    selectItem(item.id);
    renderAll();
    toast('已创建多边形标注 ' + item.id + ' · ' + pts.length + ' 个顶点', 'ok');
  }

  /* ------------------------------------------------------------------ selection */

  function selectItem(id) {
    S.selectedId = id;
    renderList();
    renderDetail();
    draw();
  }

  function deleteSelected() {
    const stored = storedItem(S.selectedId);
    if (!stored) return;
    const idx = stored.list.indexOf(stored.item);
    if (idx >= 0) stored.list.splice(idx, 1);
    const id = stored.item.id;
    S.selectedId = null;
    renderAll();
    toast('已删除标注 ' + id);
  }

  function centerOnItem(item) {
    if (!item || !item.polygon || !item.polygon.length) return;
    const bb = window.GEOM.bbox(item.polygon);
    const cx = (bb[0] + bb[2]) / 2;
    const cy = (bb[1] + bb[3]) / 2;
    S.view.ox = S.view.w / 2 - cx * S.view.scale;
    S.view.oy = S.view.h / 2 - cy * S.view.scale;
    S.view.fitted = false;
    draw();
  }

  /* ------------------------------------------------------------------ detection */

  function captureFrame() {
    const m = S.media;
    const full = document.createElement('canvas');
    full.width = m.width;
    full.height = m.height;
    const fx = full.getContext('2d');
    try {
      if (m.kind === 'image') fx.drawImage(mediaImage, 0, 0);
      else fx.drawImage(mediaVideo, 0, 0, m.width, m.height);
    } catch (err) {
      throw new Error('无法读取当前画面：' + err.message);
    }

    const maxEdge = Number(VIDEO_CFG.maxPayloadEdge) || 1600;
    const s = Math.min(1, maxEdge / Math.max(m.width, m.height));
    let payload = full;
    if (s < 1) {
      payload = document.createElement('canvas');
      payload.width = Math.max(1, Math.round(m.width * s));
      payload.height = Math.max(1, Math.round(m.height * s));
      const px = payload.getContext('2d');
      px.imageSmoothingQuality = 'high';
      px.drawImage(full, 0, 0, payload.width, payload.height);
    }

    let dataUrl = '';
    if (window.DetectAPI.mode === 'http') {
      try {
        dataUrl = payload.toDataURL('image/jpeg',
          Number(CFG.mock && CFG.mock.jpegQuality) || 0.86);
      } catch (err) {
        throw new Error('画布被跨源内容污染，无法编码帧；请通过 http 访问页面');
      }
    }
    return { canvas: full, data_url: dataUrl, width: payload.width, height: payload.height };
  }

  async function runDetection(opts) {
    opts = opts || {};
    if (!S.media || S.busy) return;
    if (S.media.kind === 'video' && !opts.auto && !mediaVideo.paused) mediaVideo.pause();

    const idx = currentFrameIndex();
    const t = Math.round(currentTimeMs());
    let frame;
    try {
      frame = captureFrame();
    } catch (err) {
      toast(err.message, 'err');
      if (opts.auto) {
        S.auto = false;
        renderVideoBar();
      }
      return;
    }

    setBusy('detect', opts.auto ? '连续检测中…' : '检测中…', !opts.auto);
    try {
      await ensureSession();
      const res = await window.DetectAPI.detect({
        session_id: S.sessionId,
        kind: S.media.kind,
        frame: {
          index: idx, time_ms: t,
          width: S.media.width, height: S.media.height,
          canvas: frame.canvas
        },
        image: { data_url: frame.data_url, width: frame.width, height: frame.height },
        options: {
          mode: opts.mode || 'full',
          conf: 0.25,
          max_det: 80,
          prompt: S.ai.prompt
        }
      });

      const dets = (res.detections || []).map((d, i) => normalizeDetection(d, i, idx, res)).filter(Boolean);
      S.detections.set(idx, dets);
      S.lastDetect = {
        count: dets.length,
        elapsed: res.elapsed_ms,
        model: res.model,
        requestId: res.request_id,
        frame: idx,
        at: Date.now()
      };

      if (!opts.auto) {
        if (dets.length) toast('检出 ' + dets.length + ' 个目标 · ' + Math.round(res.elapsed_ms || 0) + ' ms', 'ok');
        else toast('未检出目标，可尝试 AI 辅助框选或手动画框');
      }
      renderHeader();
      renderList();
      renderMarkers();
      if (!opts.auto) renderDetail();
      draw();
    } catch (err) {
      toastError(err);
      if (opts.auto) {
        S.auto = false;
        renderVideoBar();
        toast('连续检测已自动停止，请检查接口后重试', 'err');
      }
    } finally {
      setBusy(null);
      renderHeader();
    }
  }

  function normalizeDetection(d, i, idx, res) {
    const poly = (d.polygon || [])
      .map(p => [Number(p[0]), Number(p[1])])
      .filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1]));
    if (poly.length < 3) return null;
    const score = typeof d.score === 'number' ? d.score
      : (typeof d.confidence === 'number' ? d.confidence : null);
    return {
      id: d.id ? 'm' + String(d.id) : 'm' + idx + '_' + (i + 1),
      label: d.label || 'ship',
      origin: 'model',
      shape: poly.length === 4 ? 'box' : 'polygon',
      polygon: poly,
      box: poly.length === 4 ? boxParams(poly) : null,
      score,
      frameIndex: idx,
      requestId: res && res.request_id,
      model: res && res.model ? res.model.name : null,
      edited: false
    };
  }

  function clearFrameResults() {
    if (!S.media) return;
    const idx = currentFrameIndex();
    if (!S.detections.has(idx)) {
      toast('当前帧没有模型检测结果');
      return;
    }
    S.detections.delete(idx);
    if (S.selectedId && !storedItem(S.selectedId)) S.selectedId = null;
    renderList();
    renderDetail();
    renderMarkers();
    draw();
    toast('已清除第 ' + idx + ' 帧的模型结果');
  }

  /* ------------------------------------------------------------------ AI */

  async function runSegment(opts) {
    opts = opts || {};
    if (!S.media || S.busy) return;
    if (S.media.kind === 'video' && !mediaVideo.paused) mediaVideo.pause();

    const idx = currentFrameIndex();
    const t = Math.round(currentTimeMs());
    let frame;
    try {
      frame = captureFrame();
    } catch (err) {
      toast(err.message, 'err');
      return;
    }

    S.ai.status = 'busy';
    S.ai.roi = opts.roi || null;
    S.ai.candidates = [];
    S.ai.error = null;
    S.ai.lastReq = { roi: opts.roi || null, point: opts.point || null };
    S.ai.meta = null;
    setBusy('segment', 'AI 分割中…');
    renderAi();
    draw();

    try {
      await ensureSession();
      const res = await window.DetectAPI.segment({
        session_id: S.sessionId,
        kind: S.media.kind,
        frame: {
          index: idx, time_ms: t,
          width: S.media.width, height: S.media.height,
          canvas: frame.canvas
        },
        image: { data_url: frame.data_url, width: frame.width, height: frame.height },
        roi: opts.roi || null,
        point: opts.point || null,
        prompt: S.ai.prompt || 'ship',
        mode: opts.roi ? 'roi' : 'point',
        options: { conf: 0.25 }
      });

      if (currentFrameIndex() !== idx) {
        S.ai.status = 'idle';
        S.ai.candidates = [];
        toast('帧已切换，本次 AI 候选已丢弃');
        return;
      }
      const candidates = (res.candidates || []).filter(c =>
        c && Array.isArray(c.polygon) && c.polygon.length >= 3);
      if (!candidates.length) {
        const err = new Error('分割服务没有返回候选');
        err.code = 'NO_CANDIDATE';
        throw err;
      }
      S.ai.status = 'preview';
      S.ai.candidates = candidates;
      S.ai.active = 0;
      S.ai.meta = res;
      toast('AI 返回 ' + candidates.length + ' 个候选，采纳后计入标注', 'ok');
    } catch (err) {
      S.ai.status = 'error';
      S.ai.error = err;
      toastError(err);
    } finally {
      setBusy(null);
      renderAi();
      draw();
    }
  }

  function acceptCandidate() {
    const cand = S.ai.candidates[S.ai.active];
    if (!cand) return;
    const poly = cand.polygon.map(p => clampPoint(p));
    const item = {
      id: nextId('a'),
      label: cand.label || S.ai.prompt || 'ship',
      origin: 'ai',
      shape: poly.length === 4 ? 'box' : 'polygon',
      polygon: orderPolygon(poly),
      box: poly.length === 4 ? boxParams(orderPolygon(poly)) : null,
      score: null,
      confidence: null,
      quality: Number.isFinite(cand.quality) ? cand.quality : null,
      reason: cand.reason || '',
      frameIndex: currentFrameIndex(),
      requestId: S.ai.meta && S.ai.meta.request_id,
      model: S.ai.meta && S.ai.meta.model ? S.ai.meta.model.name : null,
      edited: false,
      createdAt: Date.now()
    };
    S.annotations.push(item);
    clearAi();
    setTool('select');
    selectItem(item.id);
    renderAll();
    toast('已采纳 AI 候选 ' + item.id + '，来源标记为 AI 辅助', 'ok');
  }

  function clearAi() {
    S.ai = {
      status: 'idle', roi: null, candidates: [], active: 0,
      meta: null, error: null, prompt: S.ai.prompt || '船舶', lastReq: null
    };
    renderAi();
    draw();
  }

  /* ------------------------------------------------------------------ render */

  function renderAll() {
    renderHeader();
    renderMeta();
    renderList();
    renderDetail();
    renderAi();
    renderVideoBar();
    renderMarkers();
    updateButtons();
    updateZoom();
    draw();
  }

  function renderHeader() {
    const m = S.media;
    els.media.textContent = m ? m.name : '未载入';
    els.res.textContent = m ? m.width + ' × ' + m.height : '—';
    if (!m) {
      els.frame.textContent = '—';
    } else if (m.kind === 'image') {
      els.frame.textContent = '单幅';
    } else {
      els.frame.textContent = '#' + currentFrameIndex() + ' · ' + timeStr(currentTimeMs());
    }
    const mode = window.DetectAPI.mode;
    els.api.textContent = mode === 'mock' ? 'mock · 本地' : 'http';
    els.api.title = window.DetectAPI.baseUrl || '';
    els.aiMode.textContent = mode;
    els.modeBadge.textContent = mode === 'mock' ? '演示推理' : '真实接口';
    els.modeBadge.className = 'badge ' + (mode === 'mock' ? 'demo' : 'live');
  }

  function renderMeta() {
    const m = S.media;
    if (!m) {
      els.meta.innerHTML = '<dt>状态</dt><dd class="dim">等待载入</dd>';
      return;
    }
    const rows = [
      ['状态', '已载入'],
      ['类型', m.kind === 'image' ? '图片' : '视频'],
      ['尺寸', m.width + ' × ' + m.height],
      ['来源', m.demo ? '示例素材' : '本地文件']
    ];
    if (m.kind === 'video') {
      rows.push(['时长', timeStr(m.duration)]);
      rows.push(['帧率', m.fps + ' fps']);
    }
    els.meta.innerHTML = rows.map(([k, v]) =>
      '<dt>' + k + '</dt><dd' + (k === '尺寸' || k === '时长' ? ' class="mono"' : '') + '>' + esc(v) + '</dd>'
    ).join('');
  }

  function renderList() {
    if (!S.media) {
      els.list.innerHTML = '<div class="dt-list-empty">载入图片或视频后<br>这里显示检测与标注结果</div>';
      els.count.textContent = '0';
      els.export.disabled = true;
      return;
    }
    const all = displayItems();
    const items = all.filter(it => S.filter === 'all' || it.origin === S.filter);
    const keepScroll = els.list.scrollTop;
    els.count.textContent = String(all.length);
    if (!items.length) {
      els.list.innerHTML = '<div class="dt-list-empty">' +
        (all.length ? '当前筛选下没有目标' : '当前帧还没有结果<br>点击「检测当前帧」或手动画框') +
        '</div>';
    } else {
      els.list.innerHTML = items.map(it => {
        const bb = window.GEOM.bbox(it.polygon);
        const title = (it.label || 'ship') + ' · ' + itemScore(it) +
          ' · ' + fmt(bb[0], 0) + ',' + fmt(bb[1], 0);
        return '<div class="dt-item' + (it.id === S.selectedId ? ' sel' : '') +
          (it.held ? ' held' : '') + '" data-id="' + esc(it.id) + '" title="' + esc(title) + '">' +
          '<span class="sw" style="background:' + itemColor(it) + '"></span>' +
          '<span class="id">' + esc(it.id) + '</span>' +
          '<span class="lb">' + esc(it.label || 'ship') + '</span>' +
          '<span class="src">' + (ORIGIN_LABEL[it.origin] || it.origin) + '</span>' +
          '<span class="cf">' + itemScore(it) + '</span>' +
          '</div>';
      }).join('');
    }
    els.export.disabled = !(S.annotations.length || S.detections.size);
    els.list.scrollTop = keepScroll;
    els.tabs.querySelectorAll('button').forEach(b =>
      b.classList.toggle('on', b.dataset.f === S.filter));
  }

  function renderDetail() {
    const item = selectedStored();
    const tag = els.selTag;
    if (!item) {
      tag.classList.add('hidden');
      const m = S.media;
      let extra = '';
      if (m) {
        const idx = currentFrameIndex();
        const dets = S.detections.get(idx) || [];
        const anns = S.annotations.filter(a => m.kind === 'image' || a.frameIndex === idx);
        extra = '<span class="mono">当前帧 #' + idx + ' · ' + timeStr(frameTimeMs(idx)) + '</span><br>' +
          '<span class="mono">模型 ' + dets.length + ' · 手工/AI ' + anns.length + '</span>';
      }
      els.detail.innerHTML = '<div class="dt-detail-empty">' +
        '<b>未选择目标</b><br>' +
        '在画布或左侧列表选择一个框，查看四角坐标、中心、尺寸与面积。<br><br>' +
        extra + '</div>';
      return;
    }

    tag.textContent = '#' + item.id;
    tag.classList.remove('hidden');
    const poly = item.polygon;
    const bb = window.GEOM.bbox(poly);
    const area = window.GEOM.shoelace(poly);
    const b = item.shape === 'box' ? getBox(item) : null;
    const center = b ? [b.cx, b.cy] : window.GEOM.centroid(poly);
    const size = b ? [b.w, b.h] : [bb[2] - bb[0], bb[3] - bb[1]];
    const angle = b ? normAngleDeg(b.angle * 180 / Math.PI) : null;
    const m = S.media;
    const frameLine = m && m.kind === 'video'
      ? '#' + item.frameIndex + ' · ' + timeStr(frameTimeMs(item.frameIndex))
      : '整幅图片';

    const source = ORIGIN_LABEL[item.origin] || item.origin;
    let scoreLine = '—';
    if (item.origin === 'model' && Number.isFinite(item.score)) scoreLine = fmt(item.score, 3) + '（模型输出）';
    if (item.origin === 'ai') scoreLine = 'null（AI 候选不伪造置信度）';

    let editor = '';
    if (item.shape === 'box' && b) {
      editor = '<div class="dt-sub">框参数<span class="spacer"></span><span>可精确编辑</span></div>' +
        '<div class="dt-grid">' +
        field('中心 X', 'cx', fmt(b.cx, 1)) +
        field('中心 Y', 'cy', fmt(b.cy, 1)) +
        field('宽 W', 'w', fmt(b.w, 1)) +
        field('高 H', 'h', fmt(b.h, 1)) +
        field('角度 °', 'angle', fmt(angle, 1)) +
        '</div>';
    }

    let vertexTable;
    if (item.shape === 'polygon') {
      vertexTable = '<table class="dt-tbl"><thead><tr><th>#</th><th>X</th><th>Y</th></tr></thead><tbody>' +
        poly.map((p, i) =>
          '<tr><td>' + (i + 1) + '</td>' +
          '<td><input class="dt-num" step="0.1" data-vi="' + i + '" data-axis="0" value="' + fmt(p[0], 1) + '"></td>' +
          '<td><input class="dt-num" step="0.1" data-vi="' + i + '" data-axis="1" value="' + fmt(p[1], 1) + '"></td>' +
          '</tr>').join('') +
        '</tbody></table>';
    } else {
      vertexTable = '<table class="dt-tbl"><thead><tr><th>#</th><th>X</th><th>Y</th></tr></thead><tbody>' +
        poly.map((p, i) =>
          '<tr><td>' + (i + 1) + '</td><td class="mono">' + fmt(p[0], 1) + '</td><td class="mono">' + fmt(p[1], 1) + '</td></tr>'
        ).join('') +
        '</tbody></table>';
    }

    els.detail.innerHTML =
      '<dl class="dt-kv">' +
      '<dt>标签</dt><dd>' + esc(item.label || 'ship') + '</dd>' +
      '<dt>形状</dt><dd>' + (item.shape === 'box' ? '旋转框' : '多边形') + '</dd>' +
      '<dt>来源</dt><dd>' + source + '</dd>' +
      '<dt>帧</dt><dd>' + frameLine + '</dd>' +
      '<dt>置信度</dt><dd>' + scoreLine + '</dd>' +
      (item.origin === 'ai' ? '<dt>质量分</dt><dd>' + (Number.isFinite(item.quality) ? fmt(item.quality, 3) : '—') + '</dd>' : '') +
      (item.requestId ? '<dt>请求 ID</dt><dd>' + esc(item.requestId) + '</dd>' : '') +
      (item.edited ? '<dt>状态</dt><dd>已人工编辑</dd>' : '') +
      '</dl>' +
      editor +
      '<div class="dt-sub">顶点坐标（原图像素）<span class="spacer"></span><span>顺时针 · 起点 min(y,x)</span></div>' +
      vertexTable +
      '<div class="dt-actions">' +
      '<button class="btn sm" data-act="copy">复制 JSON</button>' +
      '<button class="btn sm" data-act="center">定位</button>' +
      '<button class="btn sm danger" data-act="delete">删除</button>' +
      '</div>' +
      '<div class="dt-sub">几何量</div>' +
      '<dl class="dt-kv">' +
      '<dt>中心</dt><dd>' + fmt(center[0], 1) + ', ' + fmt(center[1], 1) + '</dd>' +
      '<dt>尺寸</dt><dd>' + fmt(size[0], 1) + ' × ' + fmt(size[1], 1) + ' px</dd>' +
      (angle != null ? '<dt>角度</dt><dd>' + fmt(angle, 1) + '°</dd>' : '') +
      '<dt>包围盒</dt><dd>' + fmt(bb[0], 1) + ', ' + fmt(bb[1], 1) + ' → ' + fmt(bb[2], 1) + ', ' + fmt(bb[3], 1) + '</dd>' +
      '<dt>面积</dt><dd>' + fmt(area, 0) + ' px²</dd>' +
      '</dl>' +
      '<div class="dt-note">坐标为原始媒体像素，原点左上，x 向右、y 向下。' +
      (m && m.kind === 'video' ? '标注绑定当前帧，切换帧后只显示该帧的标注。' : '') +
      '</div>';
  }

  function field(label, key, value) {
    return '<label>' + label +
      '<input class="dt-num" step="0.1" data-edit="' + key + '" value="' + value + '"></label>';
  }

  function renderAi() {
    const ai = S.ai;
    const mode = window.DetectAPI.mode;
    const segEndpoint = (window.DetectAPI.config.endpoints || {}).segment || '/vision/segment';
    const hasMedia = !!S.media;

    if (ai.status === 'busy') {
      els.ai.innerHTML =
        '<div class="dt-ai-status"><span class="dt-dot busy"></span><span>正在请求分割服务…</span></div>' +
        '<div class="dt-ai-busy"><span class="spinner"></span><span>' +
        (ai.roi ? '分析框选区域' : '分析目标附近') + '</span></div>' +
        '<div class="dt-ai-meta">POST ' + esc(segEndpoint) + ' · ' + mode + '</div>';
      return;
    }

    if (ai.status === 'preview') {
      const cards = ai.candidates.map((c, i) => {
        const bb = c.polygon && c.polygon.length ? window.GEOM.bbox(c.polygon) : [0, 0, 0, 0];
        return '<div class="dt-cand' + (i === ai.active ? ' on' : '') + '" data-ai-act="pick" data-i="' + i + '">' +
          '<div class="t"><b>候选 ' + (i + 1) + '</b>' +
          '<span class="tag ai">质量 ' + fmt(c.quality, 2) + '</span></div>' +
          '<div class="meta">' + c.polygon.length + ' 点 · 中心 ' +
          fmt((bb[0] + bb[2]) / 2, 1) + ', ' + fmt((bb[1] + bb[3]) / 2, 1) +
          ' · ' + fmt(bb[2] - bb[0], 1) + ' × ' + fmt(bb[3] - bb[1], 1) + ' px</div>' +
          '<div class="why">' + esc(c.reason || '待人工确认') + '</div>' +
          '</div>';
      }).join('');
      els.ai.innerHTML =
        '<div class="dt-ai-status"><span class="dt-dot ready"></span><span>收到 ' +
        ai.candidates.length + ' 个候选 · 尚未采纳</span></div>' +
        cards +
        '<div class="dt-ai-actions">' +
        '<button class="btn primary sm" data-ai-act="accept">采纳候选</button>' +
        '<button class="btn sm" data-ai-act="retry">重试</button>' +
        '<button class="btn sm" data-ai-act="cancel">取消</button>' +
        '</div>' +
        '<div class="dt-ai-meta">' +
        esc((ai.meta && ai.meta.request_id) || '') + ' · ' +
        Math.round((ai.meta && ai.meta.elapsed_ms) || 0) + ' ms · ' +
        esc((ai.meta && ai.meta.model && ai.meta.model.name) || mode) +
        (ai.meta && ai.meta.notice ? '<br>' + esc(ai.meta.notice) : '') +
        '</div>';
      return;
    }

    if (ai.status === 'error') {
      els.ai.innerHTML =
        '<div class="dt-ai-status"><span class="dt-dot err"></span><span>未返回候选</span></div>' +
        '<div class="dt-ai-err">' + esc(ai.error && ai.error.message || '分割失败') +
        (ai.error && ai.error.code ? '<br><span class="dim">' + esc(ai.error.code) + '</span>' : '') +
        '</div>' +
        '<div class="dt-ai-actions">' +
        '<button class="btn sm primary" data-ai-act="retry">重试</button>' +
        '<button class="btn sm" data-ai-act="manual">改手动画框</button>' +
        '</div>' +
        '<div class="dt-ai-meta">POST ' + esc(segEndpoint) + ' · ' + mode + '</div>';
      return;
    }

    els.ai.innerHTML =
      '<div class="dt-ai-status"><span class="dt-dot ' + (hasMedia ? 'ready' : '') + '"></span>' +
      '<span>' + (hasMedia ? '就绪 · 框选目标后由接口返回候选' : '等待载入媒体') + '</span></div>' +
      '<div class="dt-ai-field"><label for="dt-ai-prompt">提示词（传给分割模型）</label>' +
      '<input id="dt-ai-prompt" value="' + esc(ai.prompt) + '" placeholder="例如：船舶"></div>' +
      '<div class="dt-ai-actions">' +
      '<button class="btn primary sm" data-ai-act="roi"' + (hasMedia ? '' : ' disabled') + '>在图上框选</button>' +
      '<button class="btn sm" data-ai-act="full"' + (hasMedia ? '' : ' disabled') + '>整帧检测</button>' +
      '</div>' +
      '<p class="dt-ai-hint">选择「AI 辅助」后在目标周围拖一个粗框；也可直接点目标附近。' +
      '候选必须人工采纳，不会自动写入结果。</p>' +
      '<div class="dt-ai-meta">POST ' + esc(segEndpoint) + ' · ' + mode +
      '<br>候选 confidence = null，仅给 quality 质量分</div>';
  }

  function renderVideoBar() {
    const isVideo = S.media && S.media.kind === 'video';
    els.vbar.classList.toggle('hidden', !isVideo);
    if (!isVideo) return;
    const dur = Math.max(1, Math.round(S.media.duration || 0));
    els.seek.max = String(dur);
    if (!S.seekDragging) els.seek.value = String(Math.round(currentTimeMs()));
    els.time.textContent = timeStr(currentTimeMs()) + ' / ' + timeStr(S.media.duration);
    els.play.textContent = mediaVideo.paused ? '播放' : '暂停';
    if (document.activeElement !== els.fps) els.fps.value = String(S.media.fps || 30);
    els.auto.classList.toggle('on', S.auto);
    els.autoState.textContent = S.auto
      ? '连续检测 · 每 ' + ((Number(VIDEO_CFG.autoIntervalMs) || 900) / 1000).toFixed(1) + 's'
      : '连续检测关闭';
    els.autoState.className = 'tag' + (S.auto ? ' ai' : '');
  }

  function renderMarkers() {
    if (!S.media || S.media.kind !== 'video' || !S.media.duration) {
      els.markers.innerHTML = '';
      return;
    }
    const total = S.media.duration;
    const parts = [];
    for (const [idx, arr] of S.detections) {
      if (!arr.length) continue;
      const left = (frameTimeMs(idx) / total) * 100;
      if (left < 0 || left > 100) continue;
      parts.push('<i class="dt-marker" style="left:' + left.toFixed(3) + '%" data-frame="' + idx +
        '" title="第 ' + idx + ' 帧 · ' + arr.length + ' 个模型结果"></i>');
    }
    const frames = new Set(S.annotations.map(a => a.frameIndex));
    for (const idx of frames) {
      const left = (frameTimeMs(idx) / total) * 100;
      if (left < 0 || left > 100) continue;
      parts.push('<i class="dt-marker manual" style="left:' + left.toFixed(3) + '%" data-frame="' + idx +
        '" title="第 ' + idx + ' 帧 · 含手工/AI 标注"></i>');
    }
    els.markers.innerHTML = parts.join('');
  }

  function updateButtons() {
    const has = !!S.media;
    const busy = !!S.busy;
    const isVideo = has && S.media.kind === 'video';
    els.detect.disabled = !has || busy;
    els.auto.disabled = !has || !isVideo;
    els.clear.disabled = !has;
    els.fit.disabled = !has;
    els.one.disabled = !has;
    els.labels.disabled = !has;
    els.coords.disabled = !has;
    els.hold.disabled = !isVideo;
    els.export.disabled = !has || (!S.annotations.length && !S.detections.size);
    els.labels.classList.toggle('on', S.show.labels);
    els.coords.classList.toggle('on', S.show.coords);
    els.hold.classList.toggle('on', S.show.hold);
  }

  function setBusy(kind, text, overlay) {
    S.busy = kind || null;
    if (overlay !== false) {
      els.busy.classList.toggle('hidden', !kind);
      if (text) els.busyText.textContent = text;
    } else {
      els.busy.classList.add('hidden');
    }
    updateButtons();
  }

  /* ------------------------------------------------------------------ video controls */

  function bindVideo() {
    mediaVideo.addEventListener('play', () => { els.play.textContent = '暂停'; });
    mediaVideo.addEventListener('pause', () => { els.play.textContent = '播放'; });
    mediaVideo.addEventListener('seeked', () => {
      renderHeader();
      renderList();
      renderDetail();
      renderVideoBar();
      draw();
    });
    mediaVideo.addEventListener('timeupdate', () => {
      if (S.media && S.media.kind === 'video') {
        if (!S.seekDragging) {
          els.seek.value = String(Math.round(currentTimeMs()));
          els.time.textContent = timeStr(currentTimeMs()) + ' / ' + timeStr(S.media.duration);
          els.frame.textContent = '#' + currentFrameIndex() + ' · ' + timeStr(currentTimeMs());
        }
      }
    });

    els.play.onclick = togglePlay;
    els.prev.onclick = () => stepFrame(-1);
    els.next.onclick = () => stepFrame(1);
    els.seek.addEventListener('input', () => {
      if (!S.media || S.media.kind !== 'video') return;
      S.seekDragging = true;
      mediaVideo.currentTime = Number(els.seek.value) / 1000;
      els.time.textContent = timeStr(currentTimeMs()) + ' / ' + timeStr(S.media.duration);
    });
    els.seek.addEventListener('change', () => { S.seekDragging = false; });
    els.speed.onchange = () => {
      mediaVideo.playbackRate = Number(els.speed.value) || 1;
    };
    els.fps.onchange = () => {
      if (!S.media) return;
      if (S.busy) {
        toast('请等待当前检测完成后再修改帧率', 'err');
        renderVideoBar();
        return;
      }
      const next = clamp(parseInt(els.fps.value, 10) || 30, 1, 120);
      if (next === S.media.fps) return;
      S.media.fps = next;
      clearAi();
      // 结果与标注都按帧号绑定，改帧率后旧帧号不再对应同一时刻，必须清空
      if (S.detections.size || S.annotations.length) {
        S.detections.clear();
        S.annotations = [];
        S.selectedId = null;
        S.lastDetect = null;
        toast('帧率已更新，按帧绑定的检测与标注已清空');
      }
      renderAll();
    };
    els.markers.addEventListener('click', ev => {
      const marker = ev.target.closest('.dt-marker');
      if (!marker || !S.media) return;
      seekToFrame(Number(marker.dataset.frame));
    });
  }

  function togglePlay() {
    if (!S.media || S.media.kind !== 'video') return;
    if (mediaVideo.paused) {
      if (mediaVideo.ended) mediaVideo.currentTime = 0;
      mediaVideo.play().catch(err => toast('无法播放：' + err.message, 'err'));
    } else {
      mediaVideo.pause();
    }
  }

  function stepFrame(delta) {
    if (!S.media || S.media.kind !== 'video') return;
    mediaVideo.pause();
    const max = Math.max(0, (S.media.duration || 0) / 1000 - 1 / fps());
    mediaVideo.currentTime = clamp(mediaVideo.currentTime + delta / fps(), 0, max);
  }

  function seekToFrame(index) {
    if (!S.media || S.media.kind !== 'video') return;
    const max = Math.max(0, (S.media.duration || 0) / 1000 - 1 / fps());
    mediaVideo.currentTime = clamp(index / fps(), 0, max);
  }

  /* ------------------------------------------------------------------ loop */

  function loop(ts) {
    if (S.media && S.media.kind === 'video') {
      const idx = currentFrameIndex();
      if (idx !== S.lastFrameIndex) {
        S.lastFrameIndex = idx;
        renderHeader();
        if (ts - S.lastListAt > 250) {
          S.lastListAt = ts;
          renderList();
        }
        if (S.selectedId && !displayItems().some(x => x.id === S.selectedId)) {
          S.selectedId = null;
          renderDetail();
          renderList();
        }
        // 候选绑定的是请求时的帧，换帧后必须丢弃，避免把旧轮廓画到新画面上
        if (S.ai.status === 'preview' || S.ai.status === 'error') clearAi();
      }
      if (!mediaVideo.paused) {
        draw();
        if (ts - S.lastTimeAt > 80) {
          S.lastTimeAt = ts;
          if (!S.seekDragging) {
            els.seek.value = String(Math.round(currentTimeMs()));
            els.time.textContent = timeStr(currentTimeMs()) + ' / ' + timeStr(S.media.duration);
          }
        }
        maybeAutoDetect(ts);
      }
    }
    requestAnimationFrame(loop);
  }

  function maybeAutoDetect(ts) {
    if (!S.auto || S.busy || !S.media || S.media.kind !== 'video') return;
    if (mediaVideo.paused) return;
    const interval = Number(VIDEO_CFG.autoIntervalMs) || 900;
    if (ts - S.lastAutoAt < interval) return;
    S.lastAutoAt = ts;
    runDetection({ auto: true });
  }

  /* ------------------------------------------------------------------ bind UI */

  function bindMedia() {
    els.pick.onclick = () => els.file.click();
    els.file.onchange = () => {
      const f = els.file.files && els.file.files[0];
      els.file.value = '';
      if (f) handleFile(f);
    };
    els.demoImg.onclick = () => {
      loadImageSource('assets/harbor.png', 'harbor.png', { demo: true })
        .catch(err => toast(err.message, 'err'));
    };
    els.demoVideo.onclick = makeDemoVideo;

    const stop = ev => { ev.preventDefault(); ev.stopPropagation(); };
    [els.drop, els.stage].forEach(el => {
      el.addEventListener('dragover', ev => {
        ev.preventDefault();
        if (el === els.drop) el.classList.add('hot');
        else el.classList.add('dragover');
      });
      el.addEventListener('dragleave', ev => {
        if (el === els.drop) el.classList.remove('hot');
        else el.classList.remove('dragover');
      });
      el.addEventListener('drop', ev => {
        stop(ev);
        el.classList.remove('hot');
        el.classList.remove('dragover');
        const f = ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0];
        if (f) handleFile(f);
      });
    });
  }

  async function handleFile(file) {
    const isVideo = /^video\//.test(file.type) || /\.(mp4|webm|mov|m4v|ogv)$/i.test(file.name);
    const isImage = /^image\//.test(file.type) || /\.(png|jpe?g|webp|bmp)$/i.test(file.name);
    if (!isImage && !isVideo) {
      toast('仅支持图片或视频文件', 'err');
      return;
    }
    if (file.size > 300 * 1024 * 1024) {
      toast('文件超过 300 MB，请先压缩或裁切', 'err');
      return;
    }
    const url = URL.createObjectURL(file);
    try {
      if (isVideo) await loadVideoSource(url, file.name, { owned: true });
      else await loadImageSource(url, file.name, { owned: true });
    } catch (err) {
      URL.revokeObjectURL(url);
      toast(err.message, 'err');
    }
  }

  function bindTools() {
    $$('#dt-tools .tool').forEach(btn => {
      btn.onclick = () => setTool(btn.dataset.tool);
    });
    els.fit.onclick = () => { fitView(); draw(); };
    els.one.onclick = oneToOne;
    els.labels.onclick = () => {
      S.show.labels = !S.show.labels;
      updateButtons();
      draw();
    };
    els.coords.onclick = () => {
      S.show.coords = !S.show.coords;
      updateButtons();
      draw();
    };
    els.hold.onclick = () => {
      S.show.hold = !S.show.hold;
      if (S.selectedId && !displayItems().some(x => x.id === S.selectedId)) S.selectedId = null;
      updateButtons();
      renderList();
      renderDetail();
      draw();
    };
    els.detect.onclick = () => runDetection();
    els.auto.onclick = () => {
      if (!S.media || S.media.kind !== 'video') return;
      S.auto = !S.auto;
      S.lastAutoAt = 0;
      renderVideoBar();
      if (S.auto && mediaVideo.paused) togglePlay();
      toast(S.auto ? '连续检测已开启' : '连续检测已关闭');
    };
    els.clear.onclick = clearFrameResults;
    els.export.onclick = exportAnnotations;
    els.tabs.addEventListener('click', ev => {
      const b = ev.target.closest('button[data-f]');
      if (!b) return;
      S.filter = b.dataset.f;
      renderList();
    });
    els.list.addEventListener('click', ev => {
      const row = ev.target.closest('.dt-item');
      if (!row) return;
      selectItem(row.dataset.id);
    });
  }

  function setTool(tool) {
    S.tool = tool;
    S.draft = null;
    S.polyDraft = [];
    S.drag = null;
    if (tool !== 'ai' && S.ai.status === 'preview') clearAi();
    $$('#dt-tools .tool').forEach(b => b.classList.toggle('on', b.dataset.tool === tool));
    canvas.classList.remove('grab', 'grabbing', 'selecting', 'poly');
    if (tool === 'select') canvas.classList.add('selecting');
    else if (tool === 'pan') canvas.classList.add('grab');
    else if (tool === 'polygon') canvas.classList.add('poly');
    els.hint.textContent = {
      select: '点击选择 · 拖动移动 · 拖动顶点 / 手柄编辑',
      box: '拖动创建旋转框；按住 Shift 约束正方形',
      polygon: '逐点单击；双击或 Enter 闭合；Esc 取消',
      ai: '在目标周围拖一个粗框，交给 AI 细化',
      pan: '拖动画布 · 滚轮缩放'
    }[tool] || '';
    updateDrawInfo();
    draw();
  }

  function bindDetail() {
    els.detail.addEventListener('change', onDetailChange);
    els.detail.addEventListener('click', ev => {
      const btn = ev.target.closest('[data-act]');
      if (!btn) return;
      const act = btn.dataset.act;
      const item = selectedStored();
      if (!item) return;
      if (act === 'copy') copyItemJson(item);
      else if (act === 'center') centerOnItem(item);
      else if (act === 'delete') deleteSelected();
    });
  }

  function onDetailChange(ev) {
    const input = ev.target;
    const item = selectedStored();
    if (!item) return;
    const value = parseFloat(input.value);
    if (!Number.isFinite(value)) {
      renderDetail();
      return;
    }

    if (input.dataset.edit) {
      const key = input.dataset.edit;
      const b = Object.assign({}, getBox(item));
      if (key === 'angle') b.angle = value * Math.PI / 180;
      else b[key] = value;
      b.w = Math.max(2, b.w);
      b.h = Math.max(2, b.h);
      setBox(item, b);
      item.shape = 'box';
      item.edited = true;
      afterEdit();
      return;
    }
    if (input.dataset.vi !== undefined) {
      const vi = Number(input.dataset.vi);
      const axis = Number(input.dataset.axis);
      const p = item.polygon[vi];
      if (!p) return;
      p[axis] = clamp(value, 0, axis === 0 ? S.media.width : S.media.height);
      item.box = null;
      item.edited = true;
      afterEdit();
    }
  }

  function afterEdit() {
    renderList();
    renderMarkers();
    renderDetail();
    draw();
  }

  function copyItemJson(item) {
    const text = JSON.stringify(itemJson(item), null, 2);
    const done = () => toast('坐标 JSON 已复制', 'ok');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (_) { toast('复制失败，请手动选择文本', 'err'); }
    ta.remove();
  }

  function itemJson(item) {
    const bb = window.GEOM.bbox(item.polygon);
    const b = item.shape === 'box' ? getBox(item) : null;
    const c = b ? [b.cx, b.cy] : window.GEOM.centroid(item.polygon);
    const m = S.media;
    return {
      schema: 'ship-annotation/1.0',
      id: item.id,
      label: item.label,
      shape: item.shape,
      origin: item.origin,
      frame: m && m.kind === 'video'
        ? { index: item.frameIndex, time_ms: Math.round(frameTimeMs(item.frameIndex)), fps: fps() }
        : null,
      coordinate_space: {
        unit: 'pixel', origin: 'top-left', x_axis: 'right', y_axis: 'down',
        space: 'original_media'
      },
      polygon: item.polygon.map(p => [Number(fmt(p[0], 2)), Number(fmt(p[1], 2))]),
      bbox: [Number(fmt(bb[0], 2)), Number(fmt(bb[1], 2)), Number(fmt(bb[2], 2)), Number(fmt(bb[3], 2))],
      center: [Number(fmt(c[0], 2)), Number(fmt(c[1], 2))],
      size: b ? {
        w: Number(fmt(b.w, 2)),
        h: Number(fmt(b.h, 2)),
        angle_deg: Number(fmt(normAngleDeg(b.angle * 180 / Math.PI), 2))
      } : null,
      area_px2: Number(fmt(window.GEOM.shoelace(item.polygon), 2)),
      confidence: item.origin === 'model' && Number.isFinite(item.score) ? item.score : null,
      quality: item.origin === 'ai' && Number.isFinite(item.quality) ? item.quality : null,
      request_id: item.requestId || null,
      model: item.model || null,
      edited: !!item.edited
    };
  }

  function exportAnnotations() {
    if (!S.media) return;
    const frames = [];
    for (const [index, dets] of S.detections) {
      frames.push({
        index,
        time_ms: Math.round(frameTimeMs(index)),
        detections: dets.map(itemJson)
      });
    }
    const payload = {
      schema: 'ship-annotation-set/1.0',
      exported_at: new Date().toISOString(),
      media: {
        name: S.media.name,
        kind: S.media.kind,
        width: S.media.width,
        height: S.media.height,
        duration_ms: Math.round(S.media.duration || 0),
        fps: S.media.fps || null
      },
      coordinate_space: {
        unit: 'pixel', origin: 'top-left', x_axis: 'right', y_axis: 'down',
        space: 'original_media', polygon_order: 'clockwise; start=min(y,x)'
      },
      last_request: S.lastDetect,
      frames,
      annotations: S.annotations.map(itemJson)
    };
    const name = 'annotations-' + (S.media.name.replace(/\.[^.]+$/, '') || 'media') + '.json';
    download(name, JSON.stringify(payload, null, 2), 'application/json');
    toast('已导出 ' + (S.annotations.length + frames.reduce((n, f) => n + f.detections.length, 0)) + ' 条记录', 'ok');
  }

  function download(name, text, mime) {
    const blob = new Blob([text], { type: mime || 'application/octet-stream' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
  }

  /* ------------------------------------------------------------------ AI events */

  function bindAi() {
    els.ai.addEventListener('click', ev => {
      const btn = ev.target.closest('[data-ai-act]');
      if (!btn) return;
      const act = btn.dataset.aiAct;
      if (act === 'roi') {
        setTool('ai');
        toast('在目标周围拖一个粗框，AI 会返回候选轮廓');
      } else if (act === 'full') {
        runDetection({ mode: 'full' });
      } else if (act === 'pick') {
        S.ai.active = Number(btn.dataset.i) || 0;
        renderAi();
        draw();
      } else if (act === 'accept') {
        acceptCandidate();
      } else if (act === 'retry') {
        const req = S.ai.lastReq || {};
        if (!req.roi && !req.point) {
          toast('请先在目标周围重新框选', 'err');
          setTool('ai');
          return;
        }
        runSegment({ roi: req.roi, point: req.point });
      } else if (act === 'cancel') {
        clearAi();
      } else if (act === 'manual') {
        clearAi();
        setTool('box');
        toast('已切换到画框工具');
      }
    });
    els.ai.addEventListener('input', ev => {
      if (ev.target && ev.target.id === 'dt-ai-prompt') S.ai.prompt = ev.target.value;
    });
  }

  /* ------------------------------------------------------------------ keyboard */

  function bindKeyboard() {
    window.addEventListener('keydown', ev => {
      const tag = (ev.target && ev.target.tagName) || '';
      const editing = /^(INPUT|TEXTAREA|SELECT)$/.test(tag);
      if (ev.code === 'Space' && !editing) {
        S.space = true;
        canvas.classList.add('grab');
        ev.preventDefault();
        return;
      }
      if (ev.key === 'Escape') {
        S.draft = null;
        S.polyDraft = [];
        S.drag = null;
        if (S.ai.status === 'preview') clearAi();
        S.selectedId = null;
        updateDrawInfo();
        renderList();
        renderDetail();
        draw();
        return;
      }
      if (editing) return;
      if ((ev.key === 'Delete' || ev.key === 'Backspace') && S.selectedId) {
        ev.preventDefault();
        deleteSelected();
      }
      if (ev.key === 'Enter' && S.tool === 'polygon') {
        ev.preventDefault();
        finishPolygon();
      }
      if (ev.key === 'f' || ev.key === 'F') { fitView(); draw(); }
      if (ev.key === '1') oneToOne();
      if (ev.key === 'p' || ev.key === 'P') togglePlay();
    });
    window.addEventListener('keyup', ev => {
      if (ev.code === 'Space') {
        S.space = false;
        canvas.classList.remove('grab');
      }
    });
  }

  /* ------------------------------------------------------------------ demo video */

  async function makeDemoVideo() {
    if (S.busy) return;
    if (!window.MediaRecorder || !HTMLCanvasElement.prototype.captureStream) {
      toast('当前浏览器不支持合成演示视频，请上传本地视频', 'err');
      return;
    }
    const mime = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']
      .find(t => window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(t));
    if (!mime) {
      toast('当前浏览器不支持 WebM 录制，请上传本地视频', 'err');
      return;
    }

    const btn = els.demoVideo;
    const oldText = btn.textContent;
    btn.disabled = true;
    setBusy('load', '合成演示视频 0%');

    try {
      const img = await new Promise((resolve, reject) => {
        const im = new Image();
        im.onload = () => resolve(im);
        im.onerror = () => reject(new Error('示例影像载入失败'));
        im.src = 'assets/harbor.png';
      });

      const cv = document.createElement('canvas');
      cv.width = 1280;
      cv.height = 720;
      const cx = cv.getContext('2d');
      const stream = cv.captureStream(30);
      const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 5000000 });
      const chunks = [];
      rec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
      const stopped = new Promise((resolve, reject) => {
        rec.onstop = resolve;
        rec.onerror = e => reject(e.error || new Error('录制失败'));
      });
      rec.start(200);

      const duration = 6000;
      const t0 = performance.now();
      await new Promise(resolve => {
        const frame = () => {
          const t = Math.min(1, (performance.now() - t0) / duration);
          const z = 1.18 + 0.18 * t;
          const s = Math.max(cv.width / img.width, cv.height / img.height) * z;
          const vw = cv.width / s;
          const vh = cv.height / s;
          const sx = (img.width - vw) * (0.12 + 0.76 * t);
          const sy = (img.height - vh) * 0.5;
          cx.drawImage(img, sx, sy, vw, vh, 0, 0, cv.width, cv.height);
          els.busyText.textContent = '合成演示视频 ' + Math.round(t * 100) + '%';
          if (t < 1) requestAnimationFrame(frame);
          else resolve();
        };
        frame();
      });

      rec.stop();
      await stopped;
      const blob = new Blob(chunks, { type: mime.split(';')[0] });
      if (!blob.size) throw new Error('录制结果为空');
      const url = URL.createObjectURL(blob);
      await loadVideoSource(url, 'demo-harbor.webm', { owned: true, demo: true });
      toast('演示视频已生成 · ' + timeStr(S.media.duration) + ' · 可逐帧检测', 'ok');
    } catch (err) {
      toast('演示视频生成失败：' + err.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = oldText;
      setBusy(null);
    }
  }

  /* ------------------------------------------------------------------ toast */

  function toast(msg, kind) {
    if (!els.toasts) return;
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = msg;
    els.toasts.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .2s';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 220);
    }, 2800);
  }

  function toastError(err) {
    const msg = err && err.message ? err.message : String(err);
    const code = err && err.code ? ' [' + err.code + ']' : '';
    toast(msg + code, 'err');
  }

  /* ------------------------------------------------------------------ debug hook */

  window.__DT__ = {
    state: S,
    draw,
    runDetection,
    runSegment,
    displayItems,
    selectItem,
    fitView,
    loadImageSource,
    loadVideoSource
  };
})();
