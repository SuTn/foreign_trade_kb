# collector-message-integrity 任务清单

## 1. 数据层：sender_name 列 + Message 模型

- [x] 1.1 `schema.sql` 为 `messages` 表增加 `sender_name TEXT`；`sqlite_store.py` 幂等迁移块（try/except OperationalError）确保旧库补列
- [x] 1.2 `interfaces.py` `Message` dataclass 增加 `sender_name: str | None`；`sqlite_store._row_to_msg`/`upsert_message` 同步读写新列
- [x] 1.3 补测试：旧库无 sender_name 时迁移不报错；upsert/list 携带 sender_name 往返一致

## 2. IDB 群聊元数据读取

- [x] 2.1 `idb_walk.py` 放开 `group-metadata` store，页面 JS 提取群 JID/群名/参与者（jid+name，防御式读取）；walk 结果新增 `groups: {g_jid: {name, members}}`
- [x] 2.2 补测试：合成 group-metadata 结构 → walk 输出 groups 映射正确；store 缺失/异常时静默降级

## 3. 采集器群聊识别与发送者解析

- [x] 3.1 `scanner._merge_idb_dom`：chat 以 `@g.us` 结尾 → 标记 group；解析入站发送者显示名（contacts → 群成员表 → DOM 显示名 → JID 回退），写入 `sender_name`
- [x] 3.2 `scanner._upsert_one`：`kind` 按群聊/单聊写入 `chats`（群聊用群名作 display_name）；`Message` 构造携带 `sender_name`
- [x] 3.3 补测试：群聊消息入库 kind=group、sender_name 正确；成员名缺失回退 JID；单聊行为不变（既有测试通过）

## 4. 复合行净化（DOM 解析）

- [x] 4.1 `dom_snapshot._parse_row` 跳过引用容器（testid 含 `message-quote`/`quoted-`），body 只含本人正文；testid 漂移时静默回退原行为
- [x] 4.2 `parse_dom_snapshot` 识别媒体行（image-album/image/video/ptt/document/audio/location），说明文字作 body，无正文用媒体标记占位，type 记录媒体类型
- [x] 4.3 fromMe：`_merge_idb_dom` 以 IDB 发送者==自身账号为权威（DOM tail 信号冲突时覆盖）；DOM-only 路径保持 tail-in/out
- [x] 4.4 补测试：引用回复 body 排除引用文本；相册/图片行入库带说明或媒体标记；fromMe 冲突时 IDB 优先

## 5. 画像摘要发送者标注

- [x] 5.1 `profile/service.py build_chat_summary`：按 `chats.kind` 分支——group 用 `{sender_name}: {body}`（我方仍 `我:`），single 保持 `我/客户`
- [x] 5.2 补测试：群聊会话摘要按发送者标注；单聊摘要格式不变

## 6. Web 聊天页发送者展示

- [x] 6.1 `chat_messages.html`：群聊会话消息气泡展示发送者名（单聊保持现状）
- [x] 6.2 补测试：群聊聊天页渲染发送者名

## 7. 收尾验证

- [x] 7.1 全量测试：`pytest -q` 全部通过（原 102 + 新增，预计 110+）
- [x] 7.2 构建检查：`compileall -q app tests` 通过
- [x] 7.3 勾选 tasks.md 全部任务并提交
