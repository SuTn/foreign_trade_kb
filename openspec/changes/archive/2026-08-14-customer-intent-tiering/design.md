# Design: 客户自动分层标签体系

## Context

现有系统已具备客户画像抽取（`app/profile/extractor.py`，LLM 从聊天摘要抽取字段写入 `profiles` key-value 表）与客户分析（`app/profile/analyzer.py`）。`profiles` 表为 `(customer_id, field, value, source, updated_at)` 结构，`upsert_profile_field` 已实现 `source=manual` 不被 `auto` 覆盖（`sqlite_store.py:60`）。`build_customer_summary`（`service.py:40`）可汇总某客户全部关联会话的近期聊天。Web 层 `routes.py` 已有客户列表（`/customers`）与画像编辑（`/customers/{id}/profile`）接口。参见 proposal.md - Why。

## Goals / Non-Goals

**Goals:**
- 新增独立分层分析模块，LLM 按初版规则输出 A/B/C/D 等级 + 标签
- 记录分层历史（等级/标签/时间），支持查看变化轨迹
- 复用 `profiles` 表存储当前 `intent_level`/`tags`，复用 manual 保护机制
- 前端客户列表展示等级徽章 + 标签，支持按等级筛选

**Non-Goals:**
- 不做跟进提醒/公海流转（依赖分层但需额外设计，后续 change）
- 不做询盘质量评分模型（垃圾询盘识别是独立能力）
- 不做分层结果的自动定时调度（本期仅手动触发，用户可每天手动跑）

## Decisions

### D1: 分层分析独立模块，复用现有摘要构建
新增 `app/profile/tiering.py`，核心函数 `tier_customer(store, llm, customer_id)`：调用 `build_customer_summary` 生成聊天摘要 → LLM 按初版规则输出结构化 JSON（`intent_level` + `tags`）→ 写入 `profiles`（`intent_level`/`tags` 字段，auto 来源）→ 写入分层历史表。
- 备选：并入 `extractor.py` 随画像抽取一起 —— 但用户明确要求「独立分层分析，可每天触发」，故独立模块便于单独触发与记录历史。弃用并入方案。

### D2: 分层历史表 `customer_tier_history`
新增表 `customer_tier_history(id, customer_id, intent_level, tags, created_at)`，每次分层分析追加一条记录。当前值存 `profiles`（`intent_level`/`tags`），历史存此表，两者分离：`profiles` 供列表快速展示，历史表供变化轨迹查询。
- 备选：只存 `profiles` 当前值 —— 无法看历史变化，违背「记录历史变化」需求。弃用。

### D3: 分层范围筛选
`analyze_customer` 支持传入客户范围。默认近期活跃客户（有近期消息的客户），用户可手动筛选（如指定客户列表）。Web API 提供 `POST /api/tiering/analyze`，body 可选 `customer_ids`（缺省=近期活跃客户）。
- 近期活跃定义：有 `messages` 且最近消息时间在 N 天内（N 可配，默认 30 天）。

### D4: 标签体系：预定义 + 自由补充
定义预定义标签集（如：已购、意向车型、议价中、待跟进、需回访、沉睡、垃圾询盘等），LLM 从预定义集中选择并允许补充自定义标签。`tags` 以逗号分隔字符串存 `profiles`。
- 备选：纯自由标签 —— 无法统一口径，筛选/统计困难。弃用。

### D5: 前端展示与筛选
`customers.html` 客户卡片显示等级徽章（A/B/C/D 不同颜色）+ 标签；新增等级筛选下拉（全部/A/B/C/D/未分层），与现有国家/公司筛选叠加。`profile_list.html` 支持编辑 `intent_level`/`tags`（manual 来源）。
- 复用现有 `_build_stats` 与客户列表查询，新增按 `profiles` 表 `intent_level` 字段 join 筛选。

## Risks / Trade-offs

- **[LLM 分层结果不稳定]** → 初版规则写入 prompt 约束输出格式；解析失败回退为「未分层」并记录错误，不阻塞其他客户。
- **[历史表无限增长]** → 每次分层一条记录，量级可控（客户数 × 触发次数）；后续可加清理策略，本期不做。
- **[近期活跃定义主观]** → 默认 30 天可配，用户可手动指定范围覆盖。
- **[标签口径不统一]** → 预定义标签集约束核心口径，自由补充标签仅作辅助，不参与筛选统计。

## Migration Plan

- 新增 `customer_tier_history` 表：`sqlite_store.py` 迁移逻辑 `CREATE TABLE IF NOT EXISTS`，幂等。
- `profiles` 表无需迁移（key-value 结构天然支持新字段）。
- 回滚：删除历史表 + 移除前端徽章/筛选即可，`profiles` 中 `intent_level`/`tags` 字段可保留（无害）。

## Open Questions

无 —— 关键决策（独立触发、历史记录、范围筛选、标签体系、前端展示）已在需求澄清时与用户确认。