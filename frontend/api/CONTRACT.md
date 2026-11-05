# 前后端接口对接说明

前端所有数据访问都经过 `api/client.js` 暴露的 `window.API`，界面代码不直接读写数据。
后端就绪后只需改 `config.js`：

```js
window.APP_CONFIG = {
  apiMode: 'http',                       // 'mock' → 'http'
  baseUrl: 'http://127.0.0.1:8000',      // 同源部署可留空
  apiPrefix: '/api/v1',
  pollIntervalMs: 250
};
```

机器可读规格见 [`openapi.yaml`](openapi.yaml)；本文补充状态机、错误码与接入顺序。

## 1. 约定

| 项 | 约定 |
|---|---|
| 传输 | HTTPS + JSON（上传分片为 `application/octet-stream`） |
| 鉴权 | `Authorization: Bearer <token>`；登录接口除外 |
| 前缀 | `/api/v1` |
| 时间 | ISO 8601 带时区，如 `2026-10-29T09:44:25+00:00` |
| 坐标 | 原图像素，原点左上，x 向右、y 向下；多边形顺时针、起点为最小 `(y, x)` 顶点 |
| 分页 | 首版不做；列表接口直接返回全量（单项目规模可控） |
| 幂等 | 分片上传与任务创建需支持重试不产生重复资源 |

### 错误信封

所有非 2xx 响应统一为：

```json
{ "error": { "code": "IMAGE_NOT_READY", "message": "影像尚未通过验证，不能创建检测任务", "details": {} } }
```

`code` 是稳定的机器可读标识，前端按 `code` 分支；`message` 面向使用者，可直接展示。
前端 `ApiError` 会保留 `status` / `code` / `message` / `details`。

| HTTP | 典型 code | 含义 |
|---|---|---|
| 401 | `UNAUTHENTICATED`、`INVALID_CREDENTIALS` | 未登录、会话失效或密码错误 |
| 403 | `PROJECT_FORBIDDEN` | 不是项目成员（跨项目访问） |
| 404 | `TASK_NOT_FOUND`、`IMAGE_NOT_FOUND`、`UPLOAD_NOT_FOUND`、`VERSION_NOT_FOUND`、`DETECTION_NOT_FOUND`、`CANDIDATE_NOT_FOUND`、`EXPORT_NOT_FOUND` | 资源不存在或已过期 |
| 409 | `IMAGE_NOT_READY`、`UPLOAD_INCOMPLETE`、`CHUNK_OUT_OF_ORDER`、`UPLOAD_CLOSED`、`RESULT_NOT_READY`、`NOTHING_TO_COMMIT`、`TASK_BUSY`、`CANDIDATE_CLOSED`、`NO_TASK` | 状态冲突 |
| 413 | `FILE_TOO_LARGE` | 超出文件或像素上限 |
| 422 | `CHECKSUM_MISMATCH`、`INVALID_POLYGON`、`INVALID_STATE`、`INVALID_REGION`、`NO_CANDIDATE` | 语义校验失败 |
| 500 | `INTERNAL_ERROR` | 服务端异常，不得暴露堆栈 |

## 2. 状态机

### 上传

```
POST /projects/{pid}/uploads
        │
        ▼
   uploading ──PUT chunks/0..N-1──▶ (chunks_received = N)
        │
        ▼ POST /uploads/{uid}/complete
   verifying[stage=merge] → [inspect] → [preview]
        │
        ├──▶ ready      (image_id 可用，可创建任务)
        └──▶ failed     (error 给出原因，需重新上传)
```

- `stage` 依次为 `merge` / `inspect` / `preview` / `ready`，前端据此渲染四步进度。
- **传输 100% 不等于可检测**：`status` 仍为 `verifying` 时不得允许创建任务。
- 续传以服务端 `chunks_received` 为准；重复提交同一分片不追加字节。
- 浏览器刷新后无法自动重新读取本地文件，必须由用户重新选择文件再续传。

### 检测任务

```
POST /projects/{pid}/tasks → running
        │  轮询 GET /tasks/{tid}
        ▼
   running ──(tiles_done 递增, temp_count 累加)──▶ merging ──▶ done
        │
        ├── POST /cancel → cancel_requested=true → cancelled（在可终止边界生效）
        └── POST /retry  → attempt+=1，重算整次尝试
```

- `temp_count` 是**临时值**，仅用于过程展示；`final_count` 在 `done` 后才出现。
- 取消请求与取消完成是两个状态，页面先显示"取消请求中"。
- 重试按尝试编号隔离中间结果，不得产生重复最终计数。

### 草稿与版本

```
v0（模型原始，不可变）
        │  GET /tasks/{tid}/draft  ← 草稿初始为 v0 副本
        ▼
   draft（唯一可写集合）
        │  PATCH /draft/detections/{did}   改几何/状态/备注
        │  POST  /draft/detections         人工或 AI 新增
        │  POST  /draft/reset              用某版本覆盖草稿
        ▼
   POST /tasks/{tid}/versions  → v1, v2 …（不可变快照）
```

- 历史版本只读；前端切到历史版本时禁用编辑并显示横幅。
- 提交时服务端计算 `summary`（added / removed / changed / confirmed / deleted）。
- 草稿与当前版本一致时返回 `409 NOTHING_TO_COMMIT`。

### AI 候选

```
POST /images/{iid}/ai-candidates → preview
        ├── accept → 写入草稿（origin=ai, confidence=null, review_state=confirmed）
        ├── cancel → cancelled（不改变正式结果）
        └── 无命中 → 422 NO_CANDIDATE（前端提示改手动标注）
```

候选未确认前**不进入**修订、统计与导出；`confidence` 必须为 `null`，不得伪造成 1.0。

## 3. 端点清单与前端调用位置

| 方法 | 路径 | 前端调用 | 说明 |
|---|---|---|---|
| POST | `/auth/login` | `API.login` | 登录并绑定项目 |
| POST | `/auth/logout` | `API.logout` | 撤销会话 |
| GET | `/me` | `API.me` | 当前账号 |
| GET | `/projects` | `API.listProjects` | 账号可访问项目 |
| GET | `/projects/{pid}` | `API.getProject` | 项目与成员 |
| POST | `/projects/{pid}/uploads` | `API.createUpload` | 创建上传会话 |
| PUT | `/uploads/{uid}/chunks/{i}` | `API.putChunk` | 上传分片 |
| GET | `/uploads/{uid}` | `API.getUpload` | 续传/状态轮询 |
| POST | `/uploads/{uid}/complete` | `API.completeUpload` | 合并与校验 |
| GET | `/images/{iid}` | `API.getImage` | 影像记录 |
| POST | `/projects/{pid}/tasks` | `API.createTask` | 创建任务 |
| GET | `/tasks/{tid}` | `API.getTask` | 进度轮询 |
| POST | `/tasks/{tid}/cancel` | `API.cancelTask` | 请求取消 |
| POST | `/tasks/{tid}/retry` | `API.retryTask` | 重试 |
| GET | `/tasks/{tid}/result` | `API.getTaskResult` | ship-detection/0.2 |
| GET | `/tasks/{tid}/draft` | `API.getDraft` | 工作副本 |
| PATCH | `/tasks/{tid}/draft/detections/{did}` | `API.patchDetection` | 编辑/确认/删除/备注 |
| POST | `/tasks/{tid}/draft/detections` | `API.addDetection` | 人工新增 |
| POST | `/tasks/{tid}/draft/reset` | `API.resetDraft` | 版本恢复 |
| GET | `/tasks/{tid}/draft/review-queue` | `API.getReviewQueue` | 疑难队列与抽样 |
| POST | `/tasks/{tid}/draft/regions` | `API.createRegion` | 区域盘点 |
| GET | `/tasks/{tid}/versions` | `API.listVersions` | 版本列表 |
| POST | `/tasks/{tid}/versions` | `API.commitVersion` | 提交新版本 |
| GET | `/versions/{vid}?task_id=` | `API.getVersion` | 版本详情 |
| POST | `/versions/{vid}/exports?task_id=` | `API.createExport` | 创建导出 |
| GET | `/exports/{eid}` | `API.getExport` | 导出状态与链接 |
| POST | `/images/{iid}/ai-candidates` | `API.requestAiCandidate` | AI 候选 |
| POST | `/ai-candidates/{cid}/accept` | `API.acceptAiCandidate` | 接受候选 |
| POST | `/ai-candidates/{cid}/cancel` | `API.cancelAiCandidate` | 取消候选 |
| GET | `/health` | `API.health` | 健康检查 |

## 4. 接入顺序建议

1. **鉴权与项目**：`/auth/login`、`/me`、`/projects/{pid}`。先把 401/403 跑通。
2. **上传**：`createUpload` → `putChunk` → `completeUpload` + 轮询。重点验证续传与校验失败。
3. **任务**：`createTask` → 轮询 `getTask` → `getTaskResult`。重点验证取消与重试不产生重复计数。
4. **草稿**：`getDraft` / `patchDetection` / `addDetection` / `commitVersion`。重点验证并发提交冲突。
5. **复核与统计**：`review-queue`、`regions`。这两处是纯计算，可先用固定输入做单元测试。
6. **AI 与导出**：`ai-candidates`、`exports`。导出返回短期签名链接，需确认撤权策略。

## 5. mock 与真实后端的差异

| 项 | mock（默认） | 真实后端 |
|---|---|---|
| 状态 | 浏览器内存，刷新即丢失 | 数据库 + 对象存储 |
| 鉴权 | 固定账号，token 不校验 | 真实会话，逐请求校验 |
| 任务进度 | `setInterval` 模拟切片推进 | Worker 真实执行 |
| 检测结果 | 预置的 `data.js` | 模型推理输出 |
| AI 候选 | 命中预置漏检目标即返回其轮廓 | 提示分割模型 |
| 导出产物 | 浏览器内生成 Blob | 服务端生成，返回签名链接 |
| 延迟 | 25–90ms 随机 | 网络与真实计算 |

mock 的行为（状态码、错误码、字段名、状态迁移）与本文档一致，因此切换适配器后
**界面不需要改**；差异只在上表这些实现细节。

## 6. 尚未定义的部分

以下能力当前接口未覆盖，属后续增强，接入前需另行约定：

- 实时推送（SSE / WebSocket）替代轮询
- 多人协作与版本冲突的合并策略（当前是整份快照提交）
- 局部快检（圈选区域单独检测）的独立范围与计数口径
- 回收站、软删除与保留期策略
- 报告模板与打印版式
- 操作审计日志查询
