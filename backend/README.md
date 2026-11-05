# 后端：为 `frontend/` 提供真实数据与计算

实现 B 方前端（PR #6，`frontend/`）声明的**全部接口**：业务 29 个端点 + 推理 5 个端点，
另加 A 侧补充端点：项目/任务统计 2、大图瓦片 3、切片预演 1、项目影像列表 1、VLM 状态 1。
检测与几何一律调用仓库里的 `algorithm/`，后端不复制算法实现。

- 技术栈：Python 3.10 / FastAPI / SQLite（WAL）/ 本地私有文件目录
- 契约来源：`frontend/api/openapi.yaml`、`frontend/api/detect-openapi.yaml`、`contracts/ship-detection-v0.2.md`
- 当前状态：**已接入（部分）**（2026-11-05 真实权重 + 真实 HTTP + 真实浏览器联调通过；未合入 main，未部署）

## 1. 启动

```powershell
# 1) 算法依赖（PyTorch / Ultralytics，体积大）
& .venv/Scripts/python.exe -m pip install -r algorithm/requirements.txt
# 2) 后端依赖
& .venv/Scripts/python.exe -m pip install -r backend/requirements.txt
# 3) 启动（默认 127.0.0.1:8000；演示数据会自动建好）
& .venv/Scripts/python.exe -m backend.server --port 8000
# 或：& scripts/run-backend.ps1
```

前端联调（另一个终端）：

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory frontend
# 浏览器打开 http://127.0.0.1:8765/login.html
```

前端切到真实后端只需改一处：`frontend/config.js` 的 `apiMode: 'mock' → 'http'`；
检测页改 `frontend/detect-config.js` 的 `mode: 'mock' → 'http'`。

### 演示账号（受控开户，不含公开注册）

| 账号 | 密码 | 项目 | 角色 |
|---|---|---|---|
| `roxysunrise` | `demo-1234` | harbor-a / harbor-b | 项目负责人 |
| `analyst` | `demo-1234` | harbor-a | 项目分析员 |

密码只在首次初始化时写入，且仅存 PBKDF2-SHA256 摘要。生产使用务必改环境变量：

```powershell
$env:SHIP_SEED_PASSWORD = "..."; $env:SHIP_SEED_ON_START = "0"   # 已有库时不再播种
```

## 2. 配置（全部可用环境变量覆盖）

| 变量 | 默认 | 说明 |
|---|---|---|
| `SHIP_VAR_DIR` | `./var` | 运行数据根目录（影像、预览、结果、导出、分片） |
| `SHIP_DB_PATH` | `./var/ship.db` | SQLite 路径 |
| `SHIP_WEIGHTS` | `models/yolov8l-obb-dota-v1-80e/best.pt` | 检测权重，不入库 |
| `SHIP_DEVICE` | `cpu` | `cpu` 或 CUDA 序号，如 `0` |
| `SHIP_CHUNK_SIZE` | 1 MiB | 上传分片大小 |
| `SHIP_MAX_UPLOAD_BYTES` | 256 MiB | 单文件上限 |
| `SHIP_MAX_PIXELS` | 67,108,864 | 解码后像素上限（与 v0.2 一致） |
| `SHIP_UPLOAD_TTL_HOURS` | 24 | 上传会话保留时间，过期后启动清理标记 `expired` |
| `SHIP_MAX_ACTIVE_TASKS` | 2 | 单项目同时排队/运行的任务上限，超出返回 429 |
| `SHIP_MAX_QUEUE_LENGTH` | 20 | 全局排队长度上限 |
| `SHIP_PRELOAD` | 0 | 设为 `1` 时启动后台预加载模型，首个交互不用等加载 |
| `SHIP_VLM_PROVIDER` | none | VLM 规划器开关：`none` / `openai`（兼容 OpenAI 协议） |
| `SHIP_VLM_BASE_URL` | `http://127.0.0.1:11434/v1` | 兼容接口地址（本地 Ollama/vLLM 或云端） |
| `SHIP_VLM_MODEL` | `qwen2.5-vl-7b` | 模型名 |
| `SHIP_VLM_API_KEY` | 空 | 云端 API 的密钥；本地部署留空 |
| `SHIP_VLM_TIMEOUT` | 8 | 单次调用超时（秒） |
| `SHIP_VLM_RETRIES` | 1 | 失败重试次数（不含首次） |
| `SHIP_VLM_MAX_TOKENS` | 200 | 单次回答上限，控成本 |
| `SHIP_VLM_DAILY_LIMIT` | 500 | 每日调用上限，超了自动降级为纯几何通道 |
| `SHIP_CORS_ORIGINS` | 本地 8765 | 允许的前端源 |

`var/` 已在 `.gitignore` 中，不会入库。

## 3. 分层

```
backend/
  server.py      ASGI 入口：错误信封、CORS、请求 ID、启动清理与预加载、重启恢复
  api.py         业务 29 端点：鉴权/项目/上传/影像/任务/草稿/版本/复核/区域/导出/AI候选
  vision.py      /vision/* 5 端点：内存直推（不落盘）、坐标还原
  jobs.py        任务状态机与 Worker：逐片进度、取消边界、重试、重启恢复
  detector.py    算法适配：单例模型 + 片级让锁 + 参数透传 + 内存推理入口
  storage.py     私有文件存储、分片合并、SHA-256
  imaging.py     解码校验、预览生成、请求体图像解码
  geometry.py    复核原因、抽样网格、区域计数（复用 algorithm 的几何）
  ai_assist.py   AI 候选核心：ROI 重检 + 轮廓兜底、合理性过滤、refine 约束（两个入口共用）
  ai_planner.py  VLM 规划器：大模型判断"有没有船"，几何仍由 ai_assist 产出；默认关闭
  tiles.py       大图瓦片：显示金字塔（不重叠）+ 推理切片预演（重叠），按需生成并缓存
  stats.py       业务统计聚合：项目/任务两个视角，只统计库内真实记录
  security.py    口令哈希、会话令牌、登录限速
  maintenance.py 启动清理：过期上传、旧尝试产物、残留临时文件
  db.py          SQLite schema 与访问
  errors.py      错误码 → HTTP 映射，且清洗绝对路径
  tools/check_contract.py  拿真实响应核对前端 OpenAPI
  tools/fsck.py            数据库 ↔ 文件对账（--delete 清孤立文件）
```

关键边界：**业务数据库是唯一写入口**。Worker 只把检测产物写到 `var/results/`，
再由 `jobs.py` 发布任务终态与 v0 版本；计算侧不直接改业务表。

## 4. 已实现能力与真实/模拟边界

| 能力 | 实现程度 | 说明 |
|---|---|---|
| 登录/会话/项目隔离 | 真实 | PBKDF2 口令、令牌摘要入库、逐请求成员校验、登录限速 |
| 可恢复上传 | 真实 | 分片按序落盘、重复片幂等、合并后长度与 SHA-256 校验 |
| 图像校验与预览 | 真实 | 复用 `algorithm.read_image`：RGB/EXIF/像素上限；生成 JPEG 预览 |
| 检测任务 | 真实 | 真实模型、逐片进度、`temp_count`/`final_count` 分离、取消与重试 |
| 检测结果 | 真实 | `ship-detection/0.2` 原样发布（含 `source_tile_id`、`tiling.*`） |
| 草稿/版本 | 真实 | v0 只读、追加版本、变更摘要、`NOTHING_TO_COMMIT` 冲突 |
| 疑难复核 | 真实计算 | 四类原因（低置信/贴缝/贴边/疑似重复）+ 空网格抽样，分数不是概率 |
| 区域统计 | 真实计算 | 中心点落入规则，model/effective/deleted/added/delta |
| 导出 | 真实 | CSV / JSON / PNG，绑定版本；单格式失败不影响其他格式 |
| AI 候选 | **部分真实** | 先在 ROI 上跑真实检测模型；模型未命中时退化为阈值轮廓（`reason` 会写明） |
| AI 候选：补框 / 改框 | 真实计算 | `mode: point/box` 补漏检；`mode: refine` + `existing_polygon` 精修已有框，带"不许跳到隔壁船"的约束 |
| `/vision/detect` | 真实 | 解码 data_url → 推理 → 按 `frame/image` 比例还原到原始媒体像素 |
| `/vision/segment` | **部分真实** | 同上；不是语义分割，`confidence` 恒为 `null`，只给 `quality` |

`/vision/detect` 的服务端策略：**不在服务端二次下采样**，`imgsz` 对齐到当前帧最大边
（上限 2048）。但前端仍会把整图缩到 1600 再 JPEG 有损编码，这部分精度损失在客户端，
需要前端调整 `maxPayloadEdge` 才能消除。

## 5. 工程化能力（2026-11-04 优化）

| 能力 | 说明 |
|---|---|
| 片级让锁 | Tiled 任务只在**单片推理**时持锁，片间让出 GPU，即时检测不再被大图任务堵死 |
| 内存直推 | `/vision/*` 不再写临时文件再读回，直接对已解码图像推理（省一次落盘 + 一次解码 + 一次哈希） |
| 输入变化快检 | tiled 入口先比 `size/mtime`，只有变化才回退全量 SHA-256；未变化时只算一次 |
| 模型预热 | `SHIP_PRELOAD=1` 启动后台加载；`/health` 暴露 `model_ready` 与 `active_tasks` |
| 条件请求 | `/files`、`/previews` 带 `ETag` + `Cache-Control: private, max-age=3600`，命中 `If-None-Match` 返回 304 |
| 请求 ID | 每个响应带 `X-Request-Id`，错误信封 `details.request_id` 可对日志 |
| 队列治理 | 单项目并发上限与全局排队上限，超出返回 `429 RATE_LIMITED` |
| 启动清理 | 过期上传标记 `expired` 并删分片；旧 attempt 结果文件与残留临时文件回收 |
| 对账工具 | `python -m backend.tools.fsck [--delete]`：数据库 ↔ 文件一致性检查 |
| 大图瓦片 | `POST /images/{id}/tile-plan`（推理切片预演）、`GET /images/{id}/tile-manifest`（金字塔层级）、`GET /tiles/{id}/{z}/{x}/{y}`（按需生成 + ETag 缓存） |
| VLM 规划器 | `ai_planner.py`：大模型只做"这块有没有船"的判断，几何仍交给 `ai_assist`；默认关闭，配置 `SHIP_VLM_PROVIDER` 才启用，失败/超时一律降级 |
| 业务统计 | `GET /projects/{id}/stats`（项目运行统计）与 `GET /tasks/{id}/stats`（单任务）；**只聚合业务库真实记录**，算法质量指标由评估体系单独产出 |

## 6. 验证（可复现）

```powershell
# 单元 + 端到端（有 best.pt 时跑真实推理，无权重自动跳过真实推理用例）
& .venv/Scripts/python.exe -m unittest discover -s tests -v

# 契约一致性：拿真实响应核对前端 OpenAPI（需后端已启动、frontend/ 已存在）
& .venv/Scripts/python.exe -m backend.tools.check_contract --base-url http://127.0.0.1:8000

# 数据库与文件对账
& .venv/Scripts/python.exe -m backend.tools.fsck
```

真实 HTTP 与真实浏览器联调（后端已启动、前端已切 `apiMode: 'http'`）：

```powershell
& .venv/Scripts/python.exe scripts/e2e_link_check.py --image frontend/assets/harbor.png   # 39 项
& .venv/Scripts/python.exe scripts/browser_link_check.py                                  # Edge headless + CDP，7 项
```

最近一次结果见 [`docs/validation/link-2026-11-05.md`](../docs/validation/link-2026-11-05.md)（含截图），
更早的分轮记录见 [`docs/validation/backend-2026-11-04.md`](../docs/validation/backend-2026-11-04.md)。

## 7. 已知限制

1. **单进程单 Worker**：任务队列在内存里，多进程部署会各自消费；GPU 只能串行，没有分布式调度。
2. **重启恢复 = 如实失败**：进程重启后未完成任务标记 `SERVER_RESTART`，不假装继续，也不支持断点续算。
3. **无推送**：进度靠轮询（前端 250 ms），未实现 SSE/WebSocket。
4. **无并发编辑冲突检测**：版本提交是整份快照，两人同时改会以最后一次提交为准（契约里明确列为后续增强）。
5. **AI 候选不是语义分割**：目前是"局部高分辨率检测 + 阈值轮廓兜底"，没有 SAM 类模型；验收时不能按分割质量评估。
6. **导出为同步生成**：放到后台线程但没有断点；大图 PNG 渲染走预览图（最长边 2048），不是原分辨率。
7. **输入变化检测的边界**：靠 `size/mtime` 快检，若有人改写内容又原样恢复 mtime，则不会被发现（威胁模型外）。
8. **未做备份恢复演练**；`fsck` 能查一致性，但不等于恢复演练。

## 8. 与前端的两处契约差异（已在实现中兼容）

- `DetectResponse.frame` 的 schema 要求 `index/time_ms/width/height` 四个字段，而
  `DETECT_API.md` 的示例只写了前两个；后端按 **schema** 输出四个字段。
- 检测页的 `detections[]` 用 `label/score/source`，与主应用 `ship-detection/0.2` 的
  `class_name/confidence/class_id` 不同；`/vision/detect` 负责转换，业务端点保持 v0.2 原样。
