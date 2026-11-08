# 单图检测结果样例

## 2026-10-29 大图模式

- `ship-detection.tiled.success.json`：真实模型在1024×512非方形JPEG上执行3片，69候选经全图多边形NMS保留32框。
- `ship-detection.tiled.empty.json`：真实模型处理4097×4097**合成黑色空图**，9片，count=0；不是实际无船卫星大图。
- `ship-detection.tiled.error.json`：真实CLI非法stride=0，INVALID_ARGUMENT，退出2。

均为实际运行输出；成功样例仍为同源DOTA学术验证素材的派生JSON，未排除训练重叠，非精度证明。契约见[v0.2](../ship-detection-v0.2.md)，完整验证见[记录](../../docs/validation/ship-detection-tiled-2026-10-29.md)。影像不上传。

## 2026-10-27 单图基线（历史）

这些JSON来自2026-10-27本地真实推理/错误检查，不是模拟检测：

- `ship-detection.success.json`：密集DOTA样例的32个模型候选。
- `ship-detection.empty.json`：无船DOTA样例，成功返回空数组。
- `ship-detection.error.json`：缺失输入，CLI退出2。

没有影像、凭据或本地绝对路径；保留影像哈希、原图框、模型信息与耗时用于学术联调。未排除DOTA训练重叠，输出不等于人工真值或精度证明；遵守[DOTA学术使用说明](https://captain-whu.github.io/DOTA/dataset.html)。源影像仅在本地datasets/smoke，按algorithm/README.md准备。正式调用格式以[契约](../ship-detection-v0.1.md)为准。
