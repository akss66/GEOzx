# Multi-Source Account Data Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready, account-scoped data center that ingests the four known Douyin Excel exports plus verified screenshot/manual data, preserves provenance and conflicts, and feeds the review dashboard and Agent runtime through one unified query service.

**Architecture:** Add an append-only import ledger around the existing `MetricSnapshot` model rather than replacing the current review stack. Source adapters parse inputs into durable staging rows; commit projects confirmed rows into normalized account, content, audience, and benchmark snapshots; `AccountDataViewService` becomes the only read boundary used by review and Agent tools. Original files remain in the existing object-storage volume and database rows retain hashes, row references, actor, account, period, and resolution history.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2 async, Alembic, Pydantic 2, openpyxl, React 18, TypeScript, TanStack Query, Ant Design, Vitest, pytest, Playwright.

## Global Constraints

- Desktop web only; mobile layouts are outside this phase.
- Every read and write is scoped by authenticated organization plus `require_account_access`.
- Source priority is `official_api > platform_export > screenshot_verified > manual_entry`; lower-priority data never silently overwrites higher-priority data.
- Original files are stored outside the database; metadata, SHA-256, storage reference, parsed rows, actor, and timestamps are stored in the database.
- Missing source values remain `NULL`; they must not be normalized to zero.
- Screenshot-derived candidates cannot enter formal review data until a user confirms them.
- Content title is never a strong identity. Strong identity is platform content ID or share URL; title plus publish time is provisional and ambiguous matches require human resolution.
- Existing `/metrics/review-workspace`, `/metrics/overview`, and `/metrics/performance-snapshots` responses remain backward compatible during migration.
- Do not expose or claim retired Douyin website-application fan/video-data scopes as usable capabilities.
- This phase does not build a Douyin mini-program or browser automation.

---

## File Map

### Backend additions

- `backend/app/models/account_data.py`: import batches, artifacts, staging rows, platform works, account snapshots, audience items, benchmark snapshots, and conflicts.
- `backend/app/schemas/account_data.py`: preview, row resolution, batch history, manual entry, source coverage, and unified view contracts.
- `backend/app/services/data_import/templates.py`: four deterministic Douyin workbook detectors and field maps.
- `backend/app/services/data_import/adapters.py`: source adapter protocol and file-source implementation.
- `backend/app/services/data_import/parser.py`: safe workbook parsing and canonical value conversion.
- `backend/app/services/data_import/identity.py`: strong/provisional content matching and ambiguity reporting.
- `backend/app/services/data_import/service.py`: durable preview, commit, revoke, and audit orchestration.
- `backend/app/services/account_data_view.py`: source-priority resolution and account-scoped read model.
- `backend/app/api/account_data.py`: import, preview, resolve, commit, revoke, artifact, manual-entry, and source-status endpoints.
- `backend/migrations/versions/20260722_0100_account_data_center.py`: additive schema and indexes.

### Backend modifications

- `backend/pyproject.toml`: add `openpyxl` and `python-multipart`.
- `backend/app/models/enums.py`: add import/source/status enums.
- `backend/app/models/metrics.py`: attach existing snapshots to batches and platform works; add nullable observed metrics.
- `backend/app/models/__init__.py`: export new models.
- `backend/app/main.py`: register `account_data.router`.
- `backend/app/services/review_workspace.py`: read through `AccountDataViewService` and expose source coverage/freshness.
- `backend/app/orchestrator/runtime_tools.py`: replace direct snapshot queries with the unified account-data service.
- `backend/app/integrations/douyin_capabilities.py`, `backend/app/schemas/platform.py`: remove retired audience-insight capability claims.
- `backend/app/services/user_deletion.py`: include new account-data rows in deletion impact and execution.

### Frontend additions

- `frontend/src/api/accountData.ts`: account-data API contracts and calls.
- `frontend/src/pages/AccountDataCenter.tsx`: account data hub and source coverage.
- `frontend/src/components/account-data/FileImportFlow.tsx`: upload, durable preview, mapping, and commit flow.
- `frontend/src/components/account-data/ImportPreviewTable.tsx`: row validation and content-match resolution.
- `frontend/src/components/account-data/ManualDataEntry.tsx`: screenshot-backed structured entry.
- `frontend/src/components/account-data/ImportBatchHistory.tsx`: provenance, failures, and revoke action.
- `frontend/src/styles/account-data-center.css`: high-fidelity desktop styling aligned to the approved shell.

### Frontend modifications

- `frontend/src/appRoutes.ts`, `frontend/src/App.tsx`: add `/accounts/:accountId/data`.
- `frontend/src/pages/Accounts.tsx`: add a visible “数据中心” action and remove retired scope suggestions.
- `frontend/src/pages/ReviewDashboard.tsx`: add source coverage, freshness, conflicts, and “更新数据” entry.
- `frontend/src/api/metrics.ts`: extend compatible review types with source metadata.
- `frontend/src/types.ts`: remove the retired `audience_insights` capability key.

---

### Task 1: Make Douyin Capability Claims Truthful

**Files:**
- Modify: `backend/app/integrations/douyin_capabilities.py`
- Modify: `backend/app/schemas/platform.py`
- Modify: `backend/tests/test_capability_registry.py`
- Modify: `backend/tests/test_platform_integrations_api.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/Accounts.tsx`
- Modify: `frontend/src/api/workspace.test.ts`

**Interfaces:**
- Produces: capability registry containing only currently supportable website-application behavior.
- Preserves: profile authorization and manual/publish-preparation state.

- [ ] **Step 1: Rewrite capability tests to reject retired scopes**

```python
def test_registry_does_not_advertise_retired_data_scopes():
    capabilities = {item.key: item for item in DOUYIN_CAPABILITIES}
    assert "audience_insights" not in capabilities
    scopes = {scope for item in capabilities.values() for scope in item.user_scopes}
    assert "fans.data.bind" not in scopes
```

- [ ] **Step 2: Run the focused backend tests and confirm failure**

Run: `cd backend && python -m pytest tests/test_capability_registry.py tests/test_platform_integrations_api.py -q`

Expected: FAIL because the registry still advertises `audience_insights` and `fans.data.bind`.

- [ ] **Step 3: Remove retired backend and frontend capability branches**

Delete the `audience_insights` registry entry, capability literal, authorization recommendation, form option, and tests that generate fan-data authorization URLs. Keep unsupported historical scopes visible only as inert strings returned by existing account authorization records; do not offer actions for them.

- [ ] **Step 4: Verify backend and frontend capability contracts**

Run: `cd backend && python -m pytest tests/test_capability_registry.py tests/test_platform_integrations_api.py -q`

Expected: PASS.

Run: `cd frontend && npm.cmd test -- src/api/workspace.test.ts && npm.cmd run build`

Expected: PASS and TypeScript build completes.

- [ ] **Step 5: Commit the truthful capability baseline**

```bash
git add backend/app/integrations/douyin_capabilities.py backend/app/schemas/platform.py backend/tests/test_capability_registry.py backend/tests/test_platform_integrations_api.py frontend/src/types.ts frontend/src/pages/Accounts.tsx frontend/src/api/workspace.test.ts
git commit -m "fix: remove retired douyin data capabilities"
```

### Task 2: Add the Import Ledger and Normalized Data Models

**Files:**
- Create: `backend/app/models/account_data.py`
- Create: `backend/migrations/versions/20260722_0100_account_data_center.py`
- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/models/metrics.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/services/user_deletion.py`
- Test: `backend/tests/test_account_data_models.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `DataImportBatch`, `DataArtifact`, `DataImportRow`, `PlatformContentRecord`, `AccountMetricSnapshot`, `AudienceProfileSnapshot`, `AudienceProfileItem`, `BenchmarkSnapshot`, and `DataConflict`.
- Produces: `DataSourceKind`, `ImportBatchStatus`, `ImportRowStatus`, `ContentIdentityConfidence`, and `ConflictStatus`.
- Extends: `MetricSnapshot.import_batch_id`, `MetricSnapshot.platform_content_record_id`, and nullable detailed metric columns.

- [ ] **Step 1: Write model and migration contract tests**

```python
async def test_import_batch_owns_artifact_and_staging_rows(session, admin, account):
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.PREVIEW_READY,
        template_code="douyin_work_list_v1",
    )
    session.add(batch)
    await session.flush()
    assert batch.id is not None


def test_migration_head_is_account_data_center():
    assert get_head_revision() == "20260722_0100"
```

- [ ] **Step 2: Run tests and confirm missing-model failures**

Run: `cd backend && python -m pytest tests/test_account_data_models.py tests/test_migrations.py -q`

Expected: FAIL on missing account-data models and migration head.

- [ ] **Step 3: Implement additive models and constraints**

Use these durable keys and constraints:

```python
class DataSourceKind(enum.StrEnum):
    OFFICIAL_API = "official_api"
    PLATFORM_EXPORT = "platform_export"
    SCREENSHOT_VERIFIED = "screenshot_verified"
    MANUAL_ENTRY = "manual_entry"


class DataImportBatch(Base, TimestampMixin):
    __tablename__ = "data_import_batches"
    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source_kind: Mapped[DataSourceKind] = mapped_column(pg_enum(DataSourceKind, "data_source_kind"))
    status: Mapped[ImportBatchStatus] = mapped_column(pg_enum(ImportBatchStatus, "import_batch_status"))
    template_code: Mapped[str] = mapped_column(String(80))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Add unique constraints for `(account_id, platform, external_content_id)` when the ID exists and `(batch_id, row_number)` for staging rows. Keep weak fingerprints indexed but not unique.

- [ ] **Step 4: Add migration upgrade/downgrade and user-deletion coverage**

The migration creates new tables first, then adds nullable foreign keys and detailed fields to `metric_snapshots`. User hard-deletion preview must count batches created by the member and cascade account-owned data only when the account itself is deleted.

- [ ] **Step 5: Verify models and migration**

Run: `cd backend && python -m pytest tests/test_account_data_models.py tests/test_migrations.py tests/test_user_deletion_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the data ledger**

```bash
git add backend/app/models backend/app/services/user_deletion.py backend/migrations/versions/20260722_0100_account_data_center.py backend/tests/test_account_data_models.py backend/tests/test_migrations.py backend/tests/test_user_deletion_api.py
git commit -m "feat: add account data import ledger"
```

### Task 3: Parse and Normalize the Four Known Excel Templates

**Files:**
- Create: `backend/app/services/data_import/__init__.py`
- Create: `backend/app/services/data_import/adapters.py`
- Create: `backend/app/services/data_import/templates.py`
- Create: `backend/app/services/data_import/parser.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/test_data_import_templates.py`

**Interfaces:**
- Produces: `DataSourceAdapter.detect(input)`, `.parse(input)`, `.normalize(rows)`, `.validate(rows)`, and `.preview(rows)` protocol.
- Produces: `FileDataSourceAdapter` as the first registered adapter.
- Produces: `detect_template(headers: list[str]) -> TemplateDefinition`.
- Produces: `parse_source_file(filename: str, data: bytes) -> ParsedDataset` for `.xlsx` and `.csv`.
- Produces canonical rows with `template_code`, `raw`, `normalized`, `errors`, and `warnings`.

- [ ] **Step 1: Add parser tests using generated in-memory workbooks**

```python
def test_work_list_template_normalizes_percentages_and_missing_values():
    workbook = workbook_bytes(
        ["作品名称", "发布时间", "播放量", "完播率", "封面点击率", "粉丝增量"],
        [["作品 A", "2026-07-18 14:11:20", "81", "0.087500", "-", "0"]],
    )
    parsed = parse_source_file("works.xlsx", workbook)
    assert parsed.template_code == "douyin_work_list_v1"
    assert parsed.rows[0].normalized["play"] == 81
    assert parsed.rows[0].normalized["completion_rate"] == 0.0875
    assert parsed.rows[0].normalized["cover_click_rate"] is None
```

Cover `douyin_daily_play_v1`, `douyin_single_content_v1`, `douyin_period_aggregate_v1`, and `douyin_work_list_v1` in both Excel and UTF-8/UTF-8-BOM CSV form, plus unknown template, duplicate headers, invalid dates, percentages outside `[0, 1]`, formulas, malformed CSV, and files over configured limits.

- [ ] **Step 2: Run parser tests and confirm failure**

Run: `cd backend && python -m pytest tests/test_data_import_templates.py -q`

Expected: FAIL because parser modules and dependencies do not exist.

- [ ] **Step 3: Add safe spreadsheet dependencies**

```toml
"openpyxl>=3.1,<4",
"python-multipart>=0.0.20,<1",
```

- [ ] **Step 4: Implement deterministic template signatures**

Each `TemplateDefinition` declares required headers, aliases, data domain, canonical field map, and row normalizer. Load workbooks with `read_only=True, data_only=True`; parse CSV with Python's `csv` module after strict UTF-8/UTF-8-BOM decoding. Reject macros, external links, unsupported extensions, embedded NUL bytes, more than 10 MB, more than 10,000 rows, or more than 100 columns.

Define the source boundary independently from file parsing:

```python
class DataSourceAdapter(Protocol):
    source_kind: DataSourceKind

    def detect(self, source: SourceInput) -> DetectionResult: ...
    def parse(self, source: SourceInput) -> ParsedDataset: ...
    def normalize(self, parsed: ParsedDataset) -> list[NormalizedRow]: ...
    def validate(self, rows: list[NormalizedRow]) -> list[ValidatedRow]: ...
    def preview(self, rows: list[ValidatedRow]) -> ImportPreview: ...
```

`FileDataSourceAdapter` owns Excel/CSV detection. A future mini-program or official API adapter must return the same `NormalizedRow` contract and therefore bypass no provenance, validation, conflict, or permission rule.

- [ ] **Step 5: Verify all four templates**

Run: `cd backend && python -m pytest tests/test_data_import_templates.py -q`

Expected: PASS with four recognized template codes and unknown files rejected without database writes.

- [ ] **Step 6: Commit the parser layer**

```bash
git add backend/pyproject.toml backend/app/services/data_import backend/tests/test_data_import_templates.py
git commit -m "feat: parse douyin account data exports"
```

### Task 4: Build Durable Preview and Content Identity Resolution

**Files:**
- Create: `backend/app/services/data_import/identity.py`
- Create: `backend/app/services/data_import/service.py`
- Test: `backend/tests/test_data_import_preview.py`
- Test: `backend/tests/test_content_identity_matching.py`

**Interfaces:**
- Produces: `create_preview(session, *, user, account, filename, content) -> DataImportBatch`.
- Produces: `resolve_row_match(session, *, batch, row_number, resolution) -> DataImportRow`.
- Produces: `match_content(session, *, account_id, normalized_row) -> ContentMatch`.

- [ ] **Step 1: Write identity and durable-preview tests**

```python
async def test_title_and_publish_time_create_only_provisional_match(session, account):
    result = await match_content(
        session,
        account_id=account.id,
        normalized_row={"title": "作品 A", "published_at": "2026-07-18T14:11:20"},
    )
    assert result.confidence == ContentIdentityConfidence.PROVISIONAL


async def test_duplicate_candidate_requires_resolution(session, batch):
    row = await load_row(session, batch.id, 2)
    assert row.status == ImportRowStatus.NEEDS_RESOLUTION
    assert len(row.candidate_content_ids) == 2
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `cd backend && python -m pytest tests/test_data_import_preview.py tests/test_content_identity_matching.py -q`

Expected: FAIL on missing preview and identity services.

- [ ] **Step 3: Implement strong and provisional matching**

Matching order is exact external content ID, canonicalized share URL, exact account plus published timestamp plus normalized title, then unresolved. Normalize whitespace and Unicode width only; do not use fuzzy similarity for automatic linking.

- [ ] **Step 4: Persist every parsed row before returning preview**

Create artifact and batch in one transaction, then store one `DataImportRow` per input row with raw values, normalized values, field errors, warnings, candidate IDs, and status. Compute SHA-256 before saving the object as `account-data/{org_id}/{account_id}/{batch_id}/{sha256}.xlsx`.

- [ ] **Step 5: Verify preview durability and idempotency**

Run: `cd backend && python -m pytest tests/test_data_import_preview.py tests/test_content_identity_matching.py -q`

Expected: PASS, including identical file hash returning the existing uncommitted batch instead of duplicating it.

- [ ] **Step 6: Commit preview and identity matching**

```bash
git add backend/app/services/data_import backend/tests/test_data_import_preview.py backend/tests/test_content_identity_matching.py
git commit -m "feat: add durable import previews"
```

### Task 5: Expose Permission-Safe Import, Commit, and Revoke APIs

**Files:**
- Create: `backend/app/schemas/account_data.py`
- Create: `backend/app/api/account_data.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_account_data_import_api.py`
- Test: `backend/tests/test_account_data_permissions.py`

**Interfaces:**
- Produces: `POST /account-data/{account_id}/imports` multipart upload.
- Produces: `GET /account-data/{account_id}/imports/{batch_id}` preview.
- Produces: `PATCH /account-data/{account_id}/imports/{batch_id}/rows/{row_number}` resolution.
- Produces: `POST /account-data/{account_id}/imports/{batch_id}/commit`.
- Produces: `POST /account-data/{account_id}/imports/{batch_id}/revoke`.
- Produces: `GET /account-data/{account_id}/imports` history.
- Produces: `GET /account-data/{account_id}/status` source coverage.

- [ ] **Step 1: Write end-to-end API and role tests**

```python
async def test_operator_can_preview_and_commit_work_list(client, operator_token, account, workbook):
    preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={"file": ("works.xlsx", workbook, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert preview.status_code == 201
    batch_id = preview.json()["id"]
    committed = await client.post(
        f"/account-data/{account.id}/imports/{batch_id}/commit",
        headers=_auth(operator_token),
    )
    assert committed.json()["status"] == "committed"
```

Also assert reviewer upload returns 403, unassigned member returns 404, unresolved commit returns 409, cross-account batch access returns 404, only lead/admin can revoke, and artifact download requires account access.

- [ ] **Step 2: Run API tests and confirm route failures**

Run: `cd backend && python -m pytest tests/test_account_data_import_api.py tests/test_account_data_permissions.py -q`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement schemas and routes**

Return safe summaries only: artifact responses expose filename, size, hash, and authenticated download URL, never local filesystem paths. All mutations re-load the batch through `(org_id, account_id, batch_id)` and enforce workspace roles.

- [ ] **Step 4: Implement atomic commit and compensating revoke**

Commit refuses rows in `invalid` or `needs_resolution`, projects all rows in one transaction, records created/updated target IDs per staging row, and marks the batch committed last. Revoke deletes only projections still owned by that batch; if a later batch superseded a row, create a conflict requiring manual resolution instead of deleting newer data.

- [ ] **Step 5: Verify API behavior**

Run: `cd backend && python -m pytest tests/test_account_data_import_api.py tests/test_account_data_permissions.py tests/test_security.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the API slice**

```bash
git add backend/app/api/account_data.py backend/app/schemas/account_data.py backend/app/main.py backend/tests/test_account_data_import_api.py backend/tests/test_account_data_permissions.py
git commit -m "feat: expose account data import workflow"
```

### Task 6: Create the Unified Account Data View

**Files:**
- Create: `backend/app/services/account_data_view.py`
- Modify: `backend/app/services/review_workspace.py`
- Modify: `backend/app/schemas/metrics.py`
- Modify: `backend/app/api/metrics.py`
- Test: `backend/tests/test_account_data_view.py`
- Modify: `backend/tests/test_review_workspace_api.py`
- Modify: `backend/tests/test_metrics_api.py`

**Interfaces:**
- Produces: `AccountDataViewService.load(account, period_start, period_end) -> AccountDataView`.
- Produces: `AccountDataView.coverage`, `.freshness`, `.conflicts`, `.content_snapshots`, `.account_snapshots`, `.audience`, and `.benchmarks`.
- Extends: `ReviewDataStatusOut` with `coverage`, `conflict_count`, `source_summary`, and `latest_confirmed_at`.

- [ ] **Step 1: Write source-priority and null-preservation tests**

```python
async def test_official_value_wins_without_destroying_export_evidence(session, account):
    view = await AccountDataViewService(session).load(account, start, end)
    metric = view.content_snapshots[0].metrics["play"]
    assert metric.value == 120
    assert metric.source == DataSourceKind.OFFICIAL_API
    assert {item.value for item in metric.observations} == {100, 120}


async def test_missing_metric_remains_none(session, account):
    view = await AccountDataViewService(session).load(account, start, end)
    assert view.content_snapshots[0].metrics["cover_click_rate"].value is None
```

- [ ] **Step 2: Run unified-view tests and confirm failure**

Run: `cd backend && python -m pytest tests/test_account_data_view.py tests/test_review_workspace_api.py -q`

Expected: FAIL because review still reads raw `MetricSnapshot` rows.

- [ ] **Step 3: Implement priority resolution and coverage calculation**

Resolve per account, content, metric, and observation period. Return both the selected observation and all evidence. Coverage reports expected versus present domains; freshness is based on observation period and confirmation time, not upload filename.

- [ ] **Step 4: Migrate review service behind the compatible response**

Keep old fields populated from the unified view. Add source metadata without changing existing field names. When only account-level daily play exists, trend is valid but content attribution remains empty and its coverage explicitly reports missing.

- [ ] **Step 5: Verify review compatibility**

Run: `cd backend && python -m pytest tests/test_account_data_view.py tests/test_review_workspace_api.py tests/test_metrics_api.py -q`

Expected: PASS for old manual rows and new import-ledger rows.

- [ ] **Step 6: Commit the unified read layer**

```bash
git add backend/app/services/account_data_view.py backend/app/services/review_workspace.py backend/app/schemas/metrics.py backend/app/api/metrics.py backend/tests/test_account_data_view.py backend/tests/test_review_workspace_api.py backend/tests/test_metrics_api.py
git commit -m "feat: unify account data reads"
```

### Task 7: Build the Desktop Account Data Center

**Files:**
- Create: `frontend/src/api/accountData.ts`
- Create: `frontend/src/api/accountData.test.ts`
- Create: `frontend/src/pages/AccountDataCenter.tsx`
- Create: `frontend/src/pages/AccountDataCenter.test.tsx`
- Create: `frontend/src/components/account-data/FileImportFlow.tsx`
- Create: `frontend/src/components/account-data/ImportPreviewTable.tsx`
- Create: `frontend/src/components/account-data/ImportBatchHistory.tsx`
- Create: `frontend/src/styles/account-data-center.css`
- Modify: `frontend/src/appRoutes.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/Accounts.tsx`

**Interfaces:**
- Consumes: Task 5 account-data endpoints.
- Produces: `/accounts/:accountId/data` desktop route.
- Produces: upload, preview, row-resolution, commit, history, and revoke interactions.

- [ ] **Step 1: Write route, API, and interaction tests**

```tsx
it("blocks commit until ambiguous work rows are resolved", async () => {
  renderDataCenter({ preview: previewWithAmbiguousRow });
  expect(screen.getByRole("button", { name: "确认导入" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "选择已有作品" }));
  await userEvent.click(screen.getByText("作品 A · 2026-07-18"));
  expect(screen.getByRole("button", { name: "确认导入" })).toBeEnabled();
});
```

Also cover direct navigation with inaccessible account, unknown template, validation summary, successful commit, and revoke confirmation.

- [ ] **Step 2: Run frontend tests and confirm failure**

Run: `cd frontend && npm.cmd test -- src/api/accountData.test.ts src/pages/AccountDataCenter.test.tsx`

Expected: FAIL because route and components do not exist.

- [ ] **Step 3: Implement the data-center shell and source coverage**

Use the approved warm-neutral shell, restrained red action color, fixed desktop content width, and no nested decorative cards. Header shows account avatar, platform, account status, last confirmed data time, source coverage, conflict count, and “导入数据”.

- [ ] **Step 4: Implement the file-import workflow**

Use a four-step flow inside the page: choose source, upload, verify rows, commit. The preview table pins row status and content match, supports editing mapped values, and keeps the original row expandable. Do not use a full-screen modal for the workflow.

- [ ] **Step 5: Add account-matrix and review entry points**

Add “数据中心” beside sync/account actions. Route navigation must preserve the same current account context and refuse silent fallback to another account.

- [ ] **Step 6: Verify frontend slice**

Run: `cd frontend && npm.cmd test -- src/api/accountData.test.ts src/pages/AccountDataCenter.test.tsx src/pages/Accounts.test.tsx && npm.cmd run build`

Expected: PASS and build succeeds.

- [ ] **Step 7: Commit the desktop data center**

```bash
git add frontend/src/api/accountData.ts frontend/src/api/accountData.test.ts frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx frontend/src/components/account-data frontend/src/styles/account-data-center.css frontend/src/appRoutes.ts frontend/src/App.tsx frontend/src/pages/Accounts.tsx
git commit -m "feat: add account data center workspace"
```

### Task 8: Add Screenshot-Backed and Structured Manual Entry

**Files:**
- Modify: `backend/app/schemas/account_data.py`
- Modify: `backend/app/api/account_data.py`
- Modify: `backend/app/services/data_import/service.py`
- Create: `backend/tests/test_manual_account_data_api.py`
- Create: `frontend/src/components/account-data/ManualDataEntry.tsx`
- Create: `frontend/src/components/account-data/ManualDataEntry.test.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.tsx`
- Modify: `frontend/src/api/accountData.ts`

**Interfaces:**
- Produces: `POST /account-data/{account_id}/manual-previews` with optional screenshot plus structured candidates.
- Reuses: durable staging, confirmation, commit, provenance, and revoke from Tasks 4–5.

- [ ] **Step 1: Write screenshot confirmation and manual form tests**

```python
async def test_screenshot_candidates_cannot_commit_before_confirmation(client, token, account, png):
    preview = await create_screenshot_preview(client, token, account.id, png)
    response = await client.post(
        f"/account-data/{account.id}/imports/{preview['id']}/commit",
        headers=_auth(token),
    )
    assert response.status_code == 409
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `cd backend && python -m pytest tests/test_manual_account_data_api.py -q`

Run: `cd frontend && npm.cmd test -- src/components/account-data/ManualDataEntry.test.tsx`

Expected: both fail because manual preview is not implemented.

- [ ] **Step 3: Implement evidence-first manual entry**

Provide forms for account diagnosis/benchmark, audience dimensions, and account-period totals. A screenshot is displayed beside editable fields. Without a configured vision extractor, fields start blank; the user fills and confirms them. Store source as `screenshot_verified` when an image is attached and `manual_entry` otherwise.

- [ ] **Step 4: Verify manual and screenshot paths**

Run: `cd backend && python -m pytest tests/test_manual_account_data_api.py tests/test_account_data_permissions.py -q`

Run: `cd frontend && npm.cmd test -- src/components/account-data/ManualDataEntry.test.tsx src/pages/AccountDataCenter.test.tsx && npm.cmd run build`

Expected: PASS.

- [ ] **Step 5: Commit manual source support**

```bash
git add backend/app/schemas/account_data.py backend/app/api/account_data.py backend/app/services/data_import/service.py backend/tests/test_manual_account_data_api.py frontend/src/components/account-data/ManualDataEntry.tsx frontend/src/components/account-data/ManualDataEntry.test.tsx frontend/src/pages/AccountDataCenter.tsx frontend/src/api/accountData.ts
git commit -m "feat: add verified manual account data entry"
```

### Task 9: Surface Data Quality in Review and Agent Runtime

**Files:**
- Modify: `backend/app/orchestrator/runtime_tools.py`
- Modify: `backend/tests/test_runtime_tools.py`
- Modify: `frontend/src/api/metrics.ts`
- Modify: `frontend/src/pages/ReviewDashboard.tsx`
- Modify: `frontend/src/pages/ReviewDashboard.test.tsx`
- Modify: `frontend/src/styles/review-dashboard.css`

**Interfaces:**
- Consumes: `AccountDataViewService` from Task 6.
- Produces: `account.data_context` tool response with selected metrics, periods, source, freshness, coverage, conflicts, and evidence references.
- Preserves: existing `account.metrics_summary` name as a compatibility alias for one release.

- [ ] **Step 1: Write Agent evidence and review-state tests**

```python
async def test_account_data_context_exposes_freshness_and_evidence(runtime_context):
    result = await execute_tool("account.data_context", {"days": 30}, runtime_context)
    assert result["coverage"]["content_metrics"] == "partial"
    assert result["metrics"]["play"]["source"] == "platform_export"
    assert result["metrics"]["play"]["evidence_refs"]
```

```tsx
it("shows stale and conflicted data without rendering fake conclusions", () => {
  renderReview(staleConflictWorkspace);
  expect(screen.getByText("数据已过期")).toBeVisible();
  expect(screen.getByText("2 项待处理冲突")).toBeVisible();
  expect(screen.getByRole("link", { name: "更新数据" })).toBeVisible();
});
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `cd backend && python -m pytest tests/test_runtime_tools.py -q`

Run: `cd frontend && npm.cmd test -- src/pages/ReviewDashboard.test.tsx`

Expected: FAIL on missing quality metadata.

- [ ] **Step 3: Replace raw Agent queries with the unified service**

The tool must never infer missing metrics as zero. Include `period_start`, `period_end`, `observed_at`, `confirmed_at`, `source`, `coverage`, `conflict_count`, and evidence references. Keep account and organization IDs server-controlled.

- [ ] **Step 4: Update review source presentation**

The review header shows data sources and cutoff time. Missing domains show explicit readiness rows. Stale/conflicted states keep valid evidence visible but suppress unsupported conclusions. “更新数据” routes to the selected account data center.

- [ ] **Step 5: Verify runtime and review**

Run: `cd backend && python -m pytest tests/test_runtime_tools.py tests/test_review_workspace_api.py -q`

Run: `cd frontend && npm.cmd test -- src/pages/ReviewDashboard.test.tsx && npm.cmd run build`

Expected: PASS.

- [ ] **Step 6: Commit unified consumption**

```bash
git add backend/app/orchestrator/runtime_tools.py backend/tests/test_runtime_tools.py frontend/src/api/metrics.ts frontend/src/pages/ReviewDashboard.tsx frontend/src/pages/ReviewDashboard.test.tsx frontend/src/styles/review-dashboard.css
git commit -m "feat: expose account data quality to review and agents"
```

### Task 10: Production Verification and Deployment Readiness

**Files:**
- Create: `frontend/e2e/account-data-import.spec.ts`
- Modify: `README.md`
- Modify: `docs/tasks/current.md`
- Modify: `docs/superpowers/specs/2026-07-22-multi-source-account-data-center-design.md`

**Interfaces:**
- Verifies: one real account, one 68-row work-list import, review consumption, Agent data context, revoke, and audit trail.

- [ ] **Step 1: Add a desktop end-to-end import scenario**

```ts
test("imports a Douyin work list and exposes it to review", async ({ page }) => {
  await loginAsAdmin(page);
  await page.goto("/accounts/1/data");
  await page.getByRole("button", { name: "导入数据" }).click();
  const header = "作品名称,发布时间,体裁,审核状态,播放量,完播率,5s完播率,封面点击率,2s跳出率,平均播放时长,点赞量,分享量,评论量,收藏量,主页访问量,粉丝增量";
  const rows = Array.from({ length: 68 }, (_, index) =>
    `作品 ${index + 1},2026-07-18 14:11:20,1min-视频,公开,81,0.0875,0.375,,0.375,9.53,6,0,3,0,3,0`,
  );
  await page.getByLabel("选择文件").setInputFiles({
    name: "douyin-work-list.csv",
    mimeType: "text/csv",
    buffer: Buffer.from([header, ...rows].join("\n"), "utf8"),
  });
  await expect(page.getByText("已识别：抖音作品列表")).toBeVisible();
  await page.getByRole("button", { name: "确认导入" }).click();
  await expect(page.getByText("68 条数据已写入")).toBeVisible();
  await page.goto("/review");
  await expect(page.getByText("平台导出")).toBeVisible();
});
```

- [ ] **Step 2: Run the complete backend quality gate**

Run: `cd backend && python -m pytest -q`

Expected: all tests pass.

Run: `cd backend && python -m ruff check app tests`

Expected: no lint errors.

- [ ] **Step 3: Run the complete frontend quality gate**

Run: `cd frontend && npm.cmd test`

Expected: all Vitest tests pass.

Run: `cd frontend && npm.cmd run lint && npm.cmd run build`

Expected: lint and production build pass.

- [ ] **Step 4: Run desktop browser verification**

Run: `cd frontend && npx.cmd playwright test e2e/account-data-import.spec.ts --project=chromium`

Expected: PASS at 1440×900 and no console errors, layout overlap, blank content, or hidden actions.

- [ ] **Step 5: Verify migration on a production-shaped database copy**

Run: `cd backend && alembic upgrade head`

Expected: revision `20260722_0100` applies without deleting legacy `metric_snapshots`.

Run: `cd backend && alembic downgrade 20260721_0400 && alembic upgrade head`

Expected: downgrade/upgrade cycle succeeds on the test database.

- [ ] **Step 6: Update operational documentation**

Document supported templates, 10 MB/10,000-row limits, role permissions, weekly import cadence, evidence retention, conflict handling, revoke semantics, backup requirement, and the explicit absence of mini-program/retired website-data APIs.

- [ ] **Step 7: Commit the verified release candidate**

```bash
git add frontend/e2e/account-data-import.spec.ts README.md docs/tasks/current.md docs/superpowers/specs/2026-07-22-multi-source-account-data-center-design.md
git commit -m "test: verify multi-source account data center"
```

---

## Execution Checkpoints

1. **Checkpoint A — truthful baseline:** Task 1 passes; the UI no longer suggests unavailable Douyin data scopes.
2. **Checkpoint B — backend import usable:** Tasks 2–5 pass; all four workbooks preview, resolve, commit, and revoke through authenticated APIs.
3. **Checkpoint C — operator acceptance:** Tasks 6–8 pass; imported and screenshot-backed data are visible in the data center and review.
4. **Checkpoint D — Agent and production acceptance:** Tasks 9–10 pass; Agent tools report provenance/coverage and the full quality gate is green.

## Rollback Strategy

- Each task is an atomic commit and may be reverted independently.
- Until Task 6, existing review endpoints continue reading legacy snapshots.
- The migration is additive; rollback removes only new account-data tables and nullable columns after verifying no release is consuming them.
- Revoking an import is a business operation recorded in the ledger, not a database migration rollback.
- Original artifacts are retained through the configured evidence-retention period even after a batch is revoked.
