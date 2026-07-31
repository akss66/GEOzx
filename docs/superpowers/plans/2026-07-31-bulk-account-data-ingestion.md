# Bulk Account Data Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow an operator to drag multiple Douyin export files into the account data center, automatically detect every supported dataset or worksheet, commit valid datasets independently, and update only overlapping canonical fields without losing provenance or unrelated values.

**Architecture:** Keep the existing import batch and artifact tables as the immutable audit ledger, add job/file ownership around batch parsing, record every non-missing normalized field as a provenance-bearing observation, and rebuild canonical projections by deterministic winner selection. Existing single-file preview/commit routes remain compatible while the new job API drives the multi-file UI.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, PostgreSQL/SQLite tests, openpyxl, ARQ, React 18, TypeScript, TanStack Query, Vitest, Testing Library.

## Global Constraints

- Preserve account and organization isolation on every query and foreign key.
- A failed file or worksheet must not roll back valid siblings in the same job.
- A missing cell never overwrites a known value; an explicit numeric zero does.
- Re-importing the same request or artifact is idempotent.
- A newer import only replaces fields that share the same canonical business key.
- Official API wins over platform export, verified screenshot, and manual entry; within equal priority, later confirmed data wins.
- Revoking or permanently deleting a source must rebuild affected canonical values from surviving observations.
- Keep the current single-file API operational until the new UI and migration are verified.
- Use `apply_patch` for edits, write a failing behavior test before production code, and commit each completed slice.

---

## Task 1: Add Import Job and Observation Persistence

**Files:**

- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/models/account_data.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/migrations/versions/20260731_0200_bulk_account_data_ingestion.py`
- Modify: `backend/tests/test_account_data_models.py`
- Create: `backend/tests/test_bulk_import_models.py`

- [x] Add failing model tests proving that one job owns many files, one file owns many dataset batches, and field observations are account-scoped.
- [x] Add enums for job/file lifecycle states without changing existing `ImportBatchStatus` values.
- [x] Add `DataImportJob` with `org_id`, `account_id`, `created_by_id`, `client_request_id`, aggregate counts, timestamps, and unique `(org_id, account_id, client_request_id)`.
- [x] Add `DataImportFile` with job ownership, artifact identity, status, error payload, retry lineage, and unique `(job_id, ordinal)`.
- [x] Extend `DataImportBatch` with nullable `job_id`, `job_file_id`, `sheet_name`, `dataset_ordinal`, and `confirmed_sequence`.
- [x] Add `DataFieldObservation` with:

```python
class DataFieldObservation(Base, TimestampMixin):
    __tablename__ = "data_field_observations"
    id: Mapped[int]
    org_id: Mapped[int]
    account_id: Mapped[int]
    import_batch_id: Mapped[int]
    import_row_id: Mapped[int | None]
    domain: Mapped[str]
    entity_key: Mapped[str]
    stat_date: Mapped[date]
    field_name: Mapped[str]
    value: Mapped[dict]
    source_kind: Mapped[DataSourceKind]
    source_priority: Mapped[int]
    confirmed_sequence: Mapped[int]
    active: Mapped[bool]
```

- [x] Add indexes for `(account_id, domain, entity_key, stat_date, field_name, active)` and idempotency uniqueness on `(import_batch_id, import_row_id, domain, entity_key, stat_date, field_name)`.
- [x] Write forward and downgrade Alembic operations, including PostgreSQL enum creation guards and SQLite-compatible test behavior.
- [x] Run `cd backend; python -m pytest tests/test_account_data_models.py tests/test_bulk_import_models.py -q`.
- [x] Commit: `feat(account-data): add bulk import job and observation models`.

## Task 2: Build the Deterministic Merge Kernel

**Files:**

- Create: `backend/app/services/data_import/merge.py`
- Create: `backend/tests/test_data_import_merge.py`

- [x] Add failing table-driven tests for account, content, audience, and benchmark business keys.
- [x] Add failing tests proving:
  - absent/blank input creates no observation;
  - explicit `0` creates an observation;
  - higher source priority wins;
  - later confirmation wins at equal priority;
  - stable ID breaks an otherwise exact tie.
- [x] Implement pure business-key builders:

```python
def account_entity_key(account_id: int) -> str:
    return f"account:{account_id}"

def content_entity_key(account_id: int, platform_content_record_id: int) -> str:
    return f"account:{account_id}:content:{platform_content_record_id}"

def audience_entity_key(account_id: int, dimension: str, label: str) -> str:
    return f"account:{account_id}:audience:{dimension}:{label}"

def benchmark_entity_key(account_id: int, benchmark_code: str) -> str:
    return f"account:{account_id}:benchmark:{benchmark_code}"
```

- [x] Implement `iter_present_fields(normalized: Mapping[str, Any])` so `None` and missing markers are ignored while `0`, `0.0`, and `False` are preserved.
- [x] Implement `choose_winner(observations)` ordered by source priority, confirmation sequence, and observation ID.
- [x] Keep the module independent of SQLAlchemy so unit tests exercise the real merge semantics without database mocks.
- [x] Run `cd backend; python -m pytest tests/test_data_import_merge.py -q`.
- [x] Commit: `feat(account-data): add deterministic field merge kernel`.

## Task 3: Make Template Detection Header-Driven

**Files:**

- Modify: `backend/app/services/data_import/templates.py`
- Modify: `backend/app/services/data_import/parser.py`
- Modify: `backend/tests/test_data_import_preview.py`
- Create: `backend/tests/test_data_import_header_matching.py`

- [x] Add failing tests for reordered columns, optional missing columns, supported headers with unrelated extra columns, duplicate aliases, and missing required headers.
- [x] Replace positional template matching with normalized header-to-column matching:

```python
@dataclass(frozen=True, slots=True)
class TemplateMatch:
    template: TemplateDefinition
    column_indexes: dict[str, int]
    ignored_headers: tuple[str, ...]
```

- [x] Implement `TemplateDefinition.match_headers(headers)` by building one normalized accepted-header lookup per template, resolving each source header to at most one canonical field, verifying every `required=True` field exists, and returning the matched indexes plus ignored headers.
- [x] Reject ambiguous mappings when two source columns resolve to the same canonical field.
- [x] Normalize rows using the resolved source-column indexes, not `enumerate(template.columns)`.
- [x] Store ignored extra headers as dataset warnings and preserve their raw values in the audit row.
- [x] Keep exact current templates valid to avoid a compatibility regression.
- [x] Run `cd backend; python -m pytest tests/test_data_import_preview.py tests/test_data_import_header_matching.py -q`.
- [x] Commit: `feat(account-data): detect templates by required headers`.

## Task 4: Parse Multiple Worksheets and Multiple Files Safely

**Files:**

- Modify: `backend/app/services/data_import/parser.py`
- Create: `backend/tests/test_data_import_multi_sheet.py`
- Modify: `backend/tests/test_data_import_preview.py`

- [x] Add failing tests for:
  - one workbook containing two supported worksheets;
  - a blank worksheet plus a supported worksheet;
  - one supported and one unknown worksheet;
  - duplicate worksheet names normalized safely;
  - aggregate row and byte limits across worksheets.
- [x] Introduce:

```python
@dataclass(frozen=True, slots=True)
class ParsedSourceFile:
    filename: str
    datasets: list[ParsedDataset]
    warnings: list[RowIssue]
```

- [x] Change `parse_source_file(filename, data)` to return `ParsedSourceFile` after iterating every worksheet and collecting its supported dataset or isolated warning.
- [x] Add `sheet_name` and `dataset_ordinal` to `ParsedDataset`.
- [x] Treat blank worksheets as skipped warnings.
- [x] Return unknown worksheets as isolated dataset failures to the bulk orchestrator instead of failing recognized siblings.
- [x] Keep archive, formula, external-link, macro, row, column, and decompression-bomb protections.
- [x] Provide a compatibility helper used by the legacy single-file endpoint when exactly one supported dataset is present.
- [x] Run `cd backend; python -m pytest tests/test_data_import_preview.py tests/test_data_import_multi_sheet.py -q`.
- [x] Commit: `feat(account-data): parse supported datasets from every worksheet`.

## Task 5: Persist Observations and Canonical Projections

**Files:**

- Create: `backend/app/services/data_import/projection.py`
- Modify: `backend/app/services/data_import/service.py`
- Modify: `backend/app/services/account_data_view.py`
- Modify: `backend/tests/test_account_data_view.py`
- Create: `backend/tests/test_data_import_projection.py`

- [x] Add failing integration tests for two overlapping 30-day imports where only shared dates/fields update and non-overlapping dates survive.
- [x] Add tests for partial content rows where omitted metrics preserve earlier values and zero replaces earlier nonzero values.
- [x] Add tests covering account, content, audience, and benchmark domains, not only content metrics.
- [x] Implement observation extraction from committed rows with provenance and a monotonic `confirmed_sequence`.
- [x] Implement `rebuild_projection(session, affected_keys)` that:
  - loads active observations for each affected field;
  - chooses the deterministic winner;
  - upserts one canonical record per business key;
  - deletes a canonical field/row only when no active observation survives.
- [x] Remove batch ID from canonical identity lookups while retaining the winning source batch as provenance.
- [x] Update `AccountDataViewService` to read canonical rows once per business key and eliminate duplicate account/audience/benchmark snapshots.
- [x] Ensure the commit transaction writes observations and canonical projection atomically for one dataset.
- [x] Run `cd backend; python -m pytest tests/test_data_import_projection.py tests/test_account_data_view.py -q`.
- [x] Commit: `feat(account-data): merge overlapping imports into canonical projections`.

## Task 6: Make Revoke and Delete Rebuild Canonical Data

**Files:**

- Modify: `backend/app/services/data_import/service.py`
- Modify: `backend/tests/test_account_data_import_api.py`
- Create: `backend/tests/test_data_import_rebuild.py`

- [x] Add failing tests where a newer source wins, is revoked, and the older surviving value becomes canonical again.
- [x] Add the same test for permanent deletion and for all four data domains.
- [x] Add tests proving revoke/delete are account-scoped and cannot affect another account's observations.
- [x] Mark observations inactive during revoke, reactivate them only through an explicit supported restore path, and rebuild affected keys in the same transaction.
- [x] Permanently delete ledger rows only after collecting affected keys and rebuilding from survivors.
- [x] Return actionable conflict responses if committed downstream records prevent deletion.
- [x] Run `cd backend; python -m pytest tests/test_data_import_rebuild.py tests/test_account_data_import_api.py -q`.
- [x] Commit: `fix(account-data): rebuild canonical values after source removal`.

## Task 7: Add the Durable Bulk Job Service and API

**Files:**

- Create: `backend/app/schemas/account_data_jobs.py`
- Create: `backend/app/services/data_import/jobs.py`
- Modify: `backend/app/api/account_data.py`
- Modify: `backend/app/worker.py`
- Create: `backend/tests/test_account_data_import_jobs_api.py`
- Create: `backend/tests/test_account_data_import_jobs_worker.py`

- [x] Add failing API tests for `POST /account-data/{account_id}/import-jobs` accepting repeated `files` multipart parts and an optional `client_request_id`.
- [x] Add failing tests proving same client request returns the original job and same artifact hash does not double-write observations.
- [x] Add failing worker test for five files where four commit and one fails without rollback.
- [x] Add failing retry test proving only the failed file is retried.
- [x] Implement endpoints:

```text
POST   /account-data/{account_id}/import-jobs
GET    /account-data/{account_id}/import-jobs/{job_id}
POST   /account-data/{account_id}/import-jobs/{job_id}/files/{file_id}/retry
```

- [x] Validate per-file and aggregate byte/file-count limits before queueing.
- [x] Persist all file metadata before returning `202 Accepted`.
- [x] Implement `execute_account_data_import_job(ctx, job_id)` and register it in `WorkerSettings.functions`.
- [x] Process each dataset in a nested transaction/savepoint; update file/job counters after each terminal dataset state.
- [x] Publish progress events using account/job-scoped payloads without leaking filenames across accounts.
- [x] Preserve the legacy preview/commit endpoints for manual review flows.
- [x] Run `cd backend; python -m pytest tests/test_account_data_import_jobs_api.py tests/test_account_data_import_jobs_worker.py -q`.
- [x] Commit: `feat(account-data): add resilient bulk import jobs`.

## Task 8: Add Multi-File Frontend Contracts and Queue

**Files:**

- Modify: `frontend/src/api/accountData.ts`
- Create: `frontend/src/components/account-data/BulkImportQueue.tsx`
- Create: `frontend/src/components/account-data/BulkImportQueue.test.tsx`
- Modify: `frontend/src/components/account-data/ImportWorkspace.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.test.tsx`
- Modify: `frontend/src/styles/account-data-center.css`

- [x] Add failing component tests proving drag/drop and file-picker both submit multiple files in one request.
- [x] Add failing tests for mixed terminal states: four successful files remain successful while one failed file exposes `重试此文件`.
- [x] Add typed job/file/dataset DTOs and:

```ts
export async function createAccountDataImportJob(
  accountId: number,
  files: File[],
  clientRequestId: string,
): Promise<AccountDataImportJob>
```

- [x] Change the input to `multiple`, accept `.xlsx,.csv`, and label the primary action `继续添加数据文件`.
- [x] Render one queue row per file with detected datasets, worksheet names, progress, warnings, failure reason, and isolated retry.
- [x] Poll the job endpoint while any file is queued or processing, then invalidate status/history queries once terminal.
- [x] Keep manual entry under `其他录入方式`; do not replace the active multi-file queue when additional files are selected.
- [x] Preserve keyboard access, visible focus, and screen-reader status announcements.
- [x] Run `cd frontend; npm test -- --run src/components/account-data/BulkImportQueue.test.tsx src/components/account-data/ManualDataEntry.test.tsx src/components/account-data/ImportBatchHistory.test.tsx`, then `npm run lint` and `npm run build`.
- [x] Commit: `feat(account-data): add multi-file import queue`.

## Task 9: Correct Coverage Semantics and Operator Copy

**Files:**

- Modify: `backend/app/services/data_import/service.py`
- Modify: `backend/app/schemas/account_data.py`
- Modify: `backend/tests/test_account_data_import_api.py`
- Modify: `frontend/src/components/account-data/DataCoverageOverview.tsx`
- Modify: `frontend/src/components/account-data/AccountDataHeader.tsx`
- Modify: `frontend/src/components/account-data/statusMeta.ts`
- Modify: `frontend/src/components/account-data/statusMeta.test.ts`
- Modify: `frontend/src/pages/AccountDataCenter.test.tsx`

- [x] Add failing backend tests that coverage reports each dataset type independently and never infers “complete account data” from one imported template.
- [x] Return supported dataset inventory with statuses `not_imported`, `available`, `stale`, `processing`, and `failed`, plus confirmed date range and latest source.
- [x] Replace `数据完整` with `已有可用数据`, `补齐数据` with `添加此类数据`, and `更新数据/更换文件` with `添加数据文件`.
- [x] Show `已导入 N/M 类数据` only when M is the explicit supported dataset inventory; otherwise show `已导入 N 类数据`.
- [x] Make pending jobs visible without counting them as confirmed coverage.
- [x] Add a clear explanation that several platform exports together form the account dataset.
- [x] Run `cd backend; python -m pytest tests/test_account_data_import_api.py -q`.
- [x] Run `cd frontend; npm test -- --run src/components/account-data/statusMeta.test.ts src/pages/AccountDataCenter.test.tsx`.
- [x] Commit: `fix(account-data): show dataset coverage without completeness claims`.

## Task 10: Backfill Existing Imports and Verify Compatibility

**Files:**

- Create: `backend/app/services/data_import/backfill.py`
- Create: `backend/tests/test_data_import_backfill.py`
- Modify: `backend/migrations/versions/20260731_0200_bulk_account_data_ingestion.py`
- Modify: `docs/superpowers/specs/2026-07-31-bulk-account-data-ingestion-design.md`

- [x] Add failing backfill tests for duplicate legacy snapshots, revoked batches, missing values, and explicit zero.
- [x] Implement an idempotent batched backfill from committed, non-revoked legacy batches into observations.
- [x] Rebuild canonical records account-by-account after observation backfill.
- [x] Record backfill checkpoints so production migration can resume safely.
- [x] Keep reads compatible during rollout: canonical projection first, legacy fallback only for accounts not yet backfilled.
- [x] Document operational commands, expected row counts, verification queries, and rollback boundaries.
- [x] Run `cd backend; python -m pytest tests/test_data_import_backfill.py -q`.
- [x] Commit: `feat(account-data): backfill canonical observations`.

## Task 11: Full Verification and Production Rollout

**Files:**

- Modify: deployment configuration only if a feature flag or worker registration requires it.
- Create: `docs/superpowers/plans/2026-07-31-bulk-account-data-ingestion-verification.md`

- [x] Run focused backend tests for parser, merge, projection, job API, job worker, revoke/delete, and backfill.
- [x] Run the complete backend suite: `cd backend; python -m pytest -q`.
- [x] Run frontend tests: `cd frontend; npm test -- --run`.
- [x] Run frontend typecheck/build: `cd frontend; npm run build`.
- [x] Run lint in both projects using the repository's existing commands.
- [x] Apply migrations in a production-like database and verify one account with:
  - four different supported exports uploaded together;
  - one malformed fifth file;
  - a second overlapping period import;
  - revoke and permanent delete fallback.
- [x] Verify browser behavior at desktop and narrow widths: drag/drop, picker, progress, isolated retry, history, coverage, and account switching.
- [x] Confirm cross-account isolation by switching accounts while a job runs.
- [ ] Deploy using the repository's existing release workflow, inspect health and worker logs, and run the same smoke scenario in production.
- [ ] Record exact commands, release identifier, migration result, and evidence in the verification document.
- [ ] Commit: `docs(account-data): record bulk ingestion verification`.
