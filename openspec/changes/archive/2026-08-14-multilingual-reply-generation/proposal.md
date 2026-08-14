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
