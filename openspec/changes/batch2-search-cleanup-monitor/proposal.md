# batch2-search-cleanup-monitor 提案

## Why

第二批优化聚焦客户与知识管理（优化计划书第 6/7/8 项）：Web 端无全局搜索能力，客户/消息/知识/画像分散在各页无法跨源检索；聊天数据无手动清理入口，历史消息只能累积无法按需清除；采集器异常（CDP 断线/进程停止）仅在首页状态卡可见，其它页面无提示，用户可能误以为数据已同步。

## What Changes

- **全局搜索页（P2）**：新增 `/search` 页 + `GET /api/search?q=`；客户（customers 表名称/电话/公司/国家 LIKE）、消息（messages_fts 全文检索）、知识库（doc_chunks_fts）、画像（profiles 字段匹配）四源检索，结果分组展示
- **手动数据清理（P2）**：新增 `/api/cleanup` 接口 + 管理页按钮；支持按会话（chat_id）或按天数（N 天前）清理；删除 messages + 对应 message_vectors（ChromaDB 按 chat_id 删）；只清理聊天消息，不动知识库文档，保留画像（profiles 不动）
- **采集器异常提示（P2）**：`base.html` 加全局横幅区域；前端定时轮询 `/api/collector/status`（app.js setInterval）；`is_alive=false` 时显示红色横幅「采集器异常」

## Capabilities

### New Capabilities

（无新增 capability，行为契约并入既有 capabilities）

### Modified Capabilities

- `web-app`: 新增全局搜索页与接口；手动数据清理管理入口；采集器异常全局横幅
- `knowledge-base`: 全局搜索覆盖知识库文档片段（doc_chunks_fts）
- `whatsapp-sync`: 手动清理聊天消息（按会话/按天数删除 messages + 向量）

## Impact

- `app/web/routes.py`: `/search` 页、`GET /api/search`、`/api/cleanup`、管理页
- `app/web/templates/search.html`（新）、`cleanup.html` 或管理区片段、`base.html` 横幅
- `app/web/static/js/app.js`: 采集器状态轮询 + 搜索交互
- `app/storage/sqlite_store.py`: 按会话/天数删除 messages（含 FTS 同步）、客户 LIKE 检索、画像字段检索
- `app/storage/chroma_store.py`: 新增 `delete_message_vectors(chat_id)`
- `app/storage/interfaces.py`: `VectorStore` 接口扩展 `delete_message_vectors`
- `app/storage/schema.sql`: 无新增表（清理/搜索复用既有表）
- `tests/`: 搜索各源检索、清理删除逻辑与画像保留断言、横幅轮询
