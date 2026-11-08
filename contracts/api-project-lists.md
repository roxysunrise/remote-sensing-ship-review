# 契约补充：项目影像列表与任务列表

日期：2026-11-05（Asia/Shanghai）
状态：**A 侧已实现，待 B 方确认后并入 `frontend/api/openapi.yaml`**
提出方：A（`codex/app-backend`）
影响：新增 2 个只读端点，不改动任何现有字段与语义。

## 1. 为什么需要

现有 `openapi.yaml` 只有 `GET /images/{image_id}`（按 id 取单张）和
`POST /projects/{project_id}/tasks`（创建任务），没有「列出项目里有哪些影像 / 任务」的端点。

后果（联调实测）：

- 刷新或重新进入工作台后，页面无法回到已有任务，只能用内置 `SCENE` 常量顶替；
  项目页显示「1 幅 harbor.png」，而后端实际有 6 个影像。
- 这分别违反 `03-B方职责` §2.1「用户能够找到已上传影像，重新进入对应任务和结果」
  与 §2.2「刷新或重新进入页面后，用户应能继续查看已有任务」。

## 2. `GET /projects/{project_id}/images`

列出项目内影像，按创建时间倒序。

| 项 | 内容 |
|---|---|
| 鉴权 | `Authorization: Bearer <token>`，逐请求校验项目成员 |
| 路径参数 | `project_id` |
| 查询参数 | 无（首版不做分页；影像数量受项目额度约束） |
| 成员校验失败 | `403 PROJECT_FORBIDDEN` |

响应 `200`：

```json
{
  "project_id": "harbor-a",
  "total": 1,
  "items": [
    {
      "id": "img_242b62fa311b",
      "project_id": "harbor-a",
      "file_name": "harbor-e2e.png",
      "status": "ready",
      "sha256": "07b4c0ad…",
      "width": 2305,
      "height": 1537,
      "mode": "RGB",
      "bytes": 1899515,
      "file_url": "/files/img_242b62fa311b",
      "preview_url": "/previews/img_242b62fa311b",
      "created_at": "2026-11-05T11:46:28+00:00"
    }
  ]
}
```

`items[]` 的字段与 `GET /images/{image_id}` 完全一致（同一份 `_image_payload`）。

## 3. `GET /projects/{project_id}/tasks`

列出项目内任务，按创建时间倒序，附带影像摘要。

| 项 | 内容 |
|---|---|
| 鉴权 | 同上 |
| 路径参数 | `project_id` |
| 查询参数 | `status`（可选，精确匹配任务状态）<br>`image_id`（可选，只看某幅影像的任务）<br>`limit`（可选，默认 50；非零整数限制到 1–200，0 采用默认值） |
| 成员校验失败 | `403 PROJECT_FORBIDDEN` |

响应 `200`：

```json
{
  "project_id": "harbor-a",
  "total": 1,
  "items": [
    {
      "id": "tsk_19e71036f0c2",
      "project_id": "harbor-a",
      "image_id": "img_242b62fa311b",
      "status": "done",
      "attempt": 1,
      "tiles_total": 12,
      "tiles_done": 12,
      "temp_count": 37,
      "final_count": 13,
      "parameters": {"conf": 0.15, "imgsz": 640, "tiling": true, "tile_size": 1024, "stride": 512},
      "cancel_requested": false,
      "error": null,
      "started_at": "2026-11-05T11:46:30+00:00",
      "finished_at": "2026-11-05T11:46:33+00:00",
      "image": {"id": "img_242b62fa311b", "file_name": "harbor-e2e.png",
                "width": 2305, "height": 1537}
    }
  ]
}
```

`total` 是当前筛选和 limit 下实际返回的条数。`items[]` 与 `GET /tasks/{task_id}` 相同，只多一个 `image` 摘要对象（避免前端为每条任务再发一次请求）。

## 4. 兼容性

- 纯新增端点，不改动任何现有请求/响应字段、状态枚举或计数口径。
- 前端 `config.js` 的 `apiMode: 'mock'` 时由 `api/mock-backend.js` 提供同名方法，离线演示不受影响。
- 长期只读，不引入写操作。

## 5. B 方接入步骤

1. 在 `frontend/api/openapi.yaml` 补这两个 path 与对应 schema（可直接引用现有 `Image` / `Task` schema，任务再加 `image` 引用）；
2. `frontend/api/client.js` 的 http 适配器新增 `listImages(projectId)`、`listTasks(projectId, params)`；
3. 项目页用它们渲染真实列表，替换 `SCENE` 兜底；
4. 刷新后据此恢复当前影像/任务，而不是清空工作台。

A 侧参考实现已在 `codex/frontend-http-wiring` 分支给出（可与本文件对照），双方确认后以 B 的版为准合入。

## 6. 实现位置

- `backend/api.py`：`list_images`、`list_tasks`
- `tests/test_backend.py`：`test_project_images_list`、`test_project_tasks_list`
- 实测：`GET /projects/harbor-a/images` → `total=6`；`GET /projects/harbor-a/tasks` → `total=6`（带 `image.file_name`）
