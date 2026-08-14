# Tasks: 场景化多语种话术生成

## 1. 生成器扩展（generator.py）

- [x] 1.1 新增 `LANGUAGES`/`SCENARIOS`/`FORMALITY`/`TERMS` 常量映射与 `SCENARIO_LIST`，`generate_reply` 扩展 `language="zh"`/`scenario="auto"`/`formality="casual"` 可选参数，`_build_system` 拼装维度指令（zh+auto+casual 与现状等价，向后兼容）
- [x] 1.2 `scenario="auto"` 时提示词内置场景识别指令，6 类场景 + 通用兜底；`regenerate_reply` 透传新参数；配套单元测试

## 2. 存储层与任务链路透传

- [x] 2.1 `reply_tasks` 表新增 `language`/`scenario`/`formality` 可空列（幂等迁移，`PRAGMA table_info` 检查），`create_reply_task` 透传
- [x] 2.2 `worker._execute_reply_task` 将新参数传入 `generate_reply`；`POST /api/reply` 与 `_reply_params` 解析可选参数；配套任务/接口测试

## 3. 前端展示

- [x] 3.1 `chat_messages.html` 回复触发区新增语种（中文/English/Русский）、场景（自动/询价/砍价/看车/物流/付款/售后）、语气（口语/正式）选择器，`hx-vals` 携带参数
- [x] 3.2 `reply_result.html` 展示生成的语种/场景标签；`app.css` 补充选择器样式（如有需要）

## 4. 测试与验证

- [x] 4.1 全量回归：`compileall` + `pytest` 通过
- [x] 4.2 手动验证：聊天页选俄语+砍价+正式生成 → 输出俄语正式话术；选自动场景识别；旧调用（无参数）行为不变
