# web-app Delta Spec

> Delta 变更，叠加于 `openspec/specs/web-app/spec.md`。

## ADDED Requirements

### Requirement: 全局搜索

系统 SHALL 提供全局搜索页与接口，跨客户、消息、知识库、画像四源检索并分组展示。

#### Scenario: 搜索客户

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回名称/电话/公司/国家匹配的客户

#### Scenario: 搜索消息

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回全文匹配的聊天消息

#### Scenario: 搜索知识库

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回匹配的知识库文档片段

#### Scenario: 搜索画像

- **WHEN** 用户输入关键字发起全局搜索
- **THEN** 系统 SHALL 返回字段值匹配的客户画像

### Requirement: 手动数据清理

系统 SHALL 提供手动清理聊天数据的管理入口，支持按会话或按天数删除，且不影响知识库文档与客户画像。

#### Scenario: 按会话清理

- **WHEN** 用户指定某会话（chat_id）请求清理
- **THEN** 系统 SHALL 删除该会话的全部聊天消息及其向量

#### Scenario: 按天数清理

- **WHEN** 用户指定天数 N 请求清理
- **THEN** 系统 SHALL 删除 N 天前的全部聊天消息及其向量

#### Scenario: 保留知识库与画像

- **WHEN** 清理聊天数据
- **THEN** 系统 SHALL 不删除知识库文档，不删除客户画像字段

### Requirement: 采集器异常全局提示

系统 SHALL 在 Web UI 全局区域展示采集器状态，采集器不可达时显示异常横幅。

#### Scenario: 展示采集器异常

- **WHEN** 采集器不在线（is_alive=false）
- **THEN** 系统 SHALL 在页面全局横幅显示「采集器异常」提示

#### Scenario: 定时检查采集状态

- **WHEN** 用户停留在任意页面
- **THEN** 前端 SHALL 定时轮询采集器状态并在异常时更新横幅
