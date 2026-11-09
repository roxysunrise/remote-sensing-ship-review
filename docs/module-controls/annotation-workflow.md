# 标注任务工作流

负责人：项目统筹。基于 main 96cf56d 的现有项目、上传检测、复核瓦片和版本功能。

真实 HTTP 模式下，项目负责人进入项目任务和待审核，项目分析员进入任务大厅和我的任务。沿用既有登录和启动脚本。主管完成检测后可以发布或定向分派。标注者领取后查看规则，再进入工作台。待审核版只读，可以切换模型原始结果，签收后从工作台导出。

草稿使用修订号保护，任务领取与审批状态持久化于 SQLite，历史库启动时通过幂等建表新增数据结构。旧检测及草稿数据仍可访问。接口、状态与错误示例见[标注任务契约](../../contracts/annotation-workflow.md)。

## 复跑

- 项目权重下运行 python -m unittest discover -s tests。标注专项覆盖双人竞争、角色权限、过期草稿、低置信度门槛、返修和签收版本。
- Edge 联调需要独立 SHIP_VAR_DIR、python scripts/seed_tile_fixture.py、现有同源开发服务以及 websocket-client。运行 python scripts/browser_annotation_check.py，使用 SHIP_FRONTEND_URL 指向前端。每次联调使用新测试库。
- 真实模型链路可用 python scripts/e2e_link_check.py 复跑。Edge 流程使用可重复的合成检测结果，模型效果与实际操作者验收单独记录。

本轮验收：项目权重 CPU 全套 93 项、瓦片 Node 12 项、真实 HTTP 原链路 39 项通过。Edge 使用带 4 个模型框的合成夹具跑通领取、数值预览和编辑、暂停、刷新续做、提交、对照和签收。使用同源 HTTP 下载签收 CSV。另将真实权重生成的 25 框结果发布并领取，验证真实 AI 候选替换保持目标 ID 和框总数。现场操作者与正式环境待验收。
