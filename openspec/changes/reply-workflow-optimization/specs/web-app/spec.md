# web-app Delta Spec

> Delta 变更，叠加于 `openspec/specs/web-app/spec.md`。

## ADDED Requirements

### Requirement: 回复结果异步轮询

系统 SHALL 在提交回复任务后，前端轮询任务状态直至完成并展示结果，而非阻塞等待。

#### Scenario: 轮询任务状态

- **WHEN** 用户触发回复生成
- **THEN** 前端 SHALL 提交任务后周期轮询 `GET /api/reply/status/{task_id}`，完成后展示建议回复与来源，失败时展示错误

### Requirement: 建议回复一键复制

系统 SHALL 为建议回复提供一键复制按钮，用户复制后自行粘贴到 WhatsApp。

#### Scenario: 复制建议回复

- **WHEN** 建议回复已生成展示
- **THEN** 页面 SHALL 提供「复制」按钮，点击后使用剪贴板 API 复制回复内容
