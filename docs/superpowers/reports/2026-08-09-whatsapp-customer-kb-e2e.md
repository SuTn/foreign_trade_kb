# 端到端联调验证报告 (9.1-9.3)

**日期**: 2026-08-09
**范围**: `whatsapp-customer-kb` change 任务 9.1-9.3
**环境**: 真实 WhatsApp Web 会话 + 本地 SQLite/Chroma + 云端 LLM (anthropic) + 本地 bge-m3 embedding

## 9.1 WhatsApp 同步 → 画像 → 回复生成 全链路 ✅

**验证步骤**:
1. 重启 `python -m app`（Web 进程 + 采集器进程），采集器自动扫描全部会话
2. 通过 `/api/collector/status` 确认 `state=running`、`alive=true`、心跳正常
3. 查询 SQLite：93 客户、45 客户有显示名、241+ 客户生成画像（`profiles` 表含公司/国家/产品兴趣/成交阶段等 7 字段，`source=auto`）
4. 客户详情页 `/customers/{id}` 显示电话、关联会话、画像字段（含来源与时间戳）
5. `POST /customers/{id}/analyze` 返回基于真实聊天的客户分析（概况/兴趣点/活跃度/跟进建议）
6. `POST /api/reply` 针对真实客户询价消息，生成引用产品知识的建议回复

**结果**: 全链路打通。采集器每会话自动触发画像抽取（executor 中跑 LLM，不阻塞事件循环）；回复生成 RAG 多路召回（profile/message_vector/chunk_vector/bm25）+ rerank + LLM 生成。

**修复项**: 采集器长扫描期间心跳保持（`i % 5` 写心跳），画像抽取 SQLite 连接跨线程问题（executor 内开新连接）。

## 9.2 本地知识导入 → RAG 检索 → 回复引用产品知识 ✅

**验证步骤**:
1. 上传 `LED_产品手册.md`（含产品线/价格/认证/质保）到 `/api/knowledge/upload`
2. 验证 RAG 索引：`doc_chunks` 入库，Chroma `query_chunks("LED面板灯价格")` 命中知识 chunk
3. 用真实客户 + 询价消息调 `/api/reply`，确认回复引用上传文档的产品知识

**实际回复要点**（引用自知识库）:
- AL-P40 LED 面板灯 40W，FOB 深圳 $8.50/台
- 1000 台总价 $8,500，最小起订量 500 台/型号
- 交货期：收到定金后 15-20 天
- 付款：30% 定金 + 70% 发货前
- 认证：CE/RoHS/UL，质保 2 年，首年光衰 <5%
- 检索来源：`chunk_vector`, `bm25_chunk`, `bm25_msg`, `message_vector`, `profile`

**修复项**: reranker 与 transformers 5.x 不兼容（FlagEmbedding `compute_score` 内部调用已移除的 `tokenizer.prepare_for_model`）。改为 `BgeReranker._score` 手动用 `tokenizer` + `model` 打分，绕过该 API。

## 9.3 本地知识导入 → Wiki 页面生成 → 导出 Obsidian vault → 图谱查看 ✅

**验证步骤**:
1. 上传同一文档，Wiki 索引异步生成 **16 个实体页**（产品型号 AL-P40/BL-12/CL-100、产品类型、认证 CE/RoHS/UL、术语 FOB/IP67/质保/光衰等）
2. `POST /api/knowledge/export-vault` 导出 16 个 `.md` 文件到 `data/vault/`
3. 每个文件含 YAML frontmatter（`source_docs`/`entity_type`/`updated`）+ `[[wikilinks]]` 互链
4. `.obsidian/app.json` 写入，文件夹可作为 Obsidian vault 打开，graph view 基于 wikilinks 自动建图

**示例** (`data/vault/al-p40.md`):
```
---
source_docs: ['b69133c8-...']
entity_type: 产品型号
updated: 1786283926
---
A系列[[led面板灯]]型号，功率40W，光通量4000流明，色温4000K，[[fob深圳]]价格为8.5美元/台
```

## 结论

三条端到端链路全部验证通过。81 项自动化测试通过。期间修复 1 个环境依赖 bug（reranker/transformers 兼容性），已加测试覆盖。
