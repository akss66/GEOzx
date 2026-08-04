# Weekly Operation Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce five distinct filming-ready scripts and a seven-day manual-publish plan from one evidence read, then create exactly five schedule entries through one final approval.

**Architecture:** Extend the existing operating Skill reports with backwards-compatible batch fields and deterministic quality results. Carry intermediate `PENDING_REVIEW` artifacts through a private, scope-validated lineage context while leaving public artifact rules unchanged. Use the existing locked before-finish approval transaction to approve the final package and create the five idempotent schedule rows.

**Tech Stack:** Python 3.12, Pydantic v2, SQLAlchemy async ORM, pytest/pytest-asyncio, Ruff, Mypy.

## Global Constraints

- Do not modify pending-work projections, database models or migrations, Task 6 ownership, or WebSocket code.
- Preserve standalone single-script behavior and existing public API contracts.
- Intermediate artifacts remain `PENDING_REVIEW`; only private same-parent lineage may consume them.
- Account data and benchmark evidence are read once per operation and referenced by every artifact.
- The only human gate is final `publishing_preparation` approval.
- Final approval creates five manual schedule rows and never calls `platform.content_publish`.
- Quality similarity uses NFKC/lowercase/whitespace-and-punctuation normalization, threshold `0.92`, and a minimum normalized length of 40 characters.

---

### Task 1: Strong batch schemas and deterministic quality

**Files:**
- Create: `backend/app/orchestrator/operation_quality.py`
- Modify: `backend/app/orchestrator/skills/operating_tasks.py`
- Modify: `backend/app/orchestrator/skills/visual_brief_generation.py`
- Modify: `backend/app/orchestrator/skills/content_calendar_planning.py`
- Test: `backend/tests/test_operation_quality.py`
- Test: `backend/tests/test_operating_skills.py`

**Interfaces:**
- Produces `QualityCheck`, `ArtifactQuality`, `TopicPlanItem`, `FilmingScript`, `VisualProductionItem`, `CalendarSlot`, and `WeeklyOperationPackage` Pydantic models.
- Produces `evaluate_topic_quality(...)`, `evaluate_script_quality(...)`, `evaluate_visual_quality(...)`, and `evaluate_calendar_quality(...)`.
- Existing `ScriptGenerationReport.title/hook/scenes` remain the first-script compatibility view.

- [ ] **Step 1: Write failing schema and normalization tests**

Add literal tests proving:

```python
assert normalize_script_text("价格！ Price, A\n") == "价格pricea"
assert copied_five_quality.status == "needs_review"
assert short_distinct_quality.status == "passed"
assert [item.topic_id for item in topic_report.topics] == [
    "topic-01", "topic-02", "topic-03", "topic-04", "topic-05"
]
assert len(script_report.scripts) == 5
```

The copied-script fixture contains five different IDs but identical Chinese `hook + voiceover`. The short-text fixture contains distinct strings shorter than 40 normalized characters.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_operation_quality.py tests/test_operating_skills.py -k "batch_schema or deterministic_quality" -q
```

Expected: failures because the typed models and quality evaluator do not exist.

- [ ] **Step 3: Implement the strong models and quality evaluator**

Use these exact quality fields:

```python
class QualityCheck(BaseModel):
    code: str
    passed: bool
    message: str
    item_ids: list[str] = Field(default_factory=list)

class ArtifactQuality(BaseModel):
    version: Literal["operation-quality/v1"] = "operation-quality/v1"
    status: Literal["passed", "needs_review"]
    score: int = Field(ge=0, le=100)
    threshold: int = Field(default=80, ge=0, le=100)
    checks: list[QualityCheck] = Field(min_length=1)
```

Implement normalization with `unicodedata.normalize("NFKC", value).lower()` and retain only `char.isalnum()`. Compare pairs with exact equality first, then `SequenceMatcher(None, left, right).ratio() >= 0.92` only when both lengths are at least 40. Compute score as the integer percentage of passed checks and set `status="passed"` only when all required checks pass and score is at least the threshold.

- [ ] **Step 4: Implement backwards-compatible report fields**

Add typed batch collections and quality to each report. Standalone script construction emits `scripts=[first_script]` and mirrors that item into legacy fields. Operation mode never pads a missing batch with template content.

- [ ] **Step 5: Verify GREEN and commit the slice**

Run the RED command plus existing standalone script tests, then commit only Task 1 files with:

```text
feat: add typed weekly content quality contracts
```

---

### Task 2: Private pending-artifact lineage

**Files:**
- Create: `backend/app/orchestrator/operation_lineage.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/orchestrator/composite_skill_runtime.py`
- Modify: `backend/app/orchestrator/skills/operating_tasks.py`
- Modify: `backend/app/orchestrator/skills/visual_brief_generation.py`
- Modify: `backend/app/orchestrator/skills/content_calendar_planning.py`
- Test: `backend/tests/test_operation_lineage.py`
- Test: `backend/tests/test_operating_skills.py`

**Interfaces:**
- Produces private `OperationLineageRef(artifact_id, version, source_skill_run_id, parent_skill_run_id)`.
- Produces async `resolve_internal_lineage_artifacts(session, *, refs, expected_parent, expected_source_ids, scope)`.
- Extends `_ServerSkillContext` with serialized lineage refs; this field is never sourced from `CapabilityRequest.structured_input`.

- [ ] **Step 1: Write failing lineage tests**

Create real persisted parent/child/deliverable fixtures. Prove a same-parent completed child with a `PENDING_REVIEW` deliverable resolves, while each of these raises `PermissionError` or `SkillRecoveryConflict`:

```python
client_only_pending_id
cross_account_ref
cross_run_ref
cross_task_ref
stale_version_ref
unrelated_child_ref
missing_parent_ref
```

Also assert the public `_confirmed_source_artifacts` resolver still rejects the same pending artifact.

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_operation_lineage.py -q
```

Expected: failures because internal lineage resolution does not exist and the composite still pauses on pending dependencies.

- [ ] **Step 3: Implement the scope validator**

Resolve all artifacts with one joined query over `Deliverable`, `ContentItem`, and `SkillRun`. Require exact equality for org/thread/turn/run/task/account/content, parent ID, source SkillRun ID, artifact ID/version, child status `completed`, and child output `composite_parent_skill_run_id`. Allow only `PENDING_REVIEW` or `APPROVED` internally. Return payloads without changing statuses.

- [ ] **Step 4: Wire the composite graph**

Pass the dependency artifact IDs in structured input for schema compatibility and pass matching lineage refs in `_ServerSkillContext`. In `_execute_operating_skill`, use internal resolution only when the private refs exactly cover requested dependency IDs; otherwise use the unchanged approved-artifact resolver. Remove the intermediate approval pause from the valid internal lineage path.

- [ ] **Step 5: Verify GREEN and commit the slice**

Run lineage and operation lifecycle tests, assert intermediate deliverables remain `PENDING_REVIEW`, then commit:

```text
feat: consume scoped operation lineage internally
```

---

### Task 3: Five scripts, linked visuals, seven slots, and final package

**Files:**
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/orchestrator/composite_skill_runtime.py`
- Modify: `backend/app/orchestrator/skills/operation_iteration.py`
- Modify: `backend/app/orchestrator/skills/operating_tasks.py`
- Modify: `backend/app/orchestrator/skills/visual_brief_generation.py`
- Modify: `backend/app/orchestrator/skills/content_calendar_planning.py`
- Test: `backend/tests/test_operating_skills.py`
- Test: `backend/tests/test_operation_iteration_skill.py`

**Interfaces:**
- Script child consumes the topic artifact through internal lineage.
- Visual and calendar children consume typed source packages and preserve IDs.
- Publishing preparation consumes all four prior artifacts and emits `WeeklyOperationPackage`.

- [ ] **Step 1: Write failing complete-package and malformed-output tests**

Use a deterministic expert harness returning five literal topics, five distinct scripts, and five visual items. Assert exact mappings and seven literal slot types:

```python
assert [slot.slot_type for slot in calendar.slots] == [
    "publish", "publish", "publish", "publish", "publish",
    "review_buffer", "review_buffer",
]
assert {slot.script_id for slot in calendar.slots if slot.slot_type == "publish"} == {
    "script-01", "script-02", "script-03", "script-04", "script-05"
}
```

Add a malformed harness returning five copied scripts and assert the script child is `needs_review`, downstream children stay pending, and no final approval exists.

- [ ] **Step 2: Verify RED**

Run the two new tests and confirm failures on missing batch fields/current intermediate approval pause.

- [ ] **Step 3: Build reports from specialist output without passing filler**

Parse typed collections from expert output and source artifact payloads. Assign missing topic IDs by stable order only. Never fabricate missing titles, voiceovers, CTA values, or visual items into a passing batch. Persist evidence refs, quality, and participating experts in every deliverable payload.

For calendar dates, calculate the next seven consecutive dates from the persisted child execution date in `Asia/Shanghai`; create five publish slots at 10:00 +08:00 and two buffer slots without script IDs. Persist the concrete dates so recovery does not recalculate them.

- [ ] **Step 4: Assemble the final package and business copy**

The final package records each source artifact ID/version and returns public next steps:

```json
[
  {"code": "start_filming", "label": "按 5 条拍摄稿开始拍摄"},
  {"code": "confirm_manual_schedule", "label": "确认 7 天安排并创建手动发布任务"}
]
```

The waiting response is `已准备 5 条拍摄稿和 7 天安排。确认后将创建 5 条手动发布任务。`

- [ ] **Step 5: Verify GREEN and commit the slice**

Run operating, operation, and artifact payload tests, then commit:

```text
feat: build five-script weekly operation packages
```

---

### Task 4: One locked approval creates exactly five schedule rows

**Files:**
- Modify: `backend/app/services/skill_approvals.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Test: `backend/tests/test_operating_skills.py`
- Test: `backend/tests/test_skill_approvals.py`
- Test: `backend/tests/test_main_agent_worker_contract.py`

**Interfaces:**
- Adds private `_create_manual_schedule_entries_for_package(...) -> tuple[int, ...]` called only from the locked before-finish approval path.
- Stores created schedule IDs in the final child output approval block so API replay returns the durable result.

- [ ] **Step 1: Strengthen Scenario A and write approval RED tests**

Seed real account/content and benchmark import data. Run the exact weekly request to the final gate. Assert intermediate artifact statuses are pending, the final artifact is pending, and there is exactly one waiting approval.

Approve through two concurrent HTTP requests backed by two independent sessions. Literal postconditions:

```python
assert sorted(response.status_code for response in responses) == [200, 200]
assert schedule_count == 5
assert {row.source_artifact_id for row in rows} == {final_artifact.id}
assert {row.source_artifact_version for row in rows} == {final_artifact.version}
assert final_artifact.status is DeliverableStatus.APPROVED
assert platform_publish_call_count == 0
```

- [ ] **Step 2: Verify RED**

Run Scenario A and the concurrent approval test. Expected: the current workflow pauses at intermediate artifacts and final approval creates no schedule rows.

- [ ] **Step 3: Implement the locked schedule effect**

Inside `finalize_skill_finish_approval`, after the existing root lock and scope checks, validate the strong package and five publish slots. Insert the five rows in the same transaction before marking the artifact approved. Use the locked child/tool state as the concurrency serialization point. On replay, return stored schedule IDs; on rejection, insert none. Any invalid slot count, duplicate script binding, wrong source, or non-passing quality raises `SkillApprovalConflict` and leaves the artifact unapproved.

- [ ] **Step 4: Verify GREEN and commit the slice**

Run concurrent approval, rejection, replay, operation lifecycle, and pending artifact public API tests. Commit:

```text
feat: create manual schedules from final operation approval
```

---

### Task 5: Partial revision reuses research and versions downstream packages

**Files:**
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/orchestrator/composite_skill_runtime.py`
- Test: `backend/tests/test_main_agent_worker_contract.py`
- Test: `backend/tests/test_operating_skills.py`
- Test: `backend/tests/test_revision_runtime_integration.py`

**Interfaces:**
- Reuses the persisted topic artifact and Task 8A evidence server context.
- Regenerates script, visual, calendar, and publishing-preparation child SkillRuns with new deliverable versions.

- [ ] **Step 1: Strengthen Scenario B and verify RED**

Execute a complete source operation, submit `第一条不要讲价格`, execute the revision, and assert:

```python
assert revised_topic_ids == original_topic_ids
assert revised_scripts[0].constraints_hit == ["第一条不要讲价格"]
assert revised_script_version == original_script_version + 1
assert old_script_payload == original_payload
assert account_data_context_tool_calls == 1
```

Expected RED: current revision planning does not yet reuse the completed topic/evidence lineage to execute only downstream children.

- [ ] **Step 2: Implement persisted checkpoint reuse**

Seed the revision composite graph with the source topic node marked completed and its artifact/version lineage. Carry the source root's evidence context into the revision root as server-owned data. Build new downstream child runs under the revision run/task scope while retaining source artifact versions in the final package.

- [ ] **Step 3: Verify GREEN and commit the slice**

Run Scenario B, run-revision, checkpoint invalidation, and operation recovery tests. Commit:

```text
feat: regenerate weekly scripts from offer revisions
```

---

### Task 6: Full verification and release review

**Files:**
- Modify only files required to resolve failures caused by Tasks 1-5.

- [ ] **Step 1: Run required test suites**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_main_agent_worker_contract.py tests/test_operation_iteration_skill.py tests/test_operating_skills.py tests/test_operation_quality.py tests/test_operation_lineage.py tests/test_skill_approvals.py tests/test_artifacts_api.py tests/test_run_revisions_service.py tests/test_revision_runtime_integration.py -q
```

- [ ] **Step 2: Run static checks**

```powershell
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app/orchestrator app/services/skill_approvals.py
git diff --check
```

- [ ] **Step 3: Review scope and behavior**

Confirm no Task 8B pending-work/model/migration file, Task 6 file, or WS file is staged. Review for cross-scope lineage, false approval status, duplicate scheduling, external publishing, filler content, and internal terminology in user copy.

- [ ] **Step 4: Commit the final verified adjustments**

Use an imperative commit message describing only the Task 8C closure and report any pre-existing static-check failures separately.
