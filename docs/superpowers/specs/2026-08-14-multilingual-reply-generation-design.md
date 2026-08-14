---
comet_change: multilingual-reply-generation
role: technical-design
canonical_spec: openspec
---

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
- `SCENARIOS = {"auto": "", "inquiry": "本消息属于询价场景，突出车型信息与价格", "bargain": "本消息属于砍价场景，强调价值与让步空间", "inspection": "本消息属于看车场景，突出车况与看车安排", "logistics": "本消息属于物流场景，说明运输与交期", "payment": "本消息属于付款场景，说明付款方式与安全", "after_sale": "本消息属于售后场景，安抚并说明质保与处理流程"}`
- `SCENARIO_LIST = ["询价", "砍价", "看车", "物流", "付款", "售后"]`
- `FORMALITY = {"casual": "", "formal": "使用正式书面语气，措辞严谨"}`
- `TERMS = "汽车外贸术语: 车架号VIN, 排量, 手续齐全, 报关单, 关税, 运输时间, 付款方式(定金/尾款), 质保, 出港, 到港。话术中使用标准贸易术语, 术语表达要准确。"`

`REPLY_SYSTEM` 追加占位：`{style}{language}{scenario}{formality}{terms}`。`scenario="auto"` 时注入场景识别指令：「先判断本条消息所属业务场景（询价/砍价/看车/物流/付款/售后），按该场景生成；无法判断时按通用处理」，即 LLM 自动识别并生成。手动指定时使用对应 `SCENARIOS` 指令。
- 备选：先单独调用一次 LLM 做场景分类再生成 —— 两次 LLM 调用成本翻倍、延迟翻倍，MVP 不必要。弃用。

### D3: regenerate 保留语种/场景/语气维度
`regenerate_reply` 透传 `language`/`scenario`/`formality`，仅切换 `NEXT_STYLE` 风格获得不同候选。`reply_result.html` 的「重新生成」按钮将三参数回传（`hx-vals`），避免回归时丢失俄语/场景设定。
- 备选：regenerate 只切风格不保留语种 —— 用户重生成俄语话术会退回中文，体验割裂。弃用。

### D4: reply_tasks 表新增 3 个可空列，链路透传
`schema.sql` 的 `reply_tasks` 追加 `language TEXT, scenario TEXT, formality TEXT`（可空，缺省走默认）。`create_reply_task`、`_execute_reply_task`、`POST /api/reply`、`_reply_params` 逐层透传。旧任务 `language=NULL` → 生成器默认 zh，无缝兼容。`GET /api/reply/status/{task_id}` 的 done 结果带 `language`/`scenario`/`formality` 供前端展示。
- 备选：将 language/scenario/formality 编码进现有 `style` 列 —— 破坏现有 style 语义与回归按钮逻辑（`NEXT_STYLE` 轮换）。弃用。

### D5: 前端聊天页新增选择，reply_result 展示维度
`chat_messages.html` 的回复触发区（`reply-{{ m.id }}` div）内、`生成回复`按钮旁，加三个原生 select：语种（中文/English/Русский）、场景（自动/询价/砍价/看车/物流/付款/售后）、语气（口语/正式）。`hx-vals` 里带 `language`/`scenario`/`formality`。`reply_result.html` 增加「语种/场景」标签展示。保持 HTMX 模式，不引入新前端框架。

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
