# Task 2 report: router and answer model profiles

## RED evidence

Before implementation, `cd backend && uv run pytest tests/test_turn_intelligence.py tests/test_model_infrastructure.py -q` produced three expected failures:

- Classification still sent the model request with `00-decision`, so the routing-profile test received the answer fixture rather than valid route JSON.
- `resolve_route_targets(..., "00-router")` returned the settings default `deepseek-chat` rather than the stored `00-decision` model `deepseek-reasoner` and its fallback.
- The required `20260730_0100_main_agent_router_profile` migration module did not yet exist.

## GREEN implementation

- Added internal workload code `AgentCode.ROUTER = "00-router"`.
- `BrainIntelligence.classify_turn` now passes `00-router` to structured model calls; `answer_turn` and other main-agent structured work retain `00-decision`.
- `resolve_route_targets` explicitly reuses the organization’s existing `00-decision` `ModelConfig` if `00-router` is absent. Only when neither config exists does the existing settings-default behavior remain.
- Seed data now creates `00-decision` and a lightweight `00-router` profile with primary model `deepseek-v4-flash`.
- Added the `20260730_0100` data migration. It creates one router profile per organization, copies primary/fallback provider IDs, fallback model, and routing parameters from `00-decision`, sets the router primary model to Flash, and is repeat-safe through `NOT EXISTS`.
- `00-router` was intentionally not added to the public expert catalog or model-infrastructure agent list, so it cannot be listed or invoked as a user-facing expert.

## Verification

- `cd backend && uv run pytest tests/test_turn_intelligence.py tests/test_model_infrastructure.py -q` — `13 passed`.
- `cd backend && uv run pytest tests/test_migrations.py tests/test_agents_api.py tests/test_turn_intelligence.py tests/test_model_infrastructure.py -q` — `44 passed` (one pre-existing Alembic configuration deprecation warning); public-agent tests still expose exactly nine agents.
- `cd backend && uv run python -m alembic heads` — `20260730_0100 (head)`.
- `cd backend && uv run ruff check app/models/enums.py app/orchestrator/brain_intelligence.py app/services/model_infrastructure.py app/seed.py tests/test_turn_intelligence.py tests/test_model_infrastructure.py` — passed.
- `git diff --check` — passed.

## Risks / follow-up

- The migration-head contract in `backend/tests/test_migrations.py` was updated to `20260730_0100` under explicit task-owner authorization. This is a required migration-contract update, not a scope expansion.
- A provider selected for `00-router` must support `deepseek-v4-flash`; the migration correctly preserves the main-agent provider association as requested, but provider model availability remains an operations configuration concern.

## Fix round 1: sparse router overlay and migration safety

### RED evidence

Before the fix, `cd backend && uv run pytest tests/test_model_infrastructure.py tests/test_turn_intelligence.py tests/test_agents_api.py tests/test_migrations.py -q` produced the expected three failures:

- A seeded-style sparse `00-router` profile resolved its Flash model with no provider instead of inheriting the provider-backed `00-decision` route, and it lost the decision fallback and route options.
- The migration downgrade removed both migrated and pre-existing `00-router` rows.
- `GET /agents/00-router` returned `404`, proving the value was accepted by the public `AgentCode` route schema before the catalog lookup rejected it.

### GREEN implementation and verification

- Replaced the public enum member with the internal `ROUTER_AGENT_CODE` constant. Classification, seed data, and the migration use this workload code without exposing it in the public expert schema.
- Router resolution now overlays a present but sparse router profile onto `00-decision`: the router’s own Flash primary model is retained, absent provider/fallback fields inherit from decision, and decision routing options are overlaid by explicit router options.
- The migration still leaves an existing router profile unchanged during upgrade. Its downgrade is now intentionally a data-preserving no-op: older code ignores the internal row, while deletion would risk destroying a pre-existing configuration that cannot be distinguished from a migrated one.
- `cd backend && uv run pytest tests/test_model_infrastructure.py tests/test_turn_intelligence.py tests/test_agents_api.py tests/test_migrations.py -q` — `46 passed` (one pre-existing Alembic configuration deprecation warning).
- `cd backend && uv run python -m alembic heads` — `20260730_0100 (head)`.
- `cd backend && uv run ruff check app/models/enums.py app/orchestrator/brain_intelligence.py app/services/model_infrastructure.py app/seed.py migrations/versions/20260730_0100_main_agent_router_profile.py tests/test_model_infrastructure.py tests/test_turn_intelligence.py tests/test_agents_api.py tests/test_migrations.py` — passed.
- `git diff --check` — passed.
