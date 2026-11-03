# 遥感船舶检测与人机协同复核 — 前端演示

面向港口遥感影像的船舶检测与复核工作台，实现自仓库
`roxysunrise/remote-sensing-ship-review` 的算法模块与四份职责/设计文档。

数据结构严格对齐 `contracts/ship-detection-v0.2.md`：坐标单位、原点、顶点顺序、
`count` 语义、`source_tile_id`、`tiling.*` 合并统计均与 Python 模块一致。

## 运行

```powershell
# 在本目录的上一级执行
python -m http.server 8765 --bind 127.0.0.1 --directory frontend
# 浏览器打开 http://127.0.0.1:8765/index.html
```

也可以直接双击 `index.html`，但标注图（PNG）导出需要 http 访问：
`file://` 会把本地图片视为跨源，canvas 被污染后无法导出位图。CSV / JSON 不受影响。

登录入口为独立页面 `login.html`，只对接真实后端（`/auth/login`、`/me`），
配置与接入说明见 [`LOGIN.md`](LOGIN.md)。工作台内原有的登录门禁仅用于
`apiMode: 'mock'` 的离线演示：真实后端部署时把 `config.js` 的 `apiMode` 改为 `'http'`，
未登录访问工作台会自动跳转到登录页。

## 图片 / 视频检测与 AI 辅助标注

第二个入口是独立页面 `detect.html`，主应用左侧导航「智能检测」可进入：

```
http://127.0.0.1:8765/detect.html
```

| 能力 | 实现 |
|---|---|
| 图片检测 | 载入 PNG / JPEG / WebP，整幅检测，支持缩放、平移、1:1 |
| 视频检测 | 载入 MP4 / WebM；播放、逐帧步进、时间轴、连续抽帧检测 |
| 分割框坐标 | 四角 / 多边形顶点、中心、宽高、角度、包围盒、面积；可精确编辑与复制 JSON |
| AI 辅助框选 | 在目标周围拖粗框或点选，调用分割接口返回候选，人工采纳后落为标注 |
| 导出 | 当前媒体的全部模型结果与手工 / AI 标注导出为 `ship-annotation-set/1.0` JSON |

### 接口已预留

界面不直接 fetch，也不直接跑模型：

```
detect.js ──▶ window.DetectAPI ──┬── mock（api/detect-mock.js，离线启发式）
                                 └── http（真实推理服务）
```

后端就绪后只改 [`detect-config.js`](detect-config.js)：

```js
window.DETECT_CONFIG = {
  mode: 'http',
  baseUrl: 'https://ship-api.example.com',  // 同源可留空
  apiPrefix: '/api/v1'
};
```

| 文件 | 作用 |
|---|---|
| [`api/DETECT_API.md`](api/DETECT_API.md) | 请求 / 响应、坐标约定、错误码、接入顺序 |
| [`api/detect-openapi.yaml`](api/detect-openapi.yaml) | 5 个端点的机器可读规格 |
| [`api/detect-api.js`](api/detect-api.js) | 双适配器与统一 `DetectApiError` |
| [`api/detect-mock.js`](api/detect-mock.js) | 浏览器内亮度连通域 / ROI 分割，仅用于演示 |

端点：`GET /vision/capabilities`、`POST /vision/sessions`、`POST /vision/detect`、
`POST /vision/segment`、`POST /vision/jobs/{job_id}/cancel`（预留，完整定义见文档）。
检测结果的多边形必须是**原始媒体像素**，
AI 候选的 `confidence` 必须为 `null`，只给 `quality` 质量分。

### 演示视频

没有预置视频文件：点击「生成演示视频」会用 `MediaRecorder` 把 `assets/harbor.png`
录成约 6 秒的 1280×720 WebM（Ken Burns 平移），再按普通视频载入。录制需要浏览器支持
`canvas.captureStream` 与 WebM 编码；不支持时请直接上传本地视频。

### 已知限制

- `detect-mock.js` 是启发式，不是模型推理；界面常驻「演示推理」标记，换真实影像后
  效果会明显下降。
- 标注绑定帧：视频里在某一帧画的框只在该帧显示，不做跨帧跟踪。
- 浏览器无法读取视频真实帧率，默认 30 fps，可在底部手动修改；帧号与逐帧步进据此计算。
  修改帧率会清空按帧绑定的检测与标注，避免时间错位。
- 导出 JSON 是前端中间格式，后端可按需映射到 `ship-detection/0.2`；本页不写项目草稿，
  需要持久化时由后端补保存接口（例如复用主应用的 `/tasks/{id}/draft`）。

## 与后端对接

前端**不直接读写数据**，所有访问都经过 `api/client.js` 暴露的 `window.API`：

```
UI (app.js) ──▶ window.API ──┬──▶ mock 适配器（api/mock-backend.js，默认）
                             └──▶ http 适配器（fetch → 真实后端）
```

切换后端只改 `config.js` 一处：

```js
window.APP_CONFIG = {
  apiMode: 'http',                    // 'mock' → 'http'
  baseUrl: 'http://127.0.0.1:8000',
  apiPrefix: '/api/v1',
  pollIntervalMs: 250
};
```

| 文件 | 作用 |
|---|---|
| `api/openapi.yaml` | OpenAPI 3.0 规格，29 个端点、18 个 schema |
| `api/CONTRACT.md` | 状态机、错误码、接入顺序、mock 与真实后端差异 |
| `api/client.js` | 双适配器与统一 `ApiError` |
| `api/mock-backend.js` | 浏览器内模拟服务端（含鉴权、权限、状态机） |
| `api/geom.js` | UI 与 mock 共用的几何实现 |
| `config.js` | 运行时配置 |

接口遵循仓库既有契约：检测结果与 `contracts/ship-detection-v0.2.md` 完全一致，
`count` 是模型候选数、`polygon` 为原图像素顺时针四点、`source_tile_id` 指向切片。


## 覆盖的功能模块

| 文档编号 | 功能 | 本演示中的位置 | 状态 |
|---|---|---|---|
| F12 | 账号与项目权限 | 登录页 / 项目空间 | 模拟鉴权，展示隔离与逐请求校验要点 |
| F13 | 可靠上传 | 上传 | 四阶段状态机、分片、暂停续传、中断恢复 |
| F02 | 检测任务 | 任务 | 12 片渐进进度、临时/最终计数分离、取消与重试 |
| F03 | 大图检测 | 任务 / 工作台 | 切片网格、来源切片、合并前后数量 |
| F04 | 影像工作台 | 工作台 | 缩放平移、旋转框显示、列表与画布双向联动 |
| F05 | 人工复核 | 工作台 | 确认/删除/新增、移动/旋转/缩放、撤销重做 |
| F06 | 疑难复核 | 疑难复核 | 四类可解释原因 + 空区域抽样 |
| F07 | 区域统计 | 区域统计 | 圈选区域、中心点计数、修订前后对比 |
| F08 | 修订历史 | 修订历史 | v0 不可变、追加版本、变更摘要、只读回看 |
| F09 | 报告导出 | 报告导出 | CSV / JSON / PNG，绑定版本 |
| F14 | AI 辅助标注 | 工作台「AI 补框」 | 候选预览、接受、取消、失败回退 |

F01（影像管理）、F10（实验展示）、F11（集成与交付）、F15（数据生命周期）
只以项目页的说明与清单形式呈现，没有独立交互。

## 真实计算 vs 模拟数据

文档反复要求「不得将模拟数据计为真实能力」，界面右上角常驻 `演示数据` 标记。

**真实（按契约算法计算，非预置结果）**
- 多边形坐标、顺时针与 `min(y,x)` 起点规范化
- 点击拾取、移动、旋转、缩放的几何运算
- 多边形交并比（Sutherland–Hodgman 裁剪，对应 `polygon_iou`）
- 疑难原因中的边界距离、中心点包含、影像边缘判定
- 区域统计（中心点落入规则）与各口径计数
- 版本差异比较与导出文件生成

**模拟（前端状态机，无后端）**
- 登录与会话、项目隔离判定
- 上传分片与校验耗时
- 检测任务逐片进度与耗时
- AI 候选生成（点击位置命中预置的漏检目标即返回其轮廓）

影像为程序合成的港口场景，检测框按契约生成并带有人为抖动，**不是模型真实推理结果**。
场景中刻意保留了模块文档描述的失败模式：贴缝小船被切成碎片后重复计数、跨缝目标、
贴边目标、以及小目标漏检。

## 数据来源

`tools/gen_scene.py` 生成 `assets/harbor.png` 与 `data.js`，并复用仓库中的真实函数：

```python
from algorithm.tiled_inference import plan_tiles      # 12 片，1024/512
from algorithm.ship_detector import canonical_polygon  # 顶点顺序与起点
```

生成结果：2305×1537 影像，48 艘船，44 艘被检出、4 艘漏检，另有 6 个误检与
2 个边界碎片，共 52 个检测；12 片共 208 个候选，合并抑制 156 个。

碎片与母框的多边形 IoU 为 0.084，低于 `merge_iou=0.1`，因此按真实 NMS 规则
**不会**被合并——这正是界面中「疑似重复检出」提示所指向的问题。

重新生成：

```powershell
python frontend/tools/gen_scene.py
```

## 操作提示

| 操作 | 方式 |
|---|---|
| 缩放 | 滚轮（以光标为中心） |
| 平移 | 空白处拖动 / 右键拖动 |
| 选择 | 点击框或左侧列表 |
| 移动框 | 拖动框内部 |
| 旋转框 | 拖动顶部圆形手柄 |
| 缩放框 | 拖动四角方块手柄 |
| 删除 / 撤销 | `Delete` / `Ctrl+Z`，`Ctrl+Y` 重做 |
| 取消选择 | `Esc` |

## 已知限制

- 无后端、无持久化：刷新页面后上传、任务与修订全部重置（真实系统须服务端存储）。
- 无并发与多人协作，不做版本冲突提示。
- 旋转框编辑为单一矩形约束，不支持任意四边形或多边形标注。
- 未接入真实模型：`data.js` 中的置信度与切片耗时均为合成值。
