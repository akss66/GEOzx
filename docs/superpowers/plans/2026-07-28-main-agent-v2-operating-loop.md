# Main Agent V2 Operating Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Main Agent that automatically routes every account-operations conversation into a direct answer, query, Skill, durable task, or approved action, with turn-anchored artifacts, an account artifact center, and a discoverable “＋” capability launcher.

**Architecture:** Separate long-lived `ConversationThread`, immutable `ConversationTurn`, idempotent `AgentRun`, versioned `SkillRun`, and durable `OperationTask` ownership. Keep `BrainTask` as the compatibility carrier for durable work while new Turn-level APIs and projections are introduced behind additive contracts. Skills orchestrate isolated experts and tools through the existing AgentKernel, permission gates, and ledgers.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic, PostgreSQL, LangGraph, ARQ, React 18, TypeScript 5.6, TanStack Query, Zustand, Ant Design, Vitest, Playwright.

## Global Constraints

- Preserve existing `BrainTask`, `/brain/tasks/*`, AgentKernel, LangGraph, ARQ, permission gates, audit ledgers, and real platform integrations during migration.
- Every new user message owns one `ConversationTurn` and one idempotent `AgentRun`.
- `account_id` is required for a formal account-operations thread; `client_id` and `project_id` remain optional.
- Ordinary conversation and deterministic data queries must not create an `OperationTask` or enter the full strategy graph.
- Every formal artifact must retain `thread_id`, `turn_id`, `run_id`, optional `skill_run_id`, and optional `task_id`.
- Specialists remain isolated and cannot dispatch specialists or bypass Tool permissions.
- Publishing, deletion, paid promotion, and authorization changes always pause for user approval.
- Frontend user copy is Chinese business language; raw JSON, Schema keys, and technical logs are not default content.
- Migrations must upgrade and downgrade cleanly and preserve legacy rows.
- Do not deploy production as part of this plan.
- The current worktree already contains an in-progress, uncommitted Thread/Turn foundation. Tasks 1 and 2 own those exact files; do not reset or recreate that work.

## File and Responsibility Map

```text
backend/app/models/conversation.py
  Durable conversation and immutable turn ownership.

backend/app/models/skill_runtime.py
  Versioned SkillRun execution record and status.

backend/app/schemas/conversation.py
  Turn submission, route decision, projection, and pagination contracts.

backend/app/schemas/skills.py
  Skill catalog and SkillRun public contracts.

backend/app/orchestrator/skills/registry.py
  Versioned business Skill definitions and user-facing catalog.

backend/app/orchestrator/capability_router.py
  Deterministic overrides plus LLM intent-to-execution-mode routing.

backend/app/orchestrator/skill_runtime.py
  Skill execution, expert/tool dispatch, quality gate, and artifact handoff.

backend/app/orchestrator/skills/account_inspection.py
  First composite Skill: account data, specialists, critic, report.

backend/app/services/conversations.py
  Authorized Thread/Turn persistence and idempotency.

backend/app/services/turn_execution.py
  Executes a routed Turn without conflating it with a BrainTask.

backend/app/services/artifacts.py
  Artifact provenance, versioning, acceptance, and account-scoped listing.

backend/app/api/conversations.py
  Additive Thread/Turn endpoints.

backend/app/api/skills.py
  User-visible Skill catalog endpoint.

backend/app/api/artifacts.py
  Account artifact center and artifact action endpoints.

frontend/src/types.ts
  Thread, Turn, Skill, Artifact, and projection discriminated unions.

frontend/src/api/brain.ts
  Typed conversation, Skill catalog, and artifact API client.

frontend/src/stores/brainConversation.ts
  Active Thread per account, replacing active BrainTask as conversation identity.

frontend/src/components/brain/TurnStream.tsx
  Renders each Turn with its own messages, experts, approvals, and artifacts.

frontend/src/components/brain/ArtifactCard.tsx
  Business-first artifact summary, actions, evidence, and version state.

frontend/src/components/brain/CapabilityLauncher.tsx
  “＋” capability, context, and expert launcher.

frontend/src/components/brain/ArtifactCenter.tsx
  Account-scoped artifact list and filters inside the operations brain.
```

---

## Phase 1: Conversation Ownership Foundation

### Task 1: Finish ConversationThread and ConversationTurn ORM ownership

**Files:**
- Modify: `backend/app/models/conversation.py`
- Modify: `backend/app/models/agent_runtime.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_conversation_models.py`

**Interfaces:**
- Produces: `ConversationThread`, `ConversationTurn`, and nullable `AgentRun.thread_id` / `turn_id`.
- Invariant: `(thread_id, client_message_id)` is unique when `client_message_id` is present.
- Invariant: new formal Threads require one explicit `account_id`.

- [ ] **Step 1: Extend the existing model test with idempotency and run ownership**

```python
@pytest.mark.asyncio
async def test_turn_client_message_is_unique_inside_thread(session, admin):
    account = models.Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="主账号",
    )
    session.add(account)
    await session.flush()
    thread = models.ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="主 Agent 对话",
    )
    session.add_all(
        [
            thread,
            models.ConversationTurn(
                thread=thread,
                org_id=admin.org_id,
                created_by_id=admin.id,
                client_message_id="same-message",
                user_input="第一次",
            ),
        ]
    )
    await session.commit()
    session.add(
        models.ConversationTurn(
            thread_id=thread.id,
            org_id=admin.org_id,
            created_by_id=admin.id,
            client_message_id="same-message",
            user_input="重复提交",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
```

- [ ] **Step 2: Run the focused model tests**

Run: `cd backend && uv run pytest tests/test_conversation_models.py -v`

Expected before the constraint is added: FAIL because duplicate client message IDs are accepted. Existing independent-turn and legacy-run tests must continue to pass.

- [ ] **Step 3: Add the model-level unique constraint**

```python
class ConversationTurn(Base, TimestampMixin):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "thread_id",
            "client_message_id",
            name="uq_conversation_turn_thread_client_message",
        ),
    )
```

Keep `client_message_id` nullable for legacy imports. Keep `AgentRun.task_id`, `thread_id`, and `turn_id` nullable so a legacy task can still run.

- [ ] **Step 4: Run focused tests and Ruff**

Run: `cd backend && uv run pytest tests/test_conversation_models.py -v`

Expected: all conversation model tests PASS.

Run: `cd backend && uv run ruff check app/models/conversation.py app/models/agent_runtime.py tests/test_conversation_models.py`

Expected: PASS.

- [ ] **Step 5: Commit only the ORM slice**

```bash
git add backend/app/models/conversation.py backend/app/models/agent_runtime.py backend/app/models/__init__.py backend/tests/test_conversation_models.py
git commit -m "feat: add durable conversation ownership"
```

### Task 2: Finish the reversible conversation foundation migration

**Files:**
- Modify: `backend/migrations/versions/20260728_0100_conversation_foundation.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: ORM columns and constraint names from Task 1.
- Produces: migration head `20260728_0100` with legacy `AgentRun` rows preserved.

- [ ] **Step 1: Add the unique constraint assertion to the migration test**

```python
turn_uniques = {
    tuple(item["column_names"])
    for item in inspector.get_unique_constraints("conversation_turns")
}
assert ("thread_id", "client_message_id") in turn_uniques
```

- [ ] **Step 2: Run the migration tests**

Run: `cd backend && uv run pytest tests/test_migrations.py -k conversation_foundation -v`

Expected before the migration constraint is added: FAIL on the unique-constraint assertion.

- [ ] **Step 3: Add the matching Alembic constraint**

Inside `op.create_table("conversation_turns", ...)` add:

```python
sa.UniqueConstraint(
    "thread_id",
    "client_message_id",
    name="uq_conversation_turn_thread_client_message",
),
```

Do not backfill legacy AgentRuns. Their `thread_id` and `turn_id` remain `NULL`.

- [ ] **Step 4: Verify upgrade, downgrade, and re-upgrade**

Run: `cd backend && uv run pytest tests/test_migrations.py -k conversation_foundation -v`

Expected: PASS, including the legacy row assertion after downgrade and re-upgrade.

Run: `cd backend && uv run pytest tests/test_conversation_models.py tests/test_migrations.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the migration slice**

```bash
git add backend/migrations/versions/20260728_0100_conversation_foundation.py backend/tests/test_migrations.py
git commit -m "feat: migrate conversation threads and turns"
```

### Checkpoint 1: Conversation foundation

- [ ] `ConversationThread` and `ConversationTurn` tests pass.
- [ ] Migration upgrades, downgrades, and preserves legacy AgentRuns.
- [ ] `git status --short` contains no leftover files owned by Tasks 1 and 2.
- [ ] Existing BrainTask runtime tests still pass: `cd backend && uv run pytest tests/test_agent_runs.py tests/test_brain_api.py -q`.

---

## Phase 2: Skill Catalog and Automatic Routing

### Task 3: Add a versioned business Skill Registry

**Files:**
- Create: `backend/app/schemas/skills.py`
- Create: `backend/app/orchestrator/skills/__init__.py`
- Create: `backend/app/orchestrator/skills/registry.py`
- Test: `backend/tests/test_skill_registry.py`

**Interfaces:**
- Produces: `SkillDefinition`, `SkillCatalogItem`, `SkillRegistry.get(code)`, and `SkillRegistry.list_for(platform)`.
- Skill codes are stable snake_case strings; versions are positive integers.

- [ ] **Step 1: Write registry contract tests**

```python
class AccountInspectionInput(BaseModel):
    days: int = Field(default=30, ge=1, le=90)


class AccountInspectionReport(BaseModel):
    summary: str


def test_registry_returns_only_platform_compatible_business_skills():
    registry = SkillRegistry(
        [
            SkillDefinition(
                code="account_inspection",
                version=1,
                name="一键账号体检",
                description="诊断当前账号",
                supported_platforms=frozenset({"douyin"}),
                input_model=AccountInspectionInput,
                output_model=AccountInspectionReport,
                expert_codes=("01-positioning", "02-content-director", "06-operator"),
                tool_codes=("account.profile", "account.data_context"),
                risk_level="low",
                approval_policy="none",
                artifact_type="account_inspection_report",
            )
        ]
    )
    assert [item.code for item in registry.list_for("douyin")] == ["account_inspection"]
    assert registry.list_for("xiaohongshu") == []
```

- [ ] **Step 2: Run the registry test**

Run: `cd backend && uv run pytest tests/test_skill_registry.py -v`

Expected: FAIL because the registry modules do not exist.

- [ ] **Step 3: Implement immutable Skill definitions and duplicate protection**

```python
@dataclass(frozen=True)
class SkillDefinition:
    code: str
    version: int
    name: str
    description: str
    supported_platforms: frozenset[str]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    expert_codes: tuple[str, ...]
    tool_codes: tuple[str, ...]
    risk_level: Literal["low", "medium", "high"]
    approval_policy: Literal["none", "before_tools", "before_finish"]
    artifact_type: str | None


class SkillRegistry:
    def __init__(self, definitions: Iterable[SkillDefinition]) -> None:
        self._definitions = {}
        for definition in definitions:
            key = (definition.code, definition.version)
            if key in self._definitions:
                raise ValueError(f"duplicate Skill definition: {key}")
            self._definitions[key] = definition

    def get(self, code: str, version: int | None = None) -> SkillDefinition:
        matches = [
            item for (item_code, _), item in self._definitions.items()
            if item_code == code
        ]
        if version is not None:
            matches = [item for item in matches if item.version == version]
        if not matches:
            raise KeyError(code)
        return max(matches, key=lambda item: item.version)

    def list_for(self, platform: str) -> list[SkillDefinition]:
        latest = {item.code: self.get(item.code) for item in self._definitions.values()}
        return sorted(
            [item for item in latest.values() if platform in item.supported_platforms],
            key=lambda item: item.name,
        )
```

- [ ] **Step 4: Verify the registry**

Run: `cd backend && uv run pytest tests/test_skill_registry.py -v`

Expected: PASS for lookup, latest-version selection, platform filtering, and duplicate rejection.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/skills.py backend/app/orchestrator/skills backend/tests/test_skill_registry.py
git commit -m "feat: add versioned business skill registry"
```

### Task 4: Define deterministic Turn execution modes

**Files:**
- Create: `backend/app/schemas/conversation.py`
- Create: `backend/app/orchestrator/capability_router.py`
- Test: `backend/tests/test_capability_router.py`

**Interfaces:**
- Consumes: `SkillRegistry.get`.
- Produces: `TurnExecutionMode` and `TurnRouteDecision`.
- Explicit `requested_skill_code` wins over LLM classification after registry and platform validation.

- [ ] **Step 1: Write deterministic routing tests**

```python
def test_explicit_skill_launch_is_not_reclassified(registry):
    decision = route_explicit_request(
        requested_skill_code="account_inspection",
        platform="douyin",
        registry=registry,
        has_account=True,
    )
    assert decision.mode == TurnExecutionMode.SKILL
    assert decision.skill_code == "account_inspection"
    assert decision.requires_operation_task is True


def test_account_skill_without_account_requests_clarification(registry):
    decision = route_explicit_request(
        requested_skill_code="account_inspection",
        platform="douyin",
        registry=registry,
        has_account=False,
    )
    assert decision.mode == TurnExecutionMode.CLARIFY
    assert decision.missing_field == "account_id"
```

- [ ] **Step 2: Run the router tests**

Run: `cd backend && uv run pytest tests/test_capability_router.py -v`

Expected: FAIL because the route contract does not exist.

- [ ] **Step 3: Implement the discriminated routing contract**

```python
class TurnExecutionMode(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    QUERY = "query"
    SKILL = "skill"
    TASK = "task"
    ACTION = "action"


class TurnRouteDecision(BaseModel):
    mode: TurnExecutionMode
    intent: str
    confidence: float = Field(ge=0, le=1)
    reason: str
    skill_code: str | None = None
    requires_account_context: bool = False
    requires_operation_task: bool = False
    missing_field: str | None = None
    clarifying_question: str | None = None
```

`route_explicit_request` must reject unknown or platform-incompatible Skills with a typed `SkillUnavailable` error. It must never silently fall back to a different Skill.

- [ ] **Step 4: Verify all deterministic branches**

Run: `cd backend && uv run pytest tests/test_capability_router.py -v`

Expected: PASS for explicit Skill, missing account, unknown Skill, incompatible platform, and no-explicit-Skill cases.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/conversation.py backend/app/orchestrator/capability_router.py backend/tests/test_capability_router.py
git commit -m "feat: define main agent execution modes"
```

### Task 5: Upgrade LLM intent classification to the new routing contract

**Files:**
- Modify: `backend/app/orchestrator/brain_intelligence.py`
- Create: `backend/app/prompts/main-agent/intent/v2.md`
- Modify: `backend/app/prompts/manifest.py`
- Create: `backend/tests/test_turn_intelligence.py`

**Interfaces:**
- Consumes: `TurnRouteDecision` from Task 4.
- Produces: `BrainIntelligence.classify_turn(..., requested_skill_code=None)`.
- Keeps `BrainIntelligence.classify` as a compatibility adapter until old callers migrate.

- [ ] **Step 1: Write classification tests for the critical boundaries**

```python
@pytest.mark.asyncio
async def test_data_question_routes_to_query_without_operation_task(monkeypatch):
    async def fake_structured_chat(*args, **kwargs):
        payload = {
            "mode": "query",
            "intent": "account_metrics",
            "confidence": 0.97,
            "reason": "只需读取最近七天数据",
            "skill_code": "account_data_query",
            "requires_account_context": True,
            "requires_operation_task": False,
        }
        return CompletionResult(
            json.dumps(payload, ensure_ascii=False),
            "deepseek-chat",
            10,
            20,
            30,
        ), 0.0

    monkeypatch.setattr(
        "app.orchestrator.brain_intelligence._structured_chat",
        fake_structured_chat,
    )
    decision = await BrainIntelligence().classify_turn(
        None,
        1,
        "查看最近七天数据",
        has_account=True,
        platform="douyin",
    )
    assert decision.mode == TurnExecutionMode.QUERY
    assert decision.requires_operation_task is False
```

Add matching tests for greeting → `ANSWER`, “制定 30 天策略” → `TASK`, and “发布这条内容” → `ACTION`.

- [ ] **Step 2: Run the new classification tests**

Run: `cd backend && uv run pytest tests/test_turn_intelligence.py -v`

Expected: FAIL because `classify_turn` and the v2 Prompt do not exist.

- [ ] **Step 3: Add and pin the v2 intent Prompt**

The Prompt must emit exactly:

```json
{
  "mode": "query",
  "intent": "account_metrics",
  "confidence": 0.97,
  "reason": "只需读取账号数据",
  "skill_code": "account_data_query",
  "requires_account_context": true,
  "requires_operation_task": false,
  "missing_field": null,
  "clarifying_question": null
}
```

Register `main-agent.intent` version `2.0.0` with its exact SHA-256 and schema version `turn-route-decision/v1`. Do not edit v1 in place.

- [ ] **Step 4: Implement `classify_turn` and the legacy adapter**

`classify_turn` first applies `_CASUAL_MESSAGES`, then explicit Skill routing, then the v2 model decision. Missing required account context returns `CLARIFY`. Existing `classify` maps:

```text
ANSWER -> conversation
CLARIFY -> clarification
QUERY -> analysis
SKILL/TASK -> workflow
ACTION -> action
```

Run: `cd backend && uv run pytest tests/test_turn_intelligence.py tests/test_brain_api.py -q`

Expected: PASS with no regression in legacy intent responses.

- [ ] **Step 5: Commit**

```bash
git add backend/app/orchestrator/brain_intelligence.py backend/app/prompts/main-agent/intent/v2.md backend/app/prompts/manifest.py backend/tests/test_turn_intelligence.py
git commit -m "feat: route main agent turns by execution mode"
```

### Checkpoint 2: Capability routing

- [ ] Skill Registry tests pass.
- [ ] Greeting, query, Skill, task, and action route tests pass.
- [ ] Explicit “＋” Skill requests cannot be reclassified by the model.
- [ ] Legacy `IntentDecision` callers still pass their existing tests.

---

## Phase 3: SkillRun and Artifact Provenance

### Task 6: Persist SkillRun and attach expert/tool execution

**Files:**
- Create: `backend/app/models/skill_runtime.py`
- Modify: `backend/app/models/brain.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/migrations/versions/20260728_0200_skill_runs.py`
- Test: `backend/tests/test_skill_runtime_models.py`

**Interfaces:**
- Produces: `SkillRun` with optional `task_id`, required Thread/Turn/Run ownership, frozen Skill version, input/output snapshots, and quality score.
- Adds nullable `skill_run_id`, `thread_id`, and `turn_id` to `AgentInvocation` and `AgentToolCall`.

- [ ] **Step 1: Write model tests**

```python
@pytest.mark.asyncio
async def test_skill_run_can_complete_without_brain_task(
    session, admin, conversation_turn, agent_run
):
    skill_run = models.SkillRun(
        org_id=admin.org_id,
        thread_id=conversation_turn.thread_id,
        turn_id=conversation_turn.id,
        run_id=agent_run.id,
        task_id=None,
        idempotency_key="query-1:account_data_query:1",
        skill_code="account_data_query",
        skill_version=1,
        status="completed",
        input_snapshot={"days": 7},
        output_snapshot={"play": 1200},
    )
    session.add(skill_run)
    await session.commit()
    assert skill_run.task_id is None
    assert skill_run.output_snapshot["play"] == 1200
```

- [ ] **Step 2: Run the model test**

Run: `cd backend && uv run pytest tests/test_skill_runtime_models.py -v`

Expected: FAIL because `SkillRun` does not exist.

- [ ] **Step 3: Implement SkillRun and nullable ledger ownership**

```python
class SkillRun(Base, TimestampMixin):
    __tablename__ = "skill_runs"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_skill_run_run_idempotency",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"), index=True
    )
    turn_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_turns.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160))
    skill_code: Mapped[str] = mapped_column(String(120), index=True)
    skill_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), index=True)
    input_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    output_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
```

- [ ] **Step 4: Add and exercise the reversible migration**

The migration creates `skill_runs` and adds nullable provenance columns to `agent_invocations` and `agent_tool_calls`. Its downgrade removes only these additions.

Run: `cd backend && uv run pytest tests/test_skill_runtime_models.py tests/test_migrations.py -q`

Expected: PASS, with legacy invocation and tool rows still readable.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/skill_runtime.py backend/app/models/brain.py backend/app/models/__init__.py backend/migrations/versions/20260728_0200_skill_runs.py backend/tests/test_skill_runtime_models.py
git commit -m "feat: persist versioned skill runs"
```

### Task 7: Add Turn provenance to artifacts and AI COO records

**Files:**
- Modify: `backend/app/models/content.py`
- Modify: `backend/app/models/ai_coo.py`
- Modify: `backend/app/models/orchestration.py`
- Create: `backend/migrations/versions/20260728_0300_turn_provenance.py`
- Test: `backend/tests/test_turn_provenance.py`

**Interfaces:**
- Consumes: Thread/Turn/Run/SkillRun tables from Tasks 1, 2, and 6.
- Produces: nullable provenance on `Deliverable`, `StrategyPlan`, `DecisionTrace`, `ReflectionRecord`, `AgentQualityScore`, and `Event`.

- [ ] **Step 1: Write a provenance round-trip test**

```python
@pytest.mark.asyncio
async def test_formal_artifact_keeps_source_turn(
    session, content_item, conversation_turn, agent_run, skill_run
):
    deliverable = Deliverable(
        content_item_id=content_item.id,
        agent_code=AgentCode.OPERATOR,
        type=DeliverableType.REVIEW_REPORT,
        version=1,
        payload={"summary": "完播率下降"},
        thread_id=conversation_turn.thread_id,
        turn_id=conversation_turn.id,
        run_id=agent_run.id,
        skill_run_id=skill_run.id,
    )
    session.add(deliverable)
    await session.commit()
    assert deliverable.turn_id == conversation_turn.id
    assert deliverable.skill_run_id == skill_run.id
```

- [ ] **Step 2: Run the provenance test**

Run: `cd backend && uv run pytest tests/test_turn_provenance.py -v`

Expected: FAIL because the provenance columns do not exist.

- [ ] **Step 3: Add nullable provenance fields**

Use the same names and foreign-key behavior on each supported ledger:

```python
thread_id -> conversation_threads.id, ondelete="SET NULL"
turn_id -> conversation_turns.id, ondelete="SET NULL"
run_id -> agent_runs.id, ondelete="SET NULL"
skill_run_id -> skill_runs.id, ondelete="SET NULL"
```

Keep existing required `task_id` fields required until a later compatibility migration proves they can be relaxed. New query-only SkillRuns use `Deliverable` only when a compatible content item exists; otherwise they return a data projection rather than a formal Deliverable.

- [ ] **Step 4: Verify migration and existing AI COO models**

Run: `cd backend && uv run pytest tests/test_turn_provenance.py tests/test_ai_coo_models.py tests/test_migrations.py -q`

Expected: PASS. Legacy records retain `NULL` provenance and remain readable.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/content.py backend/app/models/ai_coo.py backend/app/models/orchestration.py backend/migrations/versions/20260728_0300_turn_provenance.py backend/tests/test_turn_provenance.py
git commit -m "feat: trace artifacts to source turns"
```

### Checkpoint 3: Runtime ownership

- [ ] A SkillRun can exist with or without a BrainTask.
- [ ] Expert and Tool rows can be queried by SkillRun and Turn.
- [ ] Formal artifacts and AI COO records preserve source provenance.
- [ ] All three new migrations upgrade and downgrade in order.

---

## Phase 4: Turn API and Execution

### Task 8: Add authorized ConversationThread and ConversationTurn services

**Files:**
- Create: `backend/app/services/conversations.py`
- Modify: `backend/app/schemas/conversation.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_conversation_service.py`

**Interfaces:**
- Produces:
  - `create_conversation_thread(session, user, input) -> ConversationThread`
  - `append_conversation_turn(session, user, thread_id, input) -> tuple[ConversationTurn, bool]`
  - `get_conversation_thread(session, user, thread_id) -> ConversationThread`
- Uses `require_account_access` at the service boundary.

- [ ] **Step 1: Write authorization and idempotency tests**

```python
@pytest.mark.asyncio
async def test_append_turn_returns_existing_row_for_same_client_message(
    session, admin, thread
):
    body = CreateConversationTurnRequest(
        client_message_id="message-1",
        message="查看最近七天数据",
        execution_preference="AUTO",
    )
    first, first_created = await append_conversation_turn(
        session, admin, thread.id, body
    )
    second, second_created = await append_conversation_turn(
        session, admin, thread.id, body
    )
    assert first.id == second.id
    assert first_created is True
    assert second_created is False
```

- [ ] **Step 2: Run the service tests**

Run: `cd backend && uv run pytest tests/test_conversation_service.py -v`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement scoped Thread and Turn creation**

`CreateConversationTurnRequest` contains:

```python
client_message_id: str
message: str
requested_skill_code: str | None = None
execution_preference: Literal["AUTO", "DISCUSS_ONLY", "FORMAL_TASK"] = "AUTO"
attachment_ids: list[int] = Field(default_factory=list)
```

Use a transaction and catch `IntegrityError` on the unique key to load the existing Turn. A duplicate request with a different message returns `409 CLIENT_MESSAGE_CONFLICT`.

Add `main_agent_v2_enabled: bool = False` to Settings. The flag is defined here so every later API and execution slice consumes the same configuration field.

- [ ] **Step 4: Verify scope isolation**

Run: `cd backend && uv run pytest tests/test_conversation_service.py -v`

Expected: PASS for same-message idempotency, conflicting-message rejection, unauthorized account rejection, and cross-org Thread rejection.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/conversations.py backend/app/schemas/conversation.py backend/app/config.py backend/tests/test_conversation_service.py
git commit -m "feat: add scoped conversation services"
```

### Task 9: Add additive Thread and Turn API endpoints

**Files:**
- Create: `backend/app/api/conversations.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/agent_runs.py`
- Test: `backend/tests/test_conversation_api.py`
- Modify: `backend/app/schemas/conversation.py`

**Interfaces:**
- Produces:
  - `POST /brain/conversations`
  - `GET /brain/conversations/{thread_id}`
  - `POST /brain/conversations/{thread_id}/turns`
  - `GET /brain/turns/{turn_id}`
- `claim_agent_run` accepts optional `thread_id` and `turn_id`.

- [ ] **Step 1: Write API contract tests**

```python
monkeypatch.setattr(settings, "main_agent_v2_enabled", True)
response = await client.post(
    f"/brain/conversations/{thread_id}/turns",
    headers=headers,
    json={
        "client_message_id": "turn-api-1",
        "message": "你好",
        "execution_preference": "AUTO",
    },
)
assert response.status_code == 202
assert response.json()["turn"]["thread_id"] == thread_id
assert response.json()["run"]["turn_id"] == response.json()["turn"]["id"]
assert response.json()["task_id"] is None
```

- [ ] **Step 2: Run the API test**

Run: `cd backend && uv run pytest tests/test_conversation_api.py -v`

Expected: FAIL with 404 for the new endpoints.

- [ ] **Step 3: Implement additive endpoints and run ownership**

The Turn endpoint:

1. appends or loads the idempotent Turn;
2. claims one AgentRun with `thread_id` and `turn_id`;
3. returns `202 Accepted` with the persisted Turn and claimed Run;
4. returns the same response for duplicate `client_message_id`.

Keep the endpoint behind `main_agent_v2_enabled` defaulting to false and return `503 MAIN_AGENT_V2_DISABLED` when disabled. Task 10 replaces the accepted-only response with real execution before the flag may be enabled.

- [ ] **Step 4: Verify API and legacy message endpoint**

Run: `cd backend && uv run pytest tests/test_conversation_api.py tests/test_brain_api.py -q`

Expected: PASS; `/brain/messages` remains unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/conversations.py backend/app/main.py backend/app/services/agent_runs.py backend/app/schemas/conversation.py backend/tests/test_conversation_api.py
git commit -m "feat: expose turn-based conversation API"
```

### Task 10: Execute routed Turns without forcing a BrainTask

**Files:**
- Create: `backend/app/services/turn_execution.py`
- Modify: `backend/app/api/conversations.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/schemas/conversation.py`
- Test: `backend/tests/test_turn_execution.py`

**Interfaces:**
- Produces: `execute_conversation_turn(session, user, turn, run, request) -> TurnExecutionResult`.
- Direct answer and query modes keep `task_id=None`.
- Task/action modes create a new BrainTask or explicitly continue an existing OperationTask.

- [ ] **Step 1: Write the no-task regression tests**

```python
@pytest.mark.asyncio
async def test_query_turn_does_not_create_strategy_task(
    session, admin, turn, run, monkeypatch
):
    async def fake_classify_turn(*args, **kwargs):
        return TurnRouteDecision(
            mode="query",
            intent="account_metrics",
            confidence=0.99,
            reason="确定性查询",
            skill_code="account_data_query",
            requires_account_context=True,
            requires_operation_task=False,
        )

    monkeypatch.setattr(
        "app.services.turn_execution.BrainIntelligence.classify_turn",
        fake_classify_turn,
    )
    request = CreateConversationTurnRequest(
        client_message_id="query-1",
        message="查看最近七天数据",
        execution_preference="AUTO",
    )
    result = await execute_conversation_turn(session, admin, turn, run, request)
    assert result.task_id is None
    assert result.mode == "query"
    assert await session.scalar(select(func.count(BrainTask.id))) == 0
```

Add a greeting assertion with no AgentInvocation and a strategy request assertion with one BrainTask.

- [ ] **Step 2: Run the execution tests**

Run: `cd backend && uv run pytest tests/test_turn_execution.py -v`

Expected: FAIL because `execute_conversation_turn` does not exist.

- [ ] **Step 3: Implement the execution switch**

```python
match decision.mode:
    case TurnExecutionMode.ANSWER:
        return await answer_turn_without_task(
            session=session,
            user=user,
            turn=turn,
            run=run,
        )
    case TurnExecutionMode.CLARIFY:
        return await request_turn_clarification(
            session=session,
            turn=turn,
            run=run,
            decision=decision,
        )
    case TurnExecutionMode.QUERY | TurnExecutionMode.SKILL:
        return await execute_skill_turn(
            session=session,
            user=user,
            turn=turn,
            run=run,
            request=request,
            decision=decision,
        )
    case TurnExecutionMode.TASK | TurnExecutionMode.ACTION:
        task = await create_operation_task_compat(
            session=session,
            user=user,
            turn=turn,
            decision=decision,
        )
        return await execute_operation_task_turn(
            session=session,
            user=user,
            turn=turn,
            run=run,
            task=task,
            decision=decision,
        )
```

Add a task-free conversation streaming method to `BrainRuntimeGraph` that emits Turn-scoped events and updates `ConversationTurn.assistant_response`; it must not mutate any BrainTask.

For `QUERY`, `execute_skill_turn` calls the existing `account.data_context` Tool through `ToolExecutor`, records one SkillRun, and returns a data projection. For a registered composite Skill without an executor, it returns a structured `SKILL_EXECUTOR_UNAVAILABLE` blocked result; Task 12 registers the first composite executor before rollout.

Update `POST /brain/conversations/{thread_id}/turns` to call this service and return `202` with the latest `TurnSubmissionOut`. Duplicate client message IDs return the already-associated Turn, Run, and projections without executing again.

- [ ] **Step 4: Run execution and runtime regression tests**

Run: `cd backend && uv run pytest tests/test_turn_execution.py tests/test_brain_runtime_context.py tests/test_brain_api.py -q`

Expected: PASS. “查看数据” does not create StrategyPlan; “制定策略” still reaches the existing durable runtime.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/turn_execution.py backend/app/api/conversations.py backend/app/orchestrator/brain_runtime.py backend/app/schemas/conversation.py backend/tests/test_turn_execution.py
git commit -m "feat: execute main agent turns by route"
```

---

## Phase 5: Artifact Service and Account Inspection Skill

### Task 11: Add account-scoped artifact projection and actions

**Files:**
- Create: `backend/app/schemas/artifacts.py`
- Create: `backend/app/services/artifacts.py`
- Create: `backend/app/api/artifacts.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_artifacts_api.py`

**Interfaces:**
- Produces:
  - `GET /artifacts?account_id=&artifact_type=&status=&page=&page_size=`
  - `GET /artifacts/{artifact_id}`
  - `POST /artifact-revisions`
  - `POST /artifact-acceptances`
- Returns one `ArtifactOut` identity in both Turn projection and account listing.

- [ ] **Step 1: Write pagination, authorization, and version tests**

```python
response = await client.get(
    f"/artifacts?account_id={account_id}&page=1&page_size=20",
    headers=headers,
)
assert response.status_code == 200
body = response.json()
assert body["pagination"]["page"] == 1
assert body["data"][0]["turn_id"] == source_turn_id
assert body["data"][0]["version"] == 1
```

- [ ] **Step 2: Run the artifact API tests**

Run: `cd backend && uv run pytest tests/test_artifacts_api.py -v`

Expected: FAIL with 404.

- [ ] **Step 3: Implement a business-first ArtifactOut projection**

```python
ArtifactStatus = Literal[
    "draft",
    "ready_for_review",
    "accepted",
    "revision_requested",
    "superseded",
]


class ArtifactSection(BaseModel):
    key: str
    title: str
    content: str | list[str] | dict[str, Any]


class EvidenceRef(BaseModel):
    kind: str
    id: int
    label: str


class ArtifactQuality(BaseModel):
    score: float = Field(ge=0, le=100)
    passed: bool
    issues: list[str] = Field(default_factory=list)


class ArtifactOut(BaseModel):
    id: int
    account_id: int
    thread_id: int | None
    turn_id: int | None
    run_id: int | None
    skill_run_id: int | None
    task_id: int | None
    artifact_type: str
    title: str
    version: int
    status: ArtifactStatus
    summary: str
    sections: list[ArtifactSection]
    evidence_refs: list[EvidenceRef]
    quality: ArtifactQuality | None
    created_at: datetime
```

Map internal payload keys to Chinese section titles in the service. Never return `acceptance_items` with generic “Confirm that this item...” notes.

- [ ] **Step 4: Verify artifact behavior**

Run: `cd backend && uv run pytest tests/test_artifacts_api.py tests/test_brain_api.py -q`

Expected: PASS for pagination, account isolation, same-ID detail/list results, revision version increment, and acceptance action semantics.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/artifacts.py backend/app/services/artifacts.py backend/app/api/artifacts.py backend/app/main.py backend/tests/test_artifacts_api.py
git commit -m "feat: add account artifact service"
```

### Task 12: Implement the one-click Account Inspection Skill

**Files:**
- Create: `backend/app/orchestrator/skills/account_inspection.py`
- Create: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/orchestrator/skills/registry.py`
- Modify: `backend/app/services/turn_execution.py`
- Test: `backend/tests/test_account_inspection_skill.py`

**Interfaces:**
- Consumes: `account.profile`, `account.data_context`, existing expert harness, Critic, Artifact service.
- Produces: `AccountInspectionReport` and one `account_inspection_report` Artifact.

- [ ] **Step 1: Write the complete and insufficient-data tests**

```python
@pytest.mark.asyncio
async def test_account_inspection_reports_missing_data_without_fabrication(
    runtime_context, monkeypatch
):
    async def fake_execute(*args, **kwargs):
        return {
            "coverage": {"content_metrics": "missing"},
            "metrics": {},
            "sources": [],
            "content_snapshot_count": 0,
        }

    monkeypatch.setattr(
        runtime_context.tools,
        "execute",
        fake_execute,
    )
    result = await execute_account_inspection(runtime_context, days=30)
    assert result.data_sufficiency == "insufficient"
    assert result.missing_data
    assert result.key_metrics == []
    assert "无法" in result.summary
```

Add a sufficient-data test asserting three expert invocations, one Critic evaluation, evidence references, and a next action.

- [ ] **Step 2: Run the Skill tests**

Run: `cd backend && uv run pytest tests/test_account_inspection_skill.py -v`

Expected: FAIL because the Skill executor does not exist.

- [ ] **Step 3: Implement the bounded Skill graph**

```text
account.profile + account.data_context
  -> data packet
  -> 06-operator data analysis
  -> 01-positioning positioning diagnosis
  -> 02-content-director content recommendations
  -> Critic
  -> AccountInspectionReport
  -> Artifact
```

`SkillRuntime.execute` freezes the Skill version, creates one SkillRun, records every ExpertInvocation and ToolCall with the SkillRun provenance, retries Critic failure at most twice, and persists a structured blocked result after the retry budget is exhausted.

- [ ] **Step 4: Verify the Skill and route integration**

Run: `cd backend && uv run pytest tests/test_account_inspection_skill.py tests/test_turn_execution.py tests/test_runtime_tool_executor.py -q`

Expected: PASS. The explicit Skill route and natural-language route produce the same Skill code, execution graph, and Artifact type.

- [ ] **Step 5: Commit**

```bash
git add backend/app/orchestrator/skills/account_inspection.py backend/app/orchestrator/skill_runtime.py backend/app/orchestrator/skills/registry.py backend/app/services/turn_execution.py backend/tests/test_account_inspection_skill.py
git commit -m "feat: add one-click account inspection skill"
```

### Task 13: Expose the user-visible Skill catalog

**Files:**
- Create: `backend/app/api/skills.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas/skills.py`
- Test: `backend/tests/test_skills_api.py`

**Interfaces:**
- Produces: `GET /skills?platform=douyin&surface=composer`.
- Response contains only business metadata, required context, and availability; it excludes Prompt content, model configuration, and raw Tool parameters.

- [ ] **Step 1: Write catalog filtering tests**

```python
response = await client.get(
    "/skills?platform=douyin&surface=composer",
    headers=headers,
)
assert response.status_code == 200
item = next(row for row in response.json()["data"] if row["code"] == "account_inspection")
assert item["name"] == "一键账号体检"
assert item["category"] == "quick_operations"
assert "prompt" not in item
assert "tool_codes" not in item
```

- [ ] **Step 2: Run the API tests**

Run: `cd backend && uv run pytest tests/test_skills_api.py -v`

Expected: FAIL with 404.

- [ ] **Step 3: Implement the scoped catalog endpoint**

The response item includes:

```python
code: str
version: int
name: str
description: str
category: Literal["quick_operations", "context", "expert_help"]
icon: str
requires_account: bool
is_available: bool
unavailable_reason: str | None
```

- [ ] **Step 4: Verify security and platform filtering**

Run: `cd backend && uv run pytest tests/test_skills_api.py tests/test_skill_registry.py -q`

Expected: PASS. Disabled, unauthorized, or platform-incompatible Skills are omitted or marked unavailable without leaking internal configuration.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/skills.py backend/app/main.py backend/app/schemas/skills.py backend/tests/test_skills_api.py
git commit -m "feat: expose business skill catalog"
```

### Checkpoint 4: Backend operating loop

- [ ] “你好” returns a Turn answer and no BrainTask.
- [ ] “查看最近七天数据” returns a query projection and no StrategyPlan.
- [ ] “一键账号体检” produces one SkillRun, traceable experts, Critic result, and Artifact.
- [ ] “制定 30 天策略” creates a durable BrainTask with source Turn/Run.
- [ ] High-risk action remains paused behind existing approval gates.
- [ ] Artifact list and source Turn return the same Artifact ID.

---

## Phase 6: Frontend Turn Projection

### Task 14: Add frontend Thread, Turn, Skill, and Artifact contracts

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/brain.ts`
- Modify: `frontend/src/api/brain.test.ts`
- Modify: `frontend/src/stores/brainConversation.ts`

**Interfaces:**
- Produces:
  - `createConversation`
  - `sendConversationTurn`
  - `getConversation`
  - `listComposerSkills`
  - `listArtifacts`
  - active Thread storage by account.

- [ ] **Step 1: Add API contract tests**

```ts
it("submits one idempotent turn with an optional requested Skill", async () => {
  await sendConversationTurn(12, {
    client_message_id: "turn-1",
    message: "体检这个账号",
    requested_skill_code: "account_inspection",
    execution_preference: "AUTO",
  });
  expect(api.post).toHaveBeenCalledWith(
    "/brain/conversations/12/turns",
    expect.objectContaining({ requested_skill_code: "account_inspection" }),
  );
});
```

- [ ] **Step 2: Run the frontend API tests**

Run: `cd frontend && pnpm test -- src/api/brain.test.ts`

Expected: FAIL because the new clients and types do not exist.

- [ ] **Step 3: Add discriminated projection types**

```ts
export type TurnProjection =
  | { type: "answer"; turn_id: number; message: string }
  | { type: "progress"; turn_id: number; skill_run_id: number; stages: SkillStage[] }
  | { type: "expert"; turn_id: number; invocation: AgentInvocation }
  | { type: "approval"; turn_id: number; approval: AgentToolCall }
  | { type: "artifact"; turn_id: number; artifact: Artifact };
```

Replace `getActiveBrainTaskId` with additive `getActiveConversationThreadId`; keep the old functions until BrainHome migration is complete.

- [ ] **Step 4: Verify API and storage**

Run: `cd frontend && pnpm test -- src/api/brain.test.ts`

Expected: PASS for conversation, Skill catalog, Artifact pagination, and legacy BrainTask calls.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/brain.ts frontend/src/api/brain.test.ts frontend/src/stores/brainConversation.ts
git commit -m "feat: add turn-based frontend contracts"
```

### Task 15: Render conversation history by source Turn

**Files:**
- Create: `frontend/src/components/brain/TurnStream.tsx`
- Create: `frontend/src/components/brain/TurnStream.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Interfaces:**
- Consumes: `ConversationThreadOut.turns[].projections`.
- Produces: stable Turn sections; artifacts and strategies never render outside their source Turn.

- [ ] **Step 1: Write the historical-artifact anchoring test**

```tsx
it("keeps an artifact under the turn that created it", async () => {
  render(<TurnStream turns={[inspectionTurn, laterGreetingTurn]} />);
  const artifact = screen.getByRole("article", { name: "正式成果：账号体检报告" });
  const sourceTurn = screen.getByTestId(`turn-${inspectionTurn.id}`);
  const laterTurn = screen.getByTestId(`turn-${laterGreetingTurn.id}`);
  expect(sourceTurn).toContainElement(artifact);
  expect(laterTurn).not.toContainElement(artifact);
});
```

- [ ] **Step 2: Run the Turn stream tests**

Run: `cd frontend && pnpm test -- src/components/brain/TurnStream.test.tsx`

Expected: FAIL because `TurnStream` does not exist.

- [ ] **Step 3: Implement projection rendering**

Render projections in server order inside:

```tsx
<article data-testid={`turn-${turn.id}`}>
  <UserMessage content={turn.user_input} />
  {turn.projections.map((projection) => (
    <TurnProjectionView key={projectionKey(projection)} projection={projection} />
  ))}
</article>
```

Remove unconditional task-wide rendering of `AICOOConversationRecord` and `runtime.acceptances` from the bottom of `ConversationStream`. Keep the legacy renderer only behind the old runtime response path.

- [ ] **Step 4: Verify BrainHome behavior**

Run: `cd frontend && pnpm test -- src/components/brain/TurnStream.test.tsx src/pages/BrainHome.test.tsx`

Expected: PASS for pending Turn, streaming updates, stop/regenerate, expert lifecycle, approval mode, and fixed artifact origin.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/brain/TurnStream.tsx frontend/src/components/brain/TurnStream.test.tsx frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: project main agent history by turn"
```

### Task 16: Replace the internal acceptance card with a business Artifact card

**Files:**
- Create: `frontend/src/components/brain/ArtifactCard.tsx`
- Create: `frontend/src/components/brain/ArtifactCard.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Interfaces:**
- Consumes: `Artifact`.
- Produces: core conclusion, period, metrics, problems, recommendations, explicit actions, evidence details, and version status.

- [ ] **Step 1: Write business-copy and action tests**

```tsx
it("shows business sections and hides internal schema keys", () => {
  render(<ArtifactCard artifact={reviewArtifact} onAction={vi.fn()} />);
  expect(screen.getByText("核心结论")).toBeInTheDocument();
  expect(screen.getByText("关键数据")).toBeInTheDocument();
  expect(screen.getByText("主要问题")).toBeInTheDocument();
  expect(screen.queryByText("key_metrics")).not.toBeInTheDocument();
  expect(screen.queryByText(/Confirm that this item/)).not.toBeInTheDocument();
});
```

Add assertions for “查看完整报告”, “仅采纳报告”, “采纳并创建下一步”, and “提出修改”.

- [ ] **Step 2: Run the Artifact card tests**

Run: `cd frontend && pnpm test -- src/components/brain/ArtifactCard.test.tsx`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement unambiguous status and version UI**

Use one primary artifact status. When revision is requested, keep V1 readable and show a separate “正在生成 V2” progress row. Do not display “正式成果 V1” and “重做中” as peer statuses on one card.

- [ ] **Step 4: Verify card and BrainHome integration**

Run: `cd frontend && pnpm test -- src/components/brain/ArtifactCard.test.tsx src/pages/BrainHome.test.tsx`

Expected: PASS. Technical details are closed by default and available under “查看生成依据”.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/brain/ArtifactCard.tsx frontend/src/components/brain/ArtifactCard.test.tsx frontend/src/pages/BrainHome.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: present artifacts as business outcomes"
```

### Task 17: Add the account Artifact Center inside the operations brain

**Files:**
- Create: `frontend/src/components/brain/ArtifactCenter.tsx`
- Create: `frontend/src/components/brain/ArtifactCenter.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Interfaces:**
- Consumes: paginated `listArtifacts`.
- Produces: an account-scoped “成果” secondary view without adding a new primary navigation item.

- [ ] **Step 1: Write same-identity and filter tests**

```tsx
it("opens the same artifact identity from the account center", async () => {
  render(<ArtifactCenter accountId={42} onOpen={onOpen} />);
  await user.click(await screen.findByText("账号体检报告"));
  expect(onOpen).toHaveBeenCalledWith(
    expect.objectContaining({ id: 1201 }),
  );
});
```

Add filters for artifact type, status, and creation time.

- [ ] **Step 2: Run the center tests**

Run: `cd frontend && pnpm test -- src/components/brain/ArtifactCenter.test.tsx`

Expected: FAIL because the center does not exist.

- [ ] **Step 3: Implement the secondary view**

Add a compact “对话 / 成果” switch in BrainHome. Opening an artifact from the center shows the same `ArtifactCard` detail and offers “回到来源对话”, which scrolls to `turn_id`.

- [ ] **Step 4: Verify account switching and pagination**

Run: `cd frontend && pnpm test -- src/components/brain/ArtifactCenter.test.tsx src/pages/BrainHome.test.tsx`

Expected: PASS. Switching the current account invalidates the artifact query and never displays another account’s artifacts.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/brain/ArtifactCenter.tsx frontend/src/components/brain/ArtifactCenter.test.tsx frontend/src/pages/BrainHome.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: add account artifact center"
```

---

## Phase 7: Composer Capability Launcher

### Task 18: Add the “＋” business capability launcher

**Files:**
- Create: `frontend/src/components/brain/CapabilityLauncher.tsx`
- Create: `frontend/src/components/brain/CapabilityLauncher.test.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Interfaces:**
- Consumes: `SkillCatalogItem[]`.
- Produces: `onSelectSkill(skillCode)` and context actions for files, data packages, historical artifacts, and account selection.

- [ ] **Step 1: Write launcher accessibility and selection tests**

```tsx
it("launches account inspection as a structured Skill request", async () => {
  render(<BrainComposer {...props} skills={[accountInspectionSkill]} />);
  await user.click(screen.getByRole("button", { name: "添加能力或材料" }));
  await user.click(screen.getByRole("menuitem", { name: /一键账号体检/ }));
  expect(props.onSelectSkill).toHaveBeenCalledWith("account_inspection");
  expect(props.onChange).not.toHaveBeenCalledWith(
    expect.stringContaining("account_inspection"),
  );
});
```

- [ ] **Step 2: Run launcher tests**

Run: `cd frontend && pnpm test -- src/components/brain/CapabilityLauncher.test.tsx src/components/brain/BrainComposer.test.tsx`

Expected: FAIL because the launcher and new props do not exist.

- [ ] **Step 3: Implement the categorized menu**

The trigger is the left-most composer action with `aria-label="添加能力或材料"`. Sections render in this order:

1. 快捷运营
2. 添加上下文
3. 专家协助

Unavailable Skills remain visible with a concise reason. Keyboard interaction supports Enter, Arrow keys, Escape, and focus return to the trigger.

- [ ] **Step 4: Verify composer modes**

Run: `cd frontend && pnpm test -- src/components/brain/CapabilityLauncher.test.tsx src/components/brain/BrainComposer.test.tsx`

Expected: PASS. The launcher is hidden while the composer is in permission-confirmation mode and returns after the decision completes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/brain/CapabilityLauncher.tsx frontend/src/components/brain/CapabilityLauncher.test.tsx frontend/src/components/brain/BrainComposer.tsx frontend/src/components/brain/BrainComposer.test.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: add main agent capability launcher"
```

### Task 19: Connect launcher selection to one-click Skill execution

**Files:**
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/api/brain.ts`
- Modify: `frontend/src/stores/brainConversation.ts`

**Interfaces:**
- Consumes: `CapabilityLauncher.onSelectSkill`.
- Produces: one idempotent Turn request containing `requested_skill_code`.
- Does not insert internal Skill codes into the visible message.

- [ ] **Step 1: Write the one-click account inspection flow test**

```tsx
it("starts account inspection for the selected account from the plus menu", async () => {
  render(<BrainHome />);
  await user.click(screen.getByRole("button", { name: "添加能力或材料" }));
  await user.click(screen.getByRole("menuitem", { name: /一键账号体检/ }));
  await waitFor(() =>
    expect(sendConversationTurn).toHaveBeenCalledWith(
      activeThreadId,
      expect.objectContaining({
        requested_skill_code: "account_inspection",
        execution_preference: "AUTO",
      }),
    ),
  );
});
```

- [ ] **Step 2: Run the BrainHome test**

Run: `cd frontend && pnpm test -- src/pages/BrainHome.test.tsx -t "starts account inspection"`

Expected: FAIL because launcher selection is not wired.

- [ ] **Step 3: Implement one-click submission**

When the selected account is valid, create or reuse its active ConversationThread and immediately submit:

```ts
{
  client_message_id: createClientMessageId(),
  message: "一键账号体检",
  requested_skill_code: "account_inspection",
  execution_preference: "AUTO",
  attachment_ids: [],
}
```

When account context is missing, focus the global account selector and do not issue an API request.

- [ ] **Step 4: Verify natural language and launcher parity**

Run: `cd frontend && pnpm test -- src/pages/BrainHome.test.tsx src/api/brain.test.ts`

Expected: PASS. Natural-language “体检这个账号” and launcher selection both render the same Skill progress and artifact projection.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx frontend/src/api/brain.ts frontend/src/stores/brainConversation.ts
git commit -m "feat: launch account inspection from composer"
```

---

## Phase 8: Regression, Observability, and Rollout

### Task 20: Add cross-intent end-to-end regression coverage

**Files:**
- Create: `backend/tests/test_main_agent_v2_flow.py`
- Create: `frontend/e2e/main-agent-v2.spec.ts`
- Modify: `backend/tests/test_brain_api.py`
- Modify: `frontend/src/pages/BrainHome.test.tsx`

**Interfaces:**
- Verifies the complete ownership and UI projection contract.
- No new production interface.

- [ ] **Step 1: Add the backend cross-intent sequence**

Execute one Thread through:

```text
你好
-> 查看最近七天数据
-> 一键账号体检
-> 解释体检报告
-> 制定 30 天策略
-> 继续普通对话
```

Assert:

```python
assert len(turns) == 6
assert turns[0].agent_runs[0].task_id is None
assert turns[1].agent_runs[0].task_id is None
assert inspection_artifact.turn_id == turns[2].id
assert explanation_artifact_count == 0
assert strategy.task_id == turns[4].agent_runs[0].task_id
```

- [ ] **Step 2: Run the new backend flow**

Run: `cd backend && uv run pytest tests/test_main_agent_v2_flow.py -v`

Expected: PASS only when routing, provenance, and artifact reuse are complete.

- [ ] **Step 3: Add the Playwright user flow**

The browser test selects an account, opens “＋”, launches account inspection, expands expert details, opens the completed Artifact, switches to the Artifact Center, returns to the source Turn, and verifies that a later greeting does not move the Artifact.

- [ ] **Step 4: Run complete quality gates**

Run:

```bash
cd backend
uv run pytest
uv run ruff check .
uv run mypy app

cd ../frontend
pnpm test
pnpm lint
pnpm build
pnpm test:e2e -- main-agent-v2.spec.ts
```

Expected: all commands PASS. Record exact test totals in the final implementation handoff.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_main_agent_v2_flow.py backend/tests/test_brain_api.py frontend/e2e/main-agent-v2.spec.ts frontend/src/pages/BrainHome.test.tsx
git commit -m "test: cover main agent v2 operating loop"
```

### Task 21: Add guarded rollout and runtime diagnostics

**Files:**
- Modify: `backend/app/api/conversations.py`
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/tests/test_conversation_api.py`
- Create: `docs/runbooks/main-agent-v2-rollout.md`

**Interfaces:**
- Consumes: `main_agent_v2_enabled` from Task 8 and produces rollback instructions.
- Adds structured, non-sensitive logs for route, Turn, SkillRun, and Artifact IDs.

- [ ] **Step 1: Add configuration tests to the owning API/service tests**

Assert disabled mode keeps the old `/brain/messages` path available and returns a typed disabled response only from the new Turn endpoint.

- [ ] **Step 2: Run the flag tests**

Run: `cd backend && uv run pytest tests/test_conversation_api.py -k feature_flag -v`

Expected before configuration wiring: FAIL.

- [ ] **Step 3: Add rollout diagnostics**

Each completed Turn log contains:

```json
{
  "event": "main_agent_turn_completed",
  "thread_id": 100,
  "turn_id": 301,
  "run_id": 701,
  "mode": "skill",
  "skill_run_id": 901,
  "task_id": null,
  "artifact_ids": [1201],
  "status": "completed"
}
```

Do not log user message bodies, Prompt text, credentials, access tokens, raw platform responses, or full artifact content.

- [ ] **Step 4: Write and verify the rollout runbook**

The runbook defines:

1. migration backup and upgrade;
2. flag enablement for admin users;
3. route-distribution, error-rate, retry, approval, and artifact-provenance checks;
4. frontend fallback to legacy BrainTask projection;
5. flag disablement and migration rollback boundaries.

Run: `git diff --check -- docs/runbooks/main-agent-v2-rollout.md`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/conversations.py backend/app/services/turn_execution.py backend/tests/test_conversation_api.py docs/runbooks/main-agent-v2-rollout.md
git commit -m "chore: guard main agent v2 rollout"
```

## Final Definition of Done

- [ ] The user can enter from any operations stage using natural language.
- [ ] The main Agent automatically selects direct answer, query, Skill, task, or action.
- [ ] The “＋” launcher exposes business capabilities and starts one-click account inspection.
- [ ] Account inspection uses real account data, named specialists, a quality gate, and evidence.
- [ ] Ordinary questions never generate or reuse a 30-day strategy.
- [ ] Every formal artifact stays under its source Turn and appears in the account Artifact Center with the same ID.
- [ ] Artifact cards show conclusions, data, problems, suggestions, and explicit next actions.
- [ ] Specialist names are visible by default; evidence and quality are expandable; technical logs are a deeper view.
- [ ] Publishing, deletion, paid promotion, and authorization changes require approval.
- [ ] Thread, Turn, Run, SkillRun, Task, Artifact, Approval, Observation, and Reflection are traceable.
- [ ] Legacy BrainTask APIs and historical records remain usable during rollout.
- [ ] All backend tests, Ruff, Mypy, frontend tests, ESLint, build, and targeted Playwright flow pass.
- [ ] No production deployment is performed without a separate launch approval.

## Dependency Graph

```text
Tasks 1-2: Thread/Turn foundation
    -> Task 3: Skill Registry
    -> Tasks 4-5: routing contract and LLM integration
    -> Tasks 6-7: SkillRun and provenance
    -> Tasks 8-10: conversation API and execution
    -> Tasks 11-13: artifacts and account inspection
    -> Tasks 14-17: frontend contracts, Turn projection, cards, center
    -> Tasks 18-19: capability launcher
    -> Tasks 20-21: end-to-end regression and rollout
```

Parallel work is safe only after shared contracts are committed:

- After Task 13, backend regression work and frontend API/type work may proceed in parallel.
- After Task 14, TurnStream, ArtifactCard, and CapabilityLauncher components may be developed in parallel if each owns separate files.
- BrainHome integration tasks remain sequential because they share one large page component.
- Database migrations, router contracts, and shared Artifact types must not be implemented in parallel without a committed interface.

## Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Existing uncommitted foundation diverges from this plan | High | Tasks 1 and 2 begin by running focused tests and commit only the owned files |
| BrainTask compatibility path continues leaking task-wide artifacts | High | Turn projection becomes the default only after cross-intent tests pass; legacy projection stays isolated |
| Query intent still enters the strategy graph | High | Route contract has explicit `QUERY` mode and a no-BrainTask regression test |
| Skill definitions become another Prompt catalog | High | Skill Registry uses typed code contracts, frozen versions, permissions, and output schemas |
| Artifact Center duplicates artifacts | High | Both surfaces consume the same Artifact API identity and version chain |
| Large BrainHome file causes merge conflicts | Medium | Frontend behavior is extracted into TurnStream, ArtifactCard, ArtifactCenter, and CapabilityLauncher before integration |
| Technical logs expose sensitive inputs | High | Default projection excludes raw Prompt/tool payloads; diagnostics use IDs and sanitized summaries |
| Historical rows lack Turn provenance | Medium | New fields are nullable and legacy queries use an explicit task-level compatibility fallback |
| Account data is insufficient for diagnosis | Medium | Account Inspection returns a structured insufficient-data report and never fabricates metrics |
