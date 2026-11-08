/* 瓦片视口核心的 Node 验证：层级选择、可见块、跨块坐标、按需加载与重载。
 * 运行：node --test tests/test_tile_view.mjs
 * 不依赖浏览器、后端或任何 npm 包。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import tileView from '../frontend/tiles-view.js';

const {
  authenticatedImageUrl, normalizeManifest, levelForScale, worldRectForViewport, visibleTiles,
  tileScreenRect, tileLocalPoint, TileLayer
} = tileView;

function makeManifest(width = 1000, height = 700, edge = 256) {
  const maxZ = Math.max(0, Math.ceil(Math.log2(Math.max(width, height) / edge)));
  const levels = [];
  for (let z = 0; z <= maxZ; z += 1) {
    const factor = 2 ** z;
    const levelWidth = Math.max(1, Math.ceil(width / factor));
    const levelHeight = Math.max(1, Math.ceil(height / factor));
    levels.push({
      z, width: levelWidth, height: levelHeight,
      cols: Math.ceil(levelWidth / edge), rows: Math.ceil(levelHeight / edge),
      scale: 1 / factor
    });
  }
  return {
    image_id: 'img_test', width, height, tile_edge: edge, max_z: maxZ, levels,
    url_template: '/tiles/img_test/{z}/{x}/{y}'
  };
}

function makeViewport(scale, ox, oy, w = 800, h = 600) {
  return {
    scale, ox, oy, w, h,
    w2s(x, y) { return [x * scale + ox, y * scale + oy]; },
    s2w(x, y) { return [(x - ox) / scale, (y - oy) / scale]; }
  };
}

function fakeContext() {
  const calls = [];
  return {
    calls,
    imageSmoothingEnabled: true,
    imageSmoothingQuality: 'low',
    save() { calls.push(['save']); },
    restore() { calls.push(['restore']); },
    drawImage(image, ...args) { calls.push(['drawImage', image.id || image, ...args]); }
  };
}

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

test('manifest 校验并保留层级顺序', () => {
  const manifest = normalizeManifest(makeManifest(1024, 768));
  assert.equal(manifest.max_z, 2);
  assert.equal(manifest.tile_edge, 256);
  assert.deepEqual(manifest.levels.map(level => level.z), [0, 1, 2]);
  assert.throws(() => normalizeManifest({}), /levels/);
  assert.throws(() => normalizeManifest({ levels: [{ z: 0 }], width: 10, height: 10, tile_edge: 256 }),
    /有效层级/);
});

test('选层不放大纹理：1:1 用 z0，缩小 2 倍用 z1，放大仍用 z0', () => {
  const manifest = normalizeManifest(makeManifest(2048, 1024));
  assert.equal(levelForScale(manifest, 1).z, 0);
  assert.equal(levelForScale(manifest, 0.5).z, 1);
  assert.equal(levelForScale(manifest, 0.49).z, 1);
  assert.equal(levelForScale(manifest, 0.25).z, 2);
  assert.equal(levelForScale(manifest, 4).z, 0);
  assert.equal(levelForScale(manifest, 0.001).z, manifest.max_z);
});

test('可见块按视口裁剪，不请求整层', () => {
  const manifest = normalizeManifest(makeManifest(1000, 700));
  const level = manifest.levels[0];
  const tiles = visibleTiles(manifest, level, { left: 240, top: 250, right: 530, bottom: 270 });
  assert.deepEqual(tiles.map(tile => tile.key), ['0/0/0', '0/1/0', '0/2/0', '0/0/1', '0/1/1', '0/2/1']);
  const all = manifest.levels[0].cols * manifest.levels[0].rows;
  assert.ok(tiles.length < all, '视口只占整层的一部分');
});

test('视口完全在原图外时不请求瓦片', () => {
  const manifest = normalizeManifest(makeManifest(1000, 700));
  const level = manifest.levels[0];
  assert.deepEqual(visibleTiles(manifest, level, { left: -500, top: -500, right: -10, bottom: -10 }), []);
  assert.deepEqual(visibleTiles(manifest, level, { left: 1200, top: 900, right: 1500, bottom: 1200 }), []);
});

test('瓦片屏幕矩形首尾相接，无累计误差', () => {
  const manifest = normalizeManifest(makeManifest(1000, 700));
  const level = manifest.levels[0];
  const viewport = makeViewport(0.37, -120.5, 33.25);
  const tiles = visibleTiles(manifest, level, { left: 0, top: 0, right: 700, bottom: 500 });
  const byKey = new Map(tiles.map(tile => [tile.key, tileScreenRect(tile, viewport)]));
  const a = byKey.get('0/0/0');
  const b = byKey.get('0/1/0');
  assert.ok(Math.abs((a.sx + a.sw) - b.sx) < 1e-9);
  const c = byKey.get('0/0/1');
  assert.ok(Math.abs((a.sy + a.sh) - c.sy) < 1e-9);
  // 原图原点与 (256,256) 的屏幕坐标都应能由瓦片矩形复算出来
  assert.ok(Math.abs(a.sx - viewport.w2s(0, 0)[0]) < 1e-9);
  assert.ok(Math.abs(b.sx - viewport.w2s(256, 0)[0]) < 1e-9);
});

test('跨瓦片坐标：块内局部坐标复算 == 视口世界坐标', () => {
  const manifest = normalizeManifest(makeManifest(1000, 700));
  const viewport = makeViewport(0.83, 41, -17);
  const tiles = visibleTiles(manifest, manifest.levels[0], { left: 200, top: 200, right: 340, bottom: 340 });
  for (const tile of tiles) {
    for (const [worldX, worldY] of [[300, 280], [256, 300], [255.5, 300]]) {
      if (worldX < tile.left || worldX >= tile.left + tile.width) continue;
      if (worldY < tile.top || worldY >= tile.top + tile.height) continue;
      const local = tileLocalPoint(tile, worldX, worldY);
      const rect = tileScreenRect(tile, viewport);
      const screenX = rect.sx + local.u * viewport.scale;
      const screenY = rect.sy + local.v * viewport.scale;
      assert.ok(Math.abs(screenX - viewport.w2s(worldX, worldY)[0]) < 1e-9,
        `${tile.key} @${worldX},${worldY} x`);
      assert.ok(Math.abs(screenY - viewport.w2s(worldX, worldY)[1]) < 1e-9,
        `${tile.key} @${worldX},${worldY} y`);
    }
  }
  // (300,280) 必须落在 x=1,y=1 这一块里，且局部坐标是 (44,24)
  const tile = tiles.find(item => item.key === '0/1/1');
  assert.ok(tile, '跨块目标应命中 0/1/1');
  const local = tileLocalPoint(tile, 300, 280);
  assert.equal(local.u, 44);
  assert.equal(local.v, 24);
});

test('非整除尺寸：边缘块与后端裁切的原图矩形一致', () => {
  const manifest = normalizeManifest(makeManifest(1820, 1020));
  const top = manifest.levels[manifest.max_z];
  const tiles = visibleTiles(manifest, top, { left: 0, top: 0, right: 1820, bottom: 1020 });
  assert.equal(tiles.length, 1);
  const tile = tiles[0];
  assert.equal(tile.left + tile.width, 1820);
  assert.equal(tile.top + tile.height, 1020);
  assert.equal(tile.levelWidth, Math.ceil(1820 / 2 ** top.z));
  const local = tileLocalPoint(tile, 1820, 1020);
  assert.equal(local.u, tile.levelWidth);
  assert.equal(local.v, tile.levelHeight);
});

test('按需加载：去重、命中缓存、平移/缩放换块、重载重新请求', async () => {
  const manifest = normalizeManifest(makeManifest(600, 600));
  const requests = [];
  const layer = new TileLayer({
    requestLimit: 16,
    loadTile({ key, url }) {
      requests.push({ key, url });
      return Promise.resolve({ id: key });
    }
  });
  layer.configure(manifest, { urlTemplate: '/api/v1' + manifest.url_template });
  const ctx = fakeContext();
  const viewport = makeViewport(1, 0, 0, 300, 250);

  // 1:1 视口 300×250 → 2 块；重复 draw 在加载完成前不重复发请求
  layer.draw(ctx, viewport);
  layer.draw(ctx, viewport);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests.map(r => r.key), ['0/0/0', '0/1/0']);
  assert.equal(requests[0].url, '/api/v1/tiles/img_test/0/0/0');
  await flush();

  // 已缓存块直接画，不重复请求
  layer.draw(ctx, viewport);
  assert.equal(requests.length, 2);
  assert.equal(ctx.calls.filter(call => call[0] === 'drawImage').length, 2);
  assert.equal(layer.cache.size, 2);
  assert.equal(layer.pending.size, 0);

  // 向右平移 300px → 只需要新出现的 0/2/0（0/1/0 命中缓存）
  const panned = makeViewport(1, -300, 0, 300, 250);
  layer.draw(ctx, panned);
  assert.deepEqual(requests.slice(2).map(r => r.key), ['0/2/0']);
  await flush();

  // 缩小到 0.4 → 选 z1，请求 4 块；原 z0 缓存不再使用
  const zoomed = makeViewport(0.4, 0, 0, 300, 250);
  layer.draw(ctx, zoomed);
  assert.deepEqual(requests.slice(3).map(r => r.key).sort(),
    ['1/0/0', '1/0/1', '1/1/0', '1/1/1'].sort());
  await flush();

  // 重载：invalidate 后同样视口重新取 4 块
  const before = requests.length;
  layer.invalidate();
  layer.draw(ctx, zoomed);
  assert.equal(requests.length, before + 4);
  await flush();
  assert.equal(layer.cache.size, 4);
});

test('加载失败有限重试，不无限打后端', async () => {
  const manifest = normalizeManifest(makeManifest(300, 300));
  let calls = 0;
  const layer = new TileLayer({
    loadTile() { calls += 1; return Promise.reject(new Error('404')); }
  });
  layer.configure(manifest, { urlTemplate: '/t/{z}/{x}/{y}' });
  const viewport = makeViewport(1, 0, 0, 300, 300);
  const ctx = fakeContext();

  layer.draw(ctx, viewport);
  await flush();
  assert.equal(calls, 4, '300×300 的 z0 层可见 2×2=4 块，各请求一次');
  assert.equal(layer.failures.size, 4);

  layer.draw(ctx, viewport);       // 第 2 次尝试
  await flush();
  assert.equal(calls, 8);
  layer.draw(ctx, viewport);       // 两次失败后熔断
  await flush();
  assert.equal(calls, 8);
  assert.equal(layer.pending.size, 0);
});

test('缓存上限淘汰最久未用的块', async () => {
  const manifest = normalizeManifest(makeManifest(600, 300));
  const layer = new TileLayer({
    maxCache: 1,
    loadTile({ key }) { return Promise.resolve({ id: key }); }
  });
  layer.configure(manifest, { urlTemplate: '/t/{z}/{x}/{y}' });
  layer.draw(fakeContext(), makeViewport(1, 0, 0, 300, 200));
  await flush();
  assert.equal(layer.cache.size, 1);
  assert.deepEqual([...layer.cache.keys()], ['0/1/0']);
});

test('换图丢弃旧请求结果且不释放新影像的同名请求', async () => {
  const requests = [];
  const ctx = fakeContext();
  const view = makeViewport(1, 0, 0, 200, 200);
  const layer = new TileLayer({
    loadTile({ url }) {
      return new Promise((resolve, reject) => requests.push({ url, resolve, reject }));
    }
  });
  layer.configure(makeManifest(), { urlTemplate: '/old/{z}/{x}/{y}' });
  layer.draw(ctx, view);
  layer.reset();
  layer.configure(makeManifest(), { urlTemplate: '/new/{z}/{x}/{y}' });
  layer.draw(ctx, view);
  assert.deepEqual(requests.map(r => r.url), ['/old/0/0/0', '/new/0/0/0']);

  requests[0].resolve({ id: 'old' });
  await flush();
  assert.equal(layer.cache.size, 0);
  assert.equal(layer.pending.size, 1);
  layer.draw(ctx, view);
  assert.equal(requests.length, 2);

  // 失效重载同一影像时，过期错误也不能进入新一轮重试状态。
  layer.invalidate();
  layer.draw(ctx, view);
  requests[1].reject(new Error('stale'));
  await flush();
  assert.equal(layer.failures.size, 0);
  assert.equal(layer.pending.size, 1);
  requests[2].resolve({ id: 'new' });
  await flush();
  layer.draw(ctx, view);
  assert.deepEqual(ctx.calls.filter(c => c[0] === 'drawImage').map(c => c[1]), ['new']);
});

test('仅接受配置的 API 源，拒绝外源、非 HTTP URL 和用户信息', () => {
  const page = 'http://localhost:8765/index.html';
  const api = 'http://localhost:8000/api/v1';
  assert.equal(authenticatedImageUrl('/api/v1/previews/x', '/api/v1', page),
    'http://localhost:8765/api/v1/previews/x');
  assert.equal(authenticatedImageUrl('http://localhost:8000/api/v1/tiles/x/0/0/0', api, page),
    'http://localhost:8000/api/v1/tiles/x/0/0/0');
  for (const url of ['https://external.test/a', '//external.test/a',
    'http://localhost:8765/a', 'blob:http://localhost:8000/a',
    'http://user@localhost:8000/a']) {
    assert.throws(() => authenticatedImageUrl(url, api, page), /API 源/);
  }
});
