# reply-assist Delta Specification

## ADDED Requirements

### Requirement: 回复生成失败降级
系统 SHALL 在回复生成（LLM 或检索）失败时返回可读的降级结果，而非 500 错误。

#### Scenario: LLM 生成失败
- **WHEN** 回复生成过程中 LLM 调用失败或不可用
- **THEN** 系统 SHALL 返回可读错误信息（含失败原因提示），不返回 500

#### Scenario: 检索失败仍可提示
- **WHEN** 回复生成的检索环节失败
- **THEN** 系统 SHALL 返回可读错误信息并提示检索不可用，不返回 500
