# batch2-search-cleanup-monitor 技术设计

> 需求与范围见 proposal.md，行为契约见 specs/。本文记录高层架构决策；深度技术设计见 comet-design 阶段的 Design Doc。

## Context

Web 端无全局搜索（客户/消息/知识/画像分散）；聊天数据无清理入口；采集器异常仅首页可见。批二三项优化均落在 Web 层（routes + 模板 + store），无新外部依赖。

## Goals / Non-Goals

**Goals:**
- 全局搜索页：跨客户/消息/知识库/画像四源检索分组展示
- 手动清理：按会话或按天数删除聊天消息 + 向量，保留知识库与画像
- 采集器异常全局横幅：前端定时轮询，离线红色提示

**Non-Goals:**
- 不引入全文检索引擎（复用既有 FTS5）
- 不做清理操作的可恢复（删除即不可恢复，需前端确认）
- 不做自动清理（仅手动触发）
- 不改采集器进程行为（异常检测复用既有 status 文件）

## Decisions

### D1: 全局搜索复用既有检索原语
客户用 customers 表 LIKE（名称/电话/公司/国家）；消息用 `messages_fts`（复用 `search_fts`，FTS 行 rowid join 回 messages 取 chat_id/body）；知识库用 `doc_chunks_fts`；画像用 profiles 表字段 LIKE。四源各自查询、统一返回分组 JSON，前端分组渲染。搜索为空返回空结果不报错。

### D2: 清理 = messages + message_vectors，不动知识库/画像
按 chat_id 或 ts<now-N 天删除 messages 行；FTS 外部内容表删行后 rebuild（复用 delete_document 的 FTS 处理模式）。向量按 metadata.chat_id 删除：Chroma `where={"chat_id": ...}` 支持，新增 `VectorStore.delete_message_vectors(chat_id)`。按天数清理时按时间范围收集 chat_id 集合分别删。**profiles 与 documents 完全不动**。

### D3: 采集器异常横幅复用既有 status 端点
`/api/collector/status` 已存在（返回 `{status, alive}`）。`base.html` 加全局横幅容器，`app.js` 用 `setInterval` 定时轮询（如 15s），`alive=false` 时显示红色横幅，恢复后隐藏。不新增端点。

### D4: 清理接口需前端确认
`/api/cleanup` 为 POST + 参数（mode: chat|days, chat_id?, days?），删除前由前端弹确认（普通 `<script>confirm()` 即可）。返回删除行数与受影响会话数。

## Risks / Trade-offs

- **[清理不可恢复]** → 删除前前端确认 + 只清聊天不动知识库/画像；本地工具可接受。
- **[FTS rebuild 开销]** → 清理为低频手动操作，rebuild 成本可接受；复用既有 delete_document 模式。
- **[Chroma 双进程写锁]** → 清理走 Web 进程，与采集器低频写入冲突面小；失败时返回可读错误。
- **[搜索性能]** → LIKE 查询无索引，本地数据量小（单用户）可接受；FTS 走索引。

## Migration Plan

1. 无 schema 变更（复用既有表与 FTS）
2. 新功能走 feature 分支，build 阶段逐任务提交
3. 回归：`pytest -q` 全量；`compileall -q app`
4. 回滚：均为增量改动；清理删除不可回滚（操作层面），功能代码可回退
