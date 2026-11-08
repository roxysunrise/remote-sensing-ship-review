/* 浏览器内检测 / 分割适配器（演示用）。
 *
 * 这不是模型推理，也不冒充模型：
 *   - detect  ：Otsu 亮度阈值 + 8 邻域连通域 + PCA 最小外接旋转矩形
 *   - segment ：以 ROI 边界亮度中位数为背景，取连通域并拟合旋转矩形
 *
 * 目的只有一个：在后端就绪前，让「图片 / 视频 → 检测 → 坐标 → AI 辅助框选」
 * 的完整交互与数据结构可以跑通。真实模型接入后把 DETECT_CONFIG.mode 改成 'http'，
 * 本文件不再参与请求。
 */
'use strict';

(function (global) {
  const DEFAULT = {
    maxDetections: 80,
    detectLatencyMs: [55, 140],
    segmentLatencyMs: [120, 260]
  };

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const rid = prefix => prefix + Math.random().toString(36).slice(2, 10);
  const randBetween = ([a, b]) => a + Math.random() * (b - a);

  function fail(code, message, status) {
    const err = new Error(message);
    err.code = code;
    err.status = status || 422;
    return err;
  }

  /* ---------- 几何（优先复用页面上的 GEOM，保持坐标规范化一致） ---------- */

  function canonical(points) {
    if (global.GEOM && global.GEOM.canonicalPolygon) return global.GEOM.canonicalPolygon(points);
    const pts = points.map(([x, y]) => [Number(x), Number(y)]);
    const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
    const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
    const sorted = pts.slice().sort(
      (a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
    let first = 0;
    for (let i = 1; i < sorted.length; i++) {
      const d = sorted[i], f = sorted[first];
      if (d[1] < f[1] - 1e-9 || (Math.abs(d[1] - f[1]) <= 1e-9 && d[0] < f[0])) first = i;
    }
    return sorted.slice(first).concat(sorted.slice(0, first));
  }

  function polyIoU(a, b) {
    if (global.GEOM && global.GEOM.polyIoU) return global.GEOM.polyIoU(a, b);
    const ba = bbox(a), bb = bbox(b);
    const ix = Math.max(0, Math.min(ba[2], bb[2]) - Math.max(ba[0], bb[0]));
    const iy = Math.max(0, Math.min(ba[3], bb[3]) - Math.max(ba[1], bb[1]));
    const inter = ix * iy;
    const aa = (ba[2] - ba[0]) * (ba[3] - ba[1]);
    const ab = (bb[2] - bb[0]) * (bb[3] - bb[1]);
    return inter / Math.max(1e-6, aa + ab - inter);
  }

  function bbox(poly) {
    const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]);
    return [Math.min.apply(null, xs), Math.min.apply(null, ys),
      Math.max.apply(null, xs), Math.max.apply(null, ys)];
  }

  /* ---------- 像素工具 ---------- */

  function downscale(src, maxEdge) {
    const sw = src.width, sh = src.height;
    const s = Math.min(1, maxEdge / Math.max(sw, sh));
    const w = Math.max(1, Math.round(sw * s));
    const h = Math.max(1, Math.round(sh * s));
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const cx = c.getContext('2d', { willReadFrequently: true });
    cx.drawImage(src, 0, 0, w, h);
    return { canvas: c, ctx: cx, scale: s, w, h };
  }

  function luminance(img) {
    const d = img.data, n = img.width * img.height;
    const lum = new Uint8Array(n);
    for (let i = 0, p = 0; i < n; i++, p += 4) {
      lum[i] = (d[p] * 77 + d[p + 1] * 150 + d[p + 2] * 29) >> 8;
    }
    return lum;
  }

  function histogram(lum) {
    const h = new Uint32Array(256);
    for (let i = 0; i < lum.length; i++) h[lum[i]]++;
    return h;
  }

  function otsu(hist, total) {
    let sum = 0;
    for (let i = 0; i < 256; i++) sum += i * hist[i];
    let sumB = 0, wB = 0, best = 0, bestVar = -1;
    for (let t = 0; t < 256; t++) {
      wB += hist[t];
      if (!wB) continue;
      const wF = total - wB;
      if (!wF) break;
      sumB += t * hist[t];
      const mB = sumB / wB;
      const mF = (sum - sumB) / wF;
      const v = wB * wF * (mB - mF) * (mB - mF);
      if (v > bestVar) { bestVar = v; best = t; }
    }
    return best;
  }

  function median(values) {
    if (!values.length) return 0;
    const a = values.slice().sort((x, y) => x - y);
    return a[a.length >> 1];
  }

  /**
   * 8 邻域连通域。返回面积在 [minArea, maxArea] 内的分量。
   * 面积超限的分量仍会完成标记，但不再保留像素列表，避免大背景占内存。
   */
  function components(mask, w, h, minArea, maxArea) {
    const labels = new Int32Array(w * h);
    const stack = [];
    const out = [];
    let label = 0;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const start = y * w + x;
        if (!mask[start] || labels[start]) continue;
        label++;
        labels[start] = label;
        stack.length = 0;
        stack.push(start);
        let pts = [];
        let tooBig = false;
        let area = 0;
        let minX = x, maxX = x, minY = y, maxY = y;
        while (stack.length) {
          const q = stack.pop();
          const qx = q % w, qy = (q / w) | 0;
          area++;
          if (!tooBig) {
            pts.push(q);
            if (pts.length > maxArea) { tooBig = true; pts = null; }
          }
          if (qx < minX) minX = qx;
          if (qx > maxX) maxX = qx;
          if (qy < minY) minY = qy;
          if (qy > maxY) maxY = qy;
          for (let dy = -1; dy <= 1; dy++) {
            const ny = qy + dy;
            if (ny < 0 || ny >= h) continue;
            for (let dx = -1; dx <= 1; dx++) {
              if (!dx && !dy) continue;
              const nx = qx + dx;
              if (nx < 0 || nx >= w) continue;
              const n = ny * w + nx;
              if (mask[n] && !labels[n]) {
                labels[n] = label;
                stack.push(n);
              }
            }
          }
        }
        if (tooBig || area < minArea) continue;
        out.push({
          pts, area, minX, minY, maxX, maxY,
          bw: maxX - minX + 1, bh: maxY - minY + 1
        });
      }
    }
    return out;
  }

  /** PCA 最小外接旋转矩形；返回原始像素坐标下的 4 点 polygon。 */
  function obb(comp, w, scale) {
    const pts = comp.pts;
    const n = pts.length;
    let sx = 0, sy = 0;
    for (let i = 0; i < n; i++) {
      sx += pts[i] % w;
      sy += (pts[i] / w) | 0;
    }
    const mx = sx / n, my = sy / n;
    let cxx = 0, cyy = 0, cxy = 0;
    for (let i = 0; i < n; i++) {
      const dx = (pts[i] % w) - mx;
      const dy = ((pts[i] / w) | 0) - my;
      cxx += dx * dx; cyy += dy * dy; cxy += dx * dy;
    }
    cxx /= n; cyy /= n; cxy /= n;
    const theta = 0.5 * Math.atan2(2 * cxy, cxx - cyy);
    const ux = Math.cos(theta), uy = Math.sin(theta);
    let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
    for (let i = 0; i < n; i++) {
      const dx = (pts[i] % w) - mx;
      const dy = ((pts[i] / w) | 0) - my;
      const u = dx * ux + dy * uy;
      const v = -dx * uy + dy * ux;
      if (u < minU) minU = u;
      if (u > maxU) maxU = u;
      if (v < minV) minV = v;
      if (v > maxV) maxV = v;
    }
    const cu = (minU + maxU) / 2, cv = (minV + maxV) / 2;
    const cx = mx + cu * ux - cv * uy;
    const cy = my + cu * uy + cv * ux;
    const bw = Math.max(1, maxU - minU);
    const bh = Math.max(1, maxV - minV);
    const corners = [];
    for (const [su, sv] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const px = cx + ux * (su * bw / 2) - uy * (sv * bh / 2);
      const py = cy + uy * (su * bw / 2) + ux * (sv * bh / 2);
      corners.push([px / scale, py / scale]);
    }
    return {
      polygon: canonical(corners),
      cx: cx / scale, cy: cy / scale,
      w: bw / scale, h: bh / scale,
      angle: theta
    };
  }

  function nms(items, threshold) {
    const kept = [];
    for (const it of items) {
      if (kept.some(k => polyIoU(k.polygon, it.polygon) > threshold)) continue;
      kept.push(it);
    }
    return kept;
  }

  /* ---------- 适配器 ---------- */

  function createDetectMock(config) {
    const cfg = Object.assign({}, DEFAULT, (config && config.mock) || {});
    const caps = {
      service: 'vision-inference',
      version: '0.1.0-mock',
      mode: 'mock',
      modes: ['image', 'video'],
      tasks: ['detect', 'segment'],
      models: {
        detect: { name: 'heuristic-otsu-obb', version: 'mock/0.1', note: '本地启发式，不是模型推理' },
        segment: { name: 'roi-background-split', version: 'mock/0.1', note: '本地启发式，不是模型推理' }
      },
      limits: { max_image_edge: 8192, max_video_duration_s: 600 },
      coordinates: {
        unit: 'pixel', origin: 'top-left', x_axis: 'right', y_axis: 'down',
        space: 'original_media', polygon_order: 'clockwise; start=min(y,x)'
      }
    };

    return {
      mode: 'mock',
      baseUrl: '(local heuristic)',

      async capabilities() { return caps; },

      async createSession(input) {
        await sleep(15);
        return {
          session_id: rid('ses_'),
          media_id: rid('med_'),
          kind: input && input.kind === 'video' ? 'video' : 'image',
          expires_at: new Date(Date.now() + 3600e3).toISOString(),
          mode: 'mock'
        };
      },

      async cancel() { return { cancelled: true }; },

      async detect(req) {
        await sleep(randBetween(cfg.detectLatencyMs));
        const src = req.frame && req.frame.canvas;
        if (!src) throw fail('INVALID_FRAME', '请求缺少 frame.canvas', 422);

        const t0 = performance.now();
        const { canvas, ctx, scale, w, h } = downscale(src, 1024);
        const img = ctx.getImageData(0, 0, w, h);
        const lum = luminance(img);
        const hist = histogram(lum);

        let min = 255, max = 0;
        for (let i = 0; i < 256; i++) {
          if (hist[i]) { if (i < min) min = i; if (i > max) max = i; }
        }

        let detections = [];
        if (max - min >= 28) {
          // 港口场景里船舶是近中性高亮目标；阈值下限抬高可排除码头建筑与岸线。
          const threshold = clamp(otsu(hist, w * h), 145, 210);
          const mask = new Uint8Array(w * h);
          for (let i = 0; i < mask.length; i++) mask[i] = lum[i] > threshold ? 1 : 0;

          const minArea = Math.max(10, Math.round(w * h * 0.000012));
          // 单艘船不会占画面 1.2% 以上；超过这个面积的多半是岸线、码头或成片建筑。
          const maxArea = Math.round(w * h * 0.012);
          const comps = components(mask, w, h, minArea, maxArea);
          const items = [];

          for (const c of comps) {
            const aspect = Math.max(c.bw / c.bh, c.bh / c.bw);
            const fill = c.area / Math.max(1, c.bw * c.bh);
            if (aspect < 1.55 || aspect > 18) continue;
            if (fill < 0.34) continue;
            let lumSum = 0;
            for (let i = 0; i < c.pts.length; i++) lumSum += lum[c.pts[i]];
            if (lumSum / c.area < 170) continue;
            // 横跨近半幅画面的连通域是岸线/陆地，不是单个目标
            if (c.bw > w * 0.4 || c.bh > h * 0.4) continue;
            // 外接矩形面积同样设上限，排除由多个目标粘连成的块
            if (c.bw * c.bh > w * h * 0.02) continue;
            const o = obb(c, w, scale);
            const areaScore = clamp(c.area / (w * h * 0.004), 0, 1);
            const score = clamp(0.32 + 0.44 * fill + 0.16 * Math.min(1, (aspect - 1) / 5) + 0.08 * areaScore, 0.3, 0.97);
            items.push({
              id: 'd' + (items.length + 1),
              label: 'ship',
              score: Math.round(score * 1000) / 1000,
              polygon: o.polygon,
              source: 'model'
            });
          }

          detections = nms(items.sort((a, b) => b.score - a.score), 0.35)
            .slice(0, cfg.maxDetections);
        }

        return {
          request_id: rid('req_'),
          frame: req.frame ? { index: req.frame.index || 0, time_ms: req.frame.time_ms || 0 } : null,
          model: caps.models.detect,
          elapsed_ms: Math.round((performance.now() - t0 + 8) * 10) / 10,
          count: detections.length,
          detections,
          notice: 'mock：亮度连通域启发式，非模型推理'
        };
      },

      async segment(req) {
        await sleep(randBetween(cfg.segmentLatencyMs));
        const src = req.frame && req.frame.canvas;
        if (!src) throw fail('INVALID_FRAME', '请求缺少 frame.canvas', 422);

        const t0 = performance.now();
        const W = src.width, H = src.height;
        let roi = req.roi;
        if (!roi && req.point) {
          const r = 90;
          roi = [req.point[0] - r, req.point[1] - r, req.point[0] + r, req.point[1] + r];
        }
        if (!roi || roi.length !== 4) throw fail('INVALID_REGION', '缺少 roi 或 point', 422);

        const x0 = clamp(Math.round(Math.min(roi[0], roi[2])), 0, W - 2);
        const y0 = clamp(Math.round(Math.min(roi[1], roi[3])), 0, H - 2);
        const x1 = clamp(Math.round(Math.max(roi[0], roi[2])), x0 + 2, W);
        const y1 = clamp(Math.round(Math.max(roi[1], roi[3])), y0 + 2, H);
        const rw = x1 - x0, rh = y1 - y0;
        if (rw < 6 || rh < 6) throw fail('INVALID_REGION', '框选区域太小，请重新框选', 422);

        const s = Math.min(1, 360 / Math.max(rw, rh));
        const cw = Math.max(8, Math.round(rw * s));
        const ch = Math.max(8, Math.round(rh * s));
        const c = document.createElement('canvas');
        c.width = cw; c.height = ch;
        const cx = c.getContext('2d', { willReadFrequently: true });
        cx.drawImage(src, x0, y0, rw, rh, 0, 0, cw, ch);
        const img = cx.getImageData(0, 0, cw, ch);
        const lum = luminance(img);

        // 以 ROI 边框的亮度中位数为背景，同时尝试「更亮」和「更暗」两种极性
        const border = [];
        for (let x = 0; x < cw; x++) {
          border.push(lum[x], lum[(ch - 1) * cw + x]);
        }
        for (let y = 0; y < ch; y++) {
          border.push(lum[y * cw], lum[y * cw + cw - 1]);
        }
        const bg = median(border);
        let mad = median(border.map(v => Math.abs(v - bg)));
        const delta = Math.max(14, 3.2 * mad);

        const minArea = Math.max(8, Math.round(cw * ch * 0.0015));
        const maxArea = Math.round(cw * ch * 0.96);
        const cands = [];

        for (const polarity of [1, -1]) {
          const mask = new Uint8Array(cw * ch);
          for (let i = 0; i < mask.length; i++) {
            mask[i] = polarity > 0 ? (lum[i] > bg + delta ? 1 : 0) : (lum[i] < bg - delta ? 1 : 0);
          }
          const comps = components(mask, cw, ch, minArea, maxArea);
          for (const comp of comps) {
            const fill = comp.area / Math.max(1, comp.bw * comp.bh);
            if (fill < 0.24) continue;
            const pcx = comp.minX + comp.bw / 2;
            const pcy = comp.minY + comp.bh / 2;
            const dist = Math.hypot(pcx - cw / 2, pcy - ch / 2) / Math.hypot(cw / 2, ch / 2);
            const areaRatio = comp.area / (cw * ch);
            const quality = clamp(
              0.34 + 0.4 * fill + 0.18 * (1 - clamp(dist, 0, 1)) + 0.08 * Math.min(1, areaRatio / 0.35),
              0.3, 0.97);
            const o = obb(comp, cw, s);
            const polygon = o.polygon.map(([px, py]) => [x0 + px, y0 + py]);
            cands.push({
              id: 'c' + (cands.length + 1),
              label: req.prompt || 'ship',
              quality: Math.round(quality * 1000) / 1000,
              confidence: null,
              polygon: canonical(polygon),
              reason: (polarity > 0 ? '比背景更亮' : '比背景更暗') +
                '的连通域，填充率 ' + fill.toFixed(2) + '，相对背景差 ' + delta.toFixed(0)
            });
          }
        }

        const top = nms(cands.sort((a, b) => b.quality - a.quality), 0.6).slice(0, 2);
        if (!top.length) {
          throw fail('NO_CANDIDATE', '该区域内没有找到与背景分离的目标，请扩大框选范围或改用手动画框', 422);
        }

        return {
          request_id: rid('req_'),
          frame: req.frame ? { index: req.frame.index || 0, time_ms: req.frame.time_ms || 0 } : null,
          model: caps.models.segment,
          elapsed_ms: Math.round((performance.now() - t0 + 10) * 10) / 10,
          roi: [x0, y0, x1, y1],
          candidates: top,
          notice: 'mock：基于 ROI 背景的启发式分割，非模型推理'
        };
      }
    };
  }

  global.createDetectMock = createDetectMock;
})(window);
