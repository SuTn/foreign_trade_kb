# collector-message-integrity 提案

## Why

当前采集器对 WhatsApp 会话的处理存在两条消息完整性缺口：**群聊完全被跳过**（`idb_walk` 不读 `group-metadata`、chat `kind` 硬编码 `single`，群聊消息永远无法入库，客户被拉进群后该群沟通完全不进知识），以及**复合行正文失真**（引用回复会把被引用文本混入 body、相册/图片消息被整行忽略、`fromMe` 仅靠 tail-in/tail-out 单点判断偶有误判）。这两条缺口导致入库聊天数据不完整、归属不准确，进而影响画像抽取与 RAG 召回的准确性。

## What Changes

- **群聊会话归属**：读取 IDB `group-metadata` store（群名/成员），识别 `@g.us` 会话并落库为 `kind=group`；群聊入站消息解析成员显示名（LID→名字），入库 `sender_jid` 同时记录显示名
- **画像摘要区分发送者**：`build_chat_summary` 在群聊会话按发送者标注（`成员名: 正文`），单聊保持 `我/客户` 不变，使 LLM 画像抽取能区分群内不同成员
- **Web 聊天页显示发送者**：群聊消息气泡展示发送者名，单聊保持现状
- **引用回复净化**：DOM 解析排除引用块（`message-quote`/引用文本），body 只保留本人正文
- **相册/媒体消息**：`image-album` 行不再被忽略，按可用的说明文字/媒体标记入库
- **fromMe 判断增强**：多信号联合判断（tail-in/out + 发送人 JID 与自身账号比对），降低误判

## Capabilities

### New Capabilities

- `whatsapp-sync/group-chat`: 群聊会话识别、`kind=group` 标记、群成员显示名解析与消息发送者归属

### Modified Capabilities

- `whatsapp-sync`: 复合行（引用回复/相册）正文提取与 `fromMe` 判断行为变化；群聊不再被排除，纳入同步范围
- `customer-profile`: 画像摘要构造对群聊会话按发送者标注，抽取输入内容语义变化

## Impact

- `app/collector/idb_walk.py`: 读取 `group-metadata` store，扩展 walk 返回结构（群名/成员映射）
- `app/collector/dom_snapshot.py`: 引用块排除、相册行识别、fromMe 多信号
- `app/collector/scanner.py`: 群聊识别与 `kind` 写入、发送者显示名入库
- `app/collector/merger.py`: 合并逻辑携带发送者显示名
- `app/storage/schema.sql` / `sqlite_store.py` / `interfaces.py`: messages 增加发送者显示名、chats 记录群名；迁移幂等
- `app/profile/service.py`: `build_chat_summary` 群聊发送者标注
- `app/web/templates/chat_messages.html`: 群聊发送者展示
- `tests/`: 新增群聊/引用净化/相册/fromMe 测试，既有测试保持通过
