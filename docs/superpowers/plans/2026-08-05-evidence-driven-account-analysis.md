# Evidence-Driven Account Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-safe `account_data_analysis` Skill that answers natural-language account-data questions from confirmed imported data, with deterministic calculations, explicit answerability, traceable evidence, expert interpretation, and V4 WorkTurn delivery.

**Architecture:** Extend the existing V4 branch rather than introducing a second agent framework. A read-only `account.metrics_analysis` Tool will load the current account through `AccountDataViewService`, calculate typed facts and evidence deterministically, and return an answerability decision. The Skill Runtime may ask `06-operator` to interpret supported facts, then validates every number and direction before projecting one `account_analysis_answer` into the existing WorkTurn and Artifact systems.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy async, PostgreSQL, LangGraph Skill Runtime, Pytest, React 18, TypeScript, Vitest, React Testing Library, Playwright, Vite.

## Global Constraints

- Analyze only data already confirmed and written for the selected account; pending import batches never participate in calculations.
- Scope every read and artifact to `org_id + user_id + account_id + thread_id + turn_id`.
- The model cannot submit or override organization, user, account, thread, or turn identifiers.
- A deterministic Tool owns values, periods, directions, ranking, confidence inputs, and evidence references; experts may interpret but never rewrite those facts.
- Missing comparison data cannot produce increase/decrease claims; missing denominators cannot produce rates.
- Correlation may be described only as an observation or testable hypothesis, never as proven causation.
- No arbitrary SQL/Python execution, new agent framework, automatic publishing, automatic monitoring, causal attribution, competitor analysis, prompt self-mutation, or new expert enum.
- Preserve the V4 single-WorkTurn UI, streaming, recovery, account isolation, and default-collapsed technical trace.
- Use test-driven development: observe the relevant test fail before writing production code.
- Make one atomic commit per task and keep the tree buildable after every commit.

---

## File Map

**Backend domain and Tool**

- Create `backend/app/services/account_metric_analysis.py`: metric registry, typed analysis facts, evidence hashing, answerability, comparison, trend, and ranking calculations.
- Modify `backend/app/orchestrator/runtime_tools.py`: strict Tool params, handler, registration, business copy, and runtime phase.
- Create `backend/tests/test_account_metric_analysis.py`: deterministic calculation and answerability tests.
- Modify `backend/tests/test_runtime_tools.py`: authenticated account scope and confirmed-data Tool contract.

**Skill and routing**

- Create `backend/app/orchestrator/skills/account_data_analysis.py`: typed Skill input/output contract and Skill definition.
- Modify `backend/app/orchestrator/skills/registry.py`: register the new Skill.
- Modify `backend/app/orchestrator/skills/public_catalog.py`: expose the capability to the current account/platform.
- Modify `backend/app/orchestrator/capability_router.py`: split simple data-presence queries, analytical questions, and fixed account inspection.
- Modify `backend/app/orchestrator/skill_runtime.py`: execute the typed analysis path, short-circuit insufficient data, call `06-operator`, verify grounded output, and create the artifact.
- Create `backend/tests/test_account_data_analysis_skill.py`: Tool/Skill/expert/critic behavior.
- Modify `backend/tests/test_skill_registry.py`, `backend/tests/test_capability_router.py`, and `backend/tests/test_skills_api.py`: catalog and routing contracts.

**Frontend and end-to-end**

- Modify `frontend/src/types.ts`: add `account_analysis_answer` payload types.
- Modify `frontend/src/components/brain/deliverablePresentation.ts`: user-facing title, summary, actions, and copy.
- Modify `frontend/src/components/brain/ArtifactCard.tsx`: render direct answer, key facts, interpretation, recommendations, limits, and aggregated evidence.
- Modify `frontend/src/components/brain/WorkTurnCard.tsx` and `frontend/src/components/brain/ProcessDisclosure.tsx`: present structured evidence without creating a second message UI.
- Modify matching Vitest files and `frontend/e2e/main-agent-v2.spec.ts`: business presentation, live progress, recovery, and account isolation.

---

### Task 1: Deterministic metric registry, evidence, and answerability

**Files:**
- Create: `backend/app/services/account_metric_analysis.py`
- Create: `backend/tests/test_account_metric_analysis.py`

**Interfaces:**
- Consumes: `AccountDataView`, `ContentMetricSnapshotView`, `AccountMetricSnapshotView`, and `AccountDataObservation` from `app.services.account_data_view`.
- Produces: `MetricDefinition`, `DateRange`, `BusinessEvidenceRef`, `AnalysisFact`, `ContentRanking`, `DataQualitySummary`, `Answerability`, `AccountMetricsAnalysisResult`, and `analyze_account_metrics(view, *, account_id, days, comparison, metric_codes, top_n, today)`.

- [ ] **Step 1: Write failing registry and answerability tests**

```python
from datetime import UTC, date, datetime

from app.models.enums import DataSourceKind
from app.services.account_data_view import (
    AccountDataFreshness,
    AccountDataMetric,
    AccountDataObservation,
    AccountDataView,
    AccountMetricSnapshotView,
)

from app.services.account_metric_analysis import (
    METRIC_REGISTRY,
    analyze_account_metrics,
)


def make_account_data_view(
    *,
    account_rows: list[tuple[str, dict[str, int | float]]] | None = None,
) -> AccountDataView:
    snapshots: list[AccountMetricSnapshotView] = []
    for evidence_id, (raw_date, values) in enumerate(account_rows or [], start=1):
        observed_at = date.fromisoformat(raw_date)
        metrics = {
            code: AccountDataMetric(
                metric=code,
                value=value,
                source=DataSourceKind.PLATFORM_EXPORT,
                observations=[AccountDataObservation(
                    metric=code,
                    value=value,
                    source=DataSourceKind.PLATFORM_EXPORT,
                    observed_at=observed_at,
                    confirmed_at=datetime(2026, 8, 5, tzinfo=UTC),
                    evidence_id=evidence_id,
                    evidence_kind="account_metric_snapshot",
                )],
            )
            for code, value in values.items()
        }
        snapshots.append(AccountMetricSnapshotView(stat_date=observed_at, metrics=metrics))
    return AccountDataView(
        coverage={},
        freshness=AccountDataFreshness(
            latest_observed_at=max((item.stat_date for item in snapshots), default=None),
            latest_confirmed_at=(datetime(2026, 8, 5, tzinfo=UTC) if snapshots else None),
            days_since_observed=0 if snapshots else None,
            days_since_confirmed=0 if snapshots else None,
        ),
        conflicts=[],
        content_snapshots=[],
        account_snapshots=snapshots,
        audience=[],
        benchmarks=[],
        evidence_rows=[],
        latest_synced_at=None,
        latest_confirmed_at=(datetime(2026, 8, 5, tzinfo=UTC) if snapshots else None),
        source_summary=[],
    )


def test_metric_registry_freezes_supported_aggregation_and_units() -> None:
    assert METRIC_REGISTRY["play"].aggregation == "sum"
    assert METRIC_REGISTRY["follower_count"].aggregation == "latest"
    assert METRIC_REGISTRY["completion_rate"].unit == "percent"


def test_analysis_refuses_trend_claim_without_previous_period() -> None:
    result = analyze_account_metrics(
        make_account_data_view(account_rows=[("2026-08-01", {"play": 120})]),
        account_id=7,
        days=7,
        comparison="previous_period",
        metric_codes=["play"],
        top_n=5,
        today=date(2026, 8, 5),
    )

    assert result.answerability.status == "partial"
    assert "play:trend" in result.answerability.unsupported_claims
    assert result.facts[0].direction == "unavailable"
```

- [ ] **Step 2: Run the tests and verify the expected import failure**

Run: `cd backend; uv run pytest tests/test_account_metric_analysis.py -q`

Expected: FAIL because `app.services.account_metric_analysis` and its types do not exist.

- [ ] **Step 3: Implement immutable types, registry, evidence hashing, and answerability**

```python
class Aggregation(StrEnum):
    SUM = "sum"
    LATEST = "latest"
    AVERAGE = "average"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    code: str
    label: str
    unit: Literal["count", "seconds", "percent"]
    aggregation: Aggregation
    minimum_samples: int


class Answerability(BaseModel):
    status: Literal["sufficient", "partial", "insufficient"]
    confidence: Decimal = Field(ge=0, le=1)
    supported_claims: list[str]
    unsupported_claims: list[str]
    missing_metrics: list[str]
    missing_periods: list[DateRange]
    reasons: list[str]
```

Use a module-level immutable mapping for the existing content/account metrics. Normalize evidence as sorted JSON with `ensure_ascii=False` and compact separators, then calculate `sha256` so the same evidence produces the same hash across retries.

- [ ] **Step 4: Add comparison, direction, sample, and empty-data cases**

```python
def test_equal_length_period_comparison_is_deterministic() -> None:
    result = analyze_account_metrics(
        make_account_data_view(account_rows=[
            ("2026-07-23", {"play": 100}),
            ("2026-07-30", {"play": 150}),
        ]),
        account_id=7,
        days=7,
        comparison="previous_period",
        metric_codes=["play"],
        top_n=5,
        today=date(2026, 8, 5),
    )
    fact = result.facts[0]
    assert fact.current_value == 150
    assert fact.previous_value == 100
    assert fact.absolute_change == 50
    assert fact.relative_change == 0.5
    assert fact.direction == "up"


def test_empty_confirmed_view_is_insufficient() -> None:
    result = analyze_account_metrics(
        make_account_data_view(), account_id=7, days=30,
        comparison="previous_period", metric_codes=["play"], top_n=5,
        today=date(2026, 8, 5),
    )
    assert result.answerability.status == "insufficient"
    assert result.facts == []
```

Run: `cd backend; uv run pytest tests/test_account_metric_analysis.py -q`

Expected: PASS, including stable hashes, current/previous windows, missing denominator, stale data, conflict disclosure, sample thresholds, and content Top/Bottom tests.

- [ ] **Step 5: Commit the deterministic domain**

```powershell
git add backend/app/services/account_metric_analysis.py backend/tests/test_account_metric_analysis.py
git commit -m "feat: add deterministic account metric analysis"
```

---

### Task 2: Register the read-only `account.metrics_analysis` Tool

**Files:**
- Modify: `backend/app/orchestrator/runtime_tools.py`
- Modify: `backend/tests/test_runtime_tools.py`

**Interfaces:**
- Consumes: `analyze_account_metrics` and `AccountMetricsAnalysisResult` from Task 1; authenticated `ToolExecutionContext` and `AccountDataViewService`.
- Produces: `AccountMetricsAnalysisParams` and registered Tool code `account.metrics_analysis` returning `AccountMetricsAnalysisResult.model_dump(mode="json")`.

- [ ] **Step 1: Write failing Tool registration and scope tests**

```python
async def test_metrics_analysis_tool_uses_authenticated_account_only(session, admin, account) -> None:
    adapter = build_runtime_tool_adapter()
    outcome = await adapter.execute(
        "account.metrics_analysis",
        {
            "days": 30,
            "comparison": "previous_period",
            "metric_codes": ["play", "follower_delta"],
            "top_n": 5,
        },
        tool_context(session=session, user=admin, account_id=account.id),
    )
    assert outcome.result["account_id"] == account.id
    assert outcome.result["query_window"]["days"] == 30


def test_metrics_analysis_tool_is_read_phase() -> None:
    assert runtime_tool_phase("account.metrics_analysis") == "read"
```

Add a second account with stronger metrics and assert none of its source IDs or values appears in the result. Add a preview-only import batch and assert it does not affect facts.

- [ ] **Step 2: Run the focused tests and verify the unknown-tool failure**

Run: `cd backend; uv run pytest tests/test_runtime_tools.py -k "metrics_analysis" -q`

Expected: FAIL with `KeyError` or unknown Tool because `account.metrics_analysis` is not registered.

- [ ] **Step 3: Implement strict params and handler**

```python
class AccountMetricsAnalysisParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=30, ge=1, le=90)
    comparison: Literal["previous_period", "none"] = "previous_period"
    metric_codes: list[str] = Field(default_factory=list, max_length=12)
    top_n: int = Field(default=5, ge=1, le=20)


async def _account_metrics_analysis(
    params: AccountMetricsAnalysisParams,
    context: ToolExecutionContext,
) -> dict[str, Any]:
    if context.account_id is None:
        raise PermissionError("selected account is required")
    account = await require_account_access(context.session, context.user, context.account_id)
    period_end = date.today()
    comparison_days = params.days if params.comparison == "previous_period" else 0
    view = await AccountDataViewService(context.session).load(
        account,
        period_end - timedelta(days=params.days + comparison_days - 1),
        period_end,
    )
    return analyze_account_metrics(
        view,
        account_id=account.id,
        days=params.days,
        comparison=params.comparison,
        metric_codes=params.metric_codes,
        top_n=params.top_n,
        today=period_end,
    ).model_dump(mode="json")
```

Register it as `side_effect_level="read"`, `scope="account"`, and allow only `ADMIN` and `USER`. Add the business description “分析当前账号已确认数据的趋势、对比、异常与作品表现”.

- [ ] **Step 4: Verify Tool, legacy Tool, lint, and type safety**

Run:

```powershell
cd backend
uv run pytest tests/test_runtime_tools.py tests/test_account_metric_analysis.py -q
uv run ruff check app/orchestrator/runtime_tools.py app/services/account_metric_analysis.py tests/test_runtime_tools.py tests/test_account_metric_analysis.py
```

Expected: all tests and Ruff checks PASS; existing `account.data_context` and `account.metrics_summary` tests remain green.

- [ ] **Step 5: Commit the Tool boundary**

```powershell
git add backend/app/orchestrator/runtime_tools.py backend/tests/test_runtime_tools.py
git commit -m "feat: expose scoped account analysis tool"
```

---

### Task 3: Add the typed `account_data_analysis` Skill contract and catalog

**Files:**
- Create: `backend/app/orchestrator/skills/account_data_analysis.py`
- Modify: `backend/app/orchestrator/skills/registry.py`
- Modify: `backend/app/orchestrator/skills/public_catalog.py`
- Modify: `backend/tests/test_skill_registry.py`
- Modify: `backend/tests/test_skills_api.py`

**Interfaces:**
- Consumes: the Tool code from Task 2 and existing `SkillDefinition`/public catalog policies.
- Produces: `AccountDataAnalysisInput`, `Recommendation`, `AccountDataAnalysisAnswer`, and `ACCOUNT_DATA_ANALYSIS_SKILL` version 1.

- [ ] **Step 1: Write failing contract and catalog tests**

```python
def test_account_data_analysis_is_a_public_douyin_skill() -> None:
    definition = skill_registry.get("account_data_analysis")
    assert definition.version == 1
    assert definition.tool_codes == ("account.metrics_analysis",)
    assert definition.expert_codes == ("06-operator",)
    assert definition.critic_policy == "required"
    assert definition.artifact_type == "account_analysis_answer"


def test_account_data_analysis_input_is_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        AccountDataAnalysisInput(question="why", days=91, extra_key=True)
```

Update the expected production registry set to contain `account_data_analysis`, and assert `/skills?platform=douyin` returns its user-facing name and availability.

- [ ] **Step 2: Run the tests and verify the missing Skill failure**

Run: `cd backend; uv run pytest tests/test_skill_registry.py tests/test_skills_api.py -q`

Expected: FAIL because `account_data_analysis` is absent.

- [ ] **Step 3: Implement strict input and output models**

```python
class AccountDataAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=1000)
    days: int = Field(default=30, ge=1, le=90)
    comparison: Literal["auto", "previous_period", "none"] = "auto"
    requested_metrics: list[str] = Field(default_factory=list, max_length=12)
    top_n: int = Field(default=5, ge=1, le=20)


class Recommendation(BaseModel):
    action: str
    rationale: str
    validation_metric: str
    observation_days: int = Field(ge=1, le=30)


class AccountDataAnalysisCriticOutcome(BaseModel):
    passed: bool
    score: int = Field(ge=0, le=100)
    iterations: int = Field(ge=1, le=2)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AccountDataAnalysisAnswer(BaseModel):
    artifact_type: Literal["account_analysis_answer"] = "account_analysis_answer"
    account_id: int = Field(gt=0)
    question: str
    answerability: Answerability
    conclusion: str
    key_facts: list[AnalysisFact]
    interpretation: list[str]
    recommendations: list[Recommendation]
    data_limits: list[str]
    next_action: str
    evidence_refs: list[BusinessEvidenceRef]
    participating_experts: list[str]
    critic: AccountDataAnalysisCriticOutcome
```

Define the Skill with `expert_codes=("06-operator",)`, `tool_codes=("account.metrics_analysis",)`, `critic_policy="required"`, `risk_level="low"`, `approval_policy="none"`, and `artifact_type="account_analysis_answer"`.

- [ ] **Step 4: Register and expose the capability**

Add `ACCOUNT_DATA_ANALYSIS_SKILL` to `skill_registry` and a `PublicSkillPolicy` using the user-facing name “账号数据分析”, description “根据已确认导入的数据回答趋势、对比、异常和作品表现问题”, and an available state whenever an account is selected.

Run: `cd backend; uv run pytest tests/test_skill_registry.py tests/test_skills_api.py -q`

Expected: PASS with exactly one new public Skill and no change to unrelated platform catalogs.

- [ ] **Step 5: Commit the Skill contract**

```powershell
git add backend/app/orchestrator/skills/account_data_analysis.py backend/app/orchestrator/skills/registry.py backend/app/orchestrator/skills/public_catalog.py backend/tests/test_skill_registry.py backend/tests/test_skills_api.py
git commit -m "feat: define account data analysis skill"
```

---

### Task 4: Route analytical questions without slowing simple data queries

**Files:**
- Modify: `backend/app/orchestrator/capability_router.py`
- Modify: `backend/app/orchestrator/brain_intelligence.py`
- Modify: `backend/tests/test_capability_router.py`
- Modify: `backend/tests/test_brain_intelligence.py`

**Interfaces:**
- Consumes: `account_data_analysis` from Task 3 and existing route modes `query`, `skill`, and `clarify`.
- Produces: deterministic route classification for data-presence, analytical, and full-inspection requests; includes `question`, `days`, `comparison`, `requested_metrics`, and `top_n` in trusted structured input.

- [ ] **Step 1: Write failing route-separation tests**

```python
@pytest.mark.parametrize("message", [
    "我现在账号有数据吗？",
    "数据更新到哪一天？",
    "现在有哪些指标？",
])
def test_presence_questions_keep_the_fast_query_route(message, account_context) -> None:
    route = route_capability(message, context=account_context)
    assert route.mode == "query"
    assert route.skill_code is None


@pytest.mark.parametrize("message", [
    "最近30天账号表现怎么样？",
    "播放量从什么时候开始下降？",
    "哪个指标变化最大？",
    "表现最差的5条作品是什么？",
])
def test_analysis_questions_route_to_typed_analysis_skill(message, account_context) -> None:
    route = route_capability(message, context=account_context)
    assert route.mode == "skill"
    assert route.skill_code == "account_data_analysis"


def test_explicit_one_click_inspection_stays_account_inspection(account_context) -> None:
    route = route_capability("给我做一次一键账号体检", context=account_context)
    assert route.skill_code == "account_inspection"
```

- [ ] **Step 2: Run focused router tests and verify the current misroutes**

Run: `cd backend; uv run pytest tests/test_capability_router.py tests/test_brain_intelligence.py -q`

Expected: FAIL because analytical messages currently use `account_data_query`, `performance_review`, or a generic model route.

- [ ] **Step 3: Implement ordered deterministic patterns and structured extraction**

Add ordered detection before migrated operation patterns:

```python
if _is_data_presence_query(normalized):
    return _query_route("deterministic_data_availability_query")
if _is_account_data_analysis(normalized):
    return _published_skill_route(
        skill_code="account_data_analysis",
        platform=platform,
        registry=registry,
        has_account=has_account,
    )
if _is_account_inspection(normalized):
    return _account_inspection_route(
        platform=platform,
        registry=registry,
        has_account=has_account,
    )
```

Use explicit Chinese metric aliases mapped to registered codes, parse `近 N 天` within 1-90, parse `最差/最好 N 条` within 1-20, and default `comparison="auto"`. Do not use an LLM for messages matching these deterministic patterns.

- [ ] **Step 4: Verify route ordering, unsupported metrics, and no-account behavior**

Add assertions that “分析最近30天播放量，但不要生成长期策略” routes to only `account_data_analysis`; “分析行业平均播放量” returns a clarification/unsupported route because no industry benchmark exists; and missing account context produces a single actionable clarification rather than a 409/retry loop.

Run: `cd backend; uv run pytest tests/test_capability_router.py tests/test_brain_intelligence.py -q`

Expected: PASS with deterministic analysis routes and unchanged greeting/capability/inspection routes.

- [ ] **Step 5: Commit routing**

```powershell
git add backend/app/orchestrator/capability_router.py backend/app/orchestrator/brain_intelligence.py backend/tests/test_capability_router.py backend/tests/test_brain_intelligence.py
git commit -m "feat: route account analysis questions deterministically"
```

---

### Task 5: Execute the Skill with grounding and bounded expert work

**Files:**
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/orchestrator/ai_coo_critic.py`
- Create: `backend/tests/test_account_data_analysis_skill.py`

**Interfaces:**
- Consumes: Skill contract from Task 3, Tool result from Task 2, `AgentHarness`, persisted ToolCall/AgentInvocation, existing Critic, artifact projection, and WorkTurn progress events.
- Produces: `_execute_account_data_analysis(session, user, thread, turn, run, task, content, skill_run, scope, definition, frozen_input, lease_owner)`, `validate_account_analysis_grounding(answer, tool_result)`, terminally consistent SkillRun/AgentRun/Turn states, and one `account_analysis_answer` artifact.

- [ ] **Step 1: Write failing insufficient-data and grounded-success tests**

```python
async def test_insufficient_data_finishes_without_invoking_expert(runtime_case) -> None:
    runtime_case.tool_result["answerability"]["status"] = "insufficient"
    result = await runtime_case.run(skill_code="account_data_analysis")
    assert runtime_case.harness.calls == []
    assert result.report["answerability"]["status"] == "insufficient"
    assert result.report["recommendations"] == []
    assert result.report["participating_experts"] == []


async def test_grounded_analysis_uses_operator_once_and_preserves_tool_facts(runtime_case) -> None:
    result = await runtime_case.run(skill_code="account_data_analysis")
    assert runtime_case.harness.calls == [AgentCode.OPERATOR]
    assert result.report["key_facts"] == runtime_case.tool_result["facts"]
    assert result.report["evidence_refs"] == runtime_case.tool_result["evidence_refs"]
    assert result.report["participating_experts"] == ["06-operator"]
```

Add tests for modified numbers, reversed direction, invented evidence, unsupported causal language, one critic redo, expert failure recovery, and terminal state consistency.

- [ ] **Step 2: Run the new Skill tests and verify the missing executor failure**

Run: `cd backend; uv run pytest tests/test_account_data_analysis_skill.py -q`

Expected: FAIL because the runtime has no dedicated analysis executor or grounding validator.

- [ ] **Step 3: Implement the bounded execution path**

Dispatch `account_data_analysis` before the generic Skill branch:

```python
if definition.code == "account_data_analysis":
    return await self._execute_account_data_analysis(
        session=session,
        user=user,
        thread=thread,
        turn=turn,
        run=run,
        task=task,
        content=content,
        skill_run=skill_run,
        scope=runtime_scope,
        definition=definition,
        frozen_input=dict(skill_run.input_snapshot or {}),
        lease_owner=lease_owner,
    )
```

Execution order:

1. emit “正在确认数据范围”;
2. execute/persist `account.metrics_analysis`;
3. emit the actual observed period and data-quality summary;
4. if `insufficient`, build a deterministic answer without expert invocation;
5. otherwise call only `06-operator` with facts, answerability, limits, question, and a prohibition on altering deterministic fields;
6. replace expert-supplied facts/evidence with Tool-owned facts/evidence before validation;
7. reject interpretation statements whose numeric tokens or metric direction disagree with facts;
8. run Critic once, allow one expert redo only for expression/recommendation quality;
9. persist one artifact and complete all terminal states in one transaction boundary.

- [ ] **Step 4: Verify focused runtime and regression suites**

Run:

```powershell
cd backend
uv run pytest tests/test_account_data_analysis_skill.py tests/test_account_inspection_skill.py tests/test_ai_coo_critic.py tests/test_skill_quality_recovery.py -q
uv run ruff check app/orchestrator/skill_runtime.py app/orchestrator/ai_coo_critic.py tests/test_account_data_analysis_skill.py
```

Expected: PASS; account inspection still invokes its original three experts, analysis invokes only operator, and insufficient data invokes none.

- [ ] **Step 5: Commit grounded Skill execution**

```powershell
git add backend/app/orchestrator/skill_runtime.py backend/app/orchestrator/ai_coo_critic.py backend/tests/test_account_data_analysis_skill.py
git commit -m "feat: execute grounded account data analysis"
```

---

### Task 6: Present the answer clearly inside the existing V4 WorkTurn

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/brain/deliverablePresentation.ts`
- Modify: `frontend/src/components/brain/deliverablePresentation.test.ts`
- Modify: `frontend/src/components/brain/ArtifactCard.tsx`
- Modify: `frontend/src/components/brain/ArtifactCard.test.tsx`
- Modify: `frontend/src/components/brain/WorkTurnCard.tsx`
- Modify: `frontend/src/components/brain/WorkTurnCard.test.tsx`
- Modify: `frontend/src/components/brain/ProcessDisclosure.tsx`
- Modify: `frontend/src/components/brain/TurnStream.test.tsx`

**Interfaces:**
- Consumes: `account_analysis_answer` artifact and V4 WorkTurn projections.
- Produces: one user-facing answer containing conclusion, key facts, interpretation, recommendations, limits, next action, aggregated evidence, participating expert, and default-collapsed technical details.

- [ ] **Step 1: Write failing business-presentation tests**

```tsx
it("presents an account analysis as a direct answer, not an abstract result", () => {
  render(<ArtifactCard artifact={accountAnalysisArtifact} />);
  expect(screen.getByRole("heading", { name: "账号数据分析" })).toBeInTheDocument();
  expect(screen.getByText("播放量较上一周期下降 28%" )).toBeInTheDocument();
  expect(screen.getByText("下一步建议")).toBeInTheDocument();
  expect(screen.queryByText("采用成果")).not.toBeInTheDocument();
});

it("keeps raw source ids and hashes inside technical details", () => {
  render(<ArtifactCard artifact={accountAnalysisArtifact} />);
  expect(screen.queryByText(/content_hash/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("查看分析依据"));
  expect(screen.getByText("已核验 2 类指标、14 条数据记录")).toBeInTheDocument();
  expect(screen.queryByText(/sha256/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run focused frontend tests and verify unknown-artifact behavior**

Run:

```powershell
cd frontend
npm test -- deliverablePresentation.test.ts ArtifactCard.test.tsx WorkTurnCard.test.tsx TurnStream.test.tsx
```

Expected: FAIL because `account_analysis_answer` lacks types and dedicated presentation.

- [ ] **Step 3: Add strict frontend types and presentation copy**

```typescript
export interface AccountAnalysisRecommendation {
  action: string;
  rationale: string;
  validation_metric: string;
  observation_days: number;
}

export interface AccountAnalysisAnswerPayload {
  artifact_type: "account_analysis_answer";
  question: string;
  conclusion: string;
  key_facts: AccountAnalysisFact[];
  interpretation: string[];
  recommendations: AccountAnalysisRecommendation[];
  data_limits: string[];
  next_action: string;
}
```

Map the artifact title to “账号数据分析”. Use “查看分析依据” for business evidence and retain “技术详情” only for Run/Tool/source IDs and hashes. The primary action is a concrete next action such as “继续分析作品表现” or “前往补充数据”, never “采用成果”.

- [ ] **Step 4: Render inside the existing card and verify UI regression**

Use semantic sections within the current `ArtifactCard`; do not add a new message bubble or result stream. Aggregate evidence by metric and period before display. Keep participating expert inside `ProcessDisclosure`. Preserve the same WorkTurn geometry before and after completion.

Run:

```powershell
cd frontend
npm test -- deliverablePresentation.test.ts ArtifactCard.test.tsx WorkTurnCard.test.tsx TurnStream.test.tsx
npm run lint
npm run build
```

Expected: all focused tests, lint, type checking, and Vite build PASS.

- [ ] **Step 5: Commit V4 presentation**

```powershell
git add frontend/src/types.ts frontend/src/components/brain/deliverablePresentation.ts frontend/src/components/brain/deliverablePresentation.test.ts frontend/src/components/brain/ArtifactCard.tsx frontend/src/components/brain/ArtifactCard.test.tsx frontend/src/components/brain/WorkTurnCard.tsx frontend/src/components/brain/WorkTurnCard.test.tsx frontend/src/components/brain/ProcessDisclosure.tsx frontend/src/components/brain/TurnStream.test.tsx
git commit -m "feat: present account analysis in work turns"
```

---

### Task 7: End-to-end verification, documentation, and release readiness

**Files:**
- Modify: `frontend/e2e/main-agent-v2.spec.ts`
- Create: `backend/tests/test_account_analysis_user_journeys.py`
- Modify: `docs/superpowers/specs/2026-08-05-evidence-driven-account-analysis-design.md`

**Interfaces:**
- Consumes: completed backend Tool/Skill/router and frontend WorkTurn presentation.
- Produces: eight acceptance journeys, account-isolation/recovery coverage, verified design status, and a release-ready branch without deployment.

- [ ] **Step 1: Add failing user-journey and browser tests**

Backend parameterized journeys:

```python
@pytest.mark.parametrize(("message", "expected_mode", "expected_skill"), [
    ("我现在账号有数据吗？", "query", None),
    ("最近30天账号表现怎么样？", "skill", "account_data_analysis"),
    ("播放量从什么时候开始下降？", "skill", "account_data_analysis"),
    ("哪个指标变化最大？", "skill", "account_data_analysis"),
    ("表现最差的5条作品是什么？", "skill", "account_data_analysis"),
    ("点赞下降但分享上涨说明什么？", "skill", "account_data_analysis"),
    ("目前的数据够不够判断留存问题？", "skill", "account_data_analysis"),
    ("只分析现状，不生成30天策略。", "skill", "account_data_analysis"),
])
def test_account_analysis_user_journeys(
    message: str,
    expected_mode: str,
    expected_skill: str | None,
) -> None:
    route = route_deterministic_request(
        message,
        platform="douyin",
        registry=skill_registry,
        has_account=True,
    )
    assert route is not None
    assert route.mode.value == expected_mode
    assert route.skill_code == expected_skill
```

Playwright must assert optimistic placement, live progress, final in-place replacement, evidence disclosure, no “采用成果”, refresh recovery without duplicates, and switching accounts without stale content.

- [ ] **Step 2: Run journey tests and verify any missing integration**

Run:

```powershell
cd backend
uv run pytest tests/test_account_analysis_user_journeys.py -q
cd ..\frontend
npm run test:e2e -- main-agent-v2.spec.ts
```

Expected: any missing API projection or browser behavior fails before final integration code is added.

- [ ] **Step 3: Wire the final existing-contract integration**

Keep the persisted `Deliverable.type` as `DeliverableType.REVIEW_REPORT` and distinguish the business payload with `payload["artifact_type"] == "account_analysis_answer"`, matching the existing account-inspection pattern. In `skill_runtime.py`, project Tool/Skill progress through existing Turn events with stable keys:

```python
await self._emit_progress(
    session,
    scope=scope,
    step_key="account-analysis:metrics",
    status="completed",
    message="已完成账号指标计算",
)
await self._emit_progress(
    session,
    scope=scope,
    step_key="account-analysis:grounding",
    status="completed",
    message="已核对结论与数据依据",
)
```

In `TurnStream.tsx`, continue deriving evidence through `businessEvidence(turn)` and include the new projection summary without adding another renderer:

```typescript
if (projection.skill_code === "account_data_analysis" && projection.evidence_ids?.length) {
  evidence.push(`分析依据：${projection.evidence_ids.length} 项`);
}
```

The API continues using the existing conversation/Skill endpoints; no new endpoint or database enum is added.

Update the design status to “已实现，等待本地合并与生产验收” only after all automated checks pass, and append the exact verification commands and counts.

- [ ] **Step 4: Run the full verification matrix**

Run:

```powershell
cd backend
uv run pytest -q
uv run ruff check app tests
cd ..\frontend
npm test
npm run lint
npm run build
npm run check:main-agent-bundle
npm run perf:check
npm run test:e2e -- main-agent-v2.spec.ts
git diff --check
git status --short
```

Expected: backend and frontend suites PASS, Ruff/ESLint/type checking/build/bundle/performance gates PASS, Playwright account-analysis/V4 journeys PASS, `git diff --check` emits no errors, and only the intended documentation update remains before the final commit.

- [ ] **Step 5: Commit verification evidence**

```powershell
git add frontend/e2e/main-agent-v2.spec.ts backend/tests/test_account_analysis_user_journeys.py docs/superpowers/specs/2026-08-05-evidence-driven-account-analysis-design.md
git commit -m "test: verify evidence-driven account analysis"
```

After this commit, invoke `superpowers:finishing-a-development-branch`, rerun the full suite on the exact branch being integrated, and present the local merge / pull request / keep-branch options. Do not deploy until the user chooses integration and the merged result passes the same verification matrix.
