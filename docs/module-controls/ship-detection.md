# 单图船舶检测控制文档

## 身份与状态

- 模块 ID：ship-detection；主责：A；实际实现会话未在本记录中确认。
- 更新日期：2026-10-27（Asia/Shanghai）。
- 状态：本地开发中；以下来自本次文件读取，不是重新运行验收。
- 本次控制规则从 main 单独提交；检测实现仍位于原工作目录的未提交文件中。干净克隆本控制文档所在提交时，尚不能据此直接运行检测。
- 已上传/合并/部署：本次不上传检测实现；未确认该模块已有其他远端提交或部署。后续接手者必须重新检查。

## 本地观察与入口

原目录：`C:/Users/28028/OneDrive/图片/文档/ChatGPT/遥感应用`。

读取到的本地文件（本控制规则分支不包含这些实现）：

- `algorithm/ship_detector.py`、`algorithm/README.md`：单图推理入口和说明。
- `algorithm/requirements.txt`、`algorithm/requirements-tested.txt`：依赖与环境快照。
- `contracts/ship-detection-v0.1.md`：检测契约提案，不能视为双方已确认。
- `scripts/run-detection.ps1`、`scripts/prepare_detection_samples.py`、`scripts/verify_detection_smoke.py`。
- `tests/test_detection_contract.py`：契约验证。
- `models/yolov8l-obb-dota-v1-80e/README.md`：权重说明；权重本身不应上传普通 Git。

本地文档描述输入为 RGB PNG/JPEG，调用 `ShipDetector.predict` 后输出 JSON 与四顶点旋转框，可用 `save_result` 保存叠框预览。具体字段和限制以实际代码及契约为准，避免在此复制第二套规范。

在原目录且依赖、权重、样例确实存在时，文档提供以下调用；本次未执行：

```powershell
& scripts/run-detection.ps1 -Image datasets/smoke/dense.png -Device cpu
& .venv/Scripts/python.exe -m unittest discover -s tests -v
& .venv/Scripts/python.exe scripts/verify_detection_smoke.py --device 0
```

## 验证与边界

本地 algorithm/README.md 记载 CPU/GPU 推理和样例结果，但本次仅阅读，没有复核环境、权重、输出、指标或测试。后续负责人在交付前重新执行必要检查并把实际命令、结果和提交号填回。

当前文档声明仅为单图缩放推理，无切片合并、HTTP、异步Worker或业务持久化；小船样例存在漏检。同源 DOTA 样例未排除训练重叠，不能称为泛化精度评估。

## 下一步

1. 联系/核对当前正在开发的会话及文件状态，避免覆盖其修改。
2. 核对代码、依赖、权重哈希、许可、坐标契约和错误处理；实际运行测试及必要样例。
3. 更新本控制文档为真实验证记录，再按 AGENTS.md 用专门功能分支交付源码、契约、说明和必要样例。
4. 对接 B 的调用需求；未完成前不宣称已接入完整应用。

## 变更记录

- 2026-10-27：建立本地未提交检测实现的导航记录；保留其所有原文件，未执行或上传该模块代码。
