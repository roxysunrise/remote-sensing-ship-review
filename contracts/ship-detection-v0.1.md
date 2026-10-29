# 单图船舶检测契约 v0.1（实现提案，待双方确认）

本契约对应 `algorithm/ship_detector.py`，不表示已合入共同标准；不包含业务 API、鉴权、任务队列或人工修订。

新增大图模式见[v0.2增量提案](ship-detection-v0.2.md)。本文件继续约束原`predict()`；原调用不自动切片、不改变schema或像素上限。

## 输入和调用

本地可信 Ultralytics OBB `.pt` 权重 + RGB PNG/JPEG 单张影像。默认权重为已下载的 YOLOv8l-OBB best.pt。类别表必须包含 `ship`；禁止假定所有模型的类别编号相同。未实现任意框架权重导入。

Python：`ShipDetector(weights=..., device="cpu").predict(image_path, conf=0.25, iou=0.7, imgsz=1024, max_det=1000)`。

对象加载一次，可连续顺序调用；不保证同一对象的并发安全。返回 JSON 可序列化的字典。`save_result(result, image_path, output_dir)` 写入 `result.json` 和 `preview.png`；同一输出目录重复运行会覆盖这两个文件。

CLI：`python -m algorithm.ship_detector IMAGE --output DIR --device cpu`。成功退出0，失败退出2；成功末行输出 `ok/count/result/preview`，错误在stderr输出 `ok=false,error.code,error.message`。第三方库可能额外输出初始化日志；需程序集成时优先调用Python方法，不把整个stdout当成单一JSON。

## 统一结果

| 字段 | 含义 |
|---|---|
| schema_version | `ship-detection/0.1` |
| created_at | 带时区UTC时间 |
| image | 文件名、文件SHA256、width、height、RGB模式 |
| model | 后端、架构、权重文件名与哈希、运行库版本、checkpoint训练库版本/epoch、类别表、设备 |
| parameters | conf、iou、imgsz、max_det、classes、half、augment、tiling |
| coordinates | 原图像素坐标系声明 |
| timing_ms | 模型构造、解码、predict调用、保存前总耗时、预览保存、后端各阶段耗时 |
| count | detections长度；是模型候选数量，不是人工确认数量 |
| warnings | 可能达到数量上限、全图缩小等提示 |
| detections | 每项含id、polygon、class_id、class_name、confidence |

`id` 是本次结果内从1开始的顺序号，不是跨图跟踪ID。`class_id` 保留原模型编号，本权重ship=1；业务按class_name识别。

`polygon` 为4×2浮点数，单位像素，原点左上，x向右、y向下；沿画面顺时针排列，起点取最小(y,x)顶点。尺寸是原始解码图像尺寸，不是网络输入尺寸；不归一化，不在展示端二次缩放。不输出角度和宽高，避免角度制混用；旋转框不代表船首方向。

顶点不裁剪，可能稍微越过影像边缘。逐点夹紧会破坏矩形，故显示时由视口截取；将来计算交叠面积需单独裁剪多边形。JSON不四舍五入、不允许NaN/Infinity，预览直接绘制JSON中的同一组点。EXIF方向非1的JPEG拒绝，要求先归一化，避免阅读器自动旋转造成坐标歧义。

空检测成功返回 `count=0,detections=[]`，同时可保存原图预览。置信度不是已校准的正确概率。

## 边界与错误

最大16,777,216像素；imgsz为32到2048的32倍数；conf/iou在(0,1]；max_det为1到10000。默认只接收RGB，不自动把灰度/RGBA当RGB。当前为本地开发入口，不是完整的公网不可信上传校验器。

错误码：INPUT_NOT_FOUND、INVALID_IMAGE、IMAGE_TOO_LARGE、EXIF_ORIENTATION、WEIGHTS_NOT_FOUND、INVALID_DEVICE、MODEL_LOAD_FAILED、INCOMPATIBLE_MODEL、MISSING_SHIP_CLASS、INVALID_ARGUMENT、OUT_OF_MEMORY、INFERENCE_FAILED、INVALID_MODEL_OUTPUT、IMAGE_CHANGED、OUTPUT_CONFLICT、ENVIRONMENT_OR_IO。

权重必须可信：PyTorch checkpoint可能包含可执行序列化对象。本地CLI错误可含文件路径，不直接用作公网业务API响应。

model_load含构造及哈希计算，可能包括首次导入；首次predict含后端初始化/预热，后续调用更快。total_before_save不含模型构造与保存，preview_save含重读影像、绘制及PNG保存，不含JSON写入。验证脚本的end_to_end_ms含predict和两份结果保存，不含模型构造。均为本地测量，不是SLA。
