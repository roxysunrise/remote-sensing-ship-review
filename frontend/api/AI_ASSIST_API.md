# AI 辅助分割接口（可填写）

检测页右侧「AI 辅助框选」→「接入设置」可以填写任意的 HTTP 服务地址、API Key
与模型名。保存后，框选 / 点选产生的 AI 候选直接请求该地址；清空后回退到内置
`DetectAPI`（mock 或 `detect-config.js` 中的检测后端）。

- 设置只保存在浏览器 `localStorage` 的 `ship.aiAssist.v1`，不会写入仓库。
- 作用范围：`detect.html` 的「AI 辅助」请求。整帧检测仍走 `/vision/detect`。
- 接口必须允许本站 Origin 的 CORS；生产环境建议由服务端代理，避免浏览器暴露 Key。

## 1. 请求

```http
POST <使用者填写的接口地址>
Authorization: Bearer <API Key>        # 填写 Key 时发送
Content-Type: application/json
Accept: application/json
```

```json
{
  "model": "sam2.1-hiera-large",
  "session_id": "vs_...",
  "prompt": "ship",
  "mode": "roi",
  "image": {
    "data_url": "data:image/jpeg;base64,...",
    "width": 2305,
    "height": 1537
  },
  "roi": [820, 30, 1010, 70],
  "point": null,
  "options": { "conf": 0.25 }
}
```

字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | string? | 设置里填写的模型名；未填则省略 |
| `session_id` | string? | 前端检测会话；自定义服务可忽略 |
| `prompt` | string | 目标提示词，默认 `ship` |
| `mode` | `"roi"` / `"point"` | 框选或点选 |
| `image.data_url` | string | 当前帧 JPEG Data URL，可直接解码 |
| `image.width/height` | number | 当前帧像素尺寸 |
| `roi` | `[x0,y0,x1,y1]` / null | `mode=roi` 时的原图像素框 |
| `point` | `[x,y]` / null | `mode=point` 时的原图像素点 |
| `options.conf` | number | 建议的置信度 / 质量下限，可忽略 |

坐标都是**原图像素坐标**，不是画布显示坐标；服务端不需要处理缩放、平移或 DPR。

## 2. 响应

```json
{
  "request_id": "req_01J...",
  "model": { "name": "sam2.1-hiera-large", "version": "2.1" },
  "candidates": [
    {
      "polygon": [[885.0, 37.3], [953.3, 39.5], [952.9, 52.2], [884.6, 50.0]],
      "quality": 0.82,
      "label": "ship",
      "reason": "船体轮廓完整"
    }
  ]
}
```

- `candidates` 必须存在且是数组，至少返回 1 个候选。
- `polygon` 至少 3 点；4 点按旋转框显示，>4 点按多边形显示。
- `quality` 为 0–1 的候选质量分，用于排序与显示；不是模型概率。
- `label` 可省略，省略时使用当前 `prompt`。
- `confidence` 不参与 AI 候选展示；不要把 AI 候选伪装成模型置信度。
- 多个候选会在界面列出，用户选择一个采纳；采纳后写入标注并记录 `request_id`。
- 无候选时返回 `422`，错误码建议 `NO_CANDIDATE`。

## 3. 错误

```json
{
  "error": {
    "code": "NO_CANDIDATE",
    "message": "该区域没有可用候选",
    "details": {}
  }
}
```

前端会把 HTTP 状态和 `error.message` 显示在 toast 中。常见情况：

| 状态 | 含义 |
|---|---|
| 401 / 403 | Key 无效或无权访问 |
| 422 `NO_CANDIDATE` | 区域内没有可用轮廓，前端提示改手动画框 |
| 429 | 触发限流 |
| 5xx | 服务端错误 |

网络失败、CORS 被拒或超时会显示“接口无法连接 / 超时”，不会静默改成 mock 结果。

## 4. 最小示例

```bash
curl -X POST "$AI_ENDPOINT" \
  -H "Authorization: Bearer $AI_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "sam2.1-hiera-large",
    "prompt": "ship",
    "mode": "roi",
    "image": {"data_url": "data:image/jpeg;base64,...", "width": 2305, "height": 1537},
    "roi": [820, 30, 1010, 70],
    "point": null
  }'
```

返回：

```json
{
  "request_id": "req_demo",
  "model": { "name": "sam2.1-hiera-large" },
  "candidates": [
    { "polygon": [[885.0, 37.3], [953.3, 39.5], [952.9, 52.2], [884.6, 50.0]], "quality": 0.82, "label": "ship" }
  ]
}
```

## 5. 与内置接口的关系

内置的 `POST /vision/segment` 使用同一份请求 / 响应形状（见 `DETECT_API.md`
的 `SegmentRequest` / `SegmentResponse`）。因此可以直接把内置服务地址填进来，
也可以写一层代理，把请求转成你实际使用的分割模型。
