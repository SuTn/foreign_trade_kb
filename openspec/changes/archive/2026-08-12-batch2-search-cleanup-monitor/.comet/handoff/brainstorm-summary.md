# Brainstorm Summary

- Change: batch2-search-cleanup-monitor
- Date: 2026-08-12

## 确认的技术方案

### 1. 全局搜索（P2）
- **聚合方式**：`GET /api/search?q=` 返回 JSON 分组（customers/messages/knowledge/profiles），`/search` 页面用 htmx 加载渲染
- 客户：customers 表 LIKE（名称/电话/公司/国家）
- 消息：messages_fts 全文检索，FTS rowid join 回 messages 取 chat_id/body/ts
- 知识库：doc_chunks_fts，join 回 doc_chunks 取 doc_id
- 画像：profiles 表字段 LIKE
- 空查询返回空结果不报错

### 2. 手动数据清理（P2）
- `POST /api/cleanup`，参数 mode: chat|days；chat 需 chat_id，days 需 N
- **按天数边界**：ts < now - N*86400（严格更早）
- 删除 messages 行 + FTS rebuild（复用 delete_document 模式）
- **向量按 chat_id 删除**：按天数先查受影响 chat_id 集合再逐个删；按会话直接删单个
- `VectorStore.delete_message_vectors(chat_id)`（Chroma where 过滤）
- **保留知识库与画像**：profiles/documents 完全不动
- 前端删除前 confirm() 确认

### 3. 采集器异常横幅（P2）
- `base.html` 加全局横幅容器
- `app.js` setInterval：常规 15s，发现 alive=false 切换 5s 快查
- alive=false 显示红色横幅「采集器异常」，恢复隐藏
- 复用既有 `/api/collector/status`（不新增端点）

## 关键取舍与风险

- **[清理不可恢复]** → 前端确认 + 只清聊天不动知识库/画像
- **[FTS rebuild 开销]** → 手动低频操作可接受
- **[Chroma 双进程写锁]** → Web 进程清理与采集器低频写入冲突面小，失败返回可读错误
- **[搜索性能]** → 本地单用户数据量小，LIKE 可接受；FTS 走索引
- **[横幅轮询开销]** → 15s 常规 / 5s 异常自适应，避免全局高轮询

## 测试策略

- 搜索：四源各自命中 + 空查询行为 + FTS join 正确性
- 清理：按会话/按天数删除行数正确、画像与知识库保留断言、向量删除调用
- 横幅：轮询逻辑走读确认（alive=false 显示，恢复隐藏）

## Spec Patch

无（proposal/design/delta spec 已覆盖确认后的方案细节）
