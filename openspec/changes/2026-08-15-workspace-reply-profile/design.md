# Design: 工作台回复/画像深化

## Context

三栏工作台已上线。左栏客户列表有搜索（`initWorkspaceFilter`）但**无意向等级筛选**（spec `web-app` 已定义，旧 `/customers` 页有 `filter-tier`，工作台未实现）。右栏 AI 建议是 `analyze_customer_full` 的自由文本（兴趣点/活跃度/跟进建议），缺少结构化、可执行的跟进建议。

目标：左栏加等级筛选；右栏新增结构化跟进建议卡片。

## Goals / Non-Goals

**Goals:**
- 工作台左栏按意向等级筛选（全部/A/B/C/D/未分层），与搜索叠加
- 右栏 AI 建议 Tab 新增"跟进建议"结构化卡片（下一步动作/建议话术/优先级/最佳时机）
- 保留现有"客户分析"文本

**Non-Goals:**
- 不重写现有客户分析
- 不做跟进建议自动定时生成（手动触发）
- 不改采集器

## Decisions

### D1: 左栏等级筛选用前端过滤（复用 `initWorkspaceFilter`）
工作台左栏加 `<select id="ws-tier">`（全部/A/B/C/D/未分层）。前端 JS 在 `initWorkspaceFilter` 基础上扩展：同时匹配搜索词 + 等级。客户行 `data-search` 已含 `intent_level=X`，可正则提取。
- 备选：后端 `?tier=` 参数重新渲染 —— 需整页/列表重载，前端过滤更即时，弃用。
- 注意：`data-search` 中 `intent_level=` 后跟值，用正则 `intent_level=([a-d])` 提取（与旧页 `initCustomerFilter` 一致）。

### D2: 跟进建议结构化输出
新增 `app/profile/followup.py`，`generate_followup(store, llm, customer_id)` 基于画像 + 聊天摘要，LLM 结构化输出 JSON：
```json
{
  "priority": "high|medium|low",
  "next_action": "下一步动作",
  "suggested_message": "建议话术",
  "best_time": "最佳跟进时机",
  "reason": "判断依据"
}
```
- 复用 `build_customer_summary` 构建摘要。
- 新增路由 `POST /customers/{id}/followup` 返回结构化卡片片段（`followup.html`）。
- 备选：并入现有 `analyze_customer` —— 输出格式不同（文本 vs 结构化），分开更清晰，弃用。

### D3: 右栏 AI 建议 Tab 并列"客户分析"与"跟进建议"
`workspace_side.html` 的 AI 建议 Tab 内分两个区块：现有"客户分析"（保留）+ 新增"跟进建议"（结构化卡片 + 生成按钮）。
- 备选：替换现有分析 —— 保留两者，弃用。

## Risks / Trade-offs

- **[LLM 结构化输出不稳定]** → 用 JSON 解析 + 容错（解析失败回退为文本展示）。
- **[等级筛选与搜索叠加]** → 复用现有 `data-search` 匹配逻辑，逻辑简单。
- **[跟进建议成本]** → 手动触发，不自动轮询。

## Migration Plan

- 无 schema 变更。
- 新增 `app/profile/followup.py`、`followup.html`、`POST /customers/{id}/followup` 路由。
- 回滚：移除筛选下拉与跟进建议区块即可。

## Open Questions

- 跟进建议是否需要持久化？（本期不持久化，每次生成展示；后续可存 `customer_summaries` 或新表）