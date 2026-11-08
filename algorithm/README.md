# YOLOv8l-OBB 船舶检测模块

已实现并在本机运行：单张RGB PNG/JPEG → ship旋转框 → JSON及同坐标叠框PNG。没有训练/改模型。源码入口为 `ship_detector.py`；契约见 [v0.1提案](../contracts/ship-detection-v0.1.md)。

## 大图推理增强

同一模块新增`ShipDetector.predict_large()`，不改变原predict的行为。切片核心为`tiled_inference.py`，契约见[v0.2提案](../contracts/ship-detection-v0.2.md)。

```powershell
& scripts/run-detection.ps1 -Image harbor.png -Output outputs/harbor -Device 0 -Tiled -TileSize 1024 -Stride 512 -MergeIou 0.1
& .venv/Scripts/python.exe -m algorithm.ship_detector harbor.png --tiled --tile-size 1024 --stride 512 --merge-iou 0.1 --device cpu --output outputs/harbor
```

```python
detector = ShipDetector(device="cpu")  # 与下文相同的导入和权重准备
result = detector.predict_large("harbor.png", tile_size=1024, stride=512, merge_iou=0.1)
save_result(result, "harbor.png", "outputs/harbor")
```

逐片独立检测，坐标加偏移后按类别做多边形NMS，保留高分框；不拼接残片、不新增训练。max_det是每片上限，merge_iou是全图去重阈值，与模型内部iou不同。最多67,108,864像素/4096片/20000个候选，超限报错，不部分成功。整张影像仍完整解码到内存；不是无限尺寸或GeoTIFF读取器。单图旧模式仍限16,777,216像素。

正常返回schema `ship-detection/0.2`、全图count、四点框、来源切片、合并前后数量与耗时；空结果count=0。缺失输入/非法stride等返回DetectionError，CLI失败退出2。例如`--tiled --stride 0`返回INVALID_ARGUMENT。真实正常/空/错误JSON见[样例目录](../contracts/examples/README.md)。

验证：

```powershell
& .venv/Scripts/python.exe -m unittest discover -s tests -v
& .venv/Scripts/python.exe scripts/verify_tiled_smoke.py --samples datasets/smoke --weights models/yolov8l-obb-dota-v1-80e/best.pt --device 0
```

2026-10-29的实际验证记录见[大图检查](../docs/validation/ship-detection-tiled-2026-10-29.md)。来源为本地DOTA样例、组合测试图及合成空图；未做独立精度评估。1024/512/0.1是DOTA参考起点，不保证对当前权重/船舶密度最优；切片可能增加耗时、残缺框或误检。

## 本机立即启动

在仓库根目录打开PowerShell，不需要激活环境：

```powershell
& scripts/run-detection.ps1 -Image datasets/smoke/dense.png -Output outputs/demo -Device 0
```

CPU使用 `-Device cpu`。自有影像路径有空格时加引号。输出目录生成 `result.json` 与 `preview.png`，重复使用该目录会覆盖结果。

完整参数入口：

```powershell
& .venv/Scripts/python.exe -m algorithm.ship_detector datasets/smoke/dense.png --device 0 --conf 0.25 --iou 0.7 --imgsz 1024 --max-det 1000 --output outputs/demo
```

## Python调用

```python
from algorithm.ship_detector import ShipDetector, save_result

detector = ShipDetector(device="cpu")  # 加载一次；GPU为"0"
result = detector.predict("datasets/smoke/dense.png")
save_result(result, "datasets/smoke/dense.png", "outputs/python-demo")
print(result["detections"])
```

失败抛出DetectionError，业务层可读取其code。类实例用于顺序复用；不承诺并发安全。保存函数与推理分开，调用方可只取得字典。

## 依赖与安装

已测：Windows、Python3.10.20、PyTorch2.9.1+cu128、torchvision0.24.1+cu128、Ultralytics8.4.189、Pillow12.2.0、RTX5060 Laptop 8GB；CPU入口也已实测。完整包快照见requirements-tested.txt，它不是跨平台锁文件。

当前 `.venv` 复用了 `E:/conda/envs/obb` 的PyTorch及基础包，在虚拟环境单独安装Ultralytics；没有升级原conda环境。这个虚拟环境不能拷贝到别的机器直接用。

新机器可在Python3.10环境创建独立venv。先按机器安装合适的PyTorch；以下为已测CUDA12.8组合，下载量较大：

```powershell
python -m venv .venv
& .venv/Scripts/python.exe -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128
& .venv/Scripts/python.exe -m pip install -r algorithm/requirements.txt
& .venv/Scripts/python.exe -m pip check
```

仅CPU环境可将索引改成 `https://download.pytorch.org/whl/cpu`。新机器/系统仍需重跑验证，不能直接继承本机验收结论。

## 权重获取

本地已有 `models/yolov8l-obb-dota-v1-80e/best.pt`，89,393,063字节，SHA256为 `6508d583f538e858d498335c0fb6efd24c9e5116823eb4152f6d6979193cd26f`。这是用户在DOTA-v1训练80轮所得的权重，checkpoint记录训练库版本8.1.5；在当前8.4.189中成功加载并推理。epoch=-1，不据此推定最佳轮次。

权重不入Git。其他机器由权重所有者分发，或在有访问权限时交互下载，命令不包含密码：

```powershell
New-Item -ItemType Directory -Force models/yolov8l-obb-dota-v1-80e
scp -P 49002 root@connect.westc.seetacloud.com:/root/autodl-tmp/runs/obb/dota_v1_yolov8l_80e/weights/best.pt models/yolov8l-obb-dota-v1-80e/best.pt
Get-FileHash models/yolov8l-obb-dota-v1-80e/best.pt -Algorithm SHA256
```

服务器需仍可访问，端口/文件路径可能变化；不要把凭据写入脚本或提交。此模块不静默下载缺失权重。它只保证当前已测权重的兼容，不保证其他.pt可用。

## 样例和验证

样例本地位于datasets/smoke（不入Git）。通过脚本从已备的DOTA-v1.5验证集筛选：

```powershell
& .venv/Scripts/python.exe scripts/prepare_detection_samples.py D:/科研/数据集/DOTA-v1.5_PKINet/val
& .venv/Scripts/python.exe -m unittest discover -s tests -v
& .venv/Scripts/python.exe scripts/verify_detection_smoke.py --device 0
```

使用其他位置时替换数据目录。脚本同时保存来源标注和manifest.json；JPEG为密集样例下半幅，标注对应y偏移512。DOTA仅供学术使用，原影像也受来源条款约束，见[DOTA说明](https://captain-whu.github.io/DOTA/dataset.html)。本地复制用于课程流程验证，不将影像放进公开仓库。

2026-10-27本机观测（conf0.25，imgsz1024；JPEG另用640验证缩放还原）：

| 样例 | 标注船舶 | 检测数 | 说明 |
|---|---:|---:|---|
| dense.png | 43 | 32 | 25个预测与至少一个船舶标注IoU≥0.5 |
| small.png | 1 | 0 | 实际漏检，保留为效果限制 |
| empty.png | 0 | 0 | 空结果与预览保存通过 |
| dense_nonsquare.jpg | 裁剪图 | 35 | 1024×512→640输入，23个预测与偏移后的原标注IoU≥0.5 |

上述IoU检查用于发现明显位置/方向错误，不是去重匹配的精度评估；这些是同源DOTA数据，训练重叠未排除，不能报告泛化mAP。GPU全幅首次predict约1.86秒，后续同图predict约50毫秒；模型构造约1.82秒。含保存的后续三个样例约113–172毫秒，首次密集样例约2.02秒，不含模型构造。单次测量会波动，结果详见outputs/smoke/verification.json。CPU启动结果位于outputs/smoke/cpu。

4项单元检查覆盖多边形顺序/退化、非有限坐标、RGB/JPEG尺寸、灰度与损坏图片拒绝、缺失输入。真实模型检查覆盖三种场景及非方形缩放与标注交叠；缺失输入CLI实测退出2并返回INPUT_NOT_FOUND。检查通过仅指实现/流程，不能把小船漏检称为检测质量通过。

## 已知限制

- 原predict只有全图缩放；新增predict_large提供切片与跨片合并。仍无异步Worker、HTTP服务或业务持久化。切片不保证消除小船漏检或改善框角度。
- 灰度/RGBA、多光谱、GeoTIFF、EXIF自动转向和视频不支持。不能将输出框方向解释为船首航向。
- 达到max_det时返回警告，仍可能漏检/误检；默认只输出ship，不区分船型。
- 依赖和模型需遵守各自许可；Ultralytics的AGPL/商业授权边界在正式发布前应核对。仓库本身尚未选开源许可证。
- 当前错误/输出为本地开发接口，不直接暴露公网，不提供鉴权、隔离、排队或不可信模型上传。
