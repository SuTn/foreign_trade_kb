# Tasks: 客户自动分层标签体系

## 1. 存储层：分层历史表

- [x] 1.1 在 `app/storage/sqlite_store.py` 迁移逻辑中新增 `customer_tier_history` 表（id, customer_id, intent_level, tags, created_at），幂等
- [x] 1.2 新增分层历史读写方法：`add_tier_history(customer_id, intent_level, tags)`、`get_tier_history(customer_id)`，并配套单元测试

## 2. 分层分析模块

- [ ] 2.1 新增 `app/profile/tiering.py`：定义预定义标签集与初版分层规则 prompt，`tier_customer(store, llm, customer_id)` 复用 `build_customer_summary` 生成摘要 → LLM 输出结构化 JSON（intent_level + tags）→ 写入 `profiles`（auto 来源）→ 写入历史表
- [ ] 2.2 解析失败回退为「未分层」并记录错误，不阻塞其他客户；无聊天数据客户标记未分层
- [ ] 2.3 新增 `tier_customers(store, llm, customer_ids)` 批量分层入口，支持范围筛选（近期活跃客户默认 N 天，可配）

## 3. Web API

- [ ] 3.1 新增 `POST /api/tiering/analyze`：body 可选 `customer_ids`（缺省=近期活跃客户），触发分层分析，返回处理结果
- [ ] 3.2 新增 `GET /api/tiering/history/{customer_id}`：返回该客户分层历史
- [ ] 3.3 客户列表查询支持按 `intent_level` 筛选（与现有国家/公司筛选叠加），配套接口测试

## 4. 前端展示

- [ ] 4.1 `customers.html` 客户卡片显示意向等级徽章（A/B/C/D 不同颜色）+ 标签；新增等级筛选下拉（全部/A/B/C/D/未分层）
- [ ] 4.2 `profile_list.html` 支持编辑 `intent_level`/`tags`（manual 来源）
- [ ] 4.3 `app.css` 增加等级徽章样式；`app.js` 增加等级筛选交互

## 5. 测试与验证

- [ ] 5.1 新增/更新单元测试：tiering 模块、分层历史读写、分层 API、等级筛选
- [ ] 5.2 手动验证：触发分层分析 → 客户获得等级/标签 → 列表按等级筛选 → 历史可查 → 人工修改后 auto 不覆盖
- [ ] 5.3 全量回归：`compileall` + `pytest` 通过