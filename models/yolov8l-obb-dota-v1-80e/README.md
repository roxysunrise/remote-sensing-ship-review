# DOTA-v1.0 YOLOv8l-OBB 自训练权重（80 epochs）

本目录的 `best.pt` 是项目自训练权重，已实际加载到后端并完成 14 张用户影像推理。

- 来源：GitHub Release `dota-v1-yolov8l-80e-weights`
  <https://github.com/roxysunrise/remote-sensing-ship-review/releases/tag/dota-v1-yolov8l-80e-weights>
- 文件：`best.pt`，89,393,063 bytes
- SHA256：`6508d583f538e858d498335c0fb6efd24c9e5116823eb4152f6d6979193cd26f`
- 训练：DOTA-v1.0，YOLOv8l-OBB，80 epochs，batch 4，imgsz 1024
- 验证：mAP50 0.6221，mAP50-95 0.4044（Release 记录；未独立复评）
- 任务：`obb`；类别含 `ship`（索引 1），共 15 类
- 权重文件不进入 Git；新机器从 Release 下载到本路径，或用 `SHIP_WEIGHTS` 指向。

上一轮临时使用的公开 `models/yolov8l-obb-public/best.pt` 已删除；后端默认只读取本目录权重。
