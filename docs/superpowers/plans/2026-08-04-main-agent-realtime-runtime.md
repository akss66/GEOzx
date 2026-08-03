# Main Agent Realtime Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每个账号和会话提供经过身份校验、可断点续传、不会重复投影的实时 WorkTurn 事件流，彻底消除刷新页面才能看到进度的问题。

**Architecture:** 在现有不可变 `Event` 表上增加组织、账号和 Turn sequence，使用事务内追加事件与 Outbox 语义形成可靠检查点。前端通过带 Bearer 认证的 fetch-SSE 订阅当前会话，按事件 ID 恢复；现有全局匿名 WebSocket 不再承载运营大脑事件。

**Tech Stack:** FastAPI、SQLAlchemy Async、PostgreSQL、Redis、ARQ、StreamingResponse、React、Fetch Streams、Vitest、pytest。

## Global Constraints

- 任何实时连接必须先校验用户、组织、账号和会话权限。
- 前端只接收当前账号和当前会话事件。
- 所有可见进度必须持久化，刷新或断线后可恢复。
- 同一 Turn 的 sequence 严格递增；迟到和重复事件不得回滚 UI。
- Token delta 可以是短暂事件，阶段完成、暂停、失败和交付更新必须持久化。
- SSE 失败时使用按事件 ID 的增量 HTTP 恢复，不刷新整个页面。
- 旧 `/ws/events` 在所有消费者迁移完成前保留，但不得继续广播运营大脑敏感事件。

---

### Task 1: 为持久事件增加账号范围和 Turn 顺序

**Files:**
- Modify: `backend/app/models/orchestration.py`
- Modify: `backend/app/models/conversation.py`
- Create: `backend/migrations/versions/20260804_0100_scope_turn_events.py`
- Modify: `backend/tests/test_conversation_models.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `Event.org_id`、`Event.account_id`、`Event.sequence`、`ConversationTurn.next_event_sequence`。

- [ ] **Step 1: 写模型与迁移失败测试**

```py
assert {"org_id", "account_id", "sequence"} <= event_columns
assert unique_constraint("events", ["turn_id", "sequence"])
assert column("conversation_turns", "next_event_sequence").default == 1
```

- [ ] **Step 2: 运行测试确认字段不存在**

Run: `cd backend && pytest tests/test_conversation_models.py tests/test_migrations.py -q`
Expected: FAIL.

- [ ] **Step 3: 添加可迁移字段与约束**

```py
org_id: Mapped[int | None] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
sequence: Mapped[int | None] = mapped_column(Integer)
```

迁移先添加 nullable 字段并回填可推导的 conversation 事件，再增加部分唯一索引 `WHERE turn_id IS NOT NULL AND sequence IS NOT NULL`。

- [ ] **Step 4: 为 ConversationTurn 添加 sequence 分配游标**

`next_event_sequence` 默认 1，每次追加持久 Turn 事件时使用 `SELECT ... FOR UPDATE` 分配并递增。

- [ ] **Step 5: 运行模型和迁移测试**

Run: `cd backend && pytest tests/test_conversation_models.py tests/test_migrations.py -q`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/models/orchestration.py backend/app/models/conversation.py backend/migrations/versions/20260804_0100_scope_turn_events.py backend/tests/test_conversation_models.py backend/tests/test_migrations.py
git commit -m "feat: scope durable runtime events to account turns"
```

### Task 2: 建立事务化 TurnEvent 服务

**Files:**
- Create: `backend/app/services/turn_events.py`
- Create: `backend/tests/test_turn_events.py`
- Modify: `backend/app/core/events.py`

**Interfaces:**
- Produces: `append_turn_event(...) -> Event`、`list_turn_events(...) -> list[Event]`、`TurnEventPayload`。

- [ ] **Step 1: 写顺序、幂等和范围失败测试**

```py
first = await append_turn_event(session, scope, "step.started", {"step": "read_data"}, "read-data")
again = await append_turn_event(session, scope, "step.started", {"step": "read_data"}, "read-data")
second = await append_turn_event(session, scope, "step.completed", {"step": "read_data"}, "read-data-done")
assert first.id == again.id
assert [first.sequence, second.sequence] == [1, 2]
```

- [ ] **Step 2: 运行测试确认服务不存在**

Run: `cd backend && pytest tests/test_turn_events.py -q`
Expected: FAIL.

- [ ] **Step 3: 实现显式范围对象**

```py
@dataclass(frozen=True)
class TurnEventScope:
    org_id: int
    account_id: int
    thread_id: int
    turn_id: int
    run_id: int | None = None
    skill_run_id: int | None = None
```

`append_turn_event` 锁定 Turn、验证 thread/account/org 一致、分配 sequence、使用 `idempotency_key` 去重并在同一事务写 Event。

- [ ] **Step 4: 限制公开事件类型与 payload**

只允许设计文档中的 `turn.*`、`step.*`、`deliverable.updated`；写入前删除 prompt、原始模型输入、密钥和内部异常栈。

- [ ] **Step 5: 运行事件服务测试**

Run: `cd backend && pytest tests/test_turn_events.py tests/test_turn_provenance.py -q`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/turn_events.py backend/tests/test_turn_events.py backend/app/core/events.py
git commit -m "feat: append idempotent main agent turn events"
```

### Task 3: 将 Turn 执行阶段写入可靠事件流

**Files:**
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/orchestrator/composite_skill_runtime.py`
- Modify: `backend/app/services/runtime_deliverables.py`
- Modify: `backend/tests/test_turn_execution.py`
- Modify: `backend/tests/test_operating_skills.py`

**Interfaces:**
- Consumes: `append_turn_event`。
- Produces: 可靠业务事件和唯一终态事件。

- [ ] **Step 1: 写阶段顺序与终态唯一性失败测试**

断言账号体检 Turn 产生 `turn.received → step.started(read_data) → step.completed(read_data) → deliverable.updated → turn.completed`，且每种终态最多一条。

- [ ] **Step 2: 运行执行测试确认当前只广播短暂事件**

Run: `cd backend && pytest tests/test_turn_execution.py tests/test_operating_skills.py -q`
Expected: FAIL.

- [ ] **Step 3: 在执行边界追加持久事件**

进入阶段前写 `step.started`，阶段事务提交后写 `step.completed`；Deliverable 版本完成后写 `deliverable.updated`；统一 finally 只允许一个终态。

- [ ] **Step 4: 分离 token delta 和业务事件**

`brain.runtime.message_delta` 继续使用 best-effort 广播；任何影响恢复的业务状态必须调用 `append_turn_event`。

- [ ] **Step 5: 运行执行和恢复测试**

Run: `cd backend && pytest tests/test_turn_execution.py tests/test_operating_skills.py tests/test_skill_quality_recovery.py -q`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/turn_execution.py backend/app/orchestrator/skill_runtime.py backend/app/orchestrator/composite_skill_runtime.py backend/app/services/runtime_deliverables.py backend/tests/test_turn_execution.py backend/tests/test_operating_skills.py
git commit -m "feat: persist main agent work turn progress"
```

### Task 4: 提供经过认证的会话 SSE 与增量恢复 API

**Files:**
- Create: `backend/app/api/turn_events.py`
- Create: `backend/app/schemas/turn_events.py`
- Create: `backend/tests/test_turn_events_api.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `GET /conversation-threads/{thread_id}/events?after_id=` 和 `GET /conversation-threads/{thread_id}/event-stream?after_id=`。

- [ ] **Step 1: 写认证、账号隔离和恢复失败测试**

测试无 token 返回 401、其他账号返回 404、`after_id` 只返回更大 ID、SSE 首帧包含当前最新 durable event ID。

- [ ] **Step 2: 运行 API 测试确认路由不存在**

Run: `cd backend && pytest tests/test_turn_events_api.py -q`
Expected: FAIL with 404.

- [ ] **Step 3: 实现增量列表 API**

复用 `get_conversation_thread` 的所有权校验，查询条件必须包含 `org_id + account_id + thread_id + id > after_id`，按 ID 升序返回，单次最多 500 条。

- [ ] **Step 4: 实现 SSE StreamingResponse**

```py
return StreamingResponse(
    stream_authorized_thread_events(scope, after_id),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

每 15 秒发送注释心跳；客户端断开时停止 Redis 订阅；任何事件发送前再次比较 scope。

- [ ] **Step 5: 运行 API 安全测试**

Run: `cd backend && pytest tests/test_turn_events_api.py tests/test_conversation_api.py -q`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/turn_events.py backend/app/schemas/turn_events.py backend/tests/test_turn_events_api.py backend/app/main.py
git commit -m "feat: stream authenticated conversation turn events"
```

### Task 5: 建立带 Bearer 认证的前端 fetch-SSE 客户端

**Files:**
- Create: `frontend/src/hooks/useConversationTurnEvents.ts`
- Create: `frontend/src/hooks/useConversationTurnEvents.test.tsx`
- Modify: `frontend/src/api/brain.ts`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Produces: `useConversationTurnEvents({ threadId, accountId, onEvent, onRecover })`。

- [ ] **Step 1: 写连接、断线和 after_id 恢复失败测试**

模拟流收到 ID 10、11 后断开，断言重连请求包含 `after_id=11`；切换账号后旧 AbortController 已取消且 last ID 清空。

- [ ] **Step 2: 运行 Hook 测试确认不存在**

Run: `cd frontend && npm test -- useConversationTurnEvents.test.tsx`
Expected: FAIL.

- [ ] **Step 3: 使用 fetch ReadableStream 实现认证 SSE**

```ts
await fetch(url, {
  headers: { Authorization: `Bearer ${localStorage.getItem(TOKEN_KEY)}` },
  signal: controller.signal,
});
```

解析 `id:`、`event:`、`data:` 行；使用 500ms 至 8s 指数退避；页面恢复可见时立即重连。

- [ ] **Step 4: 实现增量 HTTP 补偿**

每次重连先调用 `listConversationEvents(threadId, lastEventId)`，投影缺口后再打开流；重复 ID 直接忽略。

- [ ] **Step 5: 运行 Hook、API 与类型测试**

Run: `cd frontend && npm test -- useConversationTurnEvents.test.tsx brain.test.ts && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add frontend/src/hooks/useConversationTurnEvents.ts frontend/src/hooks/useConversationTurnEvents.test.tsx frontend/src/api/brain.ts frontend/src/types.ts
git commit -m "feat: resume authenticated main agent event streams"
```

### Task 6: 接入 BrainHome 并停止依赖全局 WebSocket 刷新

**Files:**
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/hooks/useEventStream.ts`
- Modify: `backend/app/api/ws.py`
- Modify: `backend/tests/test_brain_api.py`

**Interfaces:**
- Consumes: `useConversationTurnEvents`。
- Produces: 当前会话实时投影和旧流隔离策略。

- [ ] **Step 1: 写无需刷新页面的失败测试**

向 Hook 注入 `step.completed` 和 `deliverable.updated`，断言 BrainHome 原位更新且没有调用 `window.location.reload` 或整线程 refetch。

- [ ] **Step 2: 运行 BrainHome 测试确认当前依赖全局 onReconnect refetch**

Run: `cd frontend && npm test -- BrainHome.test.tsx useEventStream.test.tsx`
Expected: FAIL.

- [ ] **Step 3: 当前账号和线程绑定独立事件订阅**

Thread 改变时取消旧流；账号改变时清空旧投影后建立新流；仅在检测到 sequence 缺口时获取一次 Turn 快照。

- [ ] **Step 4: 从全局 WebSocket 过滤运营大脑事件**

`useEventStream` 继续服务内容生产等旧页面；`ws_events` 不再转发包含 `thread_id` 的私有运行事件，避免跨账号广播。

- [ ] **Step 5: 运行前后端回归测试**

Run: `cd frontend && npm test -- BrainHome.test.tsx useEventStream.test.tsx && cd ../backend && pytest tests/test_brain_api.py tests/test_turn_events_api.py -q`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx frontend/src/hooks/useEventStream.ts backend/app/api/ws.py backend/tests/test_brain_api.py
git commit -m "fix: update main agent progress without page refresh"
```

### Task 7: 增加实时可靠性与生产灰度门

**Files:**
- Create: `backend/tests/test_main_agent_realtime_contract.py`
- Create: `frontend/e2e/main-agent-reconnect.spec.ts`
- Modify: `backend/app/services/turn_observability.py`
- Modify: `docs/runbooks/main-agent-v3-rollout.md`

**Interfaces:**
- Produces: 事件延迟、重连恢复、sequence 缺口、重复投影和跨账号拒绝指标。

- [ ] **Step 1: 添加断线重连 E2E**

在运行到第 3 步时模拟网络中断，恢复后断言同一 WorkTurn 从第 3 步继续且前两步只出现一次。

- [ ] **Step 2: 添加跨账号与重复事件契约测试**

断言账号 A 的连接永远收不到账号 B 事件；相同 idempotency key 只产生一行 Event 和一次前端投影。

- [ ] **Step 3: 增加可靠性指标**

记录 `turn_event_publish_ms`、`turn_event_delivery_lag_ms`、`turn_event_sequence_gap_total`、`turn_event_duplicate_total`、`turn_stream_reconnect_total`。

- [ ] **Step 4: 更新灰度 Runbook**

先对一个内部账号开启；24 小时内要求跨账号泄露、重复副作用、重复 Turn 均为 0，事件投递 P95 小于 1 秒，断线恢复 P95 小于 2 秒。

- [ ] **Step 5: 运行质量门**

Run: `cd backend && pytest tests/test_main_agent_realtime_contract.py -q && cd ../frontend && npm run test:e2e -- main-agent-reconnect.spec.ts`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/tests/test_main_agent_realtime_contract.py frontend/e2e/main-agent-reconnect.spec.ts backend/app/services/turn_observability.py docs/runbooks/main-agent-v3-rollout.md
git commit -m "test: enforce main agent realtime reliability"
```
