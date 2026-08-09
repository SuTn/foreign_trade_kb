# Subagent Progress Checkpoint

Change: whatsapp-customer-kb
review_mode: off
build_mode: subagent-driven-development
isolation: branch

## Current Task
- Plan Task: (all 23 plan tasks complete)
- OpenSpec Task: 7 items remain outside plan-task scope (scope decision pending user)
- Stage: build plan complete; build→verify guard BLOCKED on unchecked tasks.md items
- Base commit (pre-dispatch): n/a
- RED/GREEN evidence: 43/43 tests passing

## Remaining OpenSpec items (NOT plan tasks — scope decision)
Real code gaps:
- 3.7 按需历史回溯 (collector scroll/backfill — not implemented)
- 4.10 Wiki 页面人工编辑 UI (no edit route/template)
- 8.5 采集器状态展示 UI (status API exists, no template display)
- 4.11 Wiki 失败不影响 RAG (upload route does NOT isolate WikiIndex from RagIndex — no try/except)
Validation/e2e (need real WhatsApp + real LLM):
- 9.1 端到端: WhatsApp 同步 → 画像 → 回复
- 9.2 端到端: 知识导入 → RAG → 回复引用产品知识
- 9.3 端到端: 知识导入 → Wiki → 导出 vault → 图谱

## Completed Tasks
- Task 1: 项目骨架与依赖 (commits 9a42d60, 2778b30; checkoff 3f6748f)
- Task 2: 存储层抽象接口 (commit 6845f2c)
- Task 3: SQLite 结构化存储 (commit b993028)
- Task 4: Chroma 向量存储 (commit c8a7022)
- Task 5: 模型层 (commit 84c0806)
- Task 6: ReadOnlyCDP 门面 (commit e5e1f54)
- Task 7: IDB walk + DOM + merger (commit ce10c48)
- Task 8: 采集器双 tick (commit 7878d56)
- Task 9: Playwright 启动 (commit cd5acf7)
- Task 10: docreader vendored (commit efc777e)
- Task 11: 切分 + RAG 索引 (commit 1a89143)
- Task 12: Wiki 索引 (commit 33e299c)
- Task 13: Wiki vault 导出 (commit fd22dc8)
- Task 14: RAG 管线 + 多路召回 (commit 8eeff9f)
- Task 15: Reranker (commit dfe26f1)
- Task 16: 客户匹配 + 画像抽取 (commit 26b7962)
- Task 17: 客户分析 (commit cfb60df)
- Task 18: 辅助回复生成 (commit 8b03268)
- Task 19: FastAPI Web 骨架 (commit 2c12c06; review fix e04ed90; checkoff 2a4004a)
- Task 20: Web 路由 + 模板 (commit e80e3ae; checkoff 4596bfe)
- Task 21: 双进程启动脚本 (commit 4af7f0f; checkoff 193ac51)
- Task 22: 集成测试 + 只读约束 (commit 0bd7855; review fix 7146eaf; checkoff aa78609)
