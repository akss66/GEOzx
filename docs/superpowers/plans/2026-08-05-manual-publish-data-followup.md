# Manual Publish Data Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a manually published schedule entry into one durable, source-linked data collection follow-up that disappears only after a relevant post-publication import is confirmed.

**Architecture:** Persist the publication timestamp on `ContentScheduleEntry`; derive follow-up work from published schedule rows instead of creating an orphan lifecycle table. The pending-work projection checks committed, non-revoked account/content metric imports in the same org/account and only considers imports committed after publication whose projected coverage includes the publication date.

**Tech Stack:** SQLAlchemy async ORM, FastAPI, Alembic, SQLite/PostgreSQL, pytest.

## Global Constraints

- Manual publication must never call `platform.content_publish`.
- Every read and mutation remains scoped by organization, account, and creator.
- Repeated completion is fully idempotent and preserves the original `published_at`.
- The migration must upgrade and downgrade on SQLite and compile for PostgreSQL.

---

### Task 1: Persist publication time

**Files:**
- Modify: `backend/app/models/deliverable_action.py`
- Create: `backend/migrations/versions/20260805_0100_manual_publish_followup.py`
- Create: `backend/tests/test_manual_publish_followup_migration.py`

**Interfaces:**
- Produces: `ContentScheduleEntry.published_at: datetime | None`.

- [x] Write a migration test that upgrades a minimal `content_schedule_entries` table, checks the nullable timezone-aware column, downgrades it, and compiles both operations under PostgreSQL.
- [x] Run the migration test and confirm it fails because the revision does not exist.
- [x] Add the ORM column and reversible Alembic revision with `down_revision = "20260804_0500"`.
- [x] Run the migration test and confirm it passes.

### Task 2: Project one source-linked follow-up

**Files:**
- Modify: `backend/app/services/pending_work.py`
- Modify: `backend/tests/test_pending_work_api.py`
- Verify: `backend/tests/test_main_agent_worker_contract.py`

**Interfaces:**
- Consumes: `ContentScheduleEntry.published_at` and the source `Deliverable.thread_id/turn_id`.
- Produces: pending item ID `account_data:publication:{schedule_entry_id}`, due at `published_at + 24 hours`.

- [x] Run contract Scenario C and confirm it fails because no publication follow-up exists.
- [x] Add a focused test for timestamp persistence, source linkage, exact copy, stable ID, and replay idempotency.
- [x] Set `published_at` only on the first transition from `planned` to `published`.
- [x] Query published schedule rows owned by the current user and project one account-data item per incomplete follow-up.
- [x] Run contract Scenario C and focused pending-work tests until green.

### Task 3: Close only on relevant post-publication data

**Files:**
- Modify: `backend/app/services/pending_work.py`
- Modify: `backend/tests/test_pending_work_api.py`

**Interfaces:**
- Produces: `_publication_follow_up_satisfied(...) -> bool` based on same-scope committed import projections.

- [x] Add failing tests proving pre-publication imports, benchmark-only imports, wrong-account imports, and uncovered dates do not remove the follow-up.
- [x] Add a failing test proving a post-publication account/content metric import covering the publication date removes it.
- [x] Load committed non-revoked batches after publication and accept only batches with actual account/content projections and matching period or projected observation date.
- [x] Run pending-work and data-import focused suites.

### Task 4: Verification and independent commit

**Files:**
- Verify all files above plus deliverable action and migration suites.

- [x] Run Scenario C, pending-work, data-import, and migration tests. The full deliverable-action suite currently has one unrelated Task 8A trusted-input expectation failure.
- [x] Run Ruff, mypy, and repository diff checks.
- [x] Stage only Task 8B paths and commit with a standalone message.
