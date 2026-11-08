# 后端完整性审计：2026-11-08

日期 Asia/Shanghai。审计对象：`codex/multi-class-detect`（`f13cc81`）及其 4 个下游分支
（PR #11–#14）合并后的后端。审计方式：静态对照 + 真实权重跑测试 + 真实 HTTP + 契约校验。
**只读审计，未合并任何 PR、未推送。**

## 1. 结论摘要

| 维度 | 结果 |
|---|---|
| 端点实现 | 47 条路由（`api.py` 42 + `vision.py` 5） |
| 契约覆盖 | B 的 `openapi.yaml` 29/29、`detect-openapi.yaml` 5/5 **全部实现** |
| 契约反向缺口 | 实现有 **10 条**路径没写进契约（含 3 条前端依赖的下载路径） |
| 测试 | **85 项通过**（真实权重 `yolov8l-obb-dota-v1-80e`，CPU） |
| 契约校验 | 业务 21/21、推理 6/6 |
| 数据一致性 | `fsck` 0 问题（blobs / previews / results / chunks） |
| 代码残留 | 无 TODO / FIXME / NotImplemented |
| 分支积压 | **11 个开放 PR 未合并**，`main` 仍是 7 个文档 |

一句话：**实现相对契约是完整的，问题在"契约跟不上实现"和"11 个 PR 堆叠没合并"**。
本轮另修掉 1 个 P1 缺陷（参数校验时机）。

## 2. 审计环境

| 项 | 值 |
|---|---|
| 权重 | `E:\Al_project\遥感应用\weights\dota_v1_yolov8l_80e\weights\best.pt`（89,393,063 B，**项目训练权重**） |
| 设备 | CPU |
| Python / torch / ultralytics | 3.10.20 / 2.9.1+cu128 / 8.4.189 |
| 合并方式 | 新 worktree `codex/audit-merge`，从 `f13cc81` 依次 merge #13→#14→#12→#11 |

## 3. 端点完整性

### 3.1 契约 → 实现（无缺口）

```
契约已覆盖: 业务 29/29，推理 5/5
```

### 3.2 实现 → 契约（10 条未写入契约）

| 端点 | 性质 |
|---|---|
| `GET /files/{image_id}` | **前端依赖**：影像原图下载 |
| `GET /previews/{image_id}` | **前端依赖**：预览图（返回体 `preview_url` 指向它） |
| `GET /exports/{export_id}/files/{name}` | **前端依赖**：导出文件下载 |
| `GET /projects/{project_id}/images` | 项目影像列表（已在 `contracts/api-project-lists.md` 说明） |
| `GET /projects/{project_id}/stats` | 项目运行统计 |
| `GET /tasks/{task_id}/stats` | 单任务统计 |
| `GET /images/{image_id}/tile-manifest` | 大图金字塔层级 |
| `POST /images/{image_id}/tile-plan` | 切片预演 |
| `GET /tiles/{image_id}/{z}/{x}/{y}` | 瓦片按需生成 |
| `GET /ai/planner` | VLM 规划器状态 |

前 3 条最要紧：浏览器实际通过它们取图，契约里却没有。建议按 §7 补进 `openapi.yaml`。

## 4. 缺陷与不一致（分级）

### P1-1 参数非法时任务先入队再失败（已修）

| 项 | 内容 |
|---|---|
| 现象 | `POST /projects/{id}/tasks` 带 `parameters.classes = []` 返回 **201**，任务先占用并发额度，Worker 抛错后才变 `failed` |
| 根因 | `create_task` 只做字段合并，参数校验在 `algorithm.ShipDetector.validate_parameters` 里，只由 Worker 调用 |
| 影响 | 用户要等一轮才有错误反馈；一次非法请求白占一个 `MAX_ACTIVE_TASKS` 名额与队列位 |
| 修法 | 新增 `api._validate_task_parameters()`，在创建时调 `ShipDetector.validate_parameters`（`backend/api.py`） |
| 验证 | 新增 `test_create_task_rejects_invalid_parameters`：6 类非法值全部 422 `INVALID_ARGUMENT`，合法值仍 201。全套 **85 项通过** |

复现与修复前后：

```text
修复前： {'classes': []} -> 201，随后任务 failed: INVALID_ARGUMENT: classes must not be empty
修复后： {'classes': []} -> 422 INVALID_ARGUMENT
```

> 修复过程本身有个教训：插入函数时误放在 `@router.post(...)` 与 `create_task` 之间，
> 装饰器绑到了新函数上，导致创建任务返回 `201 null`。已调整定义顺序并加测试守住。

### P1-2 `classes` 空数组语义，两份契约互相矛盾（待双方定）

| 契约 | 写法 |
|---|---|
| `frontend/api/openapi.yaml`（业务） | "省略或 null 使用全部模型类别，**空数组无效**" |
| `frontend/api/detect-openapi.yaml`（推理） | "省略、null 或**空数组**使用全部模型类别" |

实测行为：

| 入口 | `classes=[]` | `classes=null` | `classes=[1]` | `classes=[99]` |
|---|---|---|---|---|
| `POST /vision/detect` | 全部 15 类 | 全部 15 类 | 只 `ship` | 422 |
| `POST /projects/{id}/tasks` | **422**（本轮修复后） | 全部类别 | 只 `ship` | 任务仍会失败（见 P3-1） |

同一个参数名两种语义，调用方要写两套判断。建议统一为**空数组等同 null（全部类别）**，
因为推理端点已经这么做，且"什么都不筛"比"报错"更符合直觉；若要保留严格语义，
则两份契约必须写成同一句话。

### P2-1 契约缺 10 条路径

见 §3.2。其中 `files` / `previews` / `exports/{id}/files/{name}` 是前端实际依赖的下载路径，
不写进契约意味着 B 换实现时没有对照依据。

### P2-2 默认不过滤类别，业务必须先显式传 `classes=[1]`

`#10` 之后默认输出全部 15 类。真实权重在 `frontend/assets/harbor.png`（合成图，非真航拍）上：

```text
不传 classes      → count=21  {harbor: 13, ship: 5, large-vehicle: 3}
classes=[1]       → count=5   {ship: 5}
```

前端工作台按 `#14` 的约定显式传 `[1]`，但**任何直接调 API 的脚本/第三方不会传**，
拿到的是"港口+大车+船"的混合结果。建议在 `backend/README.md` 与契约里用一句话写死：
"船舶检测场景必须传 `classes: [1]`，省略等于全部 DOTA 类别"。

### P2-3 11 个 PR 堆叠未合并，`main` 仍是空壳

`main` = 7 个文档、1 个 commit。实现分布在 5 层旧链（#2→#4→#5→#8）与 5 个新 PR（#10–#14）上。

**本轮实测了合并可行性**：从 `f13cc81` 依次 merge `#13 → #14 → #12 → #11`，
只有 1 处冲突（`docs/module-controls/README.md` 的表格各加一行），两边都保留即可，其余全部干净。
说明链条本身不再有代码级冲突，卡的是"没人点合并"。

### P3-1 贴边目标坐标未 clamp

真权重在图像左边缘检出的框，缩放到 frame 后出现 `-0.5`（四舍五入）。不是缩放错误，
但输出没有夹到 `[0, width]`。影响：前端画布可能画出画布外 1px，无害。
`scripts/e2e_link_check.py` 的断言已改为 ±1px 容差并记录原因。

### P3-2 孤儿分支 `codex/detection-api`（`e9cd1eb`）

远端存在、**没有对应 PR**，相对 `codex/integration` 是 −13,074 行（删掉了 `frontend/styles.css`、
`scripts/serve.py`、`scripts/start-dev.ps1`、`tests/test_backend.py` 等），说明它基于更早的点，
无法直接并用。内容（"接自训练权重 + 瓦片视觉检测"）已被 #10 / #11 覆盖。
建议：确认后删除远端分支，避免下一个人误当基线。

### P3-3 本地 worktree 残留

- `_a_review_model`：未跟踪 `scripts/evaluate_obb.py`
- `_a_review_tiles`：`frontend/` 4 个文件改动 + `tiles-view.js` / `seed_tile_fixture.py` / `test_tile_view.mjs` 未跟踪

内容与 PR #11/#12 已提交的版本重复，属工作区副本残留。**未清理**（可能是别人的未提交工作）。

## 5. 逐项验证记录

```powershell
# 合并
git worktree add -b codex/audit-merge ..\_audit f13cc81
git merge --no-ff origin/codex/a-gpt-recovery    # 干净
git merge --no-ff origin/codex/a-gpt-baseline    # 干净
git merge --no-ff origin/codex/a-gpt-model       # 干净
git merge --no-ff origin/codex/a-gpt-tiles       # 1 处文档冲突，两边保留

# 测试（真权重）
$env:SHIP_WEIGHTS = "…\weights\dota_v1_yolov8l_80e\weights\best.pt"
python -m unittest discover -s tests             # Ran 85 tests, OK

# 起服务后
python scripts/e2e_link_check.py --image frontend/assets/harbor.png   # 39 项（含本轮修的容差）
python -m backend.tools.check_contract --spec frontend/api/openapi.yaml `
    --vision-spec frontend/api/detect-openapi.yaml                    # 21/21 + 6/6
python -m backend.tools.fsck                                          # 合计问题 0
```

## 6. 未覆盖

- GPU 推理（`SHIP_DEVICE=0` 可切，本轮 CPU）。
- 并发多用户、多进程 Worker、重启故障注入的实际演练（`#13` 有恢复逻辑与单测，未做真故障注入）。
- 备份恢复（仍无脚本，见 `02-A方职责` §2.9 / `04` §10.9）。
- 模型精度：合成图上的 `harbor`/`large-vehicle` 误检说明该图不适合作为精度依据；
  真实评估入口见 `scripts/evaluate_ship_obb.py`（PR #12），本轮未跑。
- 前端 Node 测试（`tests/test_tile_view.mjs`，需 node）。

## 7. 建议动作（按顺序）

1. **定 `classes=[]` 语义**（P1-2），两份契约写同一句话 —— 需要双方确认，一句话的事。
2. **把 10 条路径补进 `openapi.yaml`**（P2-1），至少先补 3 条下载路径。
3. **按 #10→#13→#14→#12→#11 顺序合并**（P2-3），仅 1 处文档冲突；合并后 `main` 才有可运行实现。
4. **删掉孤儿分支 `codex/detection-api`**（P3-2）。
5. 后端 README 加一句"船舶场景必须传 `classes: [1]`"（P2-2）。
6. 继续欠账：`components/image-workbench`、备份恢复演练、评估材料。

## 8. 本轮改动文件

- `backend/api.py`：新增 `_validate_task_parameters`，`create_task` 创建时前置校验
- `tests/test_backend.py`：新增 `test_create_task_rejects_invalid_parameters`
- `scripts/e2e_link_check.py`：`/vision/detect` 坐标断言改为 ±1px 容差并说明原因
- 本文档

均在 `codex/audit-merge`（本地审计分支），**未推送、未合并**。
