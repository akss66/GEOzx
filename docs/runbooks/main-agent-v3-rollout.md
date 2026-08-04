# 运营大脑 V3 发布与回滚手册

## 实时事件投影发布门禁（2026-08-04）

本节仅准备实时事件投影的发布条件，**不授权生产发布**。负责人为当班发布负责人（应用）与当班值守负责人（数据与安全）；开始灰度前必须在变更单中记录两人的姓名、时间和回滚负责人。

### 开关、范围和到期清理

- 主开关：`MAIN_AGENT_TYPED_RUNTIME_ENABLED`。它是当前类型化主 Agent 和实时投影的 kill switch；保持 `MAIN_AGENT_V2_ENABLED=true`，将本开关设为 `false` 并重启 API，即可停止创建新的类型化实时投影。
- 灰度前先以 `MAIN_AGENT_TYPED_RUNTIME_ENABLED=false` 部署；不得把开关默认打开后再观察。
- 该临时灰度门禁和日志看板的清理/复审日期为 **2026-09-04**。届时由发布负责人决定将其产品化为独立投影开关，或删除临时灰度规则。
- 回滚优先关闭上述类型化实时投影入口；保留已持久化的 `events` 记录，绝不删除或篡改它们。

### 发布顺序

1. 部署应用与加法迁移，但保持 `MAIN_AGENT_TYPED_RUNTIME_ENABLED=false`；先请求 `GET /health/ready`，确认 API、数据库和 Redis 就绪。需要额外验证进程存活时，再补查 `GET /health`。
2. 对一个内部组织、一个内部账号、一个新会话打开开关。完成一条运行中 WorkTurn 的“断开流 → HTTP recovery → 恢复流”烟囱测试。
3. 内部单账号至少观察 24 小时；通过后按稳定组织哈希放量到 5%、25%、50%、100%。每档至少观察 60 分钟，禁止同一线程跨版本。
4. 每次扩大前记录当前基线、百分位和异常计数；没有足够样本时保持当前档，不以“没有报错”代替通过。

### 首次检查与查询

1. 健康检查：`GET /health/ready` 返回成功；如需补充确认进程存活，再检查 `GET /health`。在内部账号打开会话，页面只出现一个 WorkTurn，断流后不刷新页面即可补齐步骤与成果。
2. 在受控日志中按结构化字段查询（时间范围为最近 15 分钟）：

```text
metric_name in (
  "turn_event_publish_ms", "turn_event_delivery_lag_ms",
  "turn_event_sequence_gap_total", "turn_event_duplicate_total",
  "turn_stream_reconnect_total"
)
```

日志字段必须是 `metric_name`、`metric_value` 和 `metric_dimensions`；不得把 prompt、原始事件 payload、token、账号/线程/用户 ID 用作 metric label。

3. 数据库作用域检查（结果必须为 0）：

```sql
SELECT count(*) AS scoped_event_mismatches
FROM events e
JOIN conversation_threads t ON t.id = e.thread_id
WHERE e.org_id <> t.org_id OR e.account_id <> t.account_id;
```

4. 运行事件有序性检查（结果必须为空）：

```sql
SELECT turn_id, sequence, count(*) AS duplicate_sequence
FROM events
WHERE turn_id IS NOT NULL AND sequence IS NOT NULL
GROUP BY turn_id, sequence
HAVING count(*) > 1;
```

### 24 小时通过、暂停与回滚阈值

内部单账号 24 小时通过条件：跨账号泄漏 = 0、重复副作用 = 0、重复 Turn/重复前端投影 = 0；`turn_event_delivery_lag_ms` P95 < 1 秒；从断流到 HTTP recovery 后恢复投影 P95 < 2 秒。

- `turn_event_sequence_gap_total` 的基线应为 0。任一未知 gap 先暂停扩大；同一小时出现 2 次或以上，立即回滚并调查持久化顺序与 recovery cursor。
- `turn_stream_reconnect_total` 是用户网络恢复信号，必须按活跃流连接数归一化后与发布前基线比较。5 分钟窗口超过基线 3 倍且不少于 5 次，暂停扩大；持续 15 分钟或伴随投影遗漏时回滚。
- 任何跨账号泄漏、重复副作用、重复投影、delivery P95 >= 1 秒、recovery P95 >= 2 秒，或无法查询上述指标，均为 hold；跨账号泄漏或重复副作用直接回滚。

### 回滚步骤

1. 将 `MAIN_AGENT_TYPED_RUNTIME_ENABLED=false`，重启 API，并确认新的实时投影不再创建。
2. 保留 durable DB `events`，不自动重放任何副作用 ToolCall；发布、评论、外部写入等任务必须重新由人工审批。
3. 不降级加法数据库迁移。应用回滚到上一稳定版本后，执行健康检查、内部账号只读会话和作用域 SQL。
4. 记录版本、受影响组织/账号/线程/Turn/Run ID 与 correlation ID；不得在变更单复制 prompt、token、payload 或模型响应。

## Typed runtime release gate（2026-08-03）

类型化主 Agent 运行时由两个后端环境变量共同控制：

- `MAIN_AGENT_V2_ENABLED`：启用 V2 对话基础设施。
- `MAIN_AGENT_TYPED_RUNTIME_ENABLED`：启用结构化能力请求、正式 Skill、附件、审批门和类型化会话历史；默认值为 `false`。

只有两个开关都为 `true`，系统才接受新的类型化对话和附件请求。关闭类型化运行时后，接口统一返回 HTTP 503 和稳定错误码 `MAIN_AGENT_TYPED_RUNTIME_DISABLED`；旧 `/brain/messages` 是否可用仍遵循 V2 回滚契约。

### 上线前五场景验收

1. “14天10个选题”必须在 Skill 输入快照中保存 `days=14`、`topic_count=10`。
2. “30秒脚本”必须保存 `duration_seconds=30`，专家默认值不得覆盖用户要求。
3. 发布准备必须生成真实 `publish_package_prepare` ToolCall，并在完成前停留在 `waiting_permission`。
4. 其他用户、账号或会话的附件必须返回 404，且不得向 Run 注入部分附件上下文。
5. 永久删除必须删除消息、附件、Prompt、LLMCall、技术事件和草稿成果，只保留不含内容的审批、发布和成本审计事实。

本版本数据库迁移 head 为 `20260803_0400`。放量前必须在生产克隆上升级至 `head`，并执行后端、前端和迁移全量测试。

### 灰度顺序

1. 先以 `MAIN_AGENT_TYPED_RUNTIME_ENABLED=false` 部署并执行只读健康检查。
2. 仅在内部环境同时打开两个开关并重启 API 进程。
3. 执行五场景矩阵，核对 Run、Turn、SkillRun、ToolCall 的终态一致性。
4. 按稳定的组织/账号批次扩容，同一会话不得跨运行时版本。

持续监控：终态 Run 对应非终态 Turn 必须为 0；等待审批但无审批卡片的 ToolCall 必须为 0；重复消息、ToolCall、成果及跨账号附件泄漏必须为 0；5 分钟内 5xx 或死信超过 1% 时停止放量。

### 一键应用回滚

1. 设置 `MAIN_AGENT_TYPED_RUNTIME_ENABLED=false` 并重启 API，立即阻止新类型化对话和附件请求。
2. 允许在途只读任务收口；暂停审批和外部副作用任务，发布动作未经新一轮人工审批不得重放。
3. 数据库保持 `20260803_0400`，正常应用回滚不降级数据库，因为新增表和字段均为加法变更。
4. 验证旧消息链路、账号选择和只读数据访问，并仅记录组织、账号、会话、Turn、Run ID，不复制 Prompt。

## 目标

运营大脑 V3 为每个 Turn 增加可验证的路由、首字延迟、完成耗时、总耗时、模型调用次数和工具调用次数，并收紧历史接口的公开投影。发布过程不得修改历史 Turn 的指标；历史数据保持 `NULL`，新 Turn 从 `model_call_count = 0`、`tool_call_count = 0` 开始计数。

本手册只定义发布门禁与操作顺序，不授权生产发布。

## 发布前门禁

1. CI 的 `main-agent-v3-directed`、`backend`、`migration-postgres`、`frontend` 和 `main-agent-v3-playwright` 全部通过。
2. 在脱敏的临时 PostgreSQL 克隆上从当前生产版本 `20260730_0500` 升级到 `head`，执行一次降级到 `0500` 再重新升级。
3. 对迁移前已有 Turn 抽样检查：五个 V3 指标必须全部为 `NULL`。
4. 创建新 Turn，确认 `model_call_count` 和 `tool_call_count` 从 0 开始；每次真实 provider 尝试和每次终态工具尝试分别增加 1，终态重放不增加。
5. 检查历史会话 API 与技术日志：不得出现 prompt、provider 原始请求/响应、堆栈、密钥、幂等键或内部错误文本。
6. 执行十场景矩阵：问候、能力说明、只查数据、账号体检、运营复盘、选题策划、发布准备、真实发布审批、专家失败、同会话追问。

## 分阶段放量

### 阶段 0：关闭

- 保持 V3 流量开关为 0%。
- 仅执行迁移和只读校验。
- 确认旧链路读新增 nullable 字段不报错。

### 阶段 1：内部账号

- 只对内部测试组织和专用账号开启。
- 连续观察至少 30 个有效 Turn。
- 人工核对每类场景至少 2 个 Turn 的公开投影和技术日志。

### 阶段 2：5% 灰度

- 按组织稳定哈希放量，禁止按请求随机切换，避免同一会话跨版本。
- 观察至少 60 分钟且至少 100 个 Turn。

### 阶段 3：25% / 50% / 100%

- 每档至少观察 60 分钟。
- 只有上一档所有门槛持续满足才能扩大。
- 任何一档触发停止条件，立即回到上一稳定档。

## 监控指标和阈值

按版本、组织、路由模式和 Skill 分组：

- `route_ms`：显式 Skill p95 不高于 50 ms；确定性问候、身份、能力、数据可用性和常用指标查询 p95 不高于 100 ms；其他路由 5 分钟窗口较基线恶化超过 30% 告警。
- `first_token_ms`：answer 路径 p95 不高于 3 s；连续 10 分钟超过 4 s 停止放量。
- `completion_ms`、`total_ms`：p95 较 V2 基线恶化超过 30% 停止放量。
- `model_call_count`：
  - 确定性问候/能力说明：1；
  - 明确 query/skill：路由模型 0 次；
  - 模糊 answer：路由 1 次、回答 1 次。
- `tool_call_count`：只读查询按实际工具尝试计数；审批前未调用副作用工具时为 0；拒绝、超时和失败尝试也计数，便于发现重试放大。
- Turn 终态一致性：`AgentRun` 已终态而 Turn 仍 `running` 的比例必须为 0。
- 重复回复率、伪造成果率、跨账号成果泄漏率必须为 0。
- 5xx 或 worker dead-letter：任一指标 5 分钟超过 1% 停止放量。
- 指标缺失率：新 Turn 的 `model_call_count` 缺失或终态 Turn 的 `total_ms` 缺失超过 0.5% 告警。

## 告警处理

1. 记录版本、组织、线程、Turn 和 Run ID；不要复制 prompt 或 provider 原始载荷。
2. 判断故障发生在路由、模型、专家、工具、状态收口还是前端投影。
3. 查看公开错误码和 recovery action；敏感内部错误只在受控日志中按 correlation ID 查询。
4. 若只是单 Skill 故障，先关闭该 Skill；若影响 answer/query 或发生隔离泄漏，执行全量回滚。
5. 回滚后重放仅允许幂等、无副作用任务。真实发布类任务必须重新人工确认。

## 应用回滚

1. 将 V3 流量开关降到 0%，停止创建新的 V3 Turn。
2. 等待在途无副作用任务收口；暂停所有等待审批或具有外部副作用的任务。
3. 回滚应用到上一稳定版本。
4. 保留新增列。它们是 nullable 且旧应用可以忽略，正常应用回滚不降数据库。
5. 验证旧版问候、查询、Skill 和会话历史读取。

## 数据库回滚

只有确认旧应用无法与新增列共存时才执行：

1. 先备份数据库并确认没有 V3 应用实例在运行。
2. 导出 V3 指标用于故障分析。
3. 在临时克隆上预演 `alembic downgrade 20260730_0500`。
4. 经数据库负责人批准后在线降级。

数据库降级会永久删除五个 V3 指标列，不应作为常规应用回滚手段。

## 阶段二：运营闭环 Skill 灰度门禁

阶段二只允许通过 Capability Registry 暴露已经真实接入 Runtime 的能力。定位、选题、脚本、视觉、排期、发布准备、真实发布、互动复盘、数据复盘和运营迭代不得再进入旧的通用 `REVIEW_OPTIMIZATION` 任务图。

放量前逐项核对：

1. 十场景生命周期矩阵通过，并核对每个 Skill 的专家、只读/副作用 Tool、质量门、审批策略和成果类型。
2. 视觉、排期和运营迭代缺少已确认上游成果时，只追问最小成果 ID，不自动生成替代结论。
3. 发布准备停留在人工审批；真实发布只接受已审批且版本未变化的发布包。
4. 真实发布仅对已连接官方抖音通道的账号显示为可执行。缺少 Adapter 或连接时返回 `needs_connection/blocked`，平台回调前只允许显示 `handoff_ready` 或 `waiting_platform_confirmation`，不得显示“已发布”。
5. 互动复盘只有聚合评论数而没有真实评论样本时返回 `needs_input`，不得编造情绪和高频问题，也不得自动回复外部评论。
6. 所有输入成果、聊天、附件、ToolCall、SkillRun 和最终成果必须同时满足组织、用户、账号、会话四层作用域。

灰度顺序：先开启只读的定位、选题、脚本、视觉、排期、互动和复盘；再开启发布准备；最后仅对内部已连接账号开启真实发布。任何 Skill 出现跨账号读取、虚假专家执行、虚假发布回执、重复副作用或终态不一致时，立即在 Registry 中关闭该 Skill，不扩大灰度。

阶段二回滚不降级数据库。先从 Capability Registry 隐藏对应 Skill，再停止新的副作用 ToolCall；已完成的只读成果继续可见。等待审批或平台确认的发布任务必须人工核对，不得自动重放。确需全量回滚时关闭 `MAIN_AGENT_TYPED_RUNTIME_ENABLED` 并按前述应用回滚流程恢复上一稳定版本。

## 发布完成标准

- 100% 流量连续 24 小时没有 P0/P1。
- 十场景矩阵生产只读验证通过。
- 无跨账号/跨用户会话或成果泄漏。
- 延迟、失败率和模型调用预算均在阈值内。
- 运维、产品和研发共同确认后才可关闭发布观察。

## 阶段三：性能与状态一致性门禁

阶段三先在 CI 中执行无外网的性能契约，再进入内部账号 24 小时观察。CI 必须同时通过后端主 Agent 定向测试、前端主 Agent 组件契约、TypeScript、生产构建和首屏包体预算。首屏不得预加载图表 vendor，初始 JavaScript 不得超过 900 KB，`BrainHome` 懒加载块不得超过 180 KB。

本地性能契约采用进程内确定性路由和本地数据库只读 Tool，不访问模型或外部平台：显式 Skill 路由 p95 < 50 ms，确定性 answer/query 路由 p95 < 100 ms，`account.data_context` p95 < 2 s。生产环境以数据库指标为准，不能用 CI 机器耗时替代真实观测。

### 24 小时观测 SQL（PostgreSQL）

路由、首字和总耗时按模式查看：

```sql
SELECT
  COALESCE(lower(intent->>'mode'), 'unknown') AS route_mode,
  count(*) AS turns,
  round(percentile_cont(0.95) WITHIN GROUP (ORDER BY route_ms)::numeric, 1) AS route_p95_ms,
  round((percentile_cont(0.95) WITHIN GROUP (ORDER BY first_token_ms)
    FILTER (WHERE first_token_ms IS NOT NULL))::numeric, 1) AS first_token_p95_ms,
  round((percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms)
    FILTER (WHERE total_ms IS NOT NULL))::numeric, 1) AS total_p95_ms,
  round(avg(model_call_count)::numeric, 2) AS avg_model_calls,
  round(avg(tool_call_count)::numeric, 2) AS avg_tool_calls
FROM conversation_turns
WHERE created_at >= now() - interval '24 hours'
GROUP BY 1
ORDER BY 1;
```

只读查询总耗时预算：

```sql
SELECT
  count(*) AS query_turns,
  round(percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms)::numeric, 1) AS query_p95_ms
FROM conversation_turns
WHERE created_at >= now() - interval '24 hours'
  AND lower(intent->>'mode') = 'query'
  AND status = 'completed';
```

终态 Run 与 Turn 不一致必须为 0：

```sql
SELECT count(*) AS terminal_mismatches
FROM agent_runs r
JOIN conversation_turns t ON t.id = r.turn_id
WHERE r.created_at >= now() - interval '24 hours'
  AND r.status IN ('completed','blocked','failed','dead_letter','cancelled','stopped')
  AND t.status IN ('queued','running','retry_wait','waiting_permission','waiting_decision');
```

客户端消息幂等冲突必须为 0：

```sql
SELECT thread_id, client_message_id, count(*) AS duplicate_count
FROM conversation_turns
WHERE created_at >= now() - interval '24 hours'
  AND client_message_id IS NOT NULL
GROUP BY thread_id, client_message_id
HAVING count(*) > 1;
```

Skill 阶段更新不能静默停滞。以下结果必须为空；出现记录表示运行中 Skill 超过 30 秒没有新的运行事件：

```sql
SELECT sr.id AS skill_run_id, sr.skill_code, sr.status,
       max(e.created_at) AS last_event_at
FROM skill_runs sr
LEFT JOIN events e ON e.skill_run_id = sr.id
WHERE sr.created_at >= now() - interval '24 hours'
  AND sr.status IN ('running','retry_wait','waiting_permission','needs_review')
GROUP BY sr.id, sr.skill_code, sr.status
HAVING COALESCE(max(e.created_at), min(sr.created_at)) < now() - interval '30 seconds';
```

失败和死信按路由/Skill 汇总，内部观察期要求为 0：

```sql
SELECT COALESCE(sr.skill_code, lower(t.intent->>'mode'), 'unknown') AS path,
       count(*) AS failed_runs
FROM agent_runs r
LEFT JOIN conversation_turns t ON t.id = r.turn_id
LEFT JOIN skill_runs sr ON sr.run_id = r.id
WHERE r.created_at >= now() - interval '24 hours'
  AND r.status IN ('failed','dead_letter')
GROUP BY 1
ORDER BY 2 DESC;
```

### 扩容与回滚判定

内部账号连续 24 小时必须满足：错误/死信、重复消息、终态不一致、跨账号泄漏、伪造成果均为 0；answer 首字 p95 < 3 s；query 总耗时 p95 < 2 s；显式 Skill 路由 p95 < 50 ms；确定性路由 p95 < 100 ms；运行中 Skill 每 30 秒内至少有一次阶段更新。任何一项不满足都不得扩大流量。

回滚时先关闭 `MAIN_AGENT_TYPED_RUNTIME_ENABLED`，并从 Capability Registry 隐藏故障 Skill；不降级数据库、不自动重放副作用 ToolCall。只有重新完成无外网性能契约、十场景矩阵和新的 24 小时内部观察后，才可重新放量。
