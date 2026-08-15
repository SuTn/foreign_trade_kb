# Tasks: 工作台回复/画像深化

## 1. 左栏等级筛选

- [x] 1.1 `workspace.html`：左栏加 `<select id="ws-tier">`（全部/A/B/C/D/未分层）
- [x] 1.2 `app.js`：`initWorkspaceFilter` 扩展为同时匹配搜索词 + 等级（正则提取 `intent_level=([a-d])`）
- [x] 1.3 `app.css`：筛选下拉样式

## 2. 跟进建议生成

- [x] 2.1 新增 `app/profile/followup.py`：`generate_followup(store, llm, customer_id)` 结构化 JSON 输出
- [x] 2.2 新增 `POST /customers/{id}/followup` 路由，返回 `followup.html` 卡片片段
- [x] 2.3 新增 `followup.html`：结构化卡片（优先级/下一步动作/建议话术/最佳时机/依据）
- [x] 2.4 `workspace_side.html`：AI 建议 Tab 并列"客户分析"与"跟进建议"区块

## 3. 样式

- [x] 3.1 `app.css`：跟进建议卡片、优先级徽标样式

## 4. 测试与验证

- [x] 4.1 等级筛选测试（前端逻辑或后端参数）
- [x] 4.2 跟进建议路由测试（`POST /customers/{id}/followup` 返回卡片）
- [x] 4.3 全量回归：`compileall` + `pytest` 通过
- [ ] 4.4 手动验证：左栏等级筛选生效；右栏跟进建议生成结构化卡片