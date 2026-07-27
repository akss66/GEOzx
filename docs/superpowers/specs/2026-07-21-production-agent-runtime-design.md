# Production Agent Runtime And Double-Layer Memory Design

> 状态：用户已确认
> 日期：2026-07-21
> 范围：运营大脑、专家 Harness、工具与 MCP 边界、持久化运行时、双层记忆、知识治理、可观测性与灰度上线

## 1. 目标

把当前“可演示的动态 Agent Loop”升级为可在真实账号和真实模型上长期运行的生产系统：

- 主 Agent 是唯一控制平面，根据目标、观察、用户决定和工具结果动态选择下一步。
- 专家是隔离执行单元，可被重复调用，但不能越权派发其他专家。
- 长任务脱离 HTTP 请求执行，可暂停、恢复、重试、取消，并在进程重启后继续。
- 所有副作用具备幂等键、权限门、审计记录和失败分类。
- 运行过程自动形成线程记忆，值得复用的经验自动进入待审核知识建议。
- 未经审核的经验不得污染正式知识库，也不得跨客户、项目或账号读取。

## 2. 已确认的双层记忆

### 2.1 第一层：运行时记忆

运行时记忆服务于当前 `thread_id`，自动保存：

- 用户目标、补充要求和更正。
- 主 Agent 的阶段性计划与压缩摘要。
- 用户作出的方案选择、批准、驳回和修改意见。
- 专家结论、工具观察、失败原因和下一步待办。
- 当前客户、项目、平台、账号和权限快照。

线程状态由 LangGraph checkpointer 持久化；业务可读摘要另写入 `runtime_memories`，用于审计、恢复和低成本上下文构建。原始 token 流不作为长期记忆。

### 2.2 第二层：策展知识

任务完成或形成稳定结论后，后台提取器自动生成 `KnowledgeSuggestion`：

1. 从已验收成果、明确用户决定和有证据的运营观察中提取候选知识。
2. 生成稳定指纹并去重。
3. 与同客户/项目知识进行冲突检测，记录 `new / duplicate / conflict / supersedes`。
4. 记录置信度、证据引用、适用范围和可选失效时间。
5. 进入人工审核队列。
6. 仅审核通过后创建或更新正式 `KnowledgeEntry`。

自动提取不等于自动入库。正式知识始终需要人工批准。

## 3. 生产运行时架构

```text
HTTP API
  -> 事务内创建 AgentRun + 幂等记录
  -> ARQ 仅负责投递 run_id
  -> Worker 获取数据库租约
  -> LangGraph + AsyncPostgresSaver
  -> decide -> expert/tool -> observe -> decide
  -> interrupt(permission/decision/user input)
  -> Event/Invocation/ToolCall/RuntimeMemory
  -> WebSocket/SSE 投影给前端
```

### 3.1 事实来源

- `AgentRun`：一次可恢复执行的状态、租约、重试、取消和错误分类。
- LangGraph checkpoint：节点级精确恢复点。
- `BrainTask`：业务任务与当前总体状态。
- `Event`：用户可见时间线和审计投影。
- `AgentInvocation`：专家调用账本。
- `AgentToolCall`：工具输入、权限、执行与输出账本。
- `RuntimeMemory`：线程内压缩记忆。
- `KnowledgeSuggestion / KnowledgeEntry / KnowledgeCitation`：长期知识治理。

Redis/ARQ 不是状态事实来源。队列消息丢失时，数据库中的可恢复 `AgentRun` 可以被扫描并重新投递。

### 3.2 Durable execution

- 生产异步图使用 `AsyncPostgresSaver`，测试使用内存 checkpointer。
- `thread_id` 保持稳定且短于 255 字符。
- 人工暂停使用 LangGraph `interrupt()` 与 `Command(resume=...)`，不再维护两套手写 resume graph。
- interrupt 所在节点重放，因此节点在 interrupt 前的数据库写入和外部动作必须具备幂等键。
- checkpointer 首次启动执行 `setup()`；生产启用严格 msgpack 白名单，具备密钥时加密 checkpoint。
- checkpoint 设保留策略，已关闭任务在审计保留期后可归档或清理。

参考：

- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://reference.langchain.com/python/langgraph/checkpoints

### 3.3 队列、租约与重试

- API 只创建运行记录和投递任务，不在请求内执行模型循环。
- Worker 通过 `lease_owner + lease_expires_at` 原子领取运行。
- 心跳延长租约；失去租约的 worker 不得提交后续副作用。
- 错误分为 `transient / rate_limited / auth / validation / policy / cancelled / permanent`。
- 仅瞬时错误和限流错误自动重试，使用指数退避与抖动。
- 达到最大重试次数进入 dead-letter 状态，保留恢复入口。
- 取消写入数据库，worker 在每个模型、专家和工具边界检查取消状态。

### 3.4 幂等

- 用户消息：`org_id + created_by_id + client_message_id` 唯一。
- 运行：同一幂等消息只产生一个 `AgentRun`。
- 专家调用：`run_id + step_key + attempt` 唯一。
- 工具调用：`run_id + tool_call_key` 唯一。
- 知识建议：`client_id + project_scope + fingerprint` 唯一。
- 外部平台写动作使用平台支持的幂等键；不支持时由本地执行账本阻止重复提交。

## 4. 主 Agent 与专家 Harness

### 4.1 开放式受控 ReAct

主 Agent 每轮只能输出结构化动作：

- `respond`
- `ask_user`
- `dispatch_experts`
- `call_tools`
- `request_decision`
- `request_permission`
- `finish`

运行时完整实现全部动作，不允许未知动作静默降级为完成。主 Agent 可再次调用同一专家，但必须说明新问题或新增证据，并受单专家/总轮次/Token/成本预算约束。

### 4.2 能力注册表

统一能力契约覆盖 `expert / tool / mcp_server / mcp_tool`：

- 输入与输出 schema。
- 客户/项目/账号数据范围。
- 只读、内部写、外部写、破坏性风险等级。
- 自动、确认、禁止三态权限。
- 超时、重试、并发和成本预算。
- 版本与审计标签。

所有工具和未来 MCP 调用必须通过同一个 `ToolExecutor`，不得由模型直接调用网络、数据库或 shell。

### 4.3 Prompt 工程

- 主 Agent、八位专家、总结器、记忆压缩器和知识提取器均使用版本化 Prompt 文件。
- 每次调用写入 `prompt_id + prompt_version + content_hash`。
- 组织补充指令只能追加到受控位置，不能覆盖安全与权限规则。
- 输入包明确标记可信系统上下文和不可信外部内容，防止提示注入。
- 输出先解析，再做 Pydantic schema 校验、业务校验和一次有界修复；不得用宽泛正则从任意文本猜 JSON。

## 5. 知识隔离与检索

- 所有读取必须同时匹配 `org_id + client_id + project scope + status=active`。
- 项目级任务可读取客户级知识和本项目知识；不得读取同客户其他项目知识。
- 账号级观察必须再校验账号属于当前客户/项目。
- 每次注入知识均写 `KnowledgeCitation`。
- 检索采用“范围过滤优先，相关性排序其次”；无客户/项目上下文时不返回知识。
- 第一阶段使用经过范围过滤的关键词/标签检索；语义向量检索作为独立可开关增强，仍必须先做租户过滤。

## 6. 可观测性与安全

值班必须能回答：

1. 某条用户消息当前在哪个 run、哪个节点、为何暂停或失败？
2. 哪个模型、专家或工具导致延迟、重试或成本异常？
3. 是否出现跨租户读取、重复副作用或租约争抢？

每次运行携带 `request_id / run_id / thread_id / task_id`。日志使用结构化事件，不记录 API Key、Token、完整用户正文或完整模型输出。指标至少包含运行成功率、p50/p95/p99 延迟、队列等待时间、重试率、权限暂停时长、模型错误分类、Token 和成本。

模型输出视为不可信输入。数据库写入、外部发布、删除、投流和未来 MCP 高风险工具由代码权限策略约束，system prompt 不是安全边界。

## 7. 灰度与功能开关

- `AGENT_RUNTIME_V2_ENABLED=false` 默认关闭新运行时。
- `AUTO_MEMORY_EXTRACTION_ENABLED=false` 默认关闭自动提取。
- `SEMANTIC_KNOWLEDGE_RETRIEVAL_ENABLED=false` 默认关闭语义检索。
- 先本地完整回归，再在生产仅对白名单管理员和一个指定抖音账号启用。
- 灰度顺序：只读分析 -> 内部成果写入 -> 发布包准备 -> 外部动作人工确认。
- 自动发布、投流、删除和资金动作不因本轮改造自动开放。

## 8. 生产验收门

- 两个并发任务不会串用数据库 session、客户、项目、账号或 thread。
- API/worker 任一进程在任意节点退出后，运行可从最后 checkpoint 恢复。
- 重复消息、重复队列投递和重复审批不会产生重复专家、工具或外部动作。
- 同一专家可在有新问题时被再次调用；无新增信息的循环会被预算守卫终止。
- 全部 Runtime action 有明确实现和测试，不存在静默 fallback。
- 运行时记忆自动形成；知识建议自动生成、去重、冲突标记并等待人工审核。
- 跨客户/项目/账号访问测试全部拒绝。
- Prompt 契约评测、失败注入、取消、重试、dead-letter、恢复和灰度回滚测试通过。
