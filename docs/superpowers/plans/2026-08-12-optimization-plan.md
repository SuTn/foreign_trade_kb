# 外贸客户知识库优化计划书

日期：2026-08-12

## 背景

本地外贸客户知识库，通过 CDP 只读同步 WhatsApp 聊天，RAG + Wiki 双索引辅助回复。经头脑风暴，确定 8 项优化，分两批实施。

## 分批实施总览

### 第一批：回复链路核心
| # | 项 | 优先级 | 状态 |
|---|-----|--------|------|
| 1 | 回复异步化 | P0 | 待开发 |
| 2 | CloudLLM 复用客户端 | P0 | 待开发 |
| 3 | 多轮对话 | P1 | 待开发 |
| 4 | 一键复制 | P1 | 待开发 |

### 第二批：客户与知识管理
| # | 项 | 优先级 | 状态 |
|---|-----|--------|------|
| 5 | 自定义字段/标签 | P1 | 已实现，仅确认 |
| 6 | 全局搜索页 | P2 | 待开发 |
| 7 | 手动数据清理 | P2 | 待开发 |
| 8 | 采集器异常提示 | P2 | 待开发 |

---

## 第一批详细方案

### 1. 回复异步化（P0）

**现状**：`/api/reply` 同步阻塞（`app/web/routes.py:307`），LLM 生成期间用户等待。

**方案**：
- 新增 `reply_tasks` 表（SQLite）：`id, customer_id, chat_id, message, style, status(pending/running/done/failed), result, error, created_at`
- `POST /api/reply` 改为：插入任务 → 立即返回 `task_id` → 后台线程执行 RAG + LLM
- 新增 `GET /api/reply/status/{task_id}` 返回任务状态
- 前端 `reply_result.html` 用 HTMX 轮询（`hx-trigger="every 1s"`）直到 done
- 后台线程需独立 SQLite 连接（参考 `_extract_profile_sync`，`app/collector/scanner.py:433`）

### 2. CloudLLM 复用客户端（P0）

**现状**：`app/llm/cloud_llm.py:30` 每次 `generate()` 新建 anthropic/openai client。

**方案**：
- client 懒加载缓存为实例属性（`self._client`）
- 用 `threading.Lock` 保证线程安全
- 配置固定，无需处理 api_base/key 变化

### 3. 多轮对话（P1）

**现状**：`/api/reply` 每次独立，无上下文。

**方案**：
- 新增 `reply_sessions` 表：`id, customer_id, chat_id, created_at, updated_at`
- 新增 `reply_session_messages` 表：`session_id, role(user/assistant), content, ts`
- `POST /api/reply` 支持 `session_id` 参数；无则新建会话
- 生成回复时，把会话历史作为上下文传给 LLM（增强 `REPLY_SYSTEM`）
- 前端聊天页维护当前 `session_id`，连续调整回复

### 4. 一键复制（P1）

**现状**：`app/web/templates/reply_result.html:7` 只有 textarea，需手动选中复制。

**方案**：
- `app/web/static/js/app.js` 加「复制」按钮，用 `navigator.clipboard.writeText()` 复制建议回复
- 用户自行粘贴到 WhatsApp（不做自动跳转/发送）

---

## 第二批详细方案

### 5. 自定义字段/标签（P1）— 已实现

**发现**：`app/web/templates/profile_list.html:16-20` 已有"新增字段"表单，`upsert_profile_field`（`app/storage/sqlite_store.py:60`）支持任意字段名，`source=manual` 保护。**无需改动**，仅确认。

### 6. 全局搜索页（P2）

**方案**：
- 新增 `/search` 页 + `GET /api/search?q=`
- 客户：`customers` 表 LIKE 查询（名称/电话/公司/国家）
- 消息：`messages_fts` 全文检索
- 知识库：`doc_chunks_fts` 全文检索
- 画像：`profiles` 表字段匹配
- 结果分组展示

### 7. 手动数据清理（P2）

**方案**：
- 新增 `/api/cleanup` 接口 + 管理页按钮
- 手动选择范围：**按会话**（指定 chat_id）或**按天数**（N 天前）
- 删除 `messages` + 对应 `message_vectors`（ChromaDB 按 chat_id 删）
- **只清理聊天消息，不动知识库文档**
- **保留画像**：`profiles` 表不动
- 需新增 `VectorStore.delete_message_vectors(chat_id)` 方法（`app/storage/chroma_store.py`）

### 8. 采集器异常提示（P2）

**方案**：
- `app/web/templates/base.html` 加全局横幅区域
- 前端定时轮询 `/api/collector/status`（`app.js` 加 `setInterval`）
- `is_alive` 为 false 时显示红色横幅"采集器异常"

---

## 测试策略

- 每批完成后跑全量测试（现有 160 个），保持回归通过
- 异步化：测试任务队列 + 状态查询 + 后台执行
- 多轮对话：测试会话历史持久化 + 上下文传递
- 清理：测试删除逻辑 + 画像保留断言
- 全局搜索：测试各来源检索

## 涉及文件

- `app/web/routes.py`（异步、搜索、清理、复制接口）
- `app/web/templates/`（reply_result、base、search、cleanup 页）
- `app/web/static/js/app.js`（复制、轮询、横幅）
- `app/llm/cloud_llm.py`（client 复用）
- `app/storage/schema.sql`（reply_tasks、reply_sessions 表）
- `app/storage/sqlite_store.py`（新表操作、清理）
- `app/storage/chroma_store.py`（delete_message_vectors）
- `app/storage/interfaces.py`（VectorStore 接口扩展）
- `app/reply/generator.py`（多轮上下文）