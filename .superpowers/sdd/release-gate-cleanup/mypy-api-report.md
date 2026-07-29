## Mypy Batch D2 API report

### Scope
- `backend/app/api/orchestrator.py`
- `backend/app/api/metrics.py`
- `backend/app/api/model_providers.py`
- `backend/app/api/feedback.py`
- regression test: `backend/tests/test_optimization_feedback.py`

### Before / after
- Scoped mypy before: 13 errors across 4 files
- Scoped mypy after: 0 errors across 4 files

### Execution boundaries and fixes

#### `backend/app/api/orchestrator.py`
- Boundary: publish-readiness material loading and `POST /content-items`
- Issue: SQLAlchemy scalar collections were left as `Sequence[...]` while locals/returns were declared as `list[...]`; `require_content_scope()` also returned `Project | None` / `Account | None`, but the route relied on an `assert` before dereferencing `project.id` and `account.id`.
- Fix: material query results are now concretely materialized as `list[MaterialAsset]`; `create_content_item()` now guards `project is None` and `account is None` with explicit 404s instead of using `assert`.
- Tradeoff: the new 404 guards should be unreachable on the current request contract (`CreateContentItemRequest.project_id` is required), but they make the route fail predictably if the helper contract drifts.

#### `backend/app/api/metrics.py`
- Boundary: `GET /metrics/overview`
- Issue: the base SQL filter tuple narrowed to two items and then grew conditionally; aggregate output was then folded through schemas where `play` / `completion_rate` are typed nullable, so local reductions operated on optional values.
- Fix: switched to `base_filters: list[ColumnElement[bool]]`, compare the enum column against `MetricSource.DEMO`, and coalesce local aggregates before summing/casting.
- Tradeoff: this is a local typing fix only; runtime query semantics stay the same.

#### `backend/app/api/model_providers.py`
- Boundary: provider discovery / model update conflict responses
- Issue: two handlers advertise schema returns but also legitimately return `JSONResponse` for 409 conflict payloads.
- Fix: widened the return annotations to `ModelProviderDiscoveryOut | JSONResponse` and `ModelProviderDetailOut | JSONResponse` so the type contract matches actual FastAPI behavior.
- Tradeoff: no runtime change.

#### `backend/app/api/feedback.py`
- Boundary: optimization suggestion mutation and “send to brain” routes
- Issue: both routes passed `content.project_id` (`int | None`) directly into `require_project_access()`, relying on downstream behavior for a projectless content item. This was a real nullable scope boundary, and `send_to_brain` also forwarded that nullable project into the draft request.
- Fix: added `_require_feedback_scope()` to make the project boundary explicit, keep account-role checks strict when an account is bound, and reuse the resolved non-null `project_id` for the brain draft.
- Tradeoff: projectless optimization suggestions now fail explicitly with 404 at the API boundary instead of relying on lower-layer null handling.

### Validation performed
- `uv run mypy app/api/orchestrator.py app/api/metrics.py app/api/model_providers.py app/api/feedback.py`
- `uv run pytest tests/test_orchestrator.py tests/test_metrics_api.py tests/test_model_providers_api.py tests/test_optimization_feedback.py`
- `uv run ruff check app/api/orchestrator.py app/api/metrics.py app/api/model_providers.py app/api/feedback.py tests/test_optimization_feedback.py`
- `git diff --check -- backend/app/api/orchestrator.py backend/app/api/metrics.py backend/app/api/model_providers.py backend/app/api/feedback.py backend/tests/test_optimization_feedback.py`

### Directly validated outcomes
- Primary success path: all four API modules pass scoped mypy and all related API tests pass.
- Representative failure path: projectless optimization suggestions return 404 via `test_projectless_suggestion_routes_return_404_instead_of_relying_on_null_project_scope`.
- Integration boundary: model-provider conflict routes still return 409 payloads under existing API tests.

### Residual risk / follow-up
- `orchestrator.create_content_item()` now defends impossible helper outputs, but that branch is only integration-tested indirectly; if `require_content_scope()` changes semantics later, a dedicated API test for the 404 guard would make that contract explicit.
- Test warnings are unchanged and pre-existing: JWT test secret length warnings from `pyjwt`.
