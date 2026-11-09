# SPEC.md — 遥感船舶检测与人机协同复核系统

| 项 | 内容 |
|---|---|
| 版本 | v1.0（与代码基线 `main` 对齐） |
| 对应提交 | `96cf56d`（2026-11-08） |
| 仓库 | https://github.com/roxysunrise/remote-sensing-ship-review |
| 编写方式 | 先由需求驱动规格，再由规格驱动实现；本文最后按"规格条目 → 代码位置 → 测试用例"逐条回填，保证规格与代码一致 |
| 维护约定 | 任何改变行为、字段、状态语义或计数口径的改动，必须同步本文并在 §10 记录 |

> 本文是**唯一的需求与验收来源**。`docs/` 下的模块控制文档记录实现细节与验证结果，
> `contracts/` 下是跨前后端的接口契约，两者都不得与本文冲突；冲突时以本文为准并回改代码或本文。

---

## 1. 项目目标与非目标

### 1.1 目标

面向**静态港口遥感影像**，让影像分析人员完成一条闭环：

```text
登录 → 上传影像 → 创建检测任务 → 大图查看 → 人工复核（含 AI 辅助补框）
     → 区域统计 → 保存版本 → 导出结果
```

四类可验收能力：

1. **检测可用**：从真实训练权重出发，对大幅影像做切片推理，把旋转框还原到原图像素；
2. **坐标可信**：屏幕、切片、原图三层坐标换算一致，人工编辑后仍成立；
3. **过程可追溯**：模型原始结果不可变，人工修订追加版本，导出绑定版本；
4. **权限可靠**：所有影像、任务、结果、报告按项目隔离，服务端逐请求校验。

### 1.2 非目标（本版明确不做）

| 不做 | 原因 |
|---|---|
| 连续视频流实时检测与多目标跟踪 | 与静态大图任务不同，会扩大采集/解码/跟踪范围 |
| 公开注册、短信/邮件验证 | 采用受控开户（见 §4.2） |
| 多租户计费、高可用集群 | 超出双人项目合理边界 |
| 语义分割模型（SAM 等） | 当前 AI 候选为"局部检测 + 阈值轮廓"，不宣称分割能力 |
| 真实地理坐标系（经纬度）投影 | 仅处理像素坐标；未知字段留空，不生成虚构经纬度 |

### 1.3 成功判据（验收基线）

| 编号 | 判据 | 当前状态 |
|---|---|---|
| A1 | 一条命令启动前后端，浏览器完成 §1.1 全链路 | 通过（`scripts/start-dev.ps1`） |
| A2 | 真实权重下跑通检测，结果含原图四点多边形 | 通过（12 片 → 13 框） |
| A3 | 刷新/重新进入后能找回影像与任务 | 通过（`GET /projects/{id}/images`、`/tasks`） |
| A4 | 人工修订后追加版本，旧版本只读 | 通过（v0 只读 → v1 追加） |
| A5 | 三格式导出绑定版本 | 通过（CSV/JSON/PNG） |
| A6 | 跨项目访问被服务端拒绝 | 通过（403 用例覆盖） |

---

## 2. 用户与权限

| 角色 | 能做什么 | 不能做什么 |
|---|---|---|
| 账号管理员（`users.is_admin = 1`） | 创建账号、改密、停用/启用、调整管理员 | 不等于能看所有项目影像 |
| 项目负责人（`members.role = 项目负责人`） | 项目内全部操作 + 管理本项目成员 | 不能跨项目 |
| 项目分析员（`members.role = 项目分析员`） | 查看项目数据、上传、建任务、复核、统计、导出 | 不能管理成员、不能见到其他项目 |

权限三层：

1. **会话层**：`Authorization: Bearer <token>`，令牌只存 SHA-256 摘要，7 天过期，可撤销；
2. **项目层**：每个业务端点先查 `members`，跨项目返回 `403 PROJECT_FORBIDDEN`；
3. **账号层**：`/users*` 只允许管理员，返回 `403 FORBIDDEN`（与项目权限区分）。

---

## 3. 功能规格

编号沿用项目总览的 F01–F15。状态：**✔ 已实现** / **◐ 部分实现** / **✘ 未实现**。

### F01 影像管理 ✔

**需求**：受控上传、校验、预览、列表、详情。

| 规格条目 | 实现 | 测试 |
|---|---|---|
| 上传前按扩展名与体积预检，仅接受可解码 RGB PNG/JPEG | `api.create_upload` / `storage` | `test_upload_rejects_wrong_suffix` |
| 分片按序落盘，重复片幂等 | `api.put_chunk` | `test_upload_chunk_must_be_in_order` |
| 合并后校验长度与 SHA-256，不一致不进可分析状态 | `api.complete_upload` → `_process_upload` | `test_upload_rejects_bad_checksum` |
| 图像真实解码校验（RGB/EXIF/像素上限）+ 生成 JPEG 预览 | `imaging.py` | `test_upload_pipeline_and_image_record` |
| 项目影像列表（倒序，含 `file_url`/`preview_url`） | `api.list_images` | `test_project_images_list` |
| 原图下载与预览支持 ETag 条件请求（304） | `api.get_file` / `get_preview` | `test_preview_etag_and_304` |

**约束**：未 `ready` 的影像不能建任务（`409 IMAGE_NOT_READY`，用例 `test_task_requires_ready_image`）。
**已知缺口**：F01 原文要求"备注"，当前 `images` 表无 `note` 列 —— 列入 §9 待办。

### F02 检测任务 ✔

**需求**：创建、查看进度、失败提示、取消与重试。

| 规格条目 | 实现 | 测试 |
|---|---|---|
| 任务状态机：`queued → running → merging → done`，异常分支 `failed` / `cancelled` | `jobs.TaskRuntime` | `test_task_lifecycle_end_to_end` |
| 进度来自真实切片处理，逐片写 `tiles_done` | `jobs._run` + `predict_large(on_progress=)` | 同上（断言 `tiles_done == tiles_total`） |
| 临时计数与最终计数分开发布（`temp_count` / `final_count`） | `jobs._publish` | 同上 |
| 取消在切片边界生效（`cancel_requested` → `cancelled`） | `api.cancel_task` | `test_cancel_retry_and_duplicate_guard` |
| 重试产生新 `attempt`，旧尝试产物隔离 | `api.retry_task` | 同上（断言 `attempt=2`） |
| 重复提交不产生重复最终计数 | 幂等检查 | 同上 |
| 重启时未完成任务如实标失败（`SERVER_RESTART`） | `jobs._recover_interrupted` | 代码路径 + 启动清理 |
| 任务结果与 v0 版本**原子发布**（同一事务） | `jobs._publish`（`db.tx()`） | `test_*`（#13 新增 128 行用例） |
| 参数非法在**创建时**返回 422，不入队 | `api._validate_task_parameters` | `test_create_task_rejects_invalid_parameters` |

**队列治理**：单项目并发上限（默认 2）、全局排队上限（默认 20），超限 `429 RATE_LIMITED`。

### F03 大图检测 ✔

**需求**：分区域处理大图并形成完整结果。

| 规格条目 | 实现 | 测试 |
|---|---|---|
| 重叠切片：默认 `tile_size=1024` / `stride=512`，步长覆盖整幅影像 | `algorithm/tiled_inference.plan_tiles` | `test_tiled_inference`（18 项含本例） |
| 每片四点坐标还原到原图像素 | `ShipDetector.predict_large` | `test_detection_contract` |
| 跨片重复用多边形 IoU NMS 合并（`merge_iou=0.1`） | `tiled_inference._merge` | 同上 |
| 结果记录 `source_tile_id` 与 `tiling.*` 元信息 | 结果 `schema_version = ship-detection/0.2` | `test_task_lifecycle_end_to_end` |
| 片级让锁：大图任务不阻塞即时检测 | `detector.DetectorService`（锁下沉到单片） | 算法 18 项无回归 |

**实测（真权重 `yolov8l-obb-dota-v1-80e`，CPU）**：2305×1537 影像 12 片，候选 37、合并抑制 24、最终 13 框，约 5–9 s。

### F04 影像工作台 ◐

**需求**：缩放、平移、显示框、列表定位。

| 规格条目 | 实现 | 状态 |
|---|---|---|
| 大图瓦片金字塔（层级清单 + 按需生成 + ETag 缓存） | `backend/tiles.py`、`api.tile_manifest/get_tile` | ✔ 后端 |
| 切片预演（只算网格不跑模型） | `api.tile_plan` | ✔ |
| 浏览器视口取块、切层、预取 | `frontend/tiles-view.js` | ◐ 已有实现，未做跨浏览器回归 |
| 屏幕/原图坐标换算、拾取、旋转缩放内核 | 前端 `api/geom.js` + 后端 `geometry.py` | ◐ 能力分散，未收敛为独立可复用组件 |

**已知缺口**：原计划的 `components/image-workbench` 独立组件未落地，几何能力目前由后端 `geometry.py` 与前端 `geom.js` 分别实现（前端那份只用于显示与 mock，权威计算在服务端）。

### F05 人工复核 ✔

**需求**：确认、补框、删框、改框、保存。

| 规格条目 | 实现 | 测试 |
|---|---|---|
| 草稿从 v0 懒加载，包含全部模型框 | `api._ensure_draft` | `test_draft_created_from_v0` |
| 只改几何不传状态 → 服务端自动置 `modified` | `api.patch_detection` | `test_task_lifecycle_end_to_end` |
| 人工补框 `origin=manual`、`confidence=null` | `api.add_detection` | 同上 |
| 空修改提交返回 `409 NOTHING_TO_COMMIT` | `api.commit_version` | 同上 |
| 草稿可从指定版本重置 | `api.reset_draft` | 用例覆盖 |

**历史缺陷与修复**：真实权重下草稿曾恒为空（`jobs._ensure_v0` 抢先建 `draft_state` 导致 `_ensure_draft` 早退），2026-11-05 修复并加自愈，见 §8 迭代 R7。

### F06 疑难复核 ✔

**需求**：原因提示、排序、点击定位、空区域抽样。

| 规格条目 | 实现 |
|---|---|
| 四类原因：低置信 / 贴缝 / 贴边 / 疑似重复 | `backend/geometry.py::review_reasons` |
| 空网格抽样建议（避免只看有框区域） | `geometry.sampling_grid` |
| 排序分数**不是**检测正确概率（UI 与文档均如此表述） | 契约 `ReviewItem.score` 注明 |
| 队列返回 `items` + `sampling` | `api.review_queue`（实测 11 条 + 12 格抽样） |

### F07 区域统计 ✔

| 规格条目 | 实现 |
|---|---|
| 顶点圈选多边形，保存为草稿区域 | `api.create_region` |
| 计数规则：**中心点落入**（`centre_point_inside`） | `geometry.count_region` |
| 返回 `model / effective / deleted / added / delta` 五项，区分模型数与有效数 | 同上 |
| 重叠区域不直接相加（口径写在契约） | 契约 `Region` 注明 |

### F08 修订历史与数据回流 ✔

| 规格条目 | 实现 | 测试 |
|---|---|---|
| v0 为模型原始结果，只读不可改 | `versions.readonly=1` | `test_task_lifecycle_end_to_end` |
| 提交生成新版本，带 `summary`（added/removed/changed/confirmed/deleted） | `api.commit_version` | 同上 |
| 历史版本可按版本号读取 | `api.get_version` | 同上 |
| 导出绑定版本 | `api.create_export` | 同上 |

### F09 报告导出 ✔

| 规格条目 | 实现 |
|---|---|
| 三格式：CSV（逐框）、JSON（结构化报告）、PNG（叠框图） | `api.create_export` → `_build_export` |
| 单格式失败不影响其他格式 | 独立 try 分支 |
| 下载路径带鉴权与 `Content-Disposition` | `api.download_export_file` |
| 导出记录 72 h 过期 | `config.EXPORT_TTL_HOURS` |

**已知限制**：PNG 走预览分辨率（最长边 2048），不是原分辨率渲染。

### F10 实验展示 ✘

原计划"策略对比与典型错误案例展示"。当前提供 `scripts/evaluate_ship_obb.py`（整图/切片可复跑评估）与 `docs/module-controls/ship-evaluation.md`，**但未接入页面**，也无独立港口的标注集，故标 ✘。

### F11 集成与交付 ✔

| 规格条目 | 实现 |
|---|---|
| 一条命令起前后端（选解释器→查依赖→找权重→起服务→等 health→开浏览器） | `scripts/start-dev.ps1` / `stop-dev.ps1` |
| 同源反向代理（前端零配置、无 CORS） | `scripts/serve.py` |
| 启动与账号说明 | `docs/quickstart.md` |
| 三层自检脚本 | `scripts/e2e_link_check.py`（39 项）/ `app_flow_check.py`（13 项）/ `browser_link_check.py`（7 项） |

### F12 账号与项目权限 ✔

| 规格条目 | 实现 | 测试 |
|---|---|---|
| 受控开户：管理员建号，无公开注册 | `api.create_user` | `test_admin_creates_user_and_member` |
| 用户名 3–32 位（字母数字 `.` `_` `-`），密码 ≥ 8 位 | `api._check_credentials` | `test_create_user_validation` |
| 改密 / 停用立即撤销该账号其他会话 | `security.revoke_user_sessions` | `test_password_change_revokes_other_sessions` |
| 不能停用自己、不能取消自己的管理员权限 | `api.update_user` → `409 SELF_LOCKOUT` | `test_disable_user_blocks_login_and_self_lockout` |
| 项目成员增删，至少保留一名负责人 | `api.add_member` / `remove_member` | `test_member_add_and_remove` / `test_cannot_remove_last_owner` |
| 命令行开户（不改 API 也能运维） | `scripts/create-user.py` | 手工验证（见 §7 测试记录） |

### F13 可靠上传 ✔

| 规格条目 | 实现 |
|---|---|
| 四阶段状态：`transport → merge → inspect → preview → ready` | `uploads.status/stage` |
| 刷新后按服务端进度恢复（分片数由服务端给出） | `api.get_upload` |
| 单分片 ≤ `SHIP_CHUNK_SIZE`（1 MiB）、累计 ≤ `SHIP_MAX_UPLOAD_BYTES`（256 MiB） | `api.put_chunk` |
| 过期会话 24 h 清理并标 `expired` | `maintenance.purge_expired_uploads` |
| 重复完成提交不生成多个资产 | `api.complete_upload` 幂等 |

### F14 AI 辅助标注 ✔

| 规格条目 | 实现 |
|---|---|
| 点选 / 框选 / 精修三种入口 | `api.request_ai_candidate`、`vision.segment` |
| 先在 ROI 上跑真实检测模型，未命中退化为阈值轮廓（`reason` 写明来源） | `backend/ai_assist.propose` |
| 合理性过滤（面积占比、长宽比、最小面积），不满足直接丢弃 | `ai_assist._sane` |
| 精修约束"不许跳到隔壁船"（中心偏移 ≤30% 边长、尺寸 0.4–2.5 倍） | `ai_assist`（`mode=refine`） |
| `confidence` 恒为 `null`，只给 `quality`，不伪造置信度 | 契约 `Candidate` |
| 候选未确认不进入统计/导出；接受后才写入草稿 | `api.accept_candidate` |
| VLM 规划器可选开关，失败/超时一律降级为纯几何通道 | `backend/ai_planner.py`（默认 `none`） |
| 埋点 `ai.propose / ai.accept / ai.reject / ai.plan / ai.vlm_call` | `events` 表 |

### F15 数据生命周期与运行记录 ◐

| 规格条目 | 实现 | 状态 |
|---|---|---|
| 业务库 + 私有文件分离，文件用系统内部标识 | `storage.py`、`var/blobs` | ✔ |
| 启动清理：过期上传、旧尝试产物、残留临时文件 | `maintenance.py` | ✔ |
| 库 ↔ 文件对账（`fsck`，可清孤立文件） | `tools/fsck.py` | ✔（实测 0 问题） |
| 关键操作事件（登录、上传完成、任务创建/取消/失败、修订、导出） | `events` 表 | ✔ |
| 回收站 / 项目级删除 | —— | ✘ |
| 备份与恢复演练 | —— | ✘（见 §9 T3） |

---

## 4. 接口规格

### 4.1 契约文件（唯一来源）

| 文件 | 内容 |
|---|---|
| `frontend/api/openapi.yaml` | 业务接口 39 条路径 |
| `frontend/api/detect-openapi.yaml` | 推理接口 5 条路径 |
| `contracts/ship-detection-v0.1.md` / `v0.2.md` | 检测结果数据结构（含切片元信息） |
| `contracts/api-project-lists.md` | 项目影像 / 任务列表 |
| `contracts/api-accounts.md` | 账号与成员（待并入 openapi） |

### 4.2 端点总览（实测 52 条路由）

| 分组 | 端点 | 说明 |
|---|---|---|
| 健康与鉴权 | `GET /health`、`POST /auth/login`、`POST /auth/logout`、`GET /me` | 登录返回 `token` + `user`（含 `is_admin`） |
| 账号与成员 | `GET/POST /users`、`PATCH /users/{id}`、`POST /projects/{id}/members`、`DELETE /projects/{id}/members/{uid}` | 详见 `contracts/api-accounts.md` |
| 项目 | `GET /projects`、`GET /projects/{id}`、`GET /projects/{id}/stats` | 项目详情含成员列表 |
| 上传 | `POST /projects/{id}/uploads`、`PUT /uploads/{id}/chunks/{i}`、`POST /uploads/{id}/complete`、`GET /uploads/{id}` | 四阶段状态 |
| 影像 | `GET /projects/{id}/images`、`GET /images/{id}`、`GET /files/{id}`、`GET /previews/{id}` | 后两者支持 ETag/304 |
| 任务 | `POST /projects/{id}/tasks`、`GET /projects/{id}/tasks`、`GET /tasks/{id}`、`POST /tasks/{id}/cancel`、`POST /tasks/{id}/retry`、`GET /tasks/{id}/result`、`GET /tasks/{id}/stats` | 状态机见 §5.2 |
| 草稿 | `GET /tasks/{id}/draft`、`POST/PATCH /tasks/{id}/draft/detections[...]`、`POST /tasks/{id}/draft/reset`、`GET /tasks/{id}/draft/review-queue`、`POST/GET /tasks/{id}/draft/regions` | 草稿是复核工作副本 |
| 版本与导出 | `GET/POST /tasks/{id}/versions`、`GET /versions/{id}`、`POST /versions/{id}/exports`、`GET /exports/{id}`、`GET /exports/{id}/files/{name}` | 导出绑定版本 |
| 大图瓦片 | `POST /images/{id}/tile-plan`、`GET /images/{id}/tile-manifest`、`GET /tiles/{id}/{z}/{x}/{y}` | 金字塔层级 + 按需生成 |
| AI | `POST /images/{id}/ai-candidates`、`POST /ai-candidates/{id}/accept`、`POST /ai-candidates/{id}/cancel`、`GET /ai/planner` | 候选生命周期 |
| 推理 | `GET /vision/capabilities`、`POST /vision/sessions`、`POST /vision/detect`、`POST /vision/segment`、`POST /vision/jobs/{id}/cancel` | 坐标按 `frame/image` 比例还原 |

### 4.3 错误信封

统一结构，前端只判断 `error.code`：

```json
{"error": {"code": "INVALID_ARGUMENT", "message": "…", "details": {"request_id": "req_…"}}}
```

常用码：`UNAUTHENTICATED`(401)、`FORBIDDEN`/`PROJECT_FORBIDDEN`(403)、`*_NOT_FOUND`(404)、
`NOTHING_TO_COMMIT`/`ALREADY_MEMBER`/`LAST_OWNER`/`SELF_LOCKOUT`/`IMAGE_NOT_READY`(409)、
`INVALID_ARGUMENT`/`NO_CANDIDATE`/`CHUNK_OUT_OF_ORDER`(409/422)、`RATE_LIMITED`(429)、
`INVALID_CREDENTIALS`(401)。

---

## 5. 数据规格

### 5.1 表结构（SQLite，14 张）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `users` | `id, username, password_hash, display_name, is_admin, status, created_at` | PBKDF2-SHA256（120k 迭代），只存摘要 |
| `sessions` | `token_hash, user_id, project_id, expires_at, revoked` | 令牌只存 SHA-256；7 天过期 |
| `projects` | `id, code, name` | `code` 唯一（如 `PRJ-A-0271`） |
| `members` | `project_id, user_id, role` | 复合主键；角色二选一 |
| `uploads` | `status, stage, chunk_size, chunks_total, chunks_received, image_id, error, expires_at` | 四阶段 |
| `images` | `sha256, width, height, mode, bytes, status, stored_name` | 私有存储，`stored_name` 为内部标识 |
| `tasks` | `status, attempt, tiles_total, tiles_done, temp_count, final_count, parameters, cancel_requested, result_path` | 状态机载体 |
| `versions` | `task_id, label, readonly, summary, counts, detections` | v0 只读；后续追加 |
| `draft_state` | `task_id, base_version, updated_at` | 草稿基线 |
| `draft_detections` | `task_id, detection_id, polygon, review_state, origin, note` | 复核工作副本 |
| `regions` | `task_id, name, polygon, counts` | 区域统计快照 |
| `exports` | `version_id, task_id, status, files` | 绑定版本 |
| `candidates` | `image_id, polygon, source, width, length, quality, reason, status` | AI 候选 |
| `events` | `project_id, user_id, action, target, detail, created_at` | 操作追溯与埋点 |

### 5.2 任务状态机

```text
queued ──► running ──► merging ──► done
   │           │           │
   │           ├──────────►│  (取消请求：cancel_requested=1，在当前切片边界生效)
   ▼           ▼           ▼
cancelled   failed      failed
                ▲
                └── 重启时 queued/running/merging 一律标 failed（SERVER_RESTART），不假装续算
```

**不变式**：

- `done` ⇒ `final_count` 非空且等于结果文件 `count`；
- `tiles_done == tiles_total` 只在 `done` 时成立；
- `temp_count` 是过程中累计，**不得**用于统计或导出；
- `attempt` 递增，旧 attempt 的结果文件可回收；
- 任务终态与 v0 版本在同一事务提交（避免"有结果无基线版本"）。

### 5.3 坐标与计数口径

| 口径 | 定义 |
|---|---|
| 多边形 | 四点、原图像素、顺序固定（左上起逆时针），不裁剪到边界 |
| 屏幕坐标 | 由前端视口变换得到，仅用于显示；权威值是原图坐标 |
| `/vision/*` 坐标还原 | 输出 = 输入坐标 × (`frame.width / image.width`) |
| 区域计数 | 目标**中心点**落入区域即计数，`centre_point_inside` |
| 区域五项 | `model`（模型候选）、`effective`（有效）、`deleted`、`added`、`delta` |
| 复核状态 | `pending / confirmed / modified / deleted`，四态互斥 |
| AI 候选 | `confidence` 恒 `null`；`quality ∈ [0,1]` 不是概率 |
| 统计 `detections_total` | 模型候选数，**不是**确认数 |

---

## 6. 非功能规格

| 维度 | 规格 | 实测/边界 |
|---|---|---|
| 影像上限 | 单文件 ≤ 256 MiB；解码后 ≤ 67,108,864 像素 | 可环境变量覆盖 |
| 分片 | 1 MiB / 片，按序、幂等 | 单测覆盖越界与乱序 |
| 并发 | 单项目同时排队/运行 ≤ 2；全局队列 ≤ 20 | 超限 429 |
| 单进程 | 队列在内存，多进程会各自消费；GPU 串行 | 已知限制 |
| 重启恢复 | 未完成任务标失败，不续算 | 已知限制 |
| 检测耗时（参考） | 2305×1537 / 12 片 / CPU ≈ 5–9 s | 单机观测，非 SLA |
| 即时检测 | `/vision/detect` 内存直推，1600px ≈ 300–700 ms | 单机观测 |
| 缓存 | 预览与瓦片带 ETag，命中返回 304 | 实测 |
| 安全 | 口令 PBKDF2、令牌摘要、登录限速（5 分钟 10 次）、路径清洗 | 单测覆盖 |

---

## 7. 验证方式与结果

三层自检，均可复跑：

```powershell
# 1) 单元 + 集成（含真实推理）
python -m unittest discover -s tests

# 2) 真实 HTTP 端到端（走 /api/v1，不用 TestClient）
python scripts/e2e_link_check.py --image frontend/assets/harbor.png

# 3) 真实浏览器（Edge headless + CDP）
python scripts/app_flow_check.py
```

| 层 | 覆盖 | 结果 |
|---|---|---|
| 单元/集成 | 算法、上传、任务、草稿、版本、复核、区域、导出、瓦片、统计、AI 候选、账号 | **92 项通过** |
| 真实 HTTP | 39 项：登录→上传→检测 12 片/13 框→草稿 13→空提交 409→补框→v1→复核→区域→导出→统计→AI 候选→/vision 坐标还原 | **39 项通过** |
| 真实浏览器 | 登录→工作台渲染真实项目→点影像进工作台 15+ 目标→四视图无 JS 异常 | **13 项通过** |
| 契约 | `backend/tools/check_contract.py` 对 B 方 openapi 真实响应校验 | 业务 21/21、推理 6/6 |
| 数据一致性 | `python -m backend.tools.fsck` | 0 问题 |
| 账号接口 | `var/verify_accounts.py` | 18 项通过 |

明细记录：`docs/validation/`（`link-2026-11-05.md`、`backend-completeness-2026-11-08.md`、`accounts-2026-11-08.md`、`backend-2026-11-04.md`）。

---

## 8. 需求迭代记录（Spec-Driven 过程）

每一轮都按"发现问题 → 改规格 → 改代码 → 补验证 → 记录"推进。

| 轮次 | 日期 | 触发 | 规格变化 | 代码变化 | 验证 |
|---|---|---|---|---|---|
| R1 | 10-27 | 项目初始化 | 确定 A/B 分工、接口先行、短期分支 | 文档与协作协议 | —— |
| R2 | 10-27 | 检测能力缺失 | 新增"单图推理 → 原图四点多边形"规格 | `algorithm/ship_detector.py` | 17 项算法测试 |
| R3 | 10-29 | 整图缩放丢小目标 | 新增"重叠切片 + 跨片 IoU 合并"规格（v0.2） | `tiled_inference.py`、CLI `--tiled` | 18 项 |
| R4 | 11-04 | 前端需要真实数据 | 新增业务 29 端点 + 推理 5 端点规格 | `backend/` 全量 | 41→73 项 |
| R5 | 11-05 | 刷新后回不到任务 | **新增**"项目影像/任务列表"两条端点 | `list_images` / `list_tasks` | 78→39 项 e2e |
| R6 | 11-05 | 真权重下复核为空 | 明确"草稿必须从 v0 完整加载 + 自愈" | `_ensure_draft` / `_fill_draft` | 反证：回退后 `0 != 1` |
| R7 | 11-06 | 非港口影像检出 0 框 | 类别不再固定 `ship`，`classes` 可空=全部 | `ship_detector` / `vision` | 多类验证脚本 |
| R8 | 11-06 | 结果与 v0 可能不一致 | 新增"终态与 v0 原子发布 + 启动恢复" | `jobs._publish` / `_recover_missing_versions` | +128 行用例 |
| R9 | 11-08 | 非法参数先入队后失败 | 参数校验前置到创建时 | `_validate_task_parameters` | 6 类非法值 422 |
| R10 | 11-08 | 契约缺 10 条实现已有路径 | 契约补齐 + `classes` 空数组语义统一 | `openapi.yaml` + 归一化 | 契约 21/21 |
| R11 | 11-08 | 没有注册入口 | **新增受控开户与成员管理规格** | 5 个账号端点 + CLI | 7 项单测 + 18 项 HTTP |
| R12 | 11-09 | 交付材料缺失 | 新增 SPEC.md、架构图、报告与测试记录 | 本文及 `docs/report/` | 本文自检 |

**规格同步机制**：每轮先改本文（或对应契约），再改代码，最后把"测试用例名"回填到 §3 的表格里。
本文 §3 的每个 ✔ 条目都能在代码里找到实现、在 `tests/` 里找到用例，这是"规格与代码一致"的自证。

---

## 9. 未实现与已知限制（诚实清单）

| 编号 | 项 | 影响 | 现状 |
|---|---|---|---|
| T1 | `images` 备注字段 | F01 缺口 | 未实现 |
| T2 | 回收站 / 项目级删除 | F15 缺口 | 未实现 |
| T3 | 备份恢复演练 | 验收硬门槛 | 只有 `fsck` 一致性检查，无备份脚本 |
| T4 | `components/image-workbench` 独立组件 | F04 缺口 | 几何能力分散在后端与前端 |
| T5 | 真实标注集上的精度评估 | F10 缺口 | 有评估脚本，无独立港口标注集 |
| T6 | GPU / 并发 / 重启故障注入 | 非功能验证 | 未做 |
| T7 | 语义分割（SAM） | F14 边界 | 明确不做，候选为"局部检测 + 轮廓兜底" |
| T8 | 导出 PNG 分辨率 | F09 限制 | 走预览分辨率（2048） |
| T9 | 检测页会话（B 侧） | 登录体验 | 已转 Issue 交前端负责人 |

---

## 10. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-09 | v1.0 | 首次成文：按已实现功能回填规格，逐条绑定代码位置与测试用例；列出 9 项未实现/限制 |
