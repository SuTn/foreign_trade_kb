# Proposal: 工作台回复/画像深化（workspace-reply-profile）

## Why

三栏工作台已上线，但左栏客户列表**缺少意向等级筛选**（spec `web-app` 已定义"按意向等级筛选"，工作台左栏未实现，仅旧 `/customers` 页有）。业务员面对大量客户时，无法快速聚焦高意向（A/B 级）客户。

右栏 AI 建议目前是"生成客户分析"（`analyze_customer_full` 输出兴趣点/活跃度/跟进建议的**自由文本**），缺少**结构化、可执行的跟进建议**（下一步动作、优先级、话术建议），业务员难以直接落地。

## What Changes

- **左栏意向等级筛选**：工作台左栏加等级筛选下拉（全部/A/B/C/D/未分层），与现有搜索叠加生效。
- **AI 建议结构化**：新增"跟进建议"生成，输出结构化卡片（下一步动作 / 建议话术 / 优先级 / 最佳跟进时机），与现有"客户分析"并列或替换。
- **画像编辑确认**：右栏画像编辑（等级/标签/字段）已可用，本期确认并补齐缺失交互（如保存后反馈）。

## Capabilities

### New Capabilities
- `workspace-tier-filter`: 工作台左栏按意向等级筛选客户
- `workspace-followup`: 结构化跟进建议生成

### Modified Capabilities
- `web-app`: 工作台左栏筛选 + 右栏跟进建议

## Impact

- **app/web/routes.py**: `GET /workspace` 支持 `?tier=` 筛选参数；新增跟进建议生成端点（复用 `analyze_customer_full` 或新增 `follow_up` 结构化输出）。
- **app/web/templates/workspace.html**: 左栏加等级筛选下拉。
- **app/web/templates/workspace_customers.html**: 客户行按等级筛选过滤。
- **app/web/templates/workspace_side.html**: AI 建议 Tab 增加"跟进建议"结构化卡片。
- **app/web/static/js/app.js**: 等级筛选逻辑（复用 `initWorkspaceFilter` 扩展）。
- **app/web/static/css/app.css**: 筛选下拉、跟进建议卡片样式。
- **app/profile/service.py**: 新增结构化跟进建议生成函数。
- **测试**: 等级筛选、跟进建议生成。

## Non-goals

- 不重写现有"客户分析"（保留兴趣点/活跃度文本，新增跟进建议）。
- 不做跟进建议的自动定时生成（手动触发，避免 LLM 成本）。
- 不改采集器。