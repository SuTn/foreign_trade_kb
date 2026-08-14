---
comet_change: customer-intent-tiering
role: technical-design
canonical_spec: openspec
archived-with: 2026-08-14-customer-intent-tiering
status: final
---

# Design: 客户自动分层标签体系

## Context

现有系统已具备客户画像抽取（`app/profile/extractor.py`，LLM 从聊天摘要抽取字段写入 `profiles` key-value 表）与客户分析（`app/profile/analyzer.py`）。`profiles` 表为 `(customer_id, field, value, source, updated_at)` 结构，`upsert_profile_field` 已实现 `source=manual` 不被 `auto` 覆盖（`sqlite_store.py:60`）。`build_customer_summary`（`service.py:40`）可汇总某客户全部关联会话的近期聊天。Web 层已有回复异步任务机制（`reply_tasks` 表 + `app/web/worker.py` 常驻串行 worker）。客户列表筛选为纯前端 JS（`app.js` 的 `initCustomerFilter` 用 `data-search` 属性匹配）。参见 proposal.md - Why。

## Goals / Non-Goals

**Goals:**
- 新增独立分层分析模块，LLM 按初版规则输出 A/B/C/D 等级 + 标签
- 分层分析异步执行（`tiering_tasks` 任务表 + worker 消费），前端轮询进度
- 记录分层历史（`customer_tier_history`，含触发方式 auto/manual），详情页时间线展示
- 复用 `profiles` 表存储当前 `intent_level`/`tags`，复用 manual 保护机制
- 前端客户列表展示等级徽章 + 标签，按等级筛选（纯前端 JS）

**Non-Goals:**
- 不做跟进提醒/公海流转（依赖分层但需额外设计，后续 change）
- 不做询盘质量评分模型（垃圾询盘识别是独立能力）
- 不做分层结果的自动定时调度（本期仅手动触发，用户可每天手动跑）

## Decisions

### D1: 独立分层分析模块，复用现有摘要构建
新增 `app/profile/tiering.py`，核心函数 `tier_customer(store, llm, customer_id)`：调用 `build_customer_summary` 生成聊天摘要 → LLM 按初版规则输出结构化 JSON（`intent_level` + `tags`）→ 写入 `profiles`（`intent_level`/`tags` 字段，auto 来源）→ 写入分层历史表（source=auto）。
- 备选：并入 `extractor.py` 随画像抽取一起 —— 但用户明确要求「独立分层分析，可每天触发」，故独立模块便于单独触发与记录历史。弃用并入方案。

### D2: 分层异步任务表 `tiering_tasks` + 扩展 worker
新增表 `tiering_tasks(id, customer_ids(JSON), status, progress, result, error, created_at, updated_at)`。`POST /api/tiering/analyze` 创建任务，worker 消费。**扩展现有 `worker_loop`**（`app/web/worker.py`），同一循环串行消费 `reply_tasks` 和 `tiering_tasks`，保证一次一个 LLM 调用。
- 备选：新增独立 worker 线程 —— 两个 worker 并发会同时调用 LLM，违背「串行消费保证一次一个 LLM 调用」约束。弃用。

### D3: 分层历史表 `customer_tier_history`
新增表 `customer_tier_history(id, customer_id, intent_level, tags, source, created_at)`，`source` 为 `auto`（自动分层）或 `manual`（人工调整）。每次分层分析追加一条 auto 记录；人工调整等级/标签时追加一条 manual 记录。当前值存 `profiles`，历史存此表。
- 备选：只存 `profiles` 当前值 —— 无法看历史变化，违背「记录历史变化」需求。弃用。

### D4: 分层范围筛选
`tier_customers(store, llm, customer_ids)` 支持传入客户范围。默认近期活跃客户（有 `messages` 且最近消息时间在 N 天内，N 可配默认 30 天），用户可手动指定客户列表。Web API `POST /api/tiering/analyze` body 可选 `customer_ids`（缺省=近期活跃客户）。

### D5: 标签体系：预定义 + 自由补充
定义预定义标签集（如：已购、意向车型、议价中、待跟进、需回访、沉睡、垃圾询盘等），LLM 从预定义集中选择并允许补充自定义标签。`tags` 以逗号分隔字符串存 `profiles`。
- 备选：纯自由标签 —— 无法统一口径，筛选/统计困难。弃用。

### D6: 前端筛选走纯前端 JS
现有 `customers.html` 的 `data-search` 已包含 `profiles_by_customer`（含 intent_level/tags），等级筛选**无需改 data-search 结构**，只需新增等级下拉（全部/A/B/C/D/未分层）+ `app.js` 过滤逻辑，与现有国家/公司筛选叠加。
- 备选：后端 join 筛选 —— 需改路由与查询，且与现有纯前端筛选模式不一致。弃用。

### D7: 回复优先 + 分层任务限流
分层任务在 worker 中执行时，每处理一个客户检查是否有 pending `reply_tasks`，有则先消费回复（回复优先，避免分层阻塞回复）。限制单次分层任务客户数上限（可配，默认如 50），超限分批。
- 风险缓解：近期活跃客户上百时，串行分层会长时间占用 worker 阻塞回复。

### D8: 摘要长度限制
分层分析复用 `settings.profile_summary_messages` 限制 `build_customer_summary` 的摘要长度，控制 LLM 输入 token 与成本。

## Risks / Trade-offs

- **[LLM 分层结果不稳定]** → 初版规则写入 prompt 约束输出格式；解析失败回退「未分层」并记录错误，不阻塞其他客户。
- **[分层任务阻塞回复]** → D7 回复优先 + 客户数上限缓解。
- **[历史表无限增长]** → 量级可控（客户数 × 触发次数）；后续可加清理策略，本期不做。
- **[近期活跃定义主观]** → 默认 30 天可配，用户可手动指定范围覆盖。
- **[标签口径不统一]** → 预定义标签集约束核心口径，自由补充标签仅作辅助，不参与筛选统计。

## Migration Plan

- 新增 `tiering_tasks`、`customer_tier_history` 表：`sqlite_store.py` 迁移逻辑 `CREATE TABLE IF NOT EXISTS`，幂等。
- `profiles` 表无需迁移（key-value 结构天然支持新字段）。
- 回滚：删除两表 + 移除前端徽章/筛选/历史区块即可，`profiles` 中 `intent_level`/`tags` 字段可保留（无害）。

## Open Questions

无 —— 关键决策（独立触发、异步执行、历史记录、范围筛选、标签体系、前端展示、回复优先）已在需求澄清与方案审查时与用户确认。
