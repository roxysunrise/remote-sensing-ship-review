# 船舶检测控制文档

## 当前增量：大图推理增强（2026-10-29）

- 同一模块ID ship-detection，A负责；新增predict_large/CLI --tiled，复用原模型，不建立另一个业务模块。
- 本轮范围：固定尺寸重叠切片、原图四点坐标还原、按类别多边形NMS、参数与计数/耗时、正常空失败样例及验证；无训练、Worker、HTTP或业务修改。
- 分支codex/ship-tiled-inference，基于PR #4的4db0ca0（#4仍开放，依赖#2）；未采用关闭未合并的职责调整PR #1。
- 工作区：C:/Users/28028/.codex/worktrees/ship-tiled-inference/遥感应用。原工作区和旧交付工作区未修改；本地.venv Junction/权重硬链接仅供运行，不入Git。
- 入口：algorithm/ship_detector.py中的ShipDetector.predict_large与CLI；algorithm/tiled_inference.py为内部切片/几何核心，algorithm/errors.py统一CLI与导入异常。
- 接口：[v0.2增量提案](../../contracts/ship-detection-v0.2.md)，待双方确认。旧predict仍输出v0.1；调用示例/依赖见algorithm/README.md；正常/空/错误JSON见contracts/examples。
- 已实现、已实际验证：17项测试、GPU四类样例、CPU非方形JPEG、PowerShell包装器成功、CLI错误退出2、pip check。详细命令/原始源码哈希/环境/结果见[验证记录](../validation/ship-detection-tiled-2026-10-29.md)及同名JSON。
- Git上传：本轮提交/PR将在检查清单后登记；尚未合并、部署或应用验收。
- 限制：完整解码RGB PNG/JPEG，67,108,864像素、4096片/20000候选上限；无残片拼接/地理坐标。参考参数不是效果保证，组合图不是独立精度证据；历史小船漏检未据此消除。
- 下一步：完成上传并审阅依赖PR，接入方支持v0.2；用独立标注大图评估整图与切片的AP/召回、边界错误、阈值和耗时，再决定优化。

以下为2026-10-27单图基线历史记录；其中无切片等边界描述仅适用于当时交付，不代表当前大图模式。

## 身份与状态

- 模块ID：ship-detection；A负责；执行者：当前用户授权的Codex检测实现/交付会话。
- 更新日期：2026-10-27（Asia/Shanghai）。
- 状态：可独立调用/待应用接入，未完成双方接口确认或整体业务验收。
- Issue：[#3](https://github.com/roxysunrise/remote-sensing-ship-review/issues/3)；分支：`codex/yolov8-obb-minimal`。
- 基线：`codex/project-ai-controls`（975db183，开放PR #2），先合入总控，再将本模块PR基线切换main。不要合入已关闭未合并PR #1的职责调整。
- 实现与验证：本控制文档所在提交包含源码、契约、样例和真实验证记录。
- 上传：实现提交`9e7bc453d4bee14e930bc58e767136a39ae61e7b`已推送并核对远端一致；[PR #4](https://github.com/roxysunrise/remote-sensing-ship-review/pull/4)已创建，base为`codex/project-ai-controls`。本条后续文档提交以分支最新HEAD为准；未合并、未部署。
- 隔离工作区：`C:/Users/28028/.codex/worktrees/ship-detection-delivery/遥感应用`。原工作区保留；未reset/clean/stash或提交其职责提案。

## 目标与边界

复用已有YOLOv8l-OBB权重，完成RGB PNG/JPEG单图推理、原图四点旋转框、类别/置信度、模型和参数元信息、JSON及叠框。无训练、切片合并、HTTP、Worker、业务存储/权限或其他框架模型导入。

A提供Python/CLI能力；应用接入按当前main协议进行，本模块不更改双方职责。公共契约仍为待确认提案。

## 文件与调用

- `algorithm/ship_detector.py`：ShipDetector.predict、save_result和CLI。
- `algorithm/README.md`、requirements文件：启动、依赖与已测环境、样例准备、限制。
- `contracts/ship-detection-v0.1.md`：坐标/输出/错误/耗时定义；`contracts/examples/`是真实正常/空结果/错误JSON，不包含影像。
- `scripts/run-detection.ps1`、`prepare_detection_samples.py`、`verify_detection_smoke.py`：本地启动/数据准备/真实模型检查。
- `tests/test_detection_contract.py`：无需权重的几何与输入检查。
- `models/yolov8l-obb-dota-v1-80e/README.md`：来源与校验；best.pt本身不入Git。
- `docs/validation/ship-detection-2026-10-27.json`：轻量验证记录，含实际源码哈希；大量输出、源影像及虚拟环境保留本地。

新机器先按algorithm/README.md准备环境、权重和合法本地样例，然后在仓库根目录：

```powershell
& scripts/run-detection.ps1 -Image datasets/smoke/dense.png -Device cpu
& .venv/Scripts/python.exe -m unittest discover -s tests -v
& .venv/Scripts/python.exe scripts/verify_detection_smoke.py --device 0
```

RGB/EXIF/像素和参数限制、原图坐标、错误码均以契约为唯一来源。空结果合法；CLI失败退出2。同步调用不提供取消、自动重试或项目权限；应用层不得直接作为公网不可信上传/模型入口。重复输出目录会覆盖已有JSON和预览，不自动保存业务版本。

## 本轮重新验证证据

2026-10-27，在隔离工作区使用原工作区`.venv/Scripts/python.exe`执行当前复制的源码；权重与样例只复制到Git忽略目录。环境：Python3.10.20、torch2.9.1+cu128、Ultralytics8.4.189、Pillow12.2.0、RTX5060 Laptop8GB。它不是从互联网干净环境安装的证明。

| 实际检查 | 结果与证据 | 未覆盖 |
|---|---|---|
| `python -m unittest discover -s tests -v` | 4项通过：顶点顺序/退化与非有限坐标、RGB/JPEG、灰度损坏/缺失输入 | 非完整安全/模糊测试 |
| `python scripts/verify_detection_smoke.py --device 0` | PASS；dense32、small0、empty0、非方形JPEG35 | small标注1艘但漏检；不是精度通过 |
| 密集框与标注IoU检查 | 25个预测与标注IoU≥0.5；缩小至640的非方形JPEG为23个，检查坐标缩放/方向/位置 | 非一对一精度评估，无泛化结论 |
| `python -m algorithm.ship_detector ... --device cpu` | 真实CPU推理成功，dense检出32，JSON/预览保存成功 | 多机/并发未测 |
| 缺失影像CLI | INPUT_NOT_FOUND，退出2；错误样例已准备 | 非全部故障码逐项实测 |
| `python -m pip check` | No broken requirements found | 其他机器需重新验证 |

轻量记录在docs/validation，完整叠框/控制台输出在隔离工作区outputs/smoke（不上传）。历史GPU热调用约50–71ms仅本机观测，具体本轮数值见验证JSON。几何正确、模型质量、业务集成和部署是不同结论。

## 协作、风险与下一步

接入方按algorithm/README.md复用ShipDetector实例，使用契约和真实JSON样例开发；先确认字段/坐标，再接入业务服务。权重不在Git，新机器缺少私有best.pt时不能直接推理；可先跑不依赖权重的单元检查。

小船漏检、密集场景漏检/误检、全图缩小损失、无切片合并均保留。样例来自DOTA-v1.5 val，与既有DOTA-v1训练同源，未排除重叠；不能当独立精度基准。许可及源影像规则见样例说明。权重只允许可信来源。

下一步：审阅前置PR #2和本模块PR；公共契约待确认；用独立合法影像验收质量，再决定阈值/切片，不自动扩大本轮范围。

## 简短变更记录

- 2026-10-27：控制规则初建时仅导航本地未提交检测实现，未运行或上传该模块；保留此历史边界。
- 2026-10-27：用户授权按总控上传；隔离交付检测实现并重新验证CPU/GPU、正常/空/错误场景，增加真实JSON样例及验证记录；未改变PR #1的关闭状态，未自动合并或部署。
- 2026-10-27：实现提交9e7bc45远端核对一致，创建Issue #3和PR #4；PR基线显式依赖#2，上传完成与合并/部署分开记录。
- 2026-10-29：同一检测模块增加大图模式，复用单图推理核心，完成覆盖/坐标/多边形去重及真实CPU/GPU检查；不宣称精度改善，未自动合并或部署。
