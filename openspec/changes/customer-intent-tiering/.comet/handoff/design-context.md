# Comet Design Handoff

- Change: customer-intent-tiering
- Phase: design
- Mode: compact
- Context hash: 27be57423b0d30a800cc93c39e6fe9d3cdf6bb4613ae3126b32fbbef40a1d216

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/customer-intent-tiering/proposal.md

- Source: openspec/changes/customer-intent-tiering/proposal.md
- Lines: 1-30
- SHA256: 09e24a0c9dad408c13b803317b22170562e32099edffcf8b44b44828b86da769

```md
# Proposal: 客户自动分层标签体系（customer-intent-tiering）

## Why

业务员需要按客户意向程度制定差异化跟进策略，但当前系统仅维护自由文本画像字段（如 `deal_stage`），无法自动将客户划分为意向等级并打标签，也无法观察客户意向随时间的变化。业务员只能凭记忆判断客户优先级，效率低且易遗漏高意向客户。

## What Changes

- **独立分层分析**：新增客户意向分层分析能力，LLM 按初版规则将客户划分为 A/B/C/D 意向等级并打业务标签（预定义核心标签 + LLM 自由补充），可独立触发（支持每天触发观察客户变化）。
- **分层历史记录**：新增分层历史表，记录每次分层的等级/标签/时间，可查看客户意向变化轨迹。
- **分层范围可配**：默认对近期活跃客户分层，用户可手动筛选客户范围。
- **人工可改**：业务员可手动调整客户等级/标签，人工值不被后续自动分层覆盖。
- **前端展示**：客户列表卡片显示等级徽章 + 标签，支持按等级筛选/排序。

## Capabilities

### New Capabilities
- `customer-intent-tiering`: 客户意向分层分析能力，包括按规则划分 A/B/C/D 等级、生成业务标签、记录分层历史、支持人工覆盖与范围筛选

### Modified Capabilities
- `customer-profile`: 画像字段新增意向等级（intent_level）与标签（tags），支持人工编辑优先
- `web-app`: 客户列表页展示等级徽章与标签，支持按等级筛选

## Impact

- **app/profile/**: 新增分层分析模块（`tiering.py`），复用 `build_customer_summary` 生成聊天摘要，LLM 输出结构化分层结果
- **app/storage/sqlite_store.py**: 新增分层历史表（`customer_tier_history`）读写；`profiles` 表复用现有 key-value 结构存储 `intent_level`/`tags`
- **app/web/routes.py**: 新增分层分析触发 API、分层历史查询 API、客户列表等级筛选
- **app/web/templates/**: `customers.html` 增加等级徽章/标签/筛选；`profile_list.html` 支持编辑等级与标签
- **app/web/static/css/app.css, js/app.js**: 等级徽章样式与筛选交互
- 无第三方依赖变更；不影响采集器只读语义与 RAG/知识库核心```

## openspec/changes/customer-intent-tiering/design.md

- Source: openspec/changes/customer-intent-tiering/design.md
- Lines: 1-56
- SHA256: b9b3cd048d4b4f063cc16b6ab2cff9230a5c1e81d4c6db720a6cac7c07d90dd1

```md
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

无 —— 关键决策（独立触发、历史记录、范围筛选、标签体系、前端展示）已在需求澄清时与用户确认。```

## openspec/changes/customer-intent-tiering/tasks.md

- Source: openspec/changes/customer-intent-tiering/tasks.md
- Lines: 1-29
- SHA256: f808b9bdb215fb6d92322e0b16175f113b6a0e93544a13e2c9eedfc01794c9b0

```md
# Tasks: 客户自动分层标签体系

## 1. 存储层：分层历史表

- [ ] 1.1 在 `app/storage/sqlite_store.py` 迁移逻辑中新增 `customer_tier_history` 表（id, customer_id, intent_level, tags, created_at），幂等
- [ ] 1.2 新增分层历史读写方法：`add_tier_history(customer_id, intent_level, tags)`、`get_tier_history(customer_id)`，并配套单元测试

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
- [ ] 5.3 全量回归：`compileall` + `pytest` 通过```

## openspec/changes/customer-intent-tiering/specs/customer-intent-tiering/spec.md

- Source: openspec/changes/customer-intent-tiering/specs/customer-intent-tiering/spec.md
- Lines: 1-67
- SHA256: b47e89161c061f1c772df87a22ec904bdefa2f8918e188bd4588353eced2e160

```md
## Purpose

提供客户意向分层分析能力，按规则将客户划分为 A/B/C/D 意向等级并生成业务标签，记录分层历史以观察客户意向变化，支持人工覆盖与分层范围筛选。

## ADDED Requirements

### Requirement: 客户意向分层分析
系统 SHALL 提供客户意向分层分析能力，由 LLM 按初版规则将客户划分为 A/B/C/D 意向等级并生成业务标签，可独立触发。

#### Scenario: 触发分层分析
- **WHEN** 用户触发客户意向分层分析
- **THEN** 系统 SHALL 对目标客户生成意向等级（A/B/C/D）与业务标签，并记录分层结果

#### Scenario: 分层范围筛选
- **WHEN** 用户指定分层分析的客户范围（如近期活跃客户）
- **THEN** 系统 SHALL 仅对范围内客户执行分层分析

#### Scenario: 无数据客户
- **WHEN** 某客户无足够聊天数据可供分析
- **THEN** 系统 SHALL 将该客户标记为未分层，不报错

#### Scenario: 异步执行分层
- **WHEN** 用户触发分层分析
- **THEN** 系统 SHALL 创建异步任务后台执行，前端可轮询任务进度直至完成

#### Scenario: 分层不阻塞回复
- **WHEN** 分层分析任务执行期间存在待处理的回复生成任务
- **THEN** 系统 SHALL 优先处理回复生成任务，分层任务不阻塞回复

### Requirement: 分层历史记录
系统 SHALL 记录每次分层分析的结果（等级/标签/时间），支持查看客户意向变化轨迹。

#### Scenario: 记录分层历史
- **WHEN** 系统完成一次客户分层分析
- **THEN** 系统 SHALL 保存该客户本次的等级、标签与时间戳

#### Scenario: 查看分层历史
- **WHEN** 用户查看某客户的分层历史
- **THEN** 系统 SHALL 展示该客户历次分层的等级、标签与时间

#### Scenario: 记录触发方式
- **WHEN** 系统记录一次分层历史
- **THEN** 系统 SHALL 记录该次分层的触发方式（自动分层或人工调整）

### Requirement: 人工覆盖优先
系统 SHALL 允许业务员手动调整客户意向等级与标签，人工值不被后续自动分层覆盖。

#### Scenario: 手动调整等级
- **WHEN** 业务员手动修改某客户意向等级或标签
- **THEN** 系统 SHALL 以人工值为准，后续自动分层不覆盖该值

### Requirement: 分层规则
系统 SHALL 按初版规则判定客户意向等级：A 类（高意向，明确确认车型/议价/索要单证/约定看车/谈付款）、B 类（中意向，详细询价/多次沟通/询问物流交期）、C 类（低意向，一般询价/简单咨询）、D 类（无效/沉睡，垃圾询盘/长期无回复）。

#### Scenario: 高意向客户判为 A
- **WHEN** 客户聊天中出现明确确认车型、议价、索要单证、约定看车或谈付款等强意向信号
- **THEN** 系统 SHALL 将该客户判定为 A 类

#### Scenario: 低意向客户判为 C
- **WHEN** 客户仅一般询价或简单咨询，无明确采购意向
- **THEN** 系统 SHALL 将该客户判定为 C 类

### Requirement: 标签体系
系统 SHALL 支持预定义核心标签与 LLM 自由补充标签，标签用于描述客户业务状态。

#### Scenario: 生成标签
- **WHEN** 系统对客户执行分层分析
- **THEN** 系统 SHALL 从预定义标签集中选择并允许 LLM 补充自定义标签```

## openspec/changes/customer-intent-tiering/specs/customer-profile/spec.md

- Source: openspec/changes/customer-intent-tiering/specs/customer-profile/spec.md
- Lines: 1-19
- SHA256: 713641e68dc70f18c6508e30cd3dde331dfa30993c8c58d552300f9f3fbe5f29

```md
## MODIFIED Requirements

### Requirement: 客户画像字段维护
系统 SHALL 为每个客户维护画像字段（姓名/公司/国家/产品兴趣/询价历史/沟通偏好/语言/成交阶段/意向等级/标签等），存储于结构化存储，并支持手动编辑修正。画像抽取所用的聊天摘要 SHALL 对群聊会话按发送者标注消息归属，使 LLM 能区分群内不同成员的发言。

#### Scenario: 自动抽取画像
- **WHEN** 某客户有新增聊天内容并触发画像更新
- **THEN** 系统 SHALL 由 LLM 从该客户近期聊天摘要中抽取/更新画像字段，带时间戳与来源标记

#### Scenario: 群聊摘要按发送者标注
- **WHEN** 画像抽取所依据的聊天摘要来自群聊会话
- **THEN** 摘要 SHALL 按发送者显示名标注每条消息（如 `成员名: 正文`），单聊保持 `我/客户` 标注不变

#### Scenario: 手动编辑优先
- **WHEN** 用户手动编辑某画像字段
- **THEN** 该字段 SHALL 以用户编辑值为准（标记为人工来源），不被后续自动抽取覆盖

#### Scenario: 意向等级与标签字段
- **WHEN** 系统对客户执行意向分层分析
- **THEN** 系统 SHALL 将意向等级（intent_level）与标签（tags）写入该客户画像字段，并遵循人工编辑优先规则```

## openspec/changes/customer-intent-tiering/specs/web-app/spec.md

- Source: openspec/changes/customer-intent-tiering/specs/web-app/spec.md
- Lines: 1-23
- SHA256: 1ed3ef5a0125a66fb8c38a2a12047bbb7537c84fe087e0d59677714908997ac3

```md
## MODIFIED Requirements

### Requirement: 客户列表与画像页
系统 SHALL 提供客户列表页与客户画像页，画像页支持编辑。

#### Scenario: 浏览客户列表
- **WHEN** 用户打开客户列表
- **THEN** 系统 SHALL 展示所有客户及其关键画像摘要

#### Scenario: 编辑画像
- **WHEN** 用户在画像页编辑某字段并保存
- **THEN** 系统 SHALL 持久化该编辑值并标记为人工来源

#### Scenario: 展示意向等级徽章与标签
- **WHEN** 用户打开客户列表
- **THEN** 每个客户卡片 SHALL 展示其意向等级徽章（A/B/C/D）与业务标签

#### Scenario: 按意向等级筛选
- **WHEN** 用户选择意向等级筛选条件
- **THEN** 客户列表 SHALL 按所选等级过滤，且与搜索及其他筛选条件叠加生效

#### Scenario: 编辑意向等级与标签
- **WHEN** 用户在画像页编辑意向等级或标签并保存
- **THEN** 系统 SHALL 持久化该编辑值并标记为人工来源```

