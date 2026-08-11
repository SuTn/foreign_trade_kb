# Brainstorm Summary

- Change: collector-message-integrity
- Date: 2026-08-11

## 确认的技术方案

1. **群聊发送者显示名 → 落库 `sender_name`**：messages 表新增 `sender_name TEXT`（幂等迁移），入库时快照解析结果；画像摘要/Web 直接读取，不实时 join。
2. **群聊客户关联 → 群名匹配（整体归群客户）**：复用现有 `match_customer`（无手机号→显示名=群名匹配），整个群聊归一个客户实体，不拆成员、不跨客户归并。群内成员发言通过 `sender_name` 标注保留在群客户摘要/聊天页中（信息不丢，仅归属在群客户名下）。
3. **引用回复净化 → 彻底排除**：body 只留本人正文，引用文本完全不入库。
4. **媒体消息 → 说明文字 + 媒体标记**：相册/图片/视频/文档行有说明文字采说明文字，无正文用 `[图片]`/`[相册]`/`[文档]` 等占位，type 记录媒体类型。
5. **fromMe 仲裁 → IDB 权威**：IDB 元数据可用时以发送者 JID==自身账号为准，覆盖 DOM tail 信号；DOM-only 路径保持 tail-in/out。
6. **群聊归属**：chat 以 `@g.us` 结尾 → kind=group；chat_id=群 JID，sender_jid=成员 JID，sender_name=成员显示名（contacts→群成员表→DOM→JID 回退）。
7. **IDB 群聊元数据**：放开 group-metadata store，页面 JS 防御式提取群 JID/群名/参与者，walk 结果新增 `groups: {g_jid: {name, members}}`。

## 关键取舍与风险

- [group-metadata 字段结构未实测] → JS 防御式读取 + 单测合成结构覆盖；实测失败仅成员名缺失（回退 JID），不阻塞入库
- [引用块 testid 版本漂移] → 前缀匹配 + 静默降级到收集全部文本，不倒退
- [媒体行识别误伤] → 白名单 + config 可调，未知 testid 保持忽略
- [画像摘要变更影响 LLM 输出] → 单聊路径完全不变，群聊为新场景；单测断言标注格式
- [迁移列新增] → 幂等 + 全量测试回归（102 passed）

## 测试策略

- 单元测试：sender_name 迁移/往返、group-metadata walk、群聊入库（kind/sender_name）、引用净化、媒体行、fromMe 仲裁、摘要标注、Web 渲染
- 回归：全量 pytest（原 102 + 新增，预计 110+）+ compileall
- build 阶段可选实机验证 group-metadata 真实结构

## Spec Patch

无 — open 阶段 delta spec 已覆盖全部验收场景（whatsapp-sync 复合行/fromMe + whatsapp-sync/group-chat 新能力 + customer-profile 摘要标注）
