# 后端 API（对接 B 方前端） 控制文档

## 身份与状态

- 模块 ID：`backend-api`；A 主责（工程骨架 + 计算接入），业务页面与业务数据模型由 B 方提出并确认。
- 更新日期：2026-11-04（Asia/Shanghai）。
- 状态：**可接入**。本地实测通过，未合入 main，未部署，未与前端做浏览器联调。
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
| 2026-11-04 / 同上 | 重启恢复：启动时把 `queued/running/merging` 任务标记 `SERVER_RESTART` 失败 | 代码路径已实现 | 未做故障注入实测 |

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
