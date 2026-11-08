# 大图复核瓦片视口

A 方负责瓦片背景层和原图坐标一致性。组件在 `frontend/tiles-view.js`，应用接入点是 `frontend/app.js` 的 `Viewport`。工作台按当前视口和缩放级别读取私有显示瓦片，缓存按最近使用顺序淘汰。图像切换和重载使旧请求失效。瓦片与预览只向配置的 API 源发送会话令牌，重定向作为失败处理。

HTTP 模式依赖现有 `GET /images/{id}/tile-manifest`、`GET /tiles/{id}/{z}/{x}/{y}` 和 `GET /previews/{id}`。`z=0` 为原图 1:1，`z` 每增加 1 时采样步长加倍。绘制使用后端实际的原图裁切矩形，最后一行和最后一列在缩小层也与原图边缘对齐。OBB 草稿与保存仍走已有接口。清单缺失或瓦片失败时保留真实影像预览。

## 浏览器复跑

从仓库根目录执行。Python 需要项目后端依赖以及 `websocket-client`，本机需要 Edge 或 Chrome。使用三个终端。夹具每次重建固定 ID 的合成测试任务，请为它单独指定运行目录，并确保 `SHIP_DB_PATH` 未设置。

终端 1

```powershell
$env:SHIP_VAR_DIR = Join-Path $PWD 'var/tile-browser'
python scripts/seed_tile_fixture.py
python -m backend.server --port 18008
```

终端 2

```powershell
python scripts/serve.py --frontend frontend --port 18768 --backend http://127.0.0.1:18008
```

终端 3

```powershell
$env:SHIP_FRONTEND_URL = 'http://127.0.0.1:18768'
$env:SHIP_BROWSER_PORT = '19224'
node --test tests/test_tile_view.mjs
python scripts/browser_tiles_check.py
```

## 2026-11-06 本地验证

- Node 12 项通过，覆盖选层、可见块、非整除尺寸、缓存、有限重试、换图或重载时的旧响应和跨源 URL 拒绝。
- Edge 与真实 HTTP 后端 16 项通过。合成夹具为 1820×1020 四象限影像，验证鉴权瓦片缓存、原图坐标对齐、切换粗层、平移、适配视窗、实际鼠标把 OBB 从 x=455 移至 x=555 并跨越 x=512 瓦片边界、写入草稿及保存版本。
- 重新打开工作台时阻断瓦片清单，确认真实服务端预览接管并保留保存后的框。运行无未捕获浏览器异常。
- 实际港口大图与 B 方最终集成版本仍需验收。本次交付保留在本地分支，未推送、合并或部署。
