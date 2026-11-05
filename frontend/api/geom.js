/* 共享几何工具：UI 与 mock 后端都从这里取，避免两套实现。
 * 语义与仓库 algorithm/ship_detector.py 一致：
 *   polygon = 4×2 像素坐标，原点左上，顺时针，起点为最小 (y, x) 顶点。
 */
'use strict';

(function (global) {
  const EPS = 1e-9;

  function canonicalPolygon(points) {
    const pts = points.map(([x, y]) => [Number(x), Number(y)]);
    const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
    const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
    const sorted = pts.slice().sort(
      (a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
    let area2 = 0;
    for (let i = 0; i < sorted.length; i++) {
      const a = sorted[i], b = sorted[(i + 1) % sorted.length];
      area2 += a[0] * b[1] - b[0] * a[1];
    }
    if (area2 <= 1e-6) throw new Error('degenerate polygon');
    let first = 0;
    for (let i = 1; i < sorted.length; i++) {
      const d = sorted[i], f = sorted[first];
      if (d[1] < f[1] - EPS || (Math.abs(d[1] - f[1]) <= EPS && d[0] < f[0])) first = i;
    }
    return sorted.slice(first).concat(sorted.slice(0, first));
  }

  function fromPolygon(p) {
    const [p0, p1, p2] = p;
    return {
      cx: (p0[0] + p2[0]) / 2,
      cy: (p0[1] + p2[1]) / 2,
      w: Math.hypot(p1[0] - p0[0], p1[1] - p0[1]),
      h: Math.hypot(p2[0] - p1[0], p2[1] - p1[1]),
      theta: Math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    };
  }

  function cornersOf(b) {
    const c = Math.cos(b.theta), s = Math.sin(b.theta);
    const hw = b.w / 2, hh = b.h / 2;
    return [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]]
      .map(([x, y]) => [b.cx + x * c - y * s, b.cy + x * s + y * c]);
  }

  function toPolygon(b) { return canonicalPolygon(cornersOf(b)); }

  function pointInPolygon([x, y], poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function shoelace(poly) {
    let a = 0;
    for (let i = 0; i < poly.length; i++) {
      const p = poly[i], q = poly[(i + 1) % poly.length];
      a += p[0] * q[1] - q[0] * p[1];
    }
    return Math.abs(a) / 2;
  }

  /* Sutherland–Hodgman 裁剪，对应 cv2.intersectConvexConvex */
  function clipArea(a, b) {
    let out = a.map(p => [p[0], p[1]]);
    const signed = poly => {
      let s = 0;
      for (let i = 0; i < poly.length; i++) {
        const p = poly[i], q = poly[(i + 1) % poly.length];
        s += p[0] * q[1] - q[0] * p[1];
      }
      return s;
    };
    const ccw = signed(b) > 0 ? b : b.slice().reverse();
    for (let i = 0; i < ccw.length && out.length; i++) {
      const p1 = ccw[i], p2 = ccw[(i + 1) % ccw.length];
      const side = pt => (p2[0] - p1[0]) * (pt[1] - p1[1]) - (p2[1] - p1[1]) * (pt[0] - p1[0]);
      const next = [];
      for (let j = 0; j < out.length; j++) {
        const cur = out[j], nxt = out[(j + 1) % out.length];
        const dc = side(cur), dn = side(nxt);
        if (dc >= 0) next.push(cur);
        if ((dc >= 0) !== (dn >= 0)) {
          const t = dc / (dc - dn);
          next.push([cur[0] + t * (nxt[0] - cur[0]), cur[1] + t * (nxt[1] - cur[1])]);
        }
      }
      out = next;
    }
    return out.length < 3 ? 0 : shoelace(out);
  }

  function polyIoU(a, b) {
    const inter = clipArea(a, b);
    const uni = shoelace(a) + shoelace(b) - inter;
    return uni > 0 ? inter / uni : 0;
  }

  function bbox(poly) {
    const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  const centroid = poly => [
    poly.reduce((s, p) => s + p[0], 0) / poly.length,
    poly.reduce((s, p) => s + p[1], 0) / poly.length
  ];

  function distanceToRect([x, y], [x0, y0, x1, y1]) {
    if (x >= x0 && x <= x1 && y >= y0 && y <= y1)
      return Math.min(x - x0, x1 - x, y - y0, y1 - y);
    const dx = Math.max(x0 - x, 0, x - x1);
    const dy = Math.max(y0 - y, 0, y - y1);
    return Math.hypot(dx, dy);
  }

  global.GEOM = {
    canonicalPolygon, fromPolygon, cornersOf, toPolygon, pointInPolygon,
    shoelace, clipArea, polyIoU, bbox, centroid, distanceToRect
  };
})(window);
