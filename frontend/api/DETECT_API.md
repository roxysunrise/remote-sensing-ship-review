# 检测工作台接口说明（图片 / 视频 / AI 辅助分割）

本文对应页面 [`detect.html`](../detect.html)，是「界面功能部分」的接口契约。
目标很明确：**界面已经完成，后端只需要按本文实现 4 个核心端点**（另有 1 个取消端点预留），把
`detect-config.js` 的 `mode` 从 `'mock'` 改成 `'http'` 即可切换，不需要改任何界面代码。

```
detect.js (UI)
    │  只调用 window.DetectAPI
    ▼
api/detect-api.js ──┬── mock → api/detect-mock.js（浏览器内启发式，离线演示）
                    └── http → 本文 / detect-openapi.yaml（真实推理服务）
```

---

## 1. 坐标约定（与仓库 `contracts/ship-detection-v0.2.md` 一致）

| 项 | 约定 |
|---|---|
| 单位 | 原始媒体像素（图片原始尺寸 / 视频原始分辨率） |
| 原点 | 左上角 |
| 轴向 | x 向右，y 向下 |
| 多边形顺序 | 顺时针；起点为最小 `(y, x)` 顶点 |
| 坐标空间 | `original_media`，不是预览图、不是缩放后的画布 |
| 视频帧 | 坐标属于该帧的原始视频分辨率；不同帧之间不做跟踪 |

前端显示的四角坐标、中心、宽高、角度、包围盒、面积都由这份 `polygon` 计算。
服务端**不要**返回归一化坐标或画布坐标，否则界面上的数值会与真实像素不一致。

---

## 2. 配置

[`detect-config.js`](../detect-config.js)：

```js
window.DETECT_CONFIG = {
  mode: 'http',                    // 'mock' → 'http'
  baseUrl: 'https://ship-api.example.com',  // 同源部署留空
  apiPrefix: '/api/v1',
  endpoints: {
    capabilities: '/vision/capabilities',
    sessions:     '/vision/sessions',
    detect:       '/vision/detect',
    segment:      '/vision/segment',
    cancel:       '/vision/jobs/{job_id}/cancel'
  },
  timeoutMs: 20000,
  video: {
    fpsFallback: 30,
    autoIntervalMs: 900,           // 连续检测抽帧间隔
    maxPayloadEdge: 1600           // 发送帧的最长边
  }
};
```

临时联调也可以直接访问 `detect.html?api=http`，页面会调用 `DetectAPI.setMode('http')`。
鉴权沿用主应用的 `AuthSession` / `dsh.ship.token`，有 token 时自动带
`Authorization: Bearer <token>`。

跨域部署时，服务端需要允许 `Authorization` 与 `Content-Type` 的 CORS 预检；
同源部署（推荐）则不需要额外配置。

---

## 3. 客户端方法

`window.DetectAPI` 暴露 5 个方法，mock / http 两种适配器签名一致：

| 方法 | 用途 | 返回 |
|---|---|---|
| `capabilities()` | 读取服务能力与模型信息 | `Capabilities` |
| `createSession(input)` | 为当前媒体创建一次推理会话 | `{ session_id, media_id, kind }` |
| `detect(req)` | 整帧检测（图片或视频当前帧） | `DetectResponse` |
| `segment(req)` | AI 辅助分割（ROI 或点提示） | `SegmentResponse` |
| `cancel(jobId)` | 取消异步任务（预留） | `{ cancelled: true }` |

`detect.js` 里对应三处调用：

- `runDetection()` → `DetectAPI.detect(...)`，结果写入 `state.detections`
- `runSegment()` → `DetectAPI.segment(...)`，候选进入 `state.ai.candidates`
- `ensureSession()` → `DetectAPI.createSession(...)`

### 3.1 detect / segment 请求对象

```jsonc
{
  "session_id": "ses_ab12cd34",
  "kind": "video",                         // "image" | "video"
  "frame": {
    "index": 60,                           // 视频帧号；图片为 0
    "time_ms": 2000,
    "width": 1280,                         // 原始媒体尺寸
    "height": 720
  },
  "image": {
    "data_url": "data:image/jpeg;base64,...", // 下采样后的当前帧
    "width": 1600,                         // 负载尺寸；可能小于 frame 尺寸
    "height": 900
  },
  "roi": [820, 30, 1010, 70],              // segment 专用，原始像素
  "point": [900, 50],                      // segment 专用，roi 为空时使用
  "prompt": "船舶",
  "mode": "roi",                           // "roi" | "point" | "full"
  "options": { "conf": 0.25, "max_det": 80, "classes": null }
}
```

- `frame.canvas` 只存在于浏览器内存，HTTP 适配器序列化时会自动剔除。
- `image.width/height` 是**负载**尺寸，服务端需要按
  `scale = frame.width / image.width` 把结果多边形还原到原始像素。
- 视频由前端抽帧后发送，不要求服务端解码视频；如果后端希望直接收视频流，
  可以新增 `POST /vision/video/jobs`，但当前界面只依赖逐帧接口。
- `roi` 是 `[x0, y0, x1, y1]`，允许反向（前端已排序，服务端仍应兼容）。
- `options.classes`：省略或 `null` = **输出模型全部类别**（本权重 DOTA-v1 共 15 类）；
  传 `[1]` 这类类别 id 列表则只输出这些类。历史实现在此固定只输出 ship，
  导致网球场、棒球场、卡车场等影像返回 0 个框；2026-11-06 起改为默认全类。

### 3.2 DetectResponse

```json
{
  "request_id": "req_9f2c1a",
  "frame": { "index": 60, "time_ms": 2000 },
  "model": {
    "name": "yolov8l-obb", "version": "ship-detection/0.1", "device": "cpu",
    "classes": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
    "classes_filtered": false
  },
  "elapsed_ms": 84.2,
  "count": 2,
  "by_class": { "ship": 1, "tennis-court": 1 },
  "detections": [
    {
      "id": "d1",
      "label": "ship",
      "class_id": 1,
      "score": 0.91,
      "polygon": [[885.0, 37.3], [953.3, 39.5], [952.9, 52.2], [884.6, 50.0]],
      "source": "model"
    }
  ]
}
```

`label` 是模型类别名（`class_name`），`class_id` 为原模型编号，两者都可直接用于筛选与上色。
`by_class` 是按类别聚合的计数，便于前端显示"检出 tennis-court ×11"这类汇总；
`model.classes_filtered=true` 表示本次做了类别过滤。

- `polygon` 至少 3 点；4 点按旋转框显示，>4 点按多边形显示。
- `score` 是模型置信度；前端列表显示两位小数，详情显示三位。
- `id` 可省略，前端会用 `m{frame}_{i}` 生成稳定编号。
- `elapsed_ms` 会显示在界面提示与导出 JSON 中，便于对比模型。

### 3.3 SegmentResponse

```json
{
  "request_id": "req_7d1e02",
  "frame": { "index": 60, "time_ms": 2000 },
  "model": { "name": "sam2.1-hiera-large", "version": "2.1" },
  "elapsed_ms": 212.5,
  "roi": [820, 30, 1010, 70],
  "candidates": [
    {
      "id": "c1",
      "label": "ship",
      "quality": 0.83,
      "confidence": null,
      "polygon": [[885.0, 37.3], [953.3, 39.5], [952.9, 52.2], [884.6, 50.0]],
      "reason": "高对比度连通域，填充率 0.86"
    }
  ]
}
```

- `confidence` **必须为 `null`**：AI 候选不是模型检测结果，不得伪造成 1.0。
- `quality` 是 0–1 的候选质量分，用于排序与展示，不是概率。
- 返回多个候选时界面会列出，用户选中后采纳；采纳后写入标注并记录
  `request_id` 与 `model`，便于追溯。
- 没有候选时返回 `422 NO_CANDIDATE`，界面会提示「改手动画框」。

### 3.4 Capabilities

```json
{
  "service": "vision-inference",
  "version": "0.1.0",
  "modes": ["image", "video"],
  "tasks": ["detect", "segment"],
  "models": {
    "detect": { "name": "yolov8l-obb", "version": "ship-0.2" },
    "segment": { "name": "sam2.1-hiera-large", "version": "2.1" }
  },
  "limits": { "max_image_edge": 8192, "max_video_duration_s": 600 }
}
```

---

## 4. HTTP 端点

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| GET | `/vision/capabilities` | — | `Capabilities` |
| POST | `/vision/sessions` | `{kind,name,width,height,duration_ms,fps}` | `{session_id,media_id}` |
| POST | `/vision/detect` | `detect` 请求对象 | `DetectResponse` |
| POST | `/vision/segment` | `segment` 请求对象 | `SegmentResponse` |
| POST | `/vision/jobs/{job_id}/cancel` | `{}` | `{cancelled:true}` |

机器可读版本见 [`detect-openapi.yaml`](detect-openapi.yaml)。

### 4.1 会话

```http
POST /api/v1/vision/sessions
Content-Type: application/json

{ "kind": "image", "name": "harbor.png", "width": 2305, "height": 1537 }
```

会话用于把同一媒体的多次检测归组、做配额与日志。首版可以返回固定前缀的
`session_id`，不要求服务端保存大文件；前端每次都会带上当前帧的 JPEG。

### 4.2 错误信封

与主应用一致：

```json
{ "error": { "code": "NO_CANDIDATE", "message": "该区域没有可用候选", "details": {} } }
```

| HTTP | code | 含义 |
|---|---|---|
| 400 | `INVALID_FRAME` | 缺少帧或帧无法解码 |
| 401 | `UNAUTHENTICATED` | 未登录 / token 失效 |
| 403 | `FORBIDDEN` | 无该会话或项目权限 |
| 404 | `SESSION_NOT_FOUND` | 会话不存在或已过期 |
| 413 | `FRAME_TOO_LARGE` | 超过服务端像素 / 字节上限 |
| 422 | `INVALID_REGION` | ROI 为空、越界或过小 |
| 422 | `NO_CANDIDATE` | 分割没有找到目标 |
| 429 | `RATE_LIMITED` | 视频连续检测触发限流 |
| 500 | `INTERNAL_ERROR` | 服务端异常（不要返回堆栈） |
| 0 | `TIMEOUT` / `NETWORK_ERROR` | 前端本地错误码，非服务端返回 |

前端会保留 `status` / `code` / `message` / `details`，并在 toast 中显示
`message [code]`，不会吞掉错误。

---

## 5. 视频抽帧与性能

- 播放时「连续检测」按 `video.autoIntervalMs`（默认 900 ms）抽帧，不逐帧请求。
- 单帧「检测当前帧」会先暂停视频，保证结果与画面时间一致。
- 帧以 JPEG data URL 发送，最长边默认 1600 px；服务端按
  `frame.width / image.width` 还原坐标。
- 建议服务端：
  - 单帧检测 P95 < 300 ms，否则连续检测会排队；
  - 对相同 `session_id + frame.index` 做短期缓存；
  - 返回 `elapsed_ms`，界面会展示，便于定位性能问题。

---

## 6. 接入顺序（后端）

1. 实现 `GET /vision/capabilities`，返回模型名与限制；界面右上角会显示接口模式。
2. 实现 `POST /vision/sessions`，返回任意稳定 `session_id`。
3. 实现 `POST /vision/detect`：收 JPEG → 解码 → 推理 → 按比例还原多边形。
4. 实现 `POST /vision/segment`：收 ROI / point + prompt → 返回 1–2 个候选。
5. 把 `detect-config.js` 的 `mode` 改为 `'http'`，`baseUrl` 指向服务。
6. 用 `detect.html?api=http` 做联调；检查：
   - 图片检测的框与坐标是否落在真实舰船上；
   - 视频 0s / 2s / 5s 三帧的结果是否随画面变化；
   - AI 框选返回的候选采纳后，`request_id` 是否出现在详情与导出 JSON；
   - 后端断开时是否出现可读的 toast，而不是白屏。

---

## 7. 前端可替换点

| 需求 | 改哪里 |
|---|---|
| 换后端地址 / 前缀 | `detect-config.js` |
| 换检测端点路径 | `detect-config.js` → `endpoints` |
| 换请求头 / 鉴权 | `api/detect-api.js` → `createHttpAdapter().request()` |
| 换抽帧尺寸 / 间隔 | `detect-config.js` → `video` |
| 换候选展示字段 | `detect.js` → `renderAi()` / `acceptCandidate()` |
| 换 mock 算法 | `api/detect-mock.js`（只影响离线演示） |

---

## 8. mock 的边界（必须如实说明）

`api/detect-mock.js` 不是模型：

- **detect**：Otsu 亮度阈值 + 8 邻域连通域 + PCA 最小外接旋转矩形，
  再用长宽比、填充率、面积、平均亮度过滤。它针对演示用的高对比度港口场景，
  换一张真实遥感影像效果会明显下降。
- **segment**：以 ROI 边框亮度中位数为背景，取「比背景更亮 / 更暗」的连通域，
  拟合旋转矩形。它不会理解语义，也不会输出掩码。

因此界面右上角在 mock 模式显示「演示推理」，详情里显示模型名
`heuristic-otsu-obb` / `roi-background-split`，导出 JSON 也保留 `notice`。
真实接入后这些字段由服务端返回，不应再出现「启发式」字样。

---

## 9. 与主应用的关系

- `detect.html` 是独立页面，不依赖 `index.html` 的登录门禁；它复用
  `styles.css` 的设计令牌，并在主应用左侧导航增加了「智能检测」入口。
- 主应用的 `window.API`（任务 / 草稿 / 版本 / 导出）与本页的
  `window.DetectAPI` 互不覆盖；如果后端希望把检测结果直接写入草稿，
  可在 `POST /vision/detect` 返回 `draft_patch` 或在网关层转换。
- 标注导出格式 `ship-annotation-set/1.0` 是前端自定义的中间格式，
  后端可以按需映射到 `ship-detection/0.2` 的 `detections`。
