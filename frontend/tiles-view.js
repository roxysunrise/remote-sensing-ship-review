/* A 方大图复核核心：后端显示瓦片层（只负责"背景影像"）。
 *
 * 职责边界：
 *   - 输入视口（scale/ox/oy）与后端 tile-manifest，选层级、算可见块、按原图像素坐标绘制；
 *   - 不碰 OBB 状态、草稿、保存，也不改页面结构；app.js 只把它当 Viewport 的背景层。
 *   - 瓦片地址与鉴权由调用方注入 loadTile，便于浏览器带 Bearer、Node 测试用假加载器。
 *
 * 坐标约定（与 backend/tiles.py 一致）：
 *   z=0 是原图 1:1，z 越大越粗；层内像素 = 原图像素 / 2^z。
 *   因此第 z 层的 (x,y) 块左上角对应原图 (x*edge*2^z, y*edge*2^z)。
 *
 * 浏览器：window.TileView；Node：module.exports（tests/test_tile_view.mjs）。
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.TileView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const EPS = 1e-6;

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  /** 只允许将会话令牌发送到配置的 HTTP API 源。 */
  function authenticatedImageUrl(url, apiBase, pageUrl) {
    const api = new URL(apiBase, pageUrl);
    const target = new URL(url, pageUrl);
    if (!['http:', 'https:'].includes(target.protocol) || target.origin !== api.origin
        || target.username || target.password) {
      throw new Error('影像 URL 不属于配置的 API 源');
    }
    return target.href;
  }

  /** 校验并复制 manifest，避免绘制过程反复解析字符串。 */
  function normalizeManifest(manifest) {
    if (!manifest || !Array.isArray(manifest.levels) || !manifest.levels.length) {
      throw new Error('tile manifest 缺少 levels');
    }
    const width = Number(manifest.width);
    const height = Number(manifest.height);
    if (!(width > 0) || !(height > 0)) throw new Error('tile manifest 缺少影像尺寸');
    const tileEdge = Number(manifest.tile_edge);
    if (!(tileEdge > 0)) throw new Error('tile manifest 缺少 tile_edge');

    const levels = manifest.levels
      .map(level => ({
        z: Number(level.z),
        width: Number(level.width),
        height: Number(level.height),
        cols: Number(level.cols),
        rows: Number(level.rows),
        scale: Number(level.scale) || 1 / Math.pow(2, Number(level.z))
      }))
      .filter(level => Number.isFinite(level.z) && level.width > 0 && level.height > 0)
      .sort((a, b) => a.z - b.z);
    if (!levels.length) throw new Error('tile manifest 没有有效层级');

    const maxZ = Number.isFinite(Number(manifest.max_z))
      ? Number(manifest.max_z)
      : levels[levels.length - 1].z;
    return Object.assign({}, manifest, {
      width, height, tile_edge: tileEdge, levels, max_z: maxZ
    });
  }

  /**
   * 选显示层级：让一个层内像素最多占一个屏幕像素（不放大纹理）。
   * 1:1 → z0；缩小 2 倍 → z1；放大 → 仍用 z0（最清晰的一层）。
   */
  function levelForScale(manifest, scale) {
    const data = manifest && manifest.levels && manifest.levels[0] && manifest.levels[0].cols != null
      ? manifest : normalizeManifest(manifest);
    const s = Number(scale);
    const raw = Number.isFinite(s) && s > 0 ? Math.floor(Math.log2(1 / s)) : data.max_z;
    const target = clamp(raw, 0, data.max_z);
    let picked = data.levels[0];
    for (const level of data.levels) {
      if (level.z <= target) picked = level;
    }
    return picked;
  }

  /** 画布可见区域（屏幕四角）换回原图像素矩形；供选块和测试使用。 */
  function worldRectForViewport(viewport) {
    const [left, top] = viewport.s2w(0, 0);
    const [right, bottom] = viewport.s2w(viewport.w, viewport.h);
    return {
      left: Math.min(left, right),
      top: Math.min(top, bottom),
      right: Math.max(left, right),
      bottom: Math.max(top, bottom)
    };
  }

  /**
   * 视口内可见瓦片（不含预取）。返回值里的 left/top/width/height 都是原图像素，
   * 与检测框、区域、导出的坐标同一套单位。
   */
  function visibleTiles(manifest, level, rect) {
    const data = manifest && manifest.levels && manifest.levels[0] && manifest.levels[0].cols != null
      ? manifest : normalizeManifest(manifest);
    const factor = Math.pow(2, level.z);
    const worldEdge = data.tile_edge * factor;
    const area = rect || { left: 0, top: 0, right: data.width, bottom: data.height };

    const left = clamp(Number(area.left), 0, data.width);
    const top = clamp(Number(area.top), 0, data.height);
    const right = clamp(Number(area.right), 0, data.width);
    const bottom = clamp(Number(area.bottom), 0, data.height);
    if (!(right > left + EPS) || !(bottom > top + EPS)) return [];

    const x0 = clamp(Math.floor(left / worldEdge), 0, level.cols - 1);
    const x1 = clamp(Math.ceil(right / worldEdge) - 1, 0, level.cols - 1);
    const y0 = clamp(Math.floor(top / worldEdge), 0, level.rows - 1);
    const y1 = clamp(Math.ceil(bottom / worldEdge) - 1, 0, level.rows - 1);

    const tiles = [];
    for (let y = y0; y <= y1; y += 1) {
      for (let x = x0; x <= x1; x += 1) {
        const levelX = x * data.tile_edge;
        const levelY = y * data.tile_edge;
        const levelWidth = Math.max(0, Math.min(data.tile_edge, level.width - levelX));
        const levelHeight = Math.max(0, Math.min(data.tile_edge, level.height - levelY));
        if (!(levelWidth > 0) || !(levelHeight > 0)) continue;
        tiles.push({
          key: level.z + '/' + x + '/' + y,
          z: level.z, x, y,
          // 后端先在原图边缘裁切，再缩放到向上取整的层尺寸。
          // 因此边缘块必须画到精确的裁切矩形。
          left: levelX * factor,
          top: levelY * factor,
          width: Math.min(levelWidth * factor, data.width - levelX * factor),
          height: Math.min(levelHeight * factor, data.height - levelY * factor),
          levelWidth,
          levelHeight
        });
      }
    }
    return tiles;
  }

  function tileScreenRect(tile, viewport) {
    return {
      sx: viewport.ox + tile.left * viewport.scale,
      sy: viewport.oy + tile.top * viewport.scale,
      sw: tile.width * viewport.scale,
      sh: tile.height * viewport.scale
    };
  }

  /** 原图点 → 层内坐标/块内局部坐标；用于跨瓦片坐标一致性验证。 */
  function tileLocalPoint(tile, worldX, worldY) {
    const factor = Math.pow(2, tile.z);
    const lx = Number(worldX) / factor;
    const ly = Number(worldY) / factor;
    return {
      levelX: lx,
      levelY: ly,
      u: (worldX - tile.left) * tile.levelWidth / tile.width,
      v: (worldY - tile.top) * tile.levelHeight / tile.height
    };
  }

  /**
   * 画布瓦片层。
   * opts.loadTile({z,x,y,url,key}) → Promise<ImageLike>；opts.onUpdate() 在块到达/失败后调用。
   */
  class TileLayer {
    constructor(options) {
      const opts = options || {};
      this.manifest = null;
      this.urlTemplate = '';
      this.loadTile = opts.loadTile;
      this.onUpdate = opts.onUpdate || function () {};
      this.maxCache = opts.maxCache || 160;
      this.requestLimit = opts.requestLimit || 16;
      this.cache = new Map();
      this.pending = new Map();
      this.failures = new Map();
      this.preview = null;
      this.enabled = false;
    }

    configure(manifest, options) {
      this.manifest = normalizeManifest(manifest);
      const opts = options || {};
      this.urlTemplate = opts.urlTemplate || manifest.url_template || '';
      this.clear();
      this.enabled = true;
      return this.manifest;
    }

    setPreview(image) {
      this.preview = image || null;
    }

    clear() {
      this.cache.clear();
      this.pending.clear();
      this.failures.clear();
    }

    /** 同一影像重载/换图：丢弃缓存但保留 manifest 与预览。 */
    invalidate() {
      this.clear();
    }

    reset() {
      this.clear();
      this.manifest = null;
      this.urlTemplate = '';
      this.preview = null;
      this.enabled = false;
    }

    urlFor(tile) {
      if (!this.urlTemplate) return '';
      return this.urlTemplate
        .replace('{z}', String(tile.z))
        .replace('{x}', String(tile.x))
        .replace('{y}', String(tile.y));
    }

    /**
     * 画预览垫底 + 当前层已缓存的块，并为缺块发起加载。
     * 返回是否画了任何像素；调用方据此决定是否退回本地演示图。
     */
    draw(ctx, viewport) {
      if (!ctx || !viewport || !viewport.w || !viewport.h) return false;
      const scale = viewport.scale;
      // 清单还没到（或清单失败）时，至少把服务端预览按原图尺寸垫上。
      if (!this.manifest) {
        if (!this.preview) return false;
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(this.preview, viewport.ox, viewport.oy,
          viewport.worldW * scale, viewport.worldH * scale);
        return true;
      }
      if (!this.enabled) return false;
      const manifest = this.manifest;
      let drew = false;

      ctx.save();
      if (this.preview) {
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(this.preview, viewport.ox, viewport.oy,
          manifest.width * scale, manifest.height * scale);
        drew = true;
      }

      const rect = worldRectForViewport(viewport);
      const level = levelForScale(manifest, scale);
      const tiles = visibleTiles(manifest, level, rect);

      for (const tile of tiles) {
        const entry = this.cache.get(tile.key);
        if (!entry) continue;
        this._touch(tile.key);
        const dest = tileScreenRect(tile, viewport);
        ctx.imageSmoothingEnabled = scale * Math.pow(2, tile.z) < 2;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(entry.image, dest.sx, dest.sy, dest.sw, dest.sh);
        drew = true;
      }
      ctx.restore();

      let budget = this.requestLimit - this.pending.size;
      for (const tile of tiles) {
        if (budget <= 0) break;
        if (this._ensure(tile)) budget -= 1;
      }
      return drew;
    }

    _touch(key) {
      const entry = this.cache.get(key);
      if (!entry) return;
      this.cache.delete(key);
      this.cache.set(key, entry);
    }

    _evict() {
      while (this.cache.size > this.maxCache) {
        const oldest = this.cache.keys().next().value;
        this.cache.delete(oldest);
      }
    }

    _ensure(tile) {
      if (!this.loadTile) return false;
      if (this.cache.has(tile.key) || this.pending.has(tile.key)) return false;
      if ((this.failures.get(tile.key) || 0) >= 2) return false;

      const url = this.urlFor(tile);
      if (!url) return false;
      let request;
      try {
        request = Promise.resolve(this.loadTile({
          key: tile.key, z: tile.z, x: tile.x, y: tile.y, url
        }));
      } catch (error) {
        request = Promise.reject(error);
      }
      const promise = request
        .then(image => {
          if (this.pending.get(tile.key) !== promise) return;
          if (!image) throw new Error('empty tile image');
          this.cache.set(tile.key, { image });
          this._evict();
          this.onUpdate({ key: tile.key, ok: true });
        })
        .catch(error => {
          if (this.pending.get(tile.key) !== promise) return;
          const fails = (this.failures.get(tile.key) || 0) + 1;
          this.failures.set(tile.key, fails);
          this.onUpdate({ key: tile.key, ok: false, error });
        })
        .finally(() => {
          // Reset or invalidate can start the same z/x/y for a different image.
          if (this.pending.get(tile.key) === promise) this.pending.delete(tile.key);
        });
      this.pending.set(tile.key, promise);
      return true;
    }
  }

  return {
    authenticatedImageUrl,
    normalizeManifest,
    levelForScale,
    worldRectForViewport,
    visibleTiles,
    tileScreenRect,
    tileLocalPoint,
    TileLayer
  };
});
