# 船舶检测 v0.2：大图推理增强（实现提案，待双方确认）

这是 `ship-detection` 模块的新增模式，不是独立业务服务。旧 `predict()` 保持 [v0.1](ship-detection-v0.1.md) 输出和像素上限；新 `predict_large()` 输出 `ship-detection/0.2`。公共契约尚未共同确认，接入方需显式支持新版本。

## 调用及参数

```python
from algorithm.ship_detector import ShipDetector, save_result
detector = ShipDetector(device="cpu")
result = detector.predict_large("harbor.png", tile_size=1024, stride=512,
                               merge_iou=0.1, conf=0.25, iou=0.7,
                               imgsz=1024, max_det=1000)
save_result(result, "harbor.png", "outputs/harbor")
```

CLI：`python -m algorithm.ship_detector harbor.png --tiled --tile-size 1024 --stride 512 --merge-iou 0.1 --device cpu --output outputs/harbor`。

| 参数 | 规则 |
|---|---|
| tile_size | 整数32–2048，默认1024；切片上限边长，区别于网络输入imgsz |
| stride | 整数1–tile_size，默认512；等于tile_size时常规窗口不重叠，但末块贴边可能额外重叠 |
| merge_iou | (0,1]，默认0.1；全图多边形NMS阈值，IoU严格大于阈值时抑制；1关闭额外抑制 |
| iou | 仍为模型内部逐片后处理阈值，与merge_iou不同，默认0.7 |
| max_det | 1–10000，**每个切片**的模型数量上限；不是全图上限，达到时警告 |
| conf/imgsz | 沿用v0.1范围；imgsz小于切片时仍可能损失小目标 |

非tiled CLI传入非默认切片参数返回INVALID_ARGUMENT，避免静默忽略。成功退出0、失败退出2，错误信封沿用v0.1。

## 切片、坐标和合并

1. 只接收RGB PNG/JPEG，EXIF规则同v0.1；大图上限67,108,864像素。完整解码仍占用主机内存，**不是流式/磁盘分块读取**。不支持GeoTIFF、地理坐标或多光谱。
2. 行优先扫描。最后一行/列贴底/右边，不产生重复窗口；图像小于tile_size时使用原始有效尺寸，不人工补黑；模型内部缩放/letterbox由原检测器处理。
3. 每片使用同一模型实例顺序执行，不写临时切片影像，不训练。模型返回有效切片坐标后，每个顶点加切片(x,y)偏移；没有额外全图缩放或多尺度切图。
4. 汇总到原图，按类别、置信度降序做贪心NMS。分数相同时保留输入次序。外接水平框仅用于排除不相交候选对，实际抑制使用凸四边形交并比。使用OpenCV交集计算，并先平移到局部坐标再转float32。
5. 保留高分框，不平均/拼接顶点，不裁剪边缘框，不恢复被切断的船。保留v0.1的四点顺序和原图像素语义。

最多4096个切片、合并前20000个候选；超过直接报TILE_LIMIT或CANDIDATE_LIMIT，不静默截断。NMS最坏为二次复杂度，计数上限不代表性能SLA。任一片失败终止整个调用，不返回部分成功，也不自动重试。

## 输出增量

v0.1的image、model、coordinates、count、detections含义保持；`count`仍是模型候选数，不是人工确认数。

- `schema_version=ship-detection/0.2`，`parameters.tiling=true`，增加tile_size/stride/merge_iou；max_det含义为逐片上限。
- `detections[].source_tile_id`：保留下来的预测来自哪一片；id仍为当前结果内从1连续编号。
- `tiling.tile_count/candidate_count/suppressed_count`：切片数、逐片后处理后的候选总数、全图NMS抑制数。恒有candidate_count-suppressed_count=count。
- `tiling.merge_method=class_aware_polygon_nms`；tiles记录每片id、x/y、有效width/height、candidate_count、predict_ms。
- `timing_ms.decode`含读图/源文件哈希/切片规划；predict_call含切片裁剪、推理、坐标还原和记录；merge是全图NMS；total_before_save含末次源文件哈希，不含构造与保存。逐片predict_ms含裁剪与候选整理。没有v0.1单次模型backend计时，不用伪造平均值。
- warnings：MAX_DET_REACHED指明切片；TILE_BOUNDARIES说明截断风险；TILE_DOWNSCALED说明实际切片超过imgsz。

推理末尾及保存前检查源文件哈希变化。save_result仍输出result.json/preview.png，可保存大图预览；重复目录会覆盖已有结果，失败不会自动清理旧产物。调用方应使用新输出目录并判断退出码。

正常/空/失败样例见examples中的 `ship-detection.tiled.*.json`；来源和模拟/真实边界见该目录README。

## 依据和验证边界

- [DOTA原文第5.2节](https://arxiv.org/pdf/1711.10398)：1024×1024、步长512、原图恢复、按类别NMS；作者OBB阈值0.1。本模块以其参数作起点，非最优保证。
- [DOTA官方合并代码](https://github.com/CAPTAIN-WHU/DOTA_devkit/blob/master/ResultMerge_multi_process.py)：坐标偏移/尺度恢复和多边形IoU NMS参考；本实现只用单尺度，不复制其完整工具链。
- [SAHI第3节与图2](https://arxiv.org/pdf/2202.06934)：通用切片推理思路；本版不做切片微调、整图融合或论文全部功能，不据此宣称OBB精度提升。
- [YOLT第3.2/3.3节](https://arxiv.org/html/1805.09512v1#S3.SS2)：窗口位置记录、全图坐标恢复和NMS。

当前验收针对覆盖、几何、去重与真实推理流程。独立影像精度/召回、阈值选择、峰值内存和多机性能尚待评估；组合图与同源DOTA样例不作为泛化证明。无HTTP、Worker、取消恢复、业务存储或部署。
