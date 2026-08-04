# 主 Agent V3 灰度与回滚手册

本手册是主 Agent V3 唯一发布门禁。它只定义验证、灰度和回滚步骤，不授权生产发布。发布负责人、值守负责人和回滚负责人必须在变更单中实名确认。

## 发布基线

- 当前数据库迁移头：`20260804_0500`，其直接前序为 `20260804_0450`。
- CI 必须通过：后端 Worker 契约、账号写入串行屏障、待处理生命周期、前端单元测试、ESLint、TypeScript、生产构建、包体预算、真实后端 Playwright，以及 PostgreSQL `up → down → up`。
- CI 和灰度不得访问真实抖音发布接口；模型使用确定性测试 Provider，平台数据使用测试适配器。
- 发布包必须可追溯到不可变构建版本和迁移头，不允许使用浮动依赖或临时跳过测试。

## 唯一发布门

### 1. 先部署，所有能力保持关闭

部署应用和加法迁移时保持以下开关关闭：

```text
MAIN_AGENT_V2_ENABLED=false
MAIN_AGENT_TYPED_RUNTIME_ENABLED=false
AGENT_RUNTIME_ASYNC_ENABLED=false
DOUYIN_H5_PUBLISH_ENABLED=false
DOUYIN_POSTING_TASK_ENABLED=false
DOUYIN_DIRECT_PUBLISH_ENABLED=false
```

不得通过重命名开关、旧接口或按用户硬编码绕过门禁。确认回滚包、值班人员和告警路由就绪后再进入下一步。

### 2. 只读健康与迁移核验

先执行只读检查，不创建 Turn、不触发 Tool：

1. `GET /health` 返回 `status=ok`。
2. `GET /health/ready` 返回 `status=ready`，且数据库、Redis 均为 `true`。
3. `alembic current` 必须唯一指向 `20260804_0500`；`alembic heads` 必须只有一个 head。
4. 核对应用版本、Worker 版本和迁移版本来自同一发布包。

以下作用域查询必须返回 `0`：

```sql
SELECT count(*) AS cross_account_rows
FROM conversation_turns ct
JOIN conversation_threads th ON th.id = ct.thread_id
LEFT JOIN agent_runs ar ON ar.turn_id = ct.id
LEFT JOIN skill_runs sr ON sr.run_id = ar.id
WHERE ct.org_id <> th.org_id
   OR ar.org_id <> th.org_id
   OR sr.org_id <> th.org_id;

SELECT count(*) AS cross_account_artifacts
FROM deliverables d
JOIN content_items ci ON ci.id = d.content_item_id
JOIN conversation_threads th ON th.id = d.thread_id
WHERE ci.account_id <> th.account_id;
```

任何非零结果都必须停止发布，不得用数据修补掩盖作用域错误。

### 3. 单一内部组织与账号观察 24 小时

仅为一个内部组织的一个内部抖音账号开启：

```text
MAIN_AGENT_V2_ENABLED=true
MAIN_AGENT_TYPED_RUNTIME_ENABLED=true
AGENT_RUNTIME_ASYNC_ENABLED=true
```

所有抖音自动发布能力继续关闭。完成至少一轮“读取账号数据与对标 → 5 个选题 → 5 条可拍口播稿 → 中途补充要求 → 质量检查 → 7 天手动发布安排 → 记录已发布 → 数据补录待办”的真实操作；期间执行一次断线恢复和一次页面重载。

24 小时门禁要求以下计数全部为零：

- 跨组织或跨账号数据、事件、成果、待办泄漏；
- 重复消息、重复成果版本、重复 Tool 副作用或重复人工动作；
- Run、Turn、SkillRun 与 Task 的终态不一致；
- 超过 15 分钟仍无租约、无重试时间、无 Interrupt 的运行中工作；
- 无法由 Interrupt、拍摄任务、手动排期或数据新鲜度解释的待处理项；
- 自动调用抖音发布接口。

值班人每小时检查一次，并在 24 小时结束时保存下列查询结果：

```sql
SELECT thread_id, client_message_id, count(*)
FROM conversation_turns
WHERE created_at >= now() - interval '24 hours'
GROUP BY thread_id, client_message_id
HAVING count(*) > 1;

SELECT org_id, idempotency_key, count(*)
FROM agent_tool_calls
WHERE created_at >= now() - interval '24 hours'
  AND side_effect_level <> 'read'
GROUP BY org_id, idempotency_key
HAVING count(*) > 1;

SELECT content_item_id, agent_code, type, version, count(*)
FROM deliverables
WHERE created_at >= now() - interval '24 hours'
GROUP BY content_item_id, agent_code, type, version
HAVING count(*) > 1;

SELECT ar.id, ar.status AS run_status, ct.status AS turn_status, sr.status AS skill_status
FROM agent_runs ar
JOIN conversation_turns ct ON ct.id = ar.turn_id
LEFT JOIN skill_runs sr ON sr.run_id = ar.id
WHERE ar.created_at >= now() - interval '24 hours'
  AND ar.status IN ('completed', 'failed', 'blocked', 'stopped')
  AND (ct.status <> ar.status OR sr.status NOT IN (ar.status, 'completed'));

SELECT ar.id
FROM agent_runs ar
LEFT JOIN turn_interrupts ti ON ti.run_id = ar.id AND ti.status = 'pending'
WHERE ar.status IN ('claimed', 'queued', 'running', 'waiting_predecessor')
  AND ar.updated_at < now() - interval '15 minutes'
  AND ar.next_retry_at IS NULL
  AND ti.id IS NULL;
```

### 4. 时延、流式体验与责任看板

发布负责人必须能打开同一套按组织和账号过滤的看板；标签不得包含 prompt、token、原始响应或高基数用户标识。

| 指标 | 24 小时通过线 | 负责人 |
| --- | --- | --- |
| `route_ms` P95 | `< 1 s` | 主 Agent 后端 |
| `first_token_ms` P95 | `< 2 s` | 模型网关 |
| `turn_event_delivery_lag_ms` P95 | `< 1 s` | 实时事件 |
| 断线恢复完成 P95 | `< 2 s` | 前端与实时事件 |
| 完整周运营任务 P95 | `< 120 s`，人工等待时间除外 | Worker |
| 重试、死信、未解释 pending | `0` 或有已批准事件说明 | 值班负责人 |

任一看板不可用、样本无法按灰度账号隔离或指标超过阈值，都视为 hold。

### 5. 分阶段扩大

单账号 24 小时全部通过后，按稳定组织哈希依次扩大到 `5% → 25% → 50% → 100%`。每档至少观察 60 分钟，并重新执行作用域、重复副作用、终态和 pending 核验。

以下情况立即停止扩大：任一门禁非零、连续两个 5 分钟窗口超过时延阈值、Worker 队列持续增长、流式恢复缺失、人工发布项无法消除。跨账号泄漏、重复外部副作用、自动发布调用或无法解释的终态错位必须直接回滚。

## 回滚

1. 先关闭 `MAIN_AGENT_TYPED_RUNTIME_ENABLED`、`AGENT_RUNTIME_ASYNC_ENABLED` 和全部抖音能力开关，再关闭 `MAIN_AGENT_V2_ENABLED`；停止接收新运行。
2. 允许只读查询继续，确认 Worker 不再领取新任务；对已领取任务按持久化 Interrupt/receipt 判定，禁止盲目重启。
3. 回滚应用与 Worker 到上一稳定构建。保留 `events`、AgentRun、SkillRun、ToolCall、执行 attempt、Interrupt、成果版本和人工动作记录。
4. 永远不要自动重放非幂等写入。状态不明确的发布、评论或外部写操作必须人工核验平台结果后再通过公开恢复动作处理。
5. 加法迁移通常不降级。只有迁移本身导致故障、备份已验证、影响范围已评估且数据库负责人批准时，才可按 `20260804_0500 → 20260804_0450` 单步降级；应用/能力回滚优先。
6. 回滚后重复健康检查、作用域查询、终态查询和 pending 核验，并记录构建版本、迁移版本、影响组织/账号和处置结论。

回滚完成不等于事故关闭。只有所有持久化工作都有明确终态或人工责任人、没有待确认的外部副作用、且数据未发生跨账号污染，才能结束事件响应。
