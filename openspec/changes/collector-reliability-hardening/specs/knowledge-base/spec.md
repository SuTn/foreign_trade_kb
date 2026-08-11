# knowledge-base Delta Specification

## MODIFIED Requirements

### Requirement: 知识管理
系统 SHALL 在 Web UI 提供本地知识管理（上传/列表/删除/检索测试）。

#### Scenario: 上传与列表
- **WHEN** 用户上传文档
- **THEN** 系统 SHALL 解析、切分、向量化入库（RAG 索引）并异步生成 Wiki 页面（若开启），在知识列表展示该文档及其 chunk/Wiki 页面状态

#### Scenario: 检索测试
- **WHEN** 用户在知识管理页输入测试查询
- **THEN** 系统 SHALL 返回检索结果（含来源文档与片段）供验证

#### Scenario: 上传成功状态流转
- **WHEN** 文档解析与索引全部成功
- **THEN** 系统 SHALL 将该文档状态置为成功（done）

#### Scenario: 上传失败状态流转
- **WHEN** 文档解析或索引过程中发生错误
- **THEN** 系统 SHALL 将该文档状态置为失败（failed）并返回可读错误信息，不中断其他文档上传

#### Scenario: 空或损坏文档
- **WHEN** 上传的文件为空、损坏或格式不支持
- **THEN** 系统 SHALL 返回友好错误信息，不产生 500 响应，也不残留处于处理中状态的文档记录

## ADDED Requirements

### Requirement: 检索错误降级
系统 SHALL 在检索（向量化或重排）失败时返回可读的降级结果，而非 500 错误。

#### Scenario: 嵌入失败降级
- **WHEN** 检索过程中嵌入模型不可用或调用失败
- **THEN** 系统 SHALL 返回可读错误信息或降级结果，不返回 500

#### Scenario: 重排失败降级
- **WHEN** 检索过程中重排器不可用或调用失败
- **THEN** 系统 SHALL 以未重排的召回结果返回，并提示重排不可用，不返回 500
