# Brainstorm Summary

- Change: customer-intent-tiering
- Date: 2026-08-14
- Status: 已确认（用户确认方案，含审查调整）

## 确认的技术方案

### 架构
```
用户触发 → POST /api/tiering/analyze
  → 创建 tiering_task（含目标客户列表）
  → worker 消费任务
    → 对每个客户: build_customer_summary → LLM 分层 → 写 profiles + 写历史表
  → 前端轮询 GET /api/tiering/task/{id} 看进度
```

### 数据模型
1. **`tiering_tasks` 表**（任务级，异步）：
   - `id, customer_ids(JSON), status(pending/running/done/failed), progress, result, error, created_at, updated_at`
2. **`customer_tier_history` 表**（历史）：
   - `id, customer_id, intent_level, tags, source(auto/manual), created_at`

### 关键设计决策
- **D1**：独立 `app/profile/tiering.py` 模块，复用 `build_customer_summary` 生成摘要
- **D2**：`tiering_tasks` 异步任务表 + **扩展现有 worker_loop**（同一循环串行消费 reply_tasks 和 tiering_tasks，保证一次一个 LLM 调用），前端轮询进度
- **D3**：`customer_tier_history` 历史表，记录 source（auto/manual）
- **D4**：前端筛选走纯前端 JS（现有 `data-search` 已含 profiles 字段，新增等级下拉 + JS 过滤即可，无需改 data-search 结构）
- **D5**：客户详情页（chat.html）新增「分层历史」section 展示时间线
- **D6**：预定义标签集 + LLM 自由补充
- **D7**：分层任务执行时回复优先（每处理一个客户检查 pending reply_tasks，先消费回复）；限制单次分层任务客户数上限（可配）
- **D8**：分层分析复用 `settings.profile_summary_messages` 限制摘要长度，控制 LLM 成本

## 关键取舍与风险

- **[LLM 分层结果不稳定]** → 初版规则写入 prompt 约束输出格式；解析失败回退「未分层」并记录错误，不阻塞其他客户
- **[异步任务表与 reply_tasks 并存]** → 两表职责不同（批量分层 vs 单客户回复），独立实现避免耦合
- **[历史表增长]** → 量级可控（客户数 × 触发次数），后续可加清理策略，本期不做
- **[近期活跃定义主观]** → 默认 30 天可配，用户可手动指定范围

## 测试策略

- 单元测试：tiering 模块、历史读写、任务表、API
- 集成测试：分层分析流程、人工覆盖、异步任务进度

## Spec Patch

- `customer-intent-tiering` spec 增加「异步任务」场景（分层分析异步执行、进度可查）
- `customer-intent-tiering` spec 增加「历史记录触发方式」场景（source=auto/manual）
- `customer-intent-tiering` spec 增加「回复优先」场景（分层任务不阻塞回复生成）