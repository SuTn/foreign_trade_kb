# web-app Delta Specification

## ADDED Requirements

### Requirement: 接口错误反馈
系统 SHALL 在回复生成、知识检索或文档上传等接口返回降级错误时，在 Web UI 向用户呈现可读的错误信息，而非通用 500 页面。

#### Scenario: 显示可读错误
- **WHEN** 用户触发回复生成/检索/上传且后端返回降级错误
- **THEN** 系统 SHALL 在页面向用户展示该错误信息与原因提示，页面不崩溃

#### Scenario: 错误后页面可用
- **WHEN** 接口返回降级错误后
- **THEN** 系统 SHALL 保持页面其他功能可用，用户可重试或返回
