# Task 4: Complete User Lifecycle and Protected Deletion Report

## Status

Complete. The backend now provides administrator-only user CRUD, password
reset, deletion preview, and transactional permanent deletion. Permanent
deletion requires a fresh signed preview, exact target email, and the acting
administrator's secondary password.

## RED

Tests were written before implementation.

Initial lifecycle run:

```text
python -m pytest tests/test_user_deletion_api.py tests/test_users_management_api.py -q
12 failed, 11 passed
```

The failures were the missing reset/preview/permanent endpoints and missing
`app.services.user_deletion` service.

Initial ownership and schema run:

```text
python -m pytest tests/test_identity_governance_models.py tests/test_brain_api.py tests/test_content_workspace_api.py tests/test_matrix_distribution_api.py tests/test_knowledge_api.py tests/test_agents_api.py tests/test_llm_gateway.py -q
7 failed, 3 passed
```

The failures demonstrated `SET NULL` creator FKs, missing authenticated request
context for LLM calls, and absent `created_by_id` writes at BrainTask,
ContentItem, matrix-task, agent-workspace, and LLMCall creation boundaries.

The new migration test initially failed because revision
`20260720_0200_user_deletion_restrict_ownership` did not exist. A later
transaction-focused RED test also proved that successful secondary-password
verification committed too early: an injected deletion failure left credential
state changed instead of rolling the entire operation back.

Final migration review added a downgrade/re-upgrade regression test. It failed
because the SQLite Matrix creator constraint would have been restored with the
PostgreSQL name. The helper now accepts a dialect-specific restored name, so
both engines retain the name expected by the next upgrade.

## GREEN

- Added `POST /users/{id}/reset-password`,
  `POST /users/{id}/deletion-preview`, and
  `DELETE /users/{id}/permanent`; all require an organization administrator.
- Password reset accepts only a replacement password, stores its hash, never
  returns or records plaintext, and invalidates the old login password.
- Added deterministic deletion-impact construction for explicitly owned roots
  and their dependent runtime, tool, approval, deliverable, publishing, cost,
  membership, credential, and related event records.
- Added short-lived JWT previews bound to actor, target, organization,
  operation, expiry, and a deterministic count/content digest. Reuse, expiry,
  target mismatch, organization mismatch, and stale impact are rejected with
  stable business codes.
- Required exact target email and the acting administrator's secondary
  password. Existing cooldown, lock, invalid-password, and input-cleaning
  behavior is preserved without placing secrets in logs, events, or error
  details.
- Kept secondary-password success state, all dependency cleanup, user deletion,
  and the final sanitized receipt in one database transaction and one success
  commit. Injected failures roll everything back.
- Preserved shared and legacy unowned data. Only explicit `created_by_id` roots
  and direct user associations are deleted; reviewer-only shared knowledge is
  sanitized rather than treated as owned.
- Enforced self-action and last-enabled-administrator guards. Active
  administrator rows and the target are locked before the final in-transaction
  impact and invariant checks.
- Changed MatrixDistributionPlan and KnowledgeEntry creator FKs to `RESTRICT`
  and added the corresponding Alembic migration. Direct database deletion is
  covered with SQLite foreign keys enabled; PostgreSQL offline SQL generation
  confirms the named `RESTRICT` constraints.
- Added request-scoped acting-user context and populated `created_by_id` for all
  authenticated BrainTask, ContentItem, MatrixDistributionPlan, KnowledgeEntry,
  and LLMCall creation boundaries. Runtime descendants inherit ownership from
  their root.
- Added Task 1 regression coverage for actual database-level user-delete
  restriction and persisted default `account_scope_mode`.

## Tests

Focused lifecycle, migration, model, and creation-boundary regression after all
review refinements:

```text
python -m pytest tests/test_user_deletion_api.py tests/test_users_management_api.py tests/test_identity_governance_models.py tests/test_migrations.py tests/test_brain_api.py tests/test_content_workspace_api.py tests/test_matrix_distribution_api.py tests/test_knowledge_api.py tests/test_agents_api.py tests/test_llm_gateway.py -q
90 passed, 144 warnings in 44.01s
```

Final backend suite:

```text
python -m pytest -q
256 passed, 313 warnings in 104.40s
```

Additional verification:

```text
python -m ruff check <Task 4 Python files>
All checks passed!

python -m alembic upgrade 20260720_0100:20260720_0200 --sql
python -m alembic downgrade 20260720_0200:20260720_0100 --sql
PostgreSQL upgrade/downgrade SQL generated with the expected constraints

git diff --check
passed
```

## Security Review

- Authorization and tenant binding happen before execution; cross-organization
  actor, target, and token combinations are rejected.
- Preview replay is detected from the operation receipt even after the target
  user no longer exists. Staleness compares row-level content fingerprints, so
  same-count replacements and in-place mutations require a new preview.
- Last-admin protection is checked under row locks inside the deletion
  transaction. The ordinary update path also locks enabled administrators
  before deactivation.
- Cleanup follows FK dependency order and does not weaken constraints. The user
  row is removed only after owned roots, runtime descendants, memberships,
  credentials, explicit reviewer/approver links, and matching events are
  handled.
- Events are streamed because the current event table has no organization
  column and role references may be nested in JSON. Matching is restricted to
  explicit actor/target/creator/reviewer/approver fields and owned entity IDs.
- The sole surviving `user.permanently_deleted` receipt contains exactly
  `actor_id`, `operation_id`, `timestamp`, and categorized `counts`; no name,
  email, content, password, token, or historical detail is retained.

## Key Decisions

- Successful secondary-password verification gained a `commit_on_success=False`
  mode so permanent deletion owns the transaction boundary. Existing callers
  retain their prior default behavior; failed attempts still persist for lockout
  enforcement.
- The preview digest fingerprints persisted rows rather than only counts and
  IDs. This remains deterministic on SQLite and PostgreSQL and detects stale
  state even if a database reuses a primary key.
- Shared records that merely reference the target as a reviewer are sanitized;
  they are not deleted unless they are otherwise an explicitly owned or related
  record.

## Changed Files

- `backend/app/services/user_deletion.py`
- `backend/app/services/user_management.py`
- `backend/app/services/admin_security.py`
- `backend/app/schemas/auth.py`
- `backend/app/api/users.py`
- `backend/app/core/auth.py`
- `backend/app/core/request_context.py`
- `backend/app/llm/gateway.py`
- `backend/app/models/distribution.py`
- `backend/app/models/knowledge.py`
- `backend/app/api/brain.py`
- `backend/app/api/orchestrator.py`
- `backend/app/api/matrix_distribution.py`
- `backend/app/services/agent_workspace.py`
- `backend/app/orchestrator/brain_adapter.py`
- `backend/migrations/versions/20260720_0200_user_deletion_restrict_ownership.py`
- `backend/tests/test_user_deletion_api.py`
- `backend/tests/test_users_management_api.py`
- `backend/tests/test_identity_governance_models.py`
- `backend/tests/test_brain_api.py`
- `backend/tests/test_content_workspace_api.py`
- `backend/tests/test_matrix_distribution_api.py`
- `backend/tests/test_knowledge_api.py`
- `backend/tests/test_agents_api.py`
- `backend/tests/test_llm_gateway.py`
- `backend/tests/test_migrations.py`

## Concerns

- A live PostgreSQL upgrade was not available in this workspace. The migration
  was validated through Alembic PostgreSQL offline SQL generation, while actual
  `RESTRICT` behavior was exercised against SQLite with foreign keys enabled.
- The local test environment overrides the JWT HMAC key with a 30-byte value,
  producing PyJWT's existing recommendation warning. Production configuration
  should use at least 32 bytes for HS256.
- The suite retains the existing Starlette `TestClient` deprecation warning.
