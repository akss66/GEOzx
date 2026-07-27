# Douyin Official Publishing Loop Implementation Plan

> **For Codex:** Execute this plan incrementally with tests first. Keep the existing approval ledger, account permissions, OAuth storage, and imported historical data unchanged.

**Goal:** Deliver the first production-safe Douyin publishing loop: an approved publish package becomes an official H5 handoff, the resulting Douyin work is bound back to the originating account, and the platform content identity is available to the data center.

**Architecture:** Add a durable `PlatformPublishJob` ledger between approved publish packages and Douyin. H5 publishing is the user-initiated transport. `share_id` plus the `create_video` callback is the primary identity-return mechanism. Douyin posting-task APIs remain optional capability-gated verification tools and are never treated as the media-upload transport.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, httpx, React, TypeScript, Vitest, pytest.

---

## Task 1: Add the durable publish-job ledger

**Files:**
- Create: `backend/app/models/publishing.py`
- Create: `backend/migrations/versions/20260727_0100_platform_publish_jobs.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/tests/test_migrations.py`
- Test: `backend/tests/test_platform_publish_job_model.py`

**Steps:**
1. Write model tests proving:
   - a job is scoped by `org_id` and `account_id`;
   - `active_client_id` and `active_project_id` are optional frozen execution context;
   - the same `(org_id, idempotency_key)` cannot create two jobs;
   - `share_id`, posting-task identity, callback identity, error details, and retry metadata are nullable durable fields;
   - the default status is `draft`.
2. Run the model test and confirm it fails because the model does not exist.
3. Add `PlatformPublishJob` and string-backed status constants. Store package data as structured JSON while keeping searchable identity and lifecycle columns explicit.
4. Add the additive Alembic migration with `down_revision = "20260723_0200"`.
5. Export the model and update the expected migration head.
6. Run the model and migration tests.

## Task 2: Implement official Douyin H5 publishing primitives

**Files:**
- Modify: `backend/app/integrations/douyin.py`
- Modify: `backend/app/integrations/douyin_capabilities.py`
- Test: `backend/tests/test_douyin_publishing_integration.py`

**Steps:**
1. Write transport-injected tests for:
   - fetching `open/getticket/` with a client token in the `access-token` header;
   - generating the documented MD5 H5 signature from sorted `nonce_str`, `ticket`, and string `timestamp`;
   - creating a share ID with `need_callback=true`;
   - creating an H5 publish schema with exactly one supported media URL;
   - rejecting missing media, multiple media, unsupported visibility, and missing required capability;
   - decoding successful and failed Douyin responses into structured integration errors.
2. Run the tests and confirm they fail.
3. Add the open-ticket cache, share-ID client, H5 signature builder, schema builder, and typed error metadata.
4. Extend the capability declaration with `aweme.share`.
5. Run the publishing integration tests and the existing Douyin integration tests.

## Task 3: Add publish-job service and authorization boundaries

**Files:**
- Create: `backend/app/services/publishing.py`
- Create: `backend/app/schemas/publishing.py`
- Test: `backend/tests/test_publishing_service.py`

**Steps:**
1. Write service tests proving:
   - only accounts in the caller's organization and assigned scope can be used;
   - unbound accounts remain valid execution contexts;
   - optional client/project context must belong to the selected account when supplied;
   - package creation is idempotent;
   - only an approved existing `AgentToolCall` may enter official handoff;
   - direct publish remains disabled;
   - status transitions reject skipping approval, duplicate callbacks, and expired share IDs.
2. Run the tests and confirm they fail.
3. Implement job creation, approval verification, H5 handoff preparation, callback binding, retry, cancellation, and read models.
4. Extract account-token resolution from the API layer into a shared platform-auth service so publishing and sync use one implementation.
5. Run the service tests.

## Task 4: Add publish-job APIs and Douyin callback ingestion

**Files:**
- Create: `backend/app/api/publishing.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/platform_integrations.py`
- Test: `backend/tests/test_publishing_api.py`

**Steps:**
1. Write API tests for:
   - `POST /publish-jobs`;
   - `GET /publish-jobs` and `GET /publish-jobs/{id}`;
   - `POST /publish-jobs/{id}/handoff`;
   - `POST /publish-jobs/{id}/bind`;
   - `POST /publish-jobs/{id}/sync`;
   - `POST /publish-jobs/{id}/retry`;
   - `POST /publish-jobs/{id}/cancel`;
   - `create_video` callback idempotency and tenant isolation.
2. Run the tests and confirm they fail.
3. Add the router and mount it.
4. Return structured error codes and user-safe messages without exposing tokens, signatures, or raw secrets.
5. Reuse the current OAuth callback and webhook boundary without changing existing account authorization behavior.
6. Run the API tests.

## Task 5: Project official work identity into the data center

**Files:**
- Modify: `backend/app/services/publishing.py`
- Modify: `backend/app/models/account_data.py`
- Test: `backend/tests/test_publish_identity_projection.py`

**Steps:**
1. Write tests proving a successful callback:
   - creates or updates one `PlatformContentRecord`;
   - records `external_content_id`, share identity, account, platform, title, and publish time;
   - marks identity confidence as confirmed;
   - is idempotent for callback retries;
   - never overwrites historical imported metrics with unavailable fields.
2. Run the tests and confirm they fail.
3. Implement the projection and bind it to the publish job.
4. Run identity projection and account data-center tests.

## Task 6: Add the minimum desktop publishing workflow

**Files:**
- Create: `frontend/src/services/publishing.ts`
- Create: `frontend/src/types/publishing.ts`
- Modify: `frontend/src/pages/Approvals.tsx`
- Modify: `frontend/src/pages/Content.tsx`
- Test: `frontend/src/pages/__tests__/Approvals.test.tsx`
- Test: `frontend/src/pages/__tests__/Content.test.tsx`

**Steps:**
1. Write component tests for:
   - an approved Douyin package showing “前往抖音发布”;
   - clear display of selected account and frozen client/project context;
   - opening the H5 handoff only after explicit user action;
   - waiting-for-callback, bound, failed, expired, retry, and cancel states;
   - unsupported capabilities showing a precise blocked reason instead of a dead button.
2. Run the tests and confirm they fail.
3. Implement the typed service and desktop UI using the established high-fidelity design system.
4. Keep tokens, schema internals, callback payloads, and raw JSON out of the normal user interface.
5. Run frontend tests and the production build.

## Task 7: Feature flags, observability, and release verification

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Create: `backend/tests/test_publishing_feature_flags.py`
- Modify: `README.md`

**Steps:**
1. Add default-safe flags:
   - `DOUYIN_H5_PUBLISH_ENABLED=false`;
   - `DOUYIN_POSTING_TASK_ENABLED=false`;
   - `DOUYIN_DIRECT_PUBLISH_ENABLED=false`.
2. Add tests proving disabled capabilities cannot cause an external write.
3. Add structured event/log fields for job ID, account ID, state transition, Douyin log ID, retry count, and redacted error code.
4. Document required Douyin scopes and production activation order.
5. Run:
   - targeted backend publishing tests;
   - all existing Douyin and account-data tests;
   - backend test suite;
   - frontend test suite;
   - frontend production build;
   - Alembic head and upgrade checks.
6. Review the diff for unrelated workspace changes and stage only files from this plan.
7. Create a rollback-ready local commit.

