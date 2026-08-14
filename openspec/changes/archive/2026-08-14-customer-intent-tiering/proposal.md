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
- 无第三方依赖变更；不影响采集器只读语义与 RAG/知识库核心