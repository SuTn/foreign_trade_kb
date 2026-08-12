# knowledge-base Delta Spec

> Delta 变更，叠加于 `openspec/specs/knowledge-base/spec.md`。

## ADDED Requirements

### Requirement: 全局搜索覆盖知识库

系统 SHALL 在全局搜索中检索知识库文档片段并返回匹配结果。

#### Scenario: 知识库片段命中

- **WHEN** 用户发起全局搜索且关键字命中知识库文档片段
- **THEN** 系统 SHALL 返回命中的文档片段及所属文档
