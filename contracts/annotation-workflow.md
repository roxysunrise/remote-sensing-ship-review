# 标注任务契约

适用于现有业务 API 的 /api/v1 前缀，使用项目会话。标注任务以完成的检测任务 ID 为键，检测状态与人工状态分别记录。

## 查询和状态操作

- GET /projects/{project_id}/annotations 返回 items 和 current_task_id。主管看到本项目全部标注任务；标注者看到可领取项及自己的任务。current_task_id 跨项目，可能不在 items 中。
- POST /tasks/{task_id}/annotation/{operation} 返回更新后的状态、归属、revision、version_id、reason 和 target_id。
- POST /tasks/{task_id}/draft/confirm-high 接收 revision，返回 detections 与 annotation_revision，只处理置信度至少 0.45 的 pending 框。

| 操作 | 身份 | 前置状态 | 请求补充字段 |
|---|---|---|---|
| publish | 主管 | 检测完成且有 v0，尚未发布或可领取 | 无 |
| assign | 主管 | 检测完成且有 v0，尚未发布或可领取、已分派、已暂停、已退回 | assignee |
| claim | 标注者 | 可领取，无其他进行中任务 | 无 |
| start | 当前标注者 | 已分派、已领取、已暂停或已退回 | 无 |
| release | 当前标注者 | 已领取，尚未开始 | 无 |
| pause | 当前标注者 | 标注中 | 无 |
| request_release | 当前标注者 | 标注中或已暂停 | reason |
| resolve_release | 主管 | 申请交还 | 无 |
| submit | 当前标注者 | 标注中，全部框已处理 | revision |
| return | 主管 | 待审核 | reason，可选 target_id |
| accept | 主管 | 待审核 | 无 |

标注状态依次可用 available、assigned、claimed、active、paused、release_requested、submitted、returned、signed 表达。claimed 和 active 共用每人一个在办名额。暂停后保留归属和草稿。批准交还保留草稿并重新开放领取。

## 草稿与导出

GET /tasks/{task_id}/draft 返回 annotation_revision，未发布任务为 null。已发布任务修改目标、重置草稿、保存区域及采纳 AI 候选时，请将最后一个成功响应的 annotation_revision 作为 revision 发送。写入与修订号在同一数据库事务提交。候选采纳同时传 task_id；可选 replace_id 将候选几何写入选中框。

提交锁定独立版本。退回指定目标会将草稿中对应框设为 pending，已提交版本保持原样。已发布任务只能导出签收的 version_id，未发布的历史任务仍沿用原有版本导出规则。

正常示例：POST /tasks/tsk_example/annotation/submit，JSON 为 {"revision":7}，响应包含 {"status":"submitted","version_id":"v1","revision":8}。

冲突示例：使用过期修订号更新草稿返回 409 STALE_DRAFT；未处理完待确认目标返回 409 ANNOTATION_PENDING；已经领取其他任务时返回 409 ANNOTATION_BUSY。收到冲突后重新读取任务和草稿。
