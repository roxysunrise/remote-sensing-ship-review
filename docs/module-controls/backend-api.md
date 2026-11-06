# 后端 API（对接 B 方前端） 控制文档

## 增量：`/vision/detect` 多类别（2026-11-06）

- 问题：`/vision/detect` 把 `classes` 固定成 `[ship]`（`detector.predict_image` → `ShipDetector` 内部写死），
  15 类权重只输出 1 类；网球场地、棒球场、卡车场影像一律返回 0 个框，只有港口图有框。
- 改动：`backend/vision.py` 新增 `_parse_classes`（缺省/`null`/`"all"` → 全类；列表或 `"1,3"` → 过滤），
  `detect` 透传 `classes`；响应新增 `by_class`、`detections[].class_id`、`model.classes`、
  `model.classes_filtered`，`label` 不再兜底成 `ship`；`segment` 的候选 `label`/`class_id` 取自真实检测结果；
  `detector.py` 的 `_tiling_keys` 增加 `classes`；`ai_assist.py` 候选带上 `label`/`class_id`；
  `api.py`、`jobs.py` 草稿与 AI 候选入库不再把类别写死成 `1 / "ship"`。
- 兼容：不传 `classes` 的老调用方现在会收到全部类别（这是本次修复的目的）；需要旧行为显式传 `[1]`。
  `by_class`/`class_id` 为新增字段，不影响既有消费者。
- 验证：`python -m scripts.verify_multiclass_http --dir <影像目录>`（TestClient 走真实 `/vision/detect`），
  4/4 影像出现非 ship 类别（网球场 15、棒球场 10、卡车场 131、港口 105）；
  显式 `classes=[1]` 时网球场图回到 0 且 `classes_filtered=true`。单测 78 项通过。
- 未验证：未在 GPU / 生产服务实测；大图缩放到 1600 的漏检不在本次范围。
- Git：分支 `codex/multi-class-detect`，基线 `codex/integration`。

## 身份与状态

- 模块 ID：`backend-api`；A 主责（工程骨架 + 计算接入），业务页面与业务数据模型由 B 方提出并确认。
- 更新日期：2026-11-05（Asia/Shanghai）。
- 状态：**已接入（部分）**。2026-11-05 完成真实权重 + 真实 HTTP + 真实浏览器联调：登录、工作台渲染、
  上传、检测、复核、版本、区域、导出、`/vision/*` 全链路通过。仍有两处 B 侧未接：
  工作台影像卡片仍用内置演示影像（新端点 `GET /projects/{id}/images` 已就绪待接）、
  `index.html` 的"演示数据"徽章硬编码。未合入 main、未部署、未做应用验收。
- Git：分支 `codex/app-backend`（实现提交 `148be2e`，远端已核对一致），基线 `codex/ship-tiled-inference`（685eddc）。
- Issue：[#7](https://github.com/roxysunrise/remote-sensing-ship-review/issues/7)；
  PR：[#8](https://github.com/roxysunrise/remote-sensing-ship-review/pull/8)（base `codex/ship-tiled-inference`，mergeable clean）。
- 上传/合并/部署分别记录：已推送并创建 PR；**未合并、未部署、未做应用验收**。

## 目标与边界

- 负责：实现 `frontend/api/openapi.yaml` 的 29 个业务端点与 `detect-openapi.yaml` 的 5 个推理端点；
  鉴权/会话、可恢复上传、影像校验与预览、任务状态机与 Worker、草稿与版本、疑难复核、区域统计、
  导出、AI 候选、`/vision/*` 坐标还原。
- 不负责：前端页面与交互（B）；账号成员业务规则（B 提出，A 只做接入骨架与边界检查）；
  语义分割模型（尚未选型，当前用局部检测 + 阈值轮廓兜底）。
- 本轮验收条件：接口通过前端 OpenAPI 的字段/枚举校验；真实模型跑通"上传 → 任务 → 结果 → 复核 → 版本 → 导出"。

## 文件与调用

- 源码：`backend/`（`server.py` 入口，`api.py` 业务，`vision.py` 推理，`jobs.py` 任务运行时，
  `detector.py` 算法适配，`storage.py`/`imaging.py` 文件与图像，`geometry.py` 复核与区域，
  `security.py` 口令会话，`db.py` 持久化，`errors.py` 错误映射，`tools/check_contract.py` 契约校验）。
- 算法增量（本分支）：`algorithm/tiled_inference.predict_large` 增加可选 `on_progress` /
  `should_cancel`；`ShipDetector.predict_large` 透传。**默认参数下输出不变**，原 17 项测试仍通过。
- 脚本：`scripts/run-backend.ps1`。
- 依赖：`backend/requirements.txt`（FastAPI/uvicorn/pydantic/httpx）+ `algorithm/requirements.txt`。
- 启动：`python -m backend.server --port 8000`；前端改 `apiMode: 'http'` 即可联调。
- 数据：`var/`（SQLite + 私有文件），已 Git 忽略；业务库是唯一写入口，Worker 只写 `var/results/`。

## 验证证据

| 日期/提交/环境 | 实际命令 | 结果 | 未覆盖 |
|---|---|---|---|
| 2026-11-04 / 本地 / Windows + Python3.10.20 + CPU 推理 | `python -m unittest tests.test_backend -v` | 17 项通过（含真实推理端到端 27.7s） | 并发、多进程、GPU |
| 2026-11-04 / 同上 | `python -m unittest tests.test_detection_contract tests.test_tiled_inference -v` | 原 17 项仍通过（增量改动无行为变化） | — |
| 2026-11-04 / 同上 | `python -m backend.tools.check_contract --base-url http://127.0.0.1:8012`（spec 取 B 方 PR #6 的 `openapi.yaml` 与 `detect-openapi.yaml`） | 业务 21/21、推理 6/6 通过 | 未覆盖全部 29 端点（其余需构造前置状态） |
| 2026-11-04 优化轮 / 同上 + GPU | `python -m unittest discover -s tests` | **41 项通过**（18 算法 + 23 后端） | 并发压力、备份恢复 |
| 2026-11-04 优化轮 / 同上 | 优化后重跑契约校验 | 业务 21/21、推理 6/6 | 同上 |
| 2026-11-04 优化轮 / 同上 | 实机 HTTP：预览 `ETag` → 304；`X-Request-Id`；`/vision/detect` 80 ms（GPU、模型已加载） | 通过 | 多客户端并发缓存行为 |
| 2026-11-04 AI 候选轮 / 同上 | `python -m unittest tests.test_ai_assist -v` | 8 项通过（脚本化检测器，不依赖权重） | 真实分割模型效果 |
| 2026-11-04 AI 候选轮 / 同上 | `python -m unittest discover -s tests` | **51 项通过**（18 算法 + 25 后端 + 8 ai_assist） | 见下 |
| 2026-11-04 瓦片/规划器轮 / 同上 | `python -m unittest tests.test_tiles_and_planner -v` | 10 项通过（瓦片 5 + 规划器 5） | 浏览器实际贴块 |
| 2026-11-04 瓦片/规划器轮 / 同上 + 实机 HTTP | 上传 1200×800 图后调用 `tile-plan` / `tile-manifest` / `/tiles/0/0/0` | 12 片（512/256）、4 层金字塔、瓦片 1652B JPEG 带 ETag、越界 404 | 大图渲染帧率、并发取块 |
| 2026-11-04 瓦片/规划器轮 / 同上 | `python -m unittest discover -s tests` | **62 项通过** | VLM 真实效果（未接模型） |
| 2026-11-04 统计轮 / 同上 | `python -m unittest tests.test_stats -v` | 3 项通过（聚合口径/时间过滤/单任务） | 大数据量下的查询性能 |
| 2026-11-04 统计轮 / 同上 | `python -m unittest discover -s tests` | **69 项通过** | 页面展示（B 侧） |
| 2026-11-04 VLM 接入轮 / 同上 | `python -m unittest tests.test_tiles_and_planner -v` | 13 项通过（预算/事件/状态） | 真实 VLM 效果 |
| 2026-11-04 VLM 接入轮 / 同上 | `python -m unittest discover -s tests` | **73 项通过** | 同上 |
| 2026-11-04 / 同上 | 重启恢复：启动时把 `queued/running/merging` 任务标记 `SERVER_RESTART` 失败 | 代码路径已实现 | 未做故障注入实测 |
| 2026-11-05 联调轮 / Windows + Python3.10.20 + 官方 OBB 权重 + CPU | `python -m unittest discover -s tests`（`SHIP_WEIGHTS` 指向真实权重） | **77 项通过**；真实推理用例首次真正执行 | GPU、并发、重启故障注入 |
| 2026-11-05 联调轮 / 同上 + 真实 uvicorn + 真实 socket | `python scripts/e2e_link_check.py`（走 `/api/v1`，非 TestClient） | **39 项通过**：登录→分片上传→影像→瓦片→任务 12 片/13 框→草稿 13→空提交 409→补框→v1→复核 11 条→区域 13/14→三格式导出→统计→AI 候选 `source=detect`→`/vision/detect` 坐标还原 | 多用户并发、GPU |
| 2026-11-05 联调轮 / Edge headless + CDP | `python scripts/browser_link_check.py` | **7 项通过**：真实 DOM 填表登录→跳转工作台→渲染"港口巡查 A 区"→检测页徽章"真实接口"；截图见 `docs/validation/screens/` | 完整页面交互回归（仅关键路径） |
| 2026-11-05 联调轮 / 同上 | 契约校验（B 方 `openapi.yaml` / `detect-openapi.yaml`） | 业务 21/21、推理 6/6 | 其余端点需构造前置状态 |

详细记录见 [`docs/validation/backend-2026-11-04.md`](../validation/backend-2026-11-04.md)。

## 协作与风险

- 上游：`algorithm/`（检测与几何，A）、`contracts/ship-detection-v0.2.md`（待双方确认）。
- 下游：B 方 `frontend/`（PR #6）。需同步的变更：
  1. 检测页 `maxPayloadEdge=1600` + JPEG 有损会把等效分辨率压到约 0.44×，建议提高到 ≥2048 或增加原图通道；
  2. `DetectResponse.frame` 的 schema 要求 `width/height`，与 `DETECT_API.md` 示例不一致，后端按 schema 输出；
  3. 检测页 `detections[]`（`label/score/source`）与主应用 v0.2（`class_name/confidence`）是两套 schema，
     由 `/vision/detect` 转换，业务端点保持 v0.2 原样；
  4. `frontend/api/geom.js` 是几何的第二份实现，只能用于显示与 mock，权威计算在服务端。
- 风险：单进程内存队列；无并发编辑冲突检测；AI 候选非语义分割；导出 PNG 走预览分辨率。
- 下一步：与前端做一次真实浏览器联调 → 补 `SSE/轮询` 以外的推送（可选）→ 语义分割模型选型（F14）。

## 简短变更记录

- 2026-11-04：新建后端骨架，实现全部 34 个端点；`predict_large` 增加进度/取消钩子（默认行为不变）；
  补 17 项后端端到端测试与契约校验工具；本地 CPU 实测通过。
- 2026-11-04：实现提交 `148be2e` 已推送并核对远端一致，创建 Issue #7 与 PR #8（base `codex/ship-tiled-inference`）。
  上传与合并/部署分别记录：未合并、未部署、未做浏览器联调。
- 2026-11-04（优化轮）：修三处缺陷（改几何自动 `modified`、分片累计上限、过期上传/旧结果回收）；
  片级让锁、`/vision/*` 内存直推、tiled 输入快检、模型预热、ETag/304、请求 ID、队列限流、`fsck` 对账。
  测试 41 项通过，契约 21/21 + 6/6；实测 `/vision/detect` 80 ms（GPU，模型已加载）、预览条件请求 304。
  行为变化：tiled 输入变化改为 `size/mtime` 快检（README 已记录边界）。仍未合并、未部署。
- 2026-11-04（AI 候选轮）：把 `vision.py` 与 `api.py` 两份重复的 ROI 逻辑合并进 `backend/ai_assist.py`；
  新增 `mode: refine` + `existing_polygon` 精修模式与"不许跳到隔壁船"的约束；补 `ai.propose / ai.accept / ai.reject`
  埋点。修掉长宽比对边取值的 bug（长宽恒等 1，导致候选被 ASPECT 过滤掉）。测试 51 项通过。仍未合并、未部署。
- 2026-11-04（瓦片/规划器轮）：新增 `tiles.py` 与三个端点（`tile-plan` 推理切片预演、`tile-manifest` 金字塔层级、
  `/tiles/{id}/{z}/{x}/{y}` 按需生成 + ETag 缓存）；新增 `ai_planner.py`（VLM 只做"有没有船"的判断，
  几何仍由 ai_assist 产出，默认关闭、失败降级），`ai_assist.propose` 增加 `gate` 钩子。
  实机验证 1200×800 图：12 片 / 4 层 / 瓦片带 ETag。测试 62 项通过。仍未合并、未部署。
- 2026-11-04（业务统计轮）：新增 `stats.py` 与两个端点（项目统计、单任务统计），只聚合业务库真实记录；
  口径（候选数≠确认数、accept_rate 分母、duration 仅统计已完成）写进接口文档。测试 69 项通过。
  仍未合并、未部署。
- 2026-11-04（VLM 接入轮，方向改为应用优先）：VLM 走 OpenAI 兼容接口，支持超时/重试/单次 token 上限/
  每日额度，超预算自动降级；每次调用写 `ai.vlm_call` 事件；新增 `GET /ai/planner` 状态端点。
  测试 73 项通过。评估体系从论文级 AP 降级为抽样自检 + 运行统计。仍未合并、未部署。
- 2026-11-05（联调轮）：**修 P0 缺陷——真实任务跑完后草稿恒为空**。`jobs._ensure_v0` 发布 v0 时顺手建了
  `draft_state`，而 `api._ensure_draft` 以"state 存在"为早退条件，导致 v0 的框从未进入 `draft_detections`；
  表现为工作台打开是空画布，且首次提交会把空草稿当成"删除全部框"提交，静默产生清空结果的版本。
  修法：删掉 `jobs` 里的 INSERT，草稿统一由 `_ensure_draft` 懒创建；抽出 `_fill_draft` 并加自愈
  （state 存在但无内容、基线版本非空时补齐）。反证：回退修复后 `test_task_lifecycle_end_to_end`
  报 `AssertionError: 0 != 1`。
  同时补 `GET /projects/{id}/images`（工作台此前无端点可列影像，只能拿内置演示影像顶替）。
  新增 `scripts/e2e_link_check.py`（真实 HTTP 39 项）与 `scripts/browser_link_check.py`（Edge headless + CDP 7 项）。
  全套 77 项测试通过，契约 21/21 + 6/6。详细记录见
  [`docs/validation/link-2026-11-05.md`](../validation/link-2026-11-05.md)。仍未合并、未部署。
- 2026-11-05（可直接运行轮）：补 `GET /projects/{id}/tasks`（带影像摘要、按 `status`/`image_id` 过滤、
  `limit` 上限 200），加上前一轮的 `GET /projects/{id}/images`，前端终于能列出项目影像与任务
  （此前只能用内置 `SCENE` 顶替，刷新后回不到已有任务）。
  新增 `scripts/serve.py`（静态页 + `/api/v1` 同源反代，前端 `baseUrl` 留空即可、无 CORS）、
  `scripts/start-dev.ps1` / `stop-dev.ps1`（一条命令起停：选解释器→查依赖→找权重→起后端→起前端→
  等 health→开浏览器，PID 记 `var/dev-pids.json`）、`scripts/app_flow_check.py`（Edge headless 走查 13 项）。
  契约补充见 [`contracts/api-project-lists.md`](../../contracts/api-project-lists.md)（待 B 方确认并入 openapi），
  使用说明见 [`docs/quickstart.md`](../quickstart.md)。
  测试 78 项通过；真实浏览器 13 项全过（影像列表 6 条、任务列表 6 条、点影像进工作台 15 个目标、四视图无 JS 异常）。
  仍未合并、未部署。
