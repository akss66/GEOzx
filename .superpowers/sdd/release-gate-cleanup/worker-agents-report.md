# Worker / Direct-Agent Release Gate Cleanup

Date: 2026-07-29
Fix commit: `36e9414` (`fix: restore direct agent bundles and worker runtime tests`)

## Scope

- `backend/app/services/agent_workspace.py`
- `backend/tests/test_agent_runs.py`

## RED

### Direct agent production regression

- `tests/test_agents_api.py::test_direct_agent_run_creates_real_artifact_and_pending_acceptance`
- `tests/test_agents_api.py::test_direct_agent_handoff_returns_audited_main_agent_draft`

Failure before fix:

- `load_agent_run()` reconstructed `AgentHarnessResult` with the pre-`output` signature.
- Runtime traceback ended at `TypeError: AgentHarnessResult.__init__() missing 1 required positional argument: 'output'`.

### Worker regression coverage drift

- `tests/test_agent_runs.py::test_worker_executes_queued_agent_run_and_persists_completion`
- `tests/test_agent_runs.py::test_worker_does_not_retry_invalid_model_route_configuration`

Failure before fix:

- Both tests still monkeypatched `app.worker.runtime_graph.start_smart`.
- Production worker start path now calls `start_routed`, so the doubles never intercepted execution.
- The completion test fell through to the real LLM path.
- The invalid-route test no longer proved terminal no-retry behavior at the real runtime boundary.

## GREEN

### Production fix

- `load_agent_run()` now rehydrates the persisted `Deliverable` via `acceptance.deliverable_id`.
- `AgentHarnessResult.output` now comes from `dict(deliverable.payload or {})`, which is the stored direct-agent result rather than synthetic filler data.

### Test fix

- Worker tests now monkeypatch `app.worker.runtime_graph.start_routed`.
- Test doubles assert routed parameters explicitly:
  - completion path expects `TurnExecutionMode.ANSWER`
  - invalid-route path expects `TurnExecutionMode.QUERY`
- The invalid-route test still proves terminal failure without retry by raising the routed boundary error chain.

## Commands

### Targeted failures

```powershell
pytest tests/test_agent_runs.py::test_worker_executes_queued_agent_run_and_persists_completion -vv
pytest tests/test_agent_runs.py::test_worker_does_not_retry_invalid_model_route_configuration -vv
pytest tests/test_agents_api.py::test_direct_agent_run_creates_real_artifact_and_pending_acceptance -vv
pytest tests/test_agents_api.py::test_direct_agent_handoff_returns_audited_main_agent_draft -vv
```

### Related full files

```powershell
pytest tests/test_agent_runs.py -vv
pytest tests/test_agents_api.py -vv
```

### Scoped static checks

```powershell
C:\Users\AKSSINA\.python\Scripts\ruff.exe check app/services/agent_workspace.py tests/test_agent_runs.py
git diff --check -- backend/app/services/agent_workspace.py backend/tests/test_agent_runs.py
```

### Scoped mypy

Attempted:

```powershell
C:\Users\AKSSINA\.python\Scripts\mypy.exe app/services/agent_workspace.py tests/test_agent_runs.py
C:\Users\AKSSINA\.python\python.exe -m mypy --version
```

Result:

- Blocked by local Windows application control policy when importing mypy's compiled `__mypyc` module.
- This was an environment restriction, not a type error in the modified files.

## Result summary

- Direct-agent invoke and handoff tests are green again.
- Worker queue completion and terminal invalid-route tests now cover the real routed boundary.
- No user cost/handoff dirty files were staged or reverted.
