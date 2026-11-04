# 后端接口交付说明（给 B 方前端）

对应实现：`backend/`（PR #8，分支 `codex/app-backend`）。接口以 `frontend/api/openapi.yaml`
与 `detect-openapi.yaml` 为准；本文写清**每个端点实际返回什么**，以及三处需要前端确认的差异。

- 基地址：`http://127.0.0.1:8000`（前端 `config.js` 的 `baseUrl`）
- 前缀：`/api/v1`（由前端 `apiPrefix` 拼接，服务端路由不带前缀）
- 鉴权：`Authorization: Bearer <token>`，除 `/health`、`/auth/login`
- 响应头：所有响应带 `X-Request-Id`；`/files`、`/previews` 带 `ETag`，支持 `If-None-Match` → 304
- 错误信封：`{"error": {"code", "message", "details"}}`，`details.request_id` 与响应头一致

共 34 个契约端点 + 4 个辅助端点（文件、预览、导出文件、区域列表）。

---

## 1. 认证与项目

### `POST /auth/login`（公开）

```json
{ "username": "roxysunrise", "password": "demo-1234", "project_id": "harbor-a" }
```

返回 `200`：

```json
{ "token": "…", "user": { "id": "usr_owner", "name": "算法负责人", "role": "项目负责人",
  "project": { "id": "harbor-a", "name": "港口巡查 A 区", "code": "PRJ-A-0271" } } }
```

失败：`401 INVALID_CREDENTIALS`（账号不存在/口令错/停用）、`403 PROJECT_FORBIDDEN`（不是该项目成员）、`404 PROJECT_NOT_FOUND`、`429 RATE_LIMITED`（同一账号 5 分钟内失败 10 次）。

### `GET /me` → 同上 `user` 对象

### `POST /auth/logout` → `204`（撤销当前令牌）

### `GET /projects` → `[{ id, name, code, members:[{id,name,role}] }]`（只返回本人是成员的项目）

### `GET /projects/{project_id}` → 单个项目对象；非成员 `403`

---

## 2. 可靠上传（四阶段）

状态机：`uploading`（transport）→ `verifying`（merge → inspect → preview）→ `ready` / `failed`。
**传输 100% ≠ 可检测**：只有 `status=ready` 且拿到 `image_id` 才能建任务。

### `POST /projects/{pid}/uploads` → `201`

```json
{ "id": "upl_…", "status": "uploading", "stage": "transport", "chunk_size": 1048576,
  "chunks_total": 3, "chunks_received": 0, "pct": 0, "image_id": null,
  "error": null, "expires_at": "…", "file_name": "a.png", "size": 3145728 }
```

请求：`{ "file_name": "a.png", "size": 3145728 }`。后缀必须是 `.png/.jpg/.jpeg`（否则 `422 INVALID_IMAGE`）；`size` 超过上限 `413 FILE_TOO_LARGE`。

### `PUT /uploads/{uid}/chunks/{index}`（`Content-Type: application/octet-stream`）

返回 `{ "upload_id", "index", "chunks_received", "pct" }`。
- 必须按序：跳号 `409 CHUNK_OUT_OF_ORDER`
- 重复提交同一片：`200` 且不追加字节（幂等）
- 单片 > 1 MiB：`422 INVALID_ARGUMENT`；累计超限：`413 FILE_TOO_LARGE`
- 会话已结束：`409 UPLOAD_CLOSED`

### `GET /uploads/{uid}` → 与创建相同结构（续传/轮询用）

### `POST /uploads/{uid}/complete` → `200`，立即返回，校验在后台做

请求：`{ "sha256": "…", "size": 3145728 }`（二者可选，但强烈建议都给）。
分片未收齐 `409 UPLOAD_INCOMPLETE`；已 ready 再调 `200`（幂等）。

轮询 `GET /uploads/{uid}` 直到 `status` 变成 `ready`（拿 `image_id`）或 `failed`（`error` 形如 `CHECKSUM_MISMATCH: …`、`INVALID_IMAGE: …`、`IMAGE_TOO_LARGE: …`）。

---

## 3. 影像、原图与预览

### `GET /images/{id}`

```json
{ "id": "img_…", "project_id": "harbor-a", "file_name": "a.png", "status": "ready",
  "sha256": "…", "width": 2305, "height": 1537, "mode": "RGB", "bytes": 1728992,
  "file_url": "/files/img_…", "preview_url": "/previews/img_…", "created_at": "…" }
```

### `GET /files/{id}`、`GET /previews/{id}`（辅助端点）

都需要 `Authorization`，会逐请求校验项目成员。响应带 `ETag` + `Cache-Control: private, max-age=3600`；
带 `If-None-Match: <etag>` 时返回 `304`（**前端请务必带上**，工作台反复打开能省掉整图重传）。

`file_url` / `preview_url` 是**相对路径**，前端拼接 `baseUrl` 即可；预览最长边 2048（JPEG）。

---

## 4. 检测任务

### `POST /projects/{pid}/tasks` → `201`

```json
{ "image_id": "img_…",
  "parameters": { "conf":0.25, "iou":0.7, "imgsz":1024, "max_det":1000,
                  "tiling":true, "tile_size":1024, "stride":512, "merge_iou":0.1 } }
```

`parameters` 可省略，缺省见 `contracts/ship-detection-v0.2.md`。

返回：

```json
{ "id": "tsk_…", "project_id": "harbor-a", "image_id": "img_…",
  "status": "queued", "attempt": 1, "tiles_total": 0, "tiles_done": 0,
  "temp_count": 0, "final_count": null, "parameters": {…},
  "cancel_requested": false, "error": null,
  "started_at": null, "finished_at": null }
```

- 影像未就绪：`409 IMAGE_NOT_READY`
- 并发上限（默认单项目 2 个排队/运行中）：`429 RATE_LIMITED`

### `GET /tasks/{id}`（前端 250 ms 轮询）

状态：`queued → running → merging → done`，异常 `failed` / `cancelled`。
- `tiles_done/tiles_total`：真实逐片进度，`tiles_total` 在建任务后即为最终值
- `temp_count`：**临时值**，合并完成前累加，只能显示为"临时"
- `final_count`：**仅 `done` 后出现**，是最终候选数
- `error`：失败时形如 `OUT_OF_MEMORY: …`、`SERVER_RESTART: …`

### `POST /tasks/{id}/cancel` → 立即返回任务对象

`cancel_requested=true`，**在切片边界生效**；页面应显示"取消请求中"，继续轮询到 `cancelled`。
`queued` 状态会被直接置为 `cancelled`。

### `POST /tasks/{id}/retry` → 仅 `failed` / `cancelled` 可用（否则 `409 TASK_BUSY`）

`attempt` 自增，计数清零；旧尝试结果文件会被清理，不会出现重复最终计数。

### `GET /tasks/{id}/result` → `ship-detection/0.2`

未完成：`409 RESULT_NOT_READY`。结构见契约；`image` 块是**业务影像记录与算法字段的并集**
（含 `id/project_id/file_url/preview_url` + `sha256/width/height/mode/file_name`）。

---

## 5. 草稿（唯一可写集合）

### `GET /tasks/{id}/draft` → `{ "task_id", "detections": [Detection] }`

首次访问时从 v0 复制而来。`Detection`：

```json
{ "id": 1, "polygon": [[x,y]×4], "class_id": 1, "class_name": "ship",
  "confidence": 0.93, "source_tile_id": 3,
  "review_state": "pending", "origin": "model", "note": null }
```

### `PATCH /tasks/{id}/draft/detections/{did}`

可传 `polygon` / `review_state` / `note` 任意组合。**只传 `polygon` 时服务端自动把状态置为
`modified`**（这样"改了框"就会体现在版本摘要里）。`polygon` 必须是 4 点，服务端会重新规范化顶点顺序。

### `POST /tasks/{id}/draft/detections` → `201`

```json
{ "polygon": [[…]×4], "origin": "manual|ai", "review_state": "confirmed", "note": "…" }
```

人工/AI 新增的 `confidence` 恒为 `null`（不伪造置信度）。

### `POST /tasks/{id}/draft/reset` → `{ "version_id": "v0" }`

用某版本覆盖草稿，返回新的 `{task_id, detections}`。

### `GET /tasks/{id}/draft/regions`（辅助端点）

列出该任务已建区域及其**实时重算**的 counts。

---

## 6. 版本

### `GET /tasks/{id}/versions` → `[{ id, label, author, created_at, readonly, summary, counts }]`

v0 由模型结果自动生成，`readonly=true`。

### `POST /tasks/{id}/versions` → `201`

请求 `{ "label": "人工修订" }`（可选）。返回含 `detections` 的完整版本。
草稿与当前版本一致：`409 NOTHING_TO_COMMIT`。`summary` 形如
`{added, removed, changed, confirmed, deleted}`（`changed` 依赖上面的自动 `modified`）。

### `GET /versions/{version_id}?task_id=…` → 只读版本详情（含 detections）

---

## 7. 疑难复核

### `GET /tasks/{id}/draft/review-queue`

```json
{ "items": [ { "detection_id": 7, "score": 96,
    "reasons": [ { "code": "DUPLICATE", "message": "…", "partner_id": 43, "iou": 0.08 },
                 { "code": "LOW_CONFIDENCE", "message": "…" } ] } ],
  "sampling": [ { "x": 0, "y": 0, "width": 512, "height": 512 } ] }
```

`code` 枚举：`LOW_CONFIDENCE / TILE_BOUNDARY / IMAGE_EDGE / DUPLICATE`。
`score` 只是排序依据，**不是正确概率**；`sampling` 是没有检测目标的 512 网格，供人工抽查漏检。

---

## 8. 区域统计

### `POST /tasks/{id}/draft/regions`

```json
{ "name": "A 区", "polygon": [[0,0],[2305,0],[2305,1537],[0,1537]] }
```

返回：

```json
{ "id": "rgn_…", "task_id": "tsk_…", "name": "A 区", "polygon": […],
  "rule": "centre_point_inside",
  "counts": { "model": 32, "effective": 33, "deleted": 0, "added": 1, "delta": 1 },
  "area_px2": 3542585 }
```

口径：**中心点落入区域**；重叠区域分别统计、不直接相加；`deleted` 不计入 `effective`。

---

## 9. 导出

### `POST /versions/{vid}/exports?task_id=…` → `202`

请求 `{ "formats": ["csv","json","png"] }`（可省略，默认三种）。

```json
{ "id": "exp_…", "version_id": "v1", "task_id": "tsk_…", "status": "running", "files": [] }
```

### `GET /exports/{id}` → 轮询到 `status` 为 `done` / `failed`

```json
{ "status": "done", "files": [
  { "format": "csv", "name": "v1-detections.csv", "url": "/exports/exp_…/files/v1-detections.csv",
    "bytes": 4096, "error": null }, … ] }
```

### `GET /exports/{id}/files/{name}`（辅助端点）→ 文件流

单个格式失败只影响该项（`error` 有值），不影响其他格式。PNG 标注图目前按预览分辨率（最长边 2048）渲染。

---

## 10. AI 辅助候选

核心逻辑只有一份：`backend/ai_assist.py`（检测页与工作台共用）。两条通道：
先在 ROI 上按**原分辨率**再跑一次检测模型，没命中才退化为 Otsu 轮廓；两条都不是语义分割。

### `POST /images/{iid}/ai-candidates`

三种提示方式（`mode` 可省略，按字段自动判定）：

```jsonc
// 1) 点选补框
{ "point": [x, y], "mode": "point" }
// 2) 框选补框
{ "mode": "box", "box": [x0, y0, x1, y1] }
// 3) 精修已有框（新增）：围绕该框外扩 25% 再取候选
{ "mode": "refine", "existing_polygon": [[x,y]×4] }
```

返回：

```json
{ "id": "cand_…", "image_id": "img_…", "status": "preview", "polygon": [[…]×4],
  "source": "detect",            // detect（局部高分辨率命中）| contour（阈值轮廓兜底）
  "confidence": null, "width": 62.5, "length": 148.2,
  "quality": 0.71, "reason": "局部高分辨率检测命中" }
```

区域没东西：`422 NO_CANDIDATE`；提示不合法：`422 INVALID_ARGUMENT`；选区太小：`422 INVALID_REGION`。

合理性过滤（会直接丢候选，不只是排序）：

| 规则 | 门限 | 说明 |
|---|---|---|
| 面积占 ROI | 2%–80% | 太小是碎片，太大是背景 |
| 长宽比 | 1.2–15 | 1:1 多为码头设施；正对船头会被过滤，可手动画 |
| 精修位移 | 中心偏移 ≤ 30% 边长的 | 防止"精修"跳到隔壁船 |
| 精修尺寸 | 0.4–2.5 倍 | 防止把整排船框成一条 |

### `POST /ai-candidates/{cid}/accept` / `cancel`

同前：接受写入草稿（`origin=ai`、`confidence=null`），取消只改状态。
现在会记录事件：`ai.propose` / `ai.accept` / `ai.reject`（含 `mode`、`source`、`quality`），
用于后面统计采纳率与平均修正次数。

### 检测页的对应端点

`POST /vision/segment` 支持同一组字段（`roi` / `point` / `existing_polygon`），
返回的 `candidates[]` 多了 `source` 字段，`model.version` 升为 `0.2`。

---

## 11. 推理端点 `/vision/*`（检测工作台）

| 端点 | 请求要点 | 返回关键字段 |
|---|---|---|
| `GET /vision/capabilities` | — | `service/version/modes/tasks/models/limits`，另加 `loaded` |
| `POST /vision/sessions` | `{kind,name,width,height}` | `{session_id, media_id, kind}` |
| `POST /vision/detect` | `{session_id, kind, frame:{index,time_ms,width,height}, image:{data_url,width,height}, options}` | `{request_id, frame:{index,time_ms,width,height}, model:{name,version,device}, elapsed_ms, count, detections:[{id,label,score,polygon,source}]}` |
| `POST /vision/segment` | 同上 + `roi` 或 `point` | `{request_id, frame, model, elapsed_ms, roi, candidates:[{id,label,quality,confidence:null,polygon,reason}]}` |
| `POST /vision/jobs/{job_id}/cancel` | `{}` | `{cancelled:true}`（同步接口，无后台作业） |

坐标语义：请求里的 `image` 是**下采样后的负载**，返回的 `polygon` 已按
`frame.width / image.width` 还原成**原始媒体像素**。服务端**不做第二次下采样**
（`imgsz` 自动对齐到帧最大边，上限 2048）。

---

## 12. 大图瓦片（显示）与切片预演

两件不同的事，接口分开：

### 11.1 推理切片预演（画网格用）

```http
POST /images/{id}/tile-plan     { "tile_size": 512, "stride": 256 }
```

```json
{ "image": { "width": 1200, "height": 800 },
  "parameters": { "tile_size": 512, "stride": 256 },
  "tile_count": 12,
  "tiles": [ { "id": 1, "x": 0, "y": 0, "width": 512, "height": 512 }, … ],
  "coverage": { "covered_px": …, "image_px": 960000, "overlap_ratio": 2.2768, "step": 256 },
  "note": "仅几何预演，未运行模型；片序与任务实际切片的 id 一致" }
```

用途：任务开始前就能画出**真实网格**、显示"这张图会切几片、重叠多少"；
`tiles[].id` 与结果里的 `source_tile_id` 一一对应，可用来做"已完成格子高亮"。

### 11.2 显示金字塔

```http
GET /images/{id}/tile-manifest
→ { "tile_edge": 256, "max_z": 3,
    "levels": [ { "z": 0, "width": 1200, "height": 800, "cols": 5, "rows": 4, "scale": 1.0 }, … ],
    "url_template": "/tiles/{image_id}/{z}/{x}/{y}" }

GET /tiles/{id}/{z}/{x}/{y}
→ JPEG，带 ETag；带 If-None-Match 命中返回 304
```

约定（**前端必须按这个实现，否则会错位**）：

| 项 | 约定 |
|---|---|
| `z` 方向 | `z=0` 是**原生分辨率**（1:1），`z` 越大越粗，最大层整图只剩一块 |
| 瓦片边长 | 256（manifest 的 `tile_edge` 为准） |
| 坐标 | `x` 向右、`y` 向下，从 0 开始；`(x,y)` 与该层像素的换算：`px = x * 256 * 2^z` |
| 越界 | 返回 `404 TILE_NOT_FOUND` |
| 鉴权 | 与其它接口一致，跨项目 `403`；可直接用于 `<img>`（带 token 时用 fetch + blob，或用短期查询令牌，见下） |

> **注意**：`<img src>` 不会自动带 `Authorization`。两种做法（任选）：
> ① 用 `fetch(url, {headers:{Authorization}})` → `blob()` → `URL.createObjectURL`；
> ② 我后续可加"短期签名查询串"（`?t=<10 分钟有效>`），前端最省事。需要就提，我加。

前端要做的事（约 100–150 行）：视口 → 需要哪几块 → 只请求缺的 → 跨层时保留旧块 → 块未到达时用低层图或纯色占位。

## 13. 错误码总表（前端按 `code` 分支，不要按 message）

| HTTP | code | 典型场景 |
|---|---|---|
| 400 | `INVALID_IMAGE`、`INVALID_FRAME` | 解码失败、非 RGB、后缀不支持 |
| 401 | `UNAUTHENTICATED`、`INVALID_CREDENTIALS` | 令牌缺失/过期、口令错 |
| 403 | `PROJECT_FORBIDDEN` | 非项目成员 |
| 404 | `IMAGE_NOT_FOUND`、`TASK_NOT_FOUND`、`UPLOAD_NOT_FOUND`、`VERSION_NOT_FOUND`、`DETECTION_NOT_FOUND`、`CANDIDATE_NOT_FOUND`、`EXPORT_NOT_FOUND`、`PROJECT_NOT_FOUND` | 资源不存在 |
| 409 | `IMAGE_NOT_READY`、`UPLOAD_INCOMPLETE`、`CHUNK_OUT_OF_ORDER`、`UPLOAD_CLOSED`、`RESULT_NOT_READY`、`NOTHING_TO_COMMIT`、`TASK_BUSY`、`CANDIDATE_CLOSED`、`NO_TASK`、`IMAGE_CHANGED` | 状态冲突 |
| 413 | `FILE_TOO_LARGE`、`FRAME_TOO_LARGE` | 超限 |
| 422 | `INVALID_ARGUMENT`、`CHECKSUM_MISMATCH`、`INVALID_POLYGON`、`INVALID_REGION`、`INVALID_STATE`、`NO_CANDIDATE`、`EXIF_ORIENTATION` | 语义校验失败 |
| 429 | `RATE_LIMITED` | 登录失败过多、任务并发超限 |
| 500 | `INTERNAL_ERROR`、`INFERENCE_FAILED`、`INVALID_MODEL_OUTPUT`、`ENVIRONMENT_OR_IO` | 服务端异常（不含堆栈） |
| 503 | `WEIGHTS_NOT_FOUND`、`MODEL_LOAD_FAILED`、`INCOMPATIBLE_MODEL`、`MISSING_SHIP_CLASS`、`INVALID_DEVICE`、`OUT_OF_MEMORY` | 计算不可用（权重缺失、显存不足等） |

---

## 14. 与前端 OpenAPI 的三处差异

1. **`DetectResponse.frame`**：schema 要求 `index/time_ms/width/height`，而 `DETECT_API.md`
   示例只写了前两个。后端按 **schema** 输出四个字段（多给不少给）。
2. **两套检测结果结构**：主应用用 `ship-detection/0.2`（`class_name/confidence/class_id`），
   检测页用 `label/score/source`。`/vision/detect` 负责转换，业务端点保持 v0.2 原样。
3. **`DetectionResult.image`**：前端 schema 指向业务 `Image`（含 `file_url/preview_url`），
   算法输出是 `file_name/sha256/width/height/mode`。后端返回两者并集。

---

## 15. 联调清单（前端需要做的 5 处）

1. `config.js` → `apiMode: 'http'`，`baseUrl` 指向后端；`detect-config.js` → `mode: 'http'`。
2. 工作台底图：把 `app.js` 里写死的 `assets/harbor.png` 换成 `state.image.preview_url`，
   尺寸用 `state.image.width/height`（否则真实框会与演示图错位）。
3. 预览/原图请求带上 `If-None-Match`（304 可省整图重传）。
4. `detect-config.js` 的 `maxPayloadEdge` 从 1600 提到 **≥2048**，或对大图改走 `/tasks`（切片）。
5. 大图（>8k）不要整图 `drawImage`，等瓦片服务接入（A 侧待交付）。

## 16. 联调自检命令

```powershell
python -m backend.server --port 8000
# 另一个终端：
& .venv/Scripts/python.exe -m backend.tools.check_contract --base-url http://127.0.0.1:8000 `
    --spec frontend/api/openapi.yaml --vision-spec frontend/api/detect-openapi.yaml
```

最近一次：业务 21/21、推理 6/6 通过。
