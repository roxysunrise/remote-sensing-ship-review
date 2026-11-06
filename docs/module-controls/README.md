# 模块控制索引

每个负责模块都用[模板](TEMPLATE.md)维护一份控制文档。开始工作先读，发生实质变更后更新，随代码上传。这里记录模块导航，不代替 Issue、契约或测试。

| 模块 ID | 负责人 | 控制文档 | 当前记录 |
|---|---|---|---|
| ship-detection | A | [船舶检测](ship-detection.md) | 类别改为不再固定 ship：`classes` 默认全 15 类，另修 /vision/detect 响应 `by_class`/`class_id` 与前端按类着色；四张实测影像 0→15/10/130/108 个框，78 项单测与 HTTP 端到端通过；历史大图切片增量见[PR #5](https://github.com/roxysunrise/remote-sensing-ship-review/pull/5)；未合并/部署 |
| backend-api | A | [后端 API](backend-api.md) | 实现 B 方前端声明的 29+5 个端点，另加瓦片 3 个、切片预演、项目/任务统计 2 个；73项测试（18 算法 + 31 后端 + 8 ai_assist + 13 瓦片/规划器 + 3 统计）与契约校验（21/21 + 6/6）本地通过；已完成片级让锁/内存直推/ETag/限流/清理、AI 补框与精修、大图瓦片与 VLM 规划器骨架；Issue #7、[PR #8](https://github.com/roxysunrise/remote-sensing-ship-review/pull/8)已上传，base 为检测分支 `codex/ship-tiled-inference`；未合并/部署 |

后续按实际工作登记大图检测、Worker、编辑组件、AI辅助、上传存储等模块，不把尚未开始的模块登记为完成。职责按实际已确认协议填写；不得用本索引改变 B 方分工。

## 给新会话的启动语

```text
这是遥感船舶检测与复核项目。先阅读根目录 AGENTS.md、模块控制索引及本次模块控制文档，核对 GitHub/本地状态，以 A 方负责人身份继续工作。保护其他会话的未提交改动；完成后更新控制文档并按规则提交分支和 PR。
```

本记录初建日期：2026-10-27。协作说明见[AI接入与上传说明](../AI接入与上传说明.md)。
