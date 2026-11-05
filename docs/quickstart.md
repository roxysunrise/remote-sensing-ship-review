# 快速开始：一条命令跑起来

目标：本机装好 Python 依赖后，`scripts/start-dev.ps1` 把后端、前端和浏览器一次拉起来，
打开就是真实后端数据，不需要改任何配置。

## 1. 前置

| 项 | 要求 |
|---|---|
| Python | 3.10（本机验证环境 `E:\conda\envs\obb`，torch 2.9.1+cu128） |
| 依赖 | `algorithm/requirements.txt` + `backend/requirements.txt` |
| 权重 | `models/yolov8l-obb-dota-v1-80e/best.pt`（不入库，自行放置） |
| 前端 | 仓库里的 `frontend/`（来自对方分支，见下） |

依赖只装一次：

```powershell
& .venv/Scripts/python.exe -m pip install -r algorithm/requirements.txt -r backend/requirements.txt
# 或者用你自己的解释器：
# & E:\conda\envs\obb\python.exe -m pip install -r algorithm/requirements.txt -r backend/requirements.txt
```

## 2. 启动

```powershell
scripts\start-dev.ps1
```

脚本会做这些事，并在每一步打印结果：

1. 选解释器（`-Python` 参数 → `SHIP_PYTHON` → `E:\conda\envs\obb\python.exe` → `python`）；
2. 检查 fastapi / uvicorn / torch / ultralytics / Pillow / opencv，缺了就直接告诉你安装命令；
3. 找权重（`-Weights` → `SHIP_WEIGHTS` → 项目权重 → `models/_local/yolov8s-obb.pt`）；
4. 起后端 `python -m backend.server --port 8000`；
5. 起 `scripts/serve.py`：静态页面 + `/api/v1` 反代，**同源**，所以前端 `baseUrl` 留空即可；
6. 轮询 `/health` 直到就绪，再打开 `http://127.0.0.1:8765/login.html`。

常用参数：

```powershell
scripts\start-dev.ps1 -ApiPort 8012 -WebPort 8766        # 换端口
scripts\start-dev.ps1 -Weights models\_local\yolov8s-obb.pt
scripts\start-dev.ps1 -Frontend ..\_wt_frontend\frontend  # 前端不在 ./frontend 时
scripts\start-dev.ps1 -NoBrowser                          # 不自动开浏览器
scripts\start-dev.ps1 -Force                              # 端口被占时先释放
scripts\stop-dev.ps1                                      # 停（PID 记在 var/dev-pids.json）
```

## 3. 登录

| 账号 | 密码 | 项目 | 角色 |
|---|---|---|---|
| `roxysunrise` | `demo-1234` | `harbor-a` / `harbor-b` | 项目负责人 |
| `analyst` | `demo-1234` | `harbor-a` | 项目分析员 |

演示口令只在首次建库时写入，且只存 PBKDF2-SHA256 摘要。正式使用前改掉：

```powershell
$env:SHIP_SEED_PASSWORD = "..."; $env:SHIP_SEED_ON_START = "0"
```

## 4. 打开后能做什么

- **项目**：真实影像列表与任务列表（点任意一行直接进入工作台或接续运行中的任务）；
- **上传**：分片上传 → 合并校验 → 图像验证 → 预览，四阶段状态真实可见；
- **任务**：创建、进度、取消、重试，进度来自真实切片处理；
- **工作台**：大图浏览、旋转框、列表定位、改框自动标记「已修改」；
- **疑难复核 / 区域统计 / 修订历史 / 报告导出**：都绑定同一个任务版本。

顶部徽章显示「真实后端」表示当前接的是 `backend/`；显示「演示数据」表示在跑浏览器内 mock。

## 5. 不想用启动脚本

```powershell
# 终端 1：后端
$env:SHIP_WEIGHTS = "models/yolov8l-obb-dota-v1-80e/best.pt"
python -m backend.server --port 8000

# 终端 2：前端（同源反代，推荐）
python scripts/serve.py --frontend frontend --port 8765 --backend http://127.0.0.1:8000

# 也可以直接用静态服务器，但要能跨域访问 8000
python -m http.server 8765 --bind 127.0.0.1 --directory frontend
```

## 6. 自检

```powershell
python -m unittest discover -s tests                              # 78 项
python scripts/e2e_link_check.py --image frontend/assets/harbor.png  # 真实 HTTP 39 项
python scripts/app_flow_check.py                                  # 真实浏览器 13 项
python scripts/browser_link_check.py                              # 登录与工作台 7 项
python -m backend.tools.check_contract --base-url http://127.0.0.1:8000 `
  --spec frontend/api/openapi.yaml --vision-spec frontend/api/detect-openapi.yaml
```

## 7. 常见问题

| 现象 | 原因与处理 |
|---|---|
| `前端目录不对，找不到 login.html` | 用 `-Frontend` 指到含 `login.html` 的目录 |
| 页面显示「演示数据」 | `frontend/config.js` 的 `apiMode` 被改成了 `mock` |
| 检测任务立刻失败 `MODEL_NOT_FOUND` | 没有权重，按 §1 放 `best.pt` 或用 `-Weights` |
| 端口被占 | 换端口，或加 `-Force` 释放 |
| 页面 404 `/api/client.js` | 反代前缀必须只匹配 `/api/v1`（`scripts/serve.py` 已处理） |
| 后端起来了但页面报 `NETWORK_ERROR` | 前端与后端不同源且没开 CORS，改用 `scripts/serve.py` |
