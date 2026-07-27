# ADR 0001: Production Agent Runtime And Double-Layer Memory

- 状态：Accepted
- 日期：2026-07-21
- 决策人：产品负责人、工程实现

## Context

现有运营大脑已经具备动态专家调度、事件流、人工确认和基础 LangGraph 循环，但图未连接生产 checkpointer，执行仍位于 HTTP 请求中，运行节点通过进程级共享变量取得 `AsyncSession`，知识读取存在只按组织过滤的路径。该实现适合演示，不适合多租户并发、进程重启和真实平台动作。

## Decision

1. 使用 PostgreSQL 作为运行事实和 LangGraph checkpoint 存储，采用 `AsyncPostgresSaver`。
2. 使用现有 Redis/ARQ 作为投递与 worker 机制，不引入 Celery。
3. API 只创建幂等运行记录；Agent loop 在 worker 中执行。
4. 使用数据库租约、心跳、错误分类、重试和 dead-letter 保证可恢复执行。
5. 主 Agent 使用开放式但受预算和权限约束的 ReAct 循环；专家不能派发专家。
6. 所有专家、工具和未来 MCP 能力统一通过 Capability Registry 与 ToolExecutor。
7. 采用双层记忆：LangGraph checkpoint + `RuntimeMemory` 承载线程记忆，`KnowledgeSuggestion` 审核后才进入正式知识。
8. 所有知识检索先执行组织、客户、项目和账号范围过滤，再做相关性排序。
9. 新运行时和自动知识提取均由默认关闭的 feature flag 控制，完成灰度后再替换旧路径。

## Superseded Decisions

`docs/superpowers/specs/2026-07-17-smart-agent-loop-interaction-design.md` 中“第一阶段不引入 Redis 后台执行”和手写 resume graph 的约束，在生产运行时范围内被本 ADR 取代。该文档的产品交互、主 Agent 唯一控制权和人工权限门仍然有效。

## Consequences

正向：

- 运行可跨进程恢复，人工暂停不再依赖原请求和原数据库 session。
- 能清晰处理重复投递、重试、取消和真实外部动作。
- 线程记忆与正式知识职责分离，降低错误经验自动污染知识库的风险。

代价：

- 增加 checkpoint 数据、运行租约和保留策略的运维责任。
- Prompt 与能力契约需要版本治理和评测，不再允许随意改内联字符串。
- 上线必须经过双运行时灰度，而不是一次性替换。

## Official References

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://reference.langchain.com/python/langgraph/checkpoints
