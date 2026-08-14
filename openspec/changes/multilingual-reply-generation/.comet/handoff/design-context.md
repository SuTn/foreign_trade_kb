# Comet Design Handoff

- Change: multilingual-reply-generation
- Phase: design
- Mode: compact
- Context hash: bbe4dcefd9a466b94b465d44b507128a8481ee157f7cdf79f8dad6ff0c237ac3

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/multilingual-reply-generation/proposal.md

- Source: openspec/changes/multilingual-reply-generation/proposal.md
- Lines: 1-33
- SHA256: b9efab67762fff1e05e966b53cc2042c66e874fe866c52b4dafb4e59c7521aae

```md
# Proposal: 场景化多语种话术生成（multilingual-reply-generation）

## Why

业务员经常需要针对询价、砍价、看车、物流、付款、售后等不同场景，用客户的母语（俄语/英语）快速生成专业回复。当前系统仅支持 4 种中文表达风格（default/concise/warm/formal），无语种、无场景识别，回复语言和业务针对性不足，业务员仍需手工翻译与改写，效率低且表达不专业。

## What Changes

- **语种支持**：回复生成支持**中/英/俄**三语输出，按所选语种生成单版本专业话术。
- **场景识别**：自动识别询价、砍价、看车、物流、付款、售后 6 类业务场景；识别失败或用户手动指定时以手动选择为准，并提供「通用」兜底。
- **语气维度**：在保留现有 4 风格（default/concise/warm/formal）前提下，新增**正式/口语** formality 维度，两者可组合，向后兼容现有调用。
- **术语内嵌**：汽车外贸领域术语写入生成提示词并抽为可配置常量（`TERMS`），本期不新建术语库表。
- **参数链路**：`reply_tasks` 表扩展 `language`/`scenario`/`formality` 字段；`POST /api/reply` 接受可选参数；worker 透传至生成器；前端聊天页提供语种/场景/语气选择。

## Capabilities

### New Capabilities
- `multilingual-copy`: 场景化多语种话术生成能力——按语种/场景/语气生成专业回复，含业务场景识别与汽车外贸术语内嵌

### Modified Capabilities
- `reply-assist`: 回复生成异步任务参数扩展——任务支持语种/场景/语气参数，前端可指定生成维度

## Impact

- **app/reply/generator.py**: 新增语种/场景/语气维度，扩展 `generate_reply` 签名（新增 `language`/`scenario`/`formality` 可选参数）与系统提示词
- **app/storage/schema.sql**: `reply_tasks` 表新增 3 个可空列（幂等迁移）
- **app/storage/sqlite_store.py**: `create_reply_task` 透传新参数
- **app/web/worker.py**: `_execute_reply_task` 将新参数传入 `generate_reply`
- **app/web/routes.py**: `POST /api/reply` 解析 `language`/`scenario`/`formality` 可选参数
- **app/web/templates/chat_messages.html**: 回复触发区增加语种/场景/语气选择
- **app/web/templates/reply_result.html**: 展示生成的语种/场景信息
- **测试**: 生成器语种/场景/语气、任务参数透传、API 参数解析、前端选择
- 无第三方依赖变更；复用现有 RAG 召回 + 异步任务机制
```

## openspec/changes/multilingual-reply-generation/design.md

- Source: openspec/changes/multilingual-reply-generation/design.md
- Lines: 1-63
- SHA256: b21ff93f861054f4dbf892e2d0175877c5b2ad599976319d8b347c286b9381b8

```md
# Design: 场景化多语种话术生成

## Context

现有回复生成链路：`chat_messages.html` 消息上「生成回复」→ `POST /api/reply` 创建 `reply_tasks`（`app/storage/schema.sql:42`，含 `style` 列）→ worker 串行消费（`app/web/worker.py:30` `_execute_reply_task`）→ `generate_reply`（`app/reply/generator.py:28`）拼提示词 → `RagPipeline.run` 召回 + LLM 生成 → 前端 `reply_result.html` 展示。生成器仅支持 `REPLY_STYLE_VARIANTS` 4 种风格（default/concise/warm/formal），语种固定中文、无场景识别。参见 proposal.md - Why。

## Goals / Non-Goals

**Goals:**
- 回复生成支持中/英/俄三语，按所选语种输出单版本专业话术
- 支持 6 类业务场景（询价/砍价/看车/物流/付款/售后）：LLM 自动识别 + 前端可手动指定，识别失败回退「通用」
- 新增 formality 维度（正式/口语），与现有 4 风格可组合，完全向后兼容
- 汽车外贸术语内嵌提示词并抽为可配常量，本期不建术语库表
- 参数沿现有异步任务链路透传，前端可选语种/场景/语气

**Non-Goals:**
- 不做「一次生成多语种双版本」——本期按所选语种生成单版本（用户已确认）
- 不新建术语库存储表/管理接口（用户已确认：提示词内置+可配常量）
- 不替换现有 4 风格体系（用户已确认：新增语气维度组合，向后兼容）
- 不做消息自动翻译入库（区别于多语种话术生成）

## Decisions

### D1: 扩展 generate_reply 签名，不新建模块
`generate_reply(pipeline, customer_id, chat_id, incoming_message, style="default", language="zh", scenario="auto", formality="casual", history=None)`。新增三个可选参数，默认值与现状等价（zh + auto + casual → 中文口语通用话术，与当前行为一致），**完全向后兼容**现有调用与测试。
- 备选：新建 `copy_generator.py` 独立模块 —— 但回复生成本就是该链路核心能力，独立模块会造成 RAG 管线重复构建。弃用。

### D2: 提示词以「维度指令」拼装，场景自动识别内嵌
`generator.py` 新增三组映射：
- `LANGUAGES = {"zh": "用简体中文回复", "en": "用英语回复", "ru": "用俄语回复"}`
- `SCENARIOS = {"auto": "", "inquiry": "本消息属于询价场景，突出车型信息与价格", "bargain": "...砍价...", "inspection": "...看车...", "logistics": "...物流...", "payment": "...付款...", "after_sale": "...售后..."}`
- `FORMALITY = {"casual": "", "formal": "使用正式书面语气，措辞严谨"}`

`scenario="auto"` 时，在 system 提示词追加指令：「先判断本条消息所属业务场景（询价/砍价/看车/物流/付款/售后），按该场景生成；无法判断时按通用处理」，即 LLM 自动识别。`REPLY_SYSTEM` 追加 `{language}{scenario}{formality}{terms}` 占位。
- 备选：先单独调用一次 LLM 做场景分类再生成 —— 两次 LLM 调用成本翻倍、延迟翻倍，MVP 不必要。弃用。

### D3: 术语常量抽为配置
`TERMS = "汽车外贸术语: 车架号VIN, 排量, 手续齐全, 报关单, 关税, 运输时间, 付款方式(定金/尾款), 质保..."`。作为 `REPLY_SYSTEM` 的一部分拼接，放 `generator.py` 顶部常量，便于后续按需求调整。不落库。
- 备选：术语库表 + 管理 API —— 用户已确认本期不做，避免过度建设。弃用。

### D4: reply_tasks 表新增 3 个可空列，链路透传
`schema.sql` 的 `reply_tasks` 追加 `language TEXT, scenario TEXT, formality TEXT`（可空，缺省走默认）。`create_reply_task`、`_execute_reply_task`、`POST /api/reply`、`_reply_params` 逐层透传。旧任务 `language=NULL` → 生成器默认 zh，无缝兼容。
- 备选：将 language/scenario/formality 编码进现有 `style` 列 —— 破坏现有 style 语义与回归按钮逻辑（`NEXT_STYLE` 轮换）。弃用。

### D5: 前端聊天页新增选择，reply_result 展示维度
`chat_messages.html` 的回复触发区（`reply-{{ m.id }}` div）内、`生成回复`按钮旁，加三个轻量选择器：语种（中文/English/Русский）、场景（自动/询价/砍价/看车/物流/付款/售后）、语气（口语/正式）。`hx-vals` 里带 `language`/`scenario`/`formality`。`reply_result.html` 增加「语种/场景」标签展示。
- 保持 HTMX 模式：选择器为原生 select，值经 `hx-vals` 提交；不引入新前端框架。

## Risks / Trade-offs

- **[自动场景识别不准]** → 前端提供手动覆盖；`scenario=auto` 时提示词允许「通用」兜底，识别偏差不阻塞生成。
- **[俄语输出质量依赖 LLM 能力]** → 提示词明确要求目标语种 + 术语约束；生成器配置常量便于后续细化术语。
- **[参数越多 prompt 越长]** → 三组维度均为短指令字符串，token 增量可忽略。
- **[后端兼容性]** → 新参数全部可选、旧任务缺省走默认，现有测试与调用不受影响。

## Migration Plan

- `reply_tasks` 表 3 列 `ALTER TABLE ... ADD COLUMN`（幂等，先查 `PRAGMA table_info` 存在则跳过）。
- 回滚：删除 3 列 + 移除前端选择器即可，生成器保留新参数（缺省行为与旧版一致）。

## Open Questions

无 —— 语种范围（中/英/俄）、场景方式（自动+手动覆盖）、语气整合（新增维度）、术语库（提示词内置）四项关键决策均已在探索阶段与用户确认。
```

## openspec/changes/multilingual-reply-generation/tasks.md

- Source: openspec/changes/multilingual-reply-generation/tasks.md
- Lines: 1-21
- SHA256: 9f65ff4f1695394307abb5a18be7a06e257e952670ef5d25619030f5907f59c1

```md
# Tasks: 场景化多语种话术生成

## 1. 生成器扩展（generator.py）

- [ ] 1.1 新增 `LANGUAGES`/`SCENARIOS`/`FORMALITY`/`TERMS` 常量映射与 `SCENARIO_LIST`，`generate_reply` 扩展 `language="zh"`/`scenario="auto"`/`formality="casual"` 可选参数，`_build_system` 拼装维度指令（zh+auto+casual 与现状等价，向后兼容）
- [ ] 1.2 `scenario="auto"` 时提示词内置场景识别指令，6 类场景 + 通用兜底；`regenerate_reply` 透传新参数；配套单元测试

## 2. 存储层与任务链路透传

- [ ] 2.1 `reply_tasks` 表新增 `language`/`scenario`/`formality` 可空列（幂等迁移，`PRAGMA table_info` 检查），`create_reply_task` 透传
- [ ] 2.2 `worker._execute_reply_task` 将新参数传入 `generate_reply`；`POST /api/reply` 与 `_reply_params` 解析可选参数；配套任务/接口测试

## 3. 前端展示

- [ ] 3.1 `chat_messages.html` 回复触发区新增语种（中文/English/Русский）、场景（自动/询价/砍价/看车/物流/付款/售后）、语气（口语/正式）选择器，`hx-vals` 携带参数
- [ ] 3.2 `reply_result.html` 展示生成的语种/场景标签；`app.css` 补充选择器样式（如有需要）

## 4. 测试与验证

- [ ] 4.1 全量回归：`compileall` + `pytest` 通过
- [ ] 4.2 手动验证：聊天页选俄语+砍价+正式生成 → 输出俄语正式话术；选自动场景识别；旧调用（无参数）行为不变
```

## openspec/changes/multilingual-reply-generation/specs/multilingual-copy/spec.md

- Source: openspec/changes/multilingual-reply-generation/specs/multilingual-copy/spec.md
- Lines: 1-53
- SHA256: 154f107e1f9dce8d02e1d02ad0b3bbf0f2f9025701bc323784422fefce3ec070

```md
## Purpose

提供场景化多语种话术生成能力：按语种（中/英/俄）、业务场景（询价/砍价/看车/物流/付款/售后）、语气（正式/口语）生成专业外贸回复话术，内置汽车外贸术语约束。

## ADDED Requirements

### Requirement: 多语种话术生成
系统 SHALL 支持按所选语种生成回复话术，语种范围覆盖简体中文、英语、俄语，输出为对应语种的单版本专业话术。

#### Scenario: 生成中文话术
- **WHEN** 用户选择简体中文请求生成回复
- **THEN** 系统 SHALL 生成简体中文回复话术

#### Scenario: 生成英语话术
- **WHEN** 用户选择英语请求生成回复
- **THEN** 系统 SHALL 生成英语回复话术

#### Scenario: 生成俄语话术
- **WHEN** 用户选择俄语请求生成回复
- **THEN** 系统 SHALL 生成俄语回复话术

### Requirement: 业务场景识别与指定
系统 SHALL 支持询价、砍价、看车、物流、付款、售后 6 类业务场景：可自动识别或由用户手动指定；自动识别失败或无匹配场景时按通用处理。

#### Scenario: 自动识别场景
- **WHEN** 用户未指定场景且消息属于询价/砍价/看车/物流/付款/售后之一
- **THEN** 系统 SHALL 识别该场景并按其生成针对性话术

#### Scenario: 手动指定场景
- **WHEN** 用户手动指定某业务场景请求生成回复
- **THEN** 系统 SHALL 按用户指定的场景生成话术，忽略自动识别结果

#### Scenario: 无法识别场景
- **WHEN** 系统无法判断消息所属业务场景
- **THEN** 系统 SHALL 按通用场景生成回复，不报错

### Requirement: 语气风格
系统 SHALL 支持口语与正式两种语气维度，可与既有表达风格组合使用。

#### Scenario: 生成口语话术
- **WHEN** 用户选择口语语气请求生成回复
- **THEN** 系统 SHALL 生成自然口语化表达的话术

#### Scenario: 生成正式话术
- **WHEN** 用户选择正式语气请求生成回复
- **THEN** 系统 SHALL 生成严谨正式书面语气的话术

### Requirement: 汽车外贸术语约束
系统 SHALL 在生成话术时应用内置汽车外贸领域术语（如车架号VIN、报关单、关税、运输时间、付款方式、质保等），确保话术术语准确、口径一致。

#### Scenario: 术语内嵌
- **WHEN** 系统生成任一语种/场景/语气的话术
- **THEN** 话术 SHALL 遵循内置汽车外贸术语表达
```

## openspec/changes/multilingual-reply-generation/specs/reply-assist/spec.md

- Source: openspec/changes/multilingual-reply-generation/specs/reply-assist/spec.md
- Lines: 1-25
- SHA256: cd440335fd5b25638688edddff1906fa6a9785f60bdbc0b02d989761ce88440b

```md
## MODIFIED Requirements

### Requirement: 回复生成异步任务

系统 SHALL 将回复生成改为异步任务：提交后立即返回任务标识，由后台线程执行 RAG + LLM，前端轮询任务状态直至完成。任务可携带语种（language）、场景（scenario）、语气（formality）参数，缺省使用默认值。

#### Scenario: 提交回复任务

- **WHEN** 用户对某条消息请求"生成回复"
- **THEN** 系统 SHALL 创建回复任务并立即返回 `task_id`，不阻塞请求直至 LLM 完成

#### Scenario: 查询任务状态

- **WHEN** 客户端请求 `GET /api/reply/status/{task_id}`
- **THEN** 系统 SHALL 返回任务当前状态（pending/running/done/failed）；done 时包含回复内容与检索来源，failed 时包含可读错误

#### Scenario: 异步失败降级

- **WHEN** 回复任务执行中 LLM 或检索失败
- **THEN** 系统 SHALL 将任务置为 failed 并记录可读错误信息，不抛出 500

#### Scenario: 提交带生成参数的任务

- **WHEN** 用户请求生成回复且携带语种/场景/语气参数
- **THEN** 系统 SHALL 将参数持久化到任务并用于生成；未携带时使用默认值（中文/通用/口语）
```

