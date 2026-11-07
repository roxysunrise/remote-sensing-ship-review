# 用户影像检测记录（2026-11-05）

## 接入方式

- 分支工作区把 `backend/` 与 `algorithm/` 接到前端检测页；`frontend/detect-config.js` 的 `mode` 为 `http`，`baseUrl` 为 `http://127.0.0.1:8000`。
- 后端使用同一 `algorithm/ship_detector.py`，没有复制或改写推理实现。
- 本机运行环境：Python 3.9 + PyTorch 2.8.0+cu128 + Ultralytics 8.4.21（`PYTHONPATH` 指向本地 `ultralytics-main`）。
- 权重：自训练 DOTA-v1.0 YOLOv8l-OBB 80 epochs（`models/yolov8l-obb-dota-v1-80e/best.pt`，89,393,063 bytes，SHA256 `6508d583...cd26f`），来自项目 GitHub Release `dota-v1-yolov8l-80e-weights`；类别包含 `ship`。
- 参数：`conf=0.25`、`iou=0.7`、`imgsz=1024`、tiling `1024/512/merge IoU 0.1`、`max_det=1000`、GPU 0。

启动：

```powershell
$env:PYTHONPATH = "D:\遥感检测相关\ultralytics-main;<repo>"
$env:SHIP_WEIGHTS = "<repo>\models\yolov8l-obb-dota-v1-80e\best.pt"
$env:SHIP_DEVICE = "0"
& C:\Users\golodenfish\.conda\envs\yolov8\python.exe -m backend.server --port 8000
```

前端：`python -m http.server 8765 --bind 127.0.0.1 --directory frontend`，打开 `/detect.html`。

## 结果

| 文件 | 尺寸 | 船框 | 切片 | 说明 |
|---|---:|---:|---:|---|
| 95F4D77A117BAF2DFB55DD396971F07E.jpg | 1500×1125 | 69 | 4 | 游艇码头，主要目标集中 |
| 9F9544CD9DD58EC4B08A4A7F5733EA24.png | 1490×2137 | 0 | 8 | 停车场/仓储，无船 |
| P0329.png | 686×870 | 0 | 1 | 卡车场，无船 |
| P0331.png | 2478×2224 | 5 | 16 | 码头，远景小船有漏检 |
| P0336.png | 2885×1405 | 5 | 10 | 码头，远景小船有漏检 |
| P0353.png | 1170×1197 | 2 | 4 | 工业区，两个框落在车辆/设备上，疑似误检 |
| P0359.png | 623×854 | 0 | 1 | 网球场，无船 |
| P0368.png | 799×1387 | 0 | 2 | 网球场，无船 |
| P0385.png | 876×885 | 0 | 1 | 网球场，无船 |
| P0396.png | 1083×1177 | 0 | 4 | 网球场，无船 |
| P0402.png | 623×853 | 0 | 1 | 网球场，无船 |
| P0454.png | 1466×956 | 0 | 2 | 车辆段，无船 |
| P0457.png | 1403×881 | 0 | 2 | 球场/草地，无船 |
| P0466.png | 1298×1415 | 0 | 4 | 光伏阵列，无船 |

合计 14 张，81 个船框；3 张码头影像有船，1 张工业区存在 2 个疑似误检。报告与逐图 JSON/预览位于 `outputs/user-2026-11-05/`（已被 `.gitignore` 忽略，仅本地保留）。

## 复跑

```powershell
& .venv/Scripts/python.exe -m algorithm.ship_detector "C:\Users\golodenfish\Desktop\数据\P0331.png" `
  --weights models/yolov8l-obb-dota-v1-80e/best.pt --tiled --tile-size 1024 --stride 512 --merge-iou 0.1 --device 0 --output outputs/P0331
```

如果使用自训练权重，把 `--weights` 或 `SHIP_WEIGHTS` 指向 `models/yolov8l-obb-dota-v1-80e/best.pt` 即可；后端会自动优先使用该文件。
