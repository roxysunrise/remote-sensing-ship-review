# AI 接入与上传说明

本地入口为根目录 `AGENTS.md`，模块导航为 `docs/module-controls/README.md`，每个模块按 `TEMPLATE.md` 建立控制文档。

支持 AGENTS.md 的 Codex 会话应以仓库为工作目录启动；已运行会话需要明确重新读取本文件和相关控制文档。其他平台若不能访问本地文件，应手动附上 AGENTS.md、索引、相关模块控制和当前请求；上传一个 Markdown 文件并不能让所有历史对话自动获知项目。

建议给新 AI 的指令：

```text
请先读项目 AGENTS.md 和 docs/module-controls/README.md，以 A 方负责人身份工作。核对本次模块控制文档、代码及 GitHub 的实际状态，继续实现本次要求。完成后更新模块控制文档，运行必要检查，仅提交本次文件，推送功能分支并创建/更新 PR，不自动合并。
```

## 本次初始化的隔离方式

原工作区存在未提交检测实现，因此本次只增加控制规则文件，在基于 origin/main 的 `codex/project-ai-controls` 分支独立提交。原工作目录保留相同文档供当前会话使用；原算法文件与 README 修改不加入本次提交。该控制规则 PR 不包含已关闭 PR #1 的职责调整，也不表示职责调整获确认。

后续正常按 AGENTS.md 上传每次授权的模块工作。需要无关改动隔离时使用独立 worktree，记录路径和分支，不能用 reset/clean 清理其他会话工作。

## 参考

[OpenAI：使用 AGENTS.md 自定义指令](https://learn.chatgpt.com/docs/agent-configuration/agents-md)。实际是否加载还取决于工具的指令发现机制、工作目录及会话状态。
