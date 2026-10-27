# 单图检测结果样例

这些JSON来自2026-10-27本地真实推理/错误检查，不是模拟检测：

- `ship-detection.success.json`：密集DOTA样例的32个模型候选。
- `ship-detection.empty.json`：无船DOTA样例，成功返回空数组。
- `ship-detection.error.json`：缺失输入，CLI退出2。

没有影像、凭据或本地绝对路径；保留影像哈希、原图框、模型信息与耗时用于学术联调。未排除DOTA训练重叠，输出不等于人工真值或精度证明；遵守[DOTA学术使用说明](https://captain-whu.github.io/DOTA/dataset.html)。源影像仅在本地datasets/smoke，按algorithm/README.md准备。正式调用格式以[契约](../ship-detection-v0.1.md)为准。
