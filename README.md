# 遥感船舶检测与人机协同复核系统

面向静态港口遥感影像，实现可靠上传、船舶旋转框检测、大图浏览、人工与 AI 辅助复核、区域统计、版本保存和导出。

当前状态：已实现本地YOLOv8l-OBB单图推理最小模块，复用已有权重，CPU/GPU已实测；完整应用、服务部署及整体技术栈尚未完成或确定。

检测模块现增加[大图推理模式](contracts/ship-detection-v0.2.md)：重叠切片、四点坐标还原和跨片多边形NMS；通过`predict_large()`或CLI `--tiled`调用。它属于同一检测模块，验证流程不等于精度提升。

## 本地检测模块

启动、依赖、样例与限制见[算法模块说明](algorithm/README.md)，输出见[检测契约v0.1提案](contracts/ship-detection-v0.1.md)，交付状态见[模块控制文档](docs/module-controls/ship-detection.md)。准备本地权重、环境与样例后运行 `scripts/run-detection.ps1 -Image datasets/smoke/dense.png -Device 0`，CPU用 `-Device cpu`。权重、影像、数据集与虚拟环境不入Git。

## 协作入口

- [双方协作管理协议](docs/双方协作管理协议.md)
- [邀请 B 方与权限管理](docs/邀请B方与权限管理.md)
- [模块交付模板](docs/模块交付模板.md)
- [接口约定目录](contracts/README.md)

本仓库最新分工：A 负责业务后端、算法、存储及大图编辑核心组件；B 负责前端页面、交互与集成。旧职责文档中 B 承担业务后端的条款已由本轮协作约定调整；旧文档原件未修改，也未复制入仓库。

## 开发原则

双方可直接开工，不以对方先交 API 为前提。已有接口遵守现有约定；接口缺失时先定义并同步公开，通过 PR 确认后成为共同标准。

建议目录（实现时按需创建）：

```text
backend/                  A：业务接口、权限和数据库
algorithm/                A：检测、大图计算、AI辅助
components/image-workbench/ A：大图和旋转框编辑核心
frontend/                 B：页面、交互和业务集成
contracts/                双方：接口、数据类型和样例
docs/                     说明、交付与验收记录
tests/                    模块与集成验证
scripts/                  启动、初始化与备份恢复
```

不提交用户影像、完整数据集、模型大文件、运行数据库或认证秘密。项目尚未选择开源许可证；公开、授权和发布需双方另行确认。
