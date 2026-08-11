# collector-reliability-hardening 验证报告

- Change: collector-reliability-hardening
- 日期: 2026-08-11
- 验证模式: full（28 tasks / 5 delta capabilities / 37 files）
- 分支: feature/20260811/collector-reliability-hardening
- base-ref: ab460c5668c37aec5098ad032ef1b7678ec32e61

## 验证结论

**通过** — 7 项完整验证全部 PASS，无 CRITICAL/WARNING 未决项。

## 逐项检查

| # | 检查项 | 结果 | 证据 |
|---|--------|------|------|
| 1 | tasks.md 全部完成 | ✅ | 28/28 勾选，0 未勾选 |
| 2 | 实现符合 design.md 高层决策 | ✅ | D1-D10 逐项核对（run 自愈/阈值重建/向量键/单例/降级/状态机/use_fp16/backfill/分页/supervisor 均实现） |
| 3 | 实现符合 Design Doc | ✅ | docs/superpowers/specs/2026-08-11-collector-reliability-hardening-design.md 决策全部落地 |
| 4 | 能力规格场景全部通过 | ✅ | 21 个 delta spec 场景均有实现或测试覆盖 |
| 5 | proposal 目标满足 | ✅ | 9 项 Blocker/High bug 全部修复 |
| 6 | delta spec 与 design doc 无矛盾 | ✅ | build 阶段无 spec 增量修改，实现与 design 一致 |
| 7 | 关联 Design Doc 可定位 | ✅ | 文件存在 |

## 测试与构建证据

- `pytest -q`: **159 passed**（121 既有 + 38 新增，无回归）
- `compileall -q app`: **通过**
- 新增测试：tests/collector/test_resilience.py（自愈/向量键/backfill/CDP 分类/重连）、tests/test_main.py（supervisor/退出码）、tests/web/test_routes.py（降级/上传状态机）、tests/rag/test_reranker.py（Ollama 回退）、tests/llm/test_bge_embedding.py（use_fp16）、tests/collector/test_idb_walk.py（分页）、tests/storage/test_sqlite_store.py（attempts 迁移）

## 代码审查

- review_mode: standard
- 最终轻量审查（final review）：**通过**，无 CRITICAL
- 2 个 IMPORTANT 已修复：backfill attempts 列幂等迁移 + CDP 致命关键字加宽（commit ce3d63b）
- 审查评估为可接受的实现者 concern：`_build_store` 的 check_same_thread=False、`_warmup_enabled` 测试隔离、chroma get-ids+delete workaround

## 验收场景对照

1. CDP 抛错后采集器自动重连续跑 → ✅ test_resilience 自愈测试
2. 同会话同日多消息向量独立 → ✅ per-message 键测试
3. Web 进程内复用 store → ✅ test_app 单例测试
4. LLM/嵌入失败可读降级 → ✅ reply/search 降级测试
5. 上传后 status 流转 → ✅ 状态机测试
6. 空/坏文件友好失败 → ✅ 上传测试
7. CPU-only use_fp16 不失败 → ✅ use_fp16 测试

## 已知限制（记录，非阻塞）

- 双进程 Chroma 写锁未彻底解决（超出本 change 范围，降频创建缓解）
- backfill_requests 旧表 attempts 迁移已加（ALTER 幂等）
- CDP 致命关键字宽匹配，误判回退可重试
