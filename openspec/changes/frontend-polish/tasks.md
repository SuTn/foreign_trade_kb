# frontend-polish Tasks

## 1. 数据层与配置

- [x] 1.1 `config.py` 新增 `avatars_dir` 配置（默认 `data/avatars`）
- [x] 1.2 `schema.sql` 更新 `customers` 定义含 `avatar_path`；`sqlite_store._init_schema` 兼容旧库 try/except `ALTER TABLE`
- [x] 1.3 `tests/conftest.py` `tmp_data` 补 `avatars_dir` monkeypatch
- [x] 1.4 测试：旧 schema 库打开后自动迁移出 `avatar_path` 列（幂等）

## 2. 采集器头像抓取

- [x] 2.1 `scanner.py` 新增头像抓取 helper（读 conversation-header img src → page.evaluate fetch → base64）
- [x] 2.2 `scan_all_chats` 打开会话后调用 helper：解析 customer_id、写 `avatars_dir/<customer_id>.<ext>`、更新 `avatar_path`；失败/无映射/超 2MB 静默跳过
- [x] 2.3 测试：mock `page.evaluate` 返回 base64 + content-type → 断言文件落盘 + `avatar_path` 更新；无客户映射跳过；失败静默

## 3. 仪表盘 API

- [x] 3.1 `routes.py` 新增 `GET /api/stats`（客户/知识库统计 + 采集状态 + 近期活跃会话，join chats/customer_chat_map）
- [x] 3.2 测试：`/api/stats` 聚合正确性（临时库造数据）

## 4. Web 静态挂载

- [x] 4.1 `app.py` 挂载 `/avatars` 静态目录（指向 `avatars_dir` 绝对路径）
- [x] 4.2 `base.html` 引入本地 CSS/JS；下载固定版本 htmx 2.x 的 `htmx.min.js` 到 `static/js/` 并本地引用

## 5. 前端改版

- [x] 5.1 `static/css/app.css`：基础样式（浅色简洁、蓝色系、卡片圆角+轻阴影）+ 头像/气泡/仪表盘组件
- [x] 5.2 `static/js/app.js`：搜索/筛选过滤 + 首字母占位取色（名称 hash → 8 色调色板）
- [x] 5.3 `customers.html`：卡片网格（头像 + 名称/电话/公司/国家）+ 搜索框 + 国家/公司筛选；`customers()` 路由预聚合画像字段拼 `data-search`
- [x] 5.4 `chat.html`：大头像 + 画像卡片分组（保留 HTMX 行内编辑）
- [x] 5.5 `chat_messages.html`：聊天气泡左右分列（保持 partial swap + 分页 + 生成回复）
- [x] 5.6 `home.html`：仪表盘四卡 + 近期活跃会话列表（`/api/stats`）
- [x] 5.7 `knowledge.html`/`knowledge_docs.html`/`knowledge_search.html`/`reply_result.html`：统一卡片/表格样式
- [x] 5.8 测试：Web 路由渲染（卡片/仪表盘/详情带 avatar_path 与占位、`/api/stats` 页面）

## 6. 文档与收尾

- [x] 6.1 README 使用流程补头像/仪表盘说明
- [x] 6.2 全量 pytest + compileall 通过
