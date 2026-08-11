# collector-message-integrity 验证报告

- Change: `collector-message-integrity`
- 日期: 2026-08-11
- 验证模式: **full**（19 tasks / 3 delta capabilities / 30 变更文件）

## 规模评估

| 指标 | 值 | 阈值 | 判定 |
|---|---|---|---|
| 任务数 | 19 | 3 | full |
| delta spec 能力数 | 3 | 1 | full |
| 变更文件数 | 30 | 4 | full |

## 验证结论

### Completeness（完整性）

- **tasks.md**: 19/19 全部勾选 ✅
- **Superpowers plan 步骤**: 全部勾选 ✅
- **Spec 覆盖**（3 个 capability 全实现）:
  - whatsapp-sync（MODIFIED）：引用回复正文净化、相册/媒体行入库、fromMe 多信号 IDB 权威 ✅
  - whatsapp-sync/group-chat（NEW）：群聊识别 kind=group、群成员显示名解析、发送者归属入库（不拆成员客户）✅
  - customer-profile（MODIFIED）：画像摘要群聊按发送者标注 ✅

### Correctness（正确性）

- 全量测试 **121 passed**（原 102 + 新增 19，含审查修复后新增）✅
- `compileall -q app tests` 通过 ✅
- 只读约束回归：`test_readonly_constraint` + `test_readonly_cdp` 4 passed ✅
- 关键场景测试覆盖：
  - 群聊入库（kind=group + sender_name + 群名 display_name）
  - 群成员 LID↔手机号双向归一解析
  - 引用回复 body 排除引用文本
  - 媒体行说明文字/媒体标记占位（含自定义前缀安全回退）
  - fromMe 冲突时 IDB 权威
  - 画像摘要群聊发送者标注 / 单聊格式不变
  - Web 聊天页群聊发送者展示 / 单聊保持"客户"
  - sender_name 迁移幂等 + 往返一致 + COALESCE 保留先写名字

### Coherence（一致性）

- 实现符合 `openspec/changes/collector-message-integrity/design.md` D1-D7 决策 ✅
- 实现符合 Design Doc `docs/superpowers/specs/2026-08-11-collector-message-integrity-design.md` ✅
- delta spec 与 design doc 无矛盾（build 阶段无 spec 漂移）✅
- 架构约束保持：采集器只读（ReadOnlyCDP + readonly 事务）、本地优先、防御式降级（group-metadata 缺失/引用 testid 漂移/未知媒体前缀均静默回退）✅

## 代码审查

- **最终轻量审查**（review_mode=standard）：返回 "With fixes" → 2 个 Important：
  1. `MEDIA_MARKERS[media_prefix]` 可配置白名单下 KeyError 崩溃风险 → 修复为 `.get() or prefix` 安全回退 ✅
  2. 群成员表发送者名查找未做 LID 归一 → 修复为 `_jid_forms()` 多形态双查 ✅
  - 附带：sender_name ON CONFLICT 改 COALESCE 保留先写名字 ✅
- **修复后复查**：返回 **Ready to merge: Yes**，无新发现问题，44 定向 + 121 全量通过 ✅

## 分支处理

- 分支 `feature/20260811/collector-message-integrity`（20 提交）→ **本地合并回 main**（--no-ff），合并后测试 121 passed，分支已删除
- main 工作区干净

## 遗留项（非阻塞）

- Minor：未知媒体前缀回退 body 保留尾部 `-`（仅展示层观感）；`lid_by_phone` 仅由 lid_to_phone 反向构建，裸手机号→lid 反向不可达（消息 from 实际只会是 lid 或完整 JID，非现实缺口）

## 结论

无 CRITICAL / WARNING 遗留。**验证通过，可归档。**
