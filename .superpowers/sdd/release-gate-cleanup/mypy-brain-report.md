# Mypy Brain Batch C Report

## Scope

- Owned production files:
  - `backend/app/orchestrator/brain_runtime.py`
  - `backend/app/api/brain.py`
  - `backend/app/orchestrator/runtime_tools.py`
  - `backend/app/orchestrator/brain_intelligence.py`
  - `backend/app/services/turn_execution.py`
  - `backend/app/orchestrator/ai_coo_runtime.py`
- Owned test files: none

## Execution Boundaries Reviewed

- `backend/app/orchestrator/brain_runtime.py`
  - LangGraph entry/resume execution for routed turns, permission resumes, observation refresh, and runtime state propagation.
- `backend/app/api/brain.py`
  - Brain task draft/message API orchestration, brief binding resolution, regeneration audit path, and runtime response assembly.
- `backend/app/orchestrator/runtime_tools.py`
  - Main-agent runtime tool contract bridge between validated params and deterministic account data views.
- `backend/app/orchestrator/brain_intelligence.py`
  - Intent classification and next-step/strategy model calls that must execute with a real DB-backed LLM routing session.
- `backend/app/services/turn_execution.py`
  - Failure-close paths that reload durable ownership after rollback.
- `backend/app/orchestrator/ai_coo_runtime.py`
  - COO strategy/task-plan synthesis from selected expert sequences.

## Root Causes

- LangGraph `ainvoke` calls were passing plain `dict` config/input values, so mypy could not match the `RunnableConfig` and `BrainRuntimeState` overloads even though runtime behavior was valid.
- `BrainRuntimeState` encoded some fields as always-present `int`, while actual runtime behavior carries nullable values for `agent_run_id`, account scope, and active client/project resolution.
- API/task orchestration reused weakly typed dictionaries (`bindings`, step contract maps), causing TypedDict and ORM assignment mismatches at update sites.
- Tool registration exposed contravariance issues: `ToolSpec.handler` expects `BaseModel`, while concrete runtime tools accept narrower Pydantic models.
- Rollback recovery code reassigned `None`-capable ORM reloads into previously non-null local variables.
- Strategy/task-plan helper signatures used invariant `list[str]` where the runtime legitimately passes narrower literal-string sequences.

## Smallest Safe Fixes

- `backend/app/orchestrator/brain_runtime.py`
  - Switched graph config to real `RunnableConfig`.
  - Built explicit `BrainRuntimeState` objects for `ainvoke` entry/resume calls.
  - Reflected nullable runtime truth in `BrainRuntimeState` for `agent_run_id`, `account_id`, `active_client_id`, and `active_project_id`.
  - Replaced untyped `**scope` / budget-state merges with local typed state copies.
  - Added local guard before recording deliverable/acceptance IDs because harness results are nullable.
- `backend/app/api/brain.py`
  - Introduced `BriefBindings` TypedDict and narrowed step-contract enrichment.
  - Made casual draft planning use an explicit empty `PlanningDecision`, avoiding optional-planning attribute access.
  - Added explicit invariants before mutating existing `task.brief` / `task.plan` and before regeneration uses `source_event_id`.
  - Narrowed runtime response and approval-audit list types.
- `backend/app/orchestrator/runtime_tools.py`
  - Normalized aggregated metric timestamps before ISO conversion.
  - Forced numeric sum to `float` before `.is_integer()`.
  - Added typed handler adapters so `ToolSpec` receives the framework-level `BaseModel` contract without weakening tool param models.
- `backend/app/orchestrator/brain_intelligence.py`
  - Narrowed explicit skill routing return path.
  - Broadened capability input to `Sequence[...]`.
  - Added explicit runtime-session requirement before LLM gateway calls.
- `backend/app/services/turn_execution.py`
  - Split rollback reload variables from non-null happy-path locals.
- `backend/app/orchestrator/ai_coo_runtime.py`
  - Relaxed task-plan builder input from `list[str]` to `Sequence[str]`.

## Mypy Counts

- Owned-file baseline from task brief: `51` errors
  - `brain_runtime.py` 27
  - `api/brain.py` 10
  - `runtime_tools.py` 6
  - `brain_intelligence.py` 2
  - `turn_execution.py` 5
  - `ai_coo_runtime.py` 1
- Owned-file final: `0` errors
- Current scoped mypy command over these 6 files still surfaces `23` errors in non-owned imported modules:
  - `app/integrations/douyin.py`
  - `app/llm/gateway.py`
  - `app/agents/video.py`
  - `app/orchestrator/engine.py`
  - `app/orchestrator/agent_harness.py`
  - `app/orchestrator/skill_runtime.py`

## Validation Run

- Scoped mypy on owned files: target files clean; overall command still red only because of the 23 external import-chain errors above.
- Pytest:
  - `tests/test_brain_api.py`
  - `tests/test_brain_react_loop.py`
  - `tests/test_brain_runtime_context.py`
  - `tests/test_runtime_tools.py`
  - `tests/test_turn_execution.py`
  - `tests/test_ai_coo_runtime.py`
  - Result: `112 passed`
- Scoped Ruff:
  - `ruff check app/orchestrator/brain_runtime.py app/api/brain.py app/orchestrator/runtime_tools.py app/orchestrator/brain_intelligence.py app/services/turn_execution.py app/orchestrator/ai_coo_runtime.py`
  - Result: clean
- Diff check:
  - `git diff --check -- [owned files]`
  - Result: clean

## Residual Concerns

- No LangGraph typing blocker remains inside owned files.
- The repo-level scoped mypy command cannot exit clean until the 23 external import-chain errors are fixed by their owners.
- `pytest` emitted existing JWT key-length warnings in `test_brain_api.py`; unrelated to this patch.
