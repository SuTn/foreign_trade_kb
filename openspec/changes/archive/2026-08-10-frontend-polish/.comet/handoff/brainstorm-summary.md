# Brainstorm Summary

- Change: frontend-polish
- Date: 2026-08-10

## 确认的技术方案

- **头像抓取**：随 `scan_all_chats` 逐会话打开顺带抓（conversation-header img src → `page.evaluate` fetch 只读 → base64 → 写 `data/avatars/<customer_id>.<ext>` → 更新 `avatar_path`）；按 customer 归属；无映射/失败/超 2MB 静默跳过 + 首字母占位兜底。备选（按需抓/独立批量任务）否决。
- **头像存储**：`customers.avatar_path` 列（schema.sql 更新 + `_init_schema` try/except ALTER 兼容旧库）+ `avatars_dir` 配置 + `/avatars` 静态挂载；`avatar_path` 存相对 URL。备选独立 avatars 表（支持手动覆盖）因 YAGNI 否决。
- **仪表盘**：新增 `GET /api/stats`（客户统计 + 知识库统计 + 采集状态 + 近期活跃会话，join chats/customer_chat_map）；状态卡沿用 `/api/collector/status` 5s 轮询，stats 一次性渲染。
- **搜索/筛选**：前端实时过滤；`customers()` 路由预聚合画像字段拼 `data-search`；名称/电话/公司/国家/画像字段搜索，国家/公司下拉 AND 叠加。
- **静态资源**：手写单文件 `static/css/app.css` + `static/js/app.js`（浅色简洁、蓝色系、卡片圆角轻阴影）；htmx 固定 2.x 本地化到 `static/js/htmx.min.js`，base.html 统一引用。
- **模板改造**：customers 卡片网格 / chat 大头像+画像卡片 / chat_messages 气泡左右分列（保持 partial swap）/ home 仪表盘四卡 / knowledge* 与 reply_result 统一样式。

## 关键取舍与风险

- [头像选择器版本漂移] WhatsApp 可能用 div background 或 src 懒加载 → 静默跳过 + 占位兜底；探测多选择器。
- [base64 回传体积] >2MB 丢弃，下次扫描重试。
- [htmx 本地化] 固定版本，README 注明来源。
- [搜索数据增长] 当前 95 客户无忧；data-search 属性设计便于日后换后端查询。

## 测试策略

- 采集器头像抓取（mock `page.evaluate` 返回 base64/content-type → 断言落盘 + avatar_path 更新；无映射/失败静默）
- `/api/stats` 聚合正确性（临时库造数据）
- 旧 schema 库 ALTER 迁移幂等
- Web 路由渲染（卡片/仪表盘/详情带 avatar_path 与占位）

## Spec Patch

无额外 Spec Patch —— 三个 delta spec（web-app / whatsapp-sync / customer-profile）已在 open 阶段写入。
