# Permanent Import Batch Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow account leads to permanently delete any import batch, automatically removing owned projections for committed batches while preserving later dependent batches.

**Architecture:** Add one scoped `DELETE` endpoint backed by a transactional deletion service. The service reuses existing revoke conflict and projection cleanup logic, quarantines local artifacts before the database commit, restores them on rollback, and purges them after a successful commit. The React page calls the endpoint through the existing API client, presents an inline destructive confirmation, then refreshes history/status and repairs the active batch selection.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, pytest, React 18, TypeScript, TanStack Query, Ant Design, Vitest, Testing Library, Playwright.

## Global Constraints

- All batch states are eligible for permanent deletion.
- A committed batch must have its owned projections removed before the batch graph is deleted.
- A batch with superseded or later-linked projections returns `409`; no later batch is changed or deleted.
- Only account leads and existing global admins may delete, matching the current revoke permission boundary.
- The endpoint is `DELETE /account-data/{account_id}/imports/{batch_id}` and succeeds with `204 No Content`.
- The operation deletes the batch, artifacts, preview rows, conflicts, and physical source files and cannot be undone.
- No new dependency or database migration is required.
- Preserve all unrelated staged and unstaged workspace changes.

---

### Task 1: Transactional backend deletion service and endpoint

**Files:**
- Modify: `backend/app/services/data_import/service.py`
- Modify: `backend/app/api/account_data.py`
- Test: `backend/tests/test_account_data_import_api.py`
- Test: `backend/tests/test_account_data_permissions.py`
- Test: `backend/tests/test_account_data_transaction_boundaries.py`

**Interfaces:**
- Produces: `DataImportDeleteConflictError(RuntimeError)`.
- Produces: `async delete_batch_permanently(session: AsyncSession, *, org_id: int, account_id: int, batch_id: int, actor: User) -> None`.
- Produces: `DELETE /account-data/{account_id}/imports/{batch_id}` with `204`, `404`, or `409`.
- Reuses: `_load_mutation_batch`, `_find_revoke_conflicts`, `_delete_row_targets`, and `_assert_batch_scope`.

- [ ] **Step 1: Write failing API integration tests for preview and committed deletion**

Add tests that upload a real workbook into `tmp_path`, capture the artifact path, and assert observable outcomes:

```python
@pytest.mark.asyncio
async def test_lead_can_permanently_delete_preview_batch_and_source_file(
    client, session, account_access_setup, operator_token, lead_token, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]
    preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={"file": ("works.xlsx", _workbook_payload(), XLSX_CONTENT_TYPE)},
    )
    batch_id = preview.json()["id"]
    download_url = preview.json()["artifacts"][0]["download_url"]
    assert list(tmp_path.rglob("*.xlsx"))

    response = await client.delete(
        f"/account-data/{account.id}/imports/{batch_id}",
        headers=_auth(lead_token),
    )

    assert response.status_code == 204
    assert await session.get(DataImportBatch, batch_id) is None
    assert list(tmp_path.rglob("*.xlsx")) == []
    assert (await client.get(download_url, headers=_auth(lead_token))).status_code == 404


@pytest.mark.asyncio
async def test_permanent_delete_of_committed_batch_removes_owned_projection(
    client, session, account_access_setup, operator_token, lead_token, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]
    preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={"file": ("daily.xlsx", _daily_play_workbook_payload(), XLSX_CONTENT_TYPE)},
    )
    batch_id = preview.json()["id"]
    assert (
        await client.post(
            f"/account-data/{account.id}/imports/{batch_id}/commit",
            headers=_auth(operator_token),
        )
    ).status_code == 200

    response = await client.delete(
        f"/account-data/{account.id}/imports/{batch_id}",
        headers=_auth(lead_token),
    )

    assert response.status_code == 204
    assert await session.get(DataImportBatch, batch_id) is None
    assert await session.scalar(
        select(AccountMetricSnapshot).where(AccountMetricSnapshot.import_batch_id == batch_id)
    ) is None
```

Use the module’s existing XLSX MIME literal if no shared constant exists; do not introduce a production constant solely for tests.

- [ ] **Step 2: Run the new integration tests and verify RED**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_account_data_import_api.py -k "permanently_delete or permanent_delete" -q
```

Expected: FAIL because the `DELETE` route does not exist and returns `405`.

- [ ] **Step 3: Write failing permission and account-scope tests**

Create separate preview batches and assert the route enforces the existing revoke boundary:

```python
operator_delete = await client.delete(url, headers=_auth(operator_token))
reviewer_delete = await client.delete(url, headers=_auth(reviewer_token))
cross_account_delete = await client.delete(
    f"/account-data/{other_account.id}/imports/{batch_id}",
    headers=_auth(lead_token),
)

assert operator_delete.status_code == 403
assert reviewer_delete.status_code == 403
assert cross_account_delete.status_code == 404
assert await session.get(DataImportBatch, batch_id) is not None
```

Use a second batch to prove the global admin receives `204`, because the lead deletion consumes its target. Run:

```powershell
Set-Location backend
uv run pytest tests/test_account_data_permissions.py -k "delete" -q
```

Expected: FAIL because the `DELETE` route does not exist.

- [ ] **Step 4: Write a failing conflict-preservation test**

Adapt the existing superseded-projection revoke fixture, call `DELETE`, and assert:

```python
assert response.status_code == 409
assert "superseded" in response.json()["detail"]
assert await session.get(DataImportBatch, batch_id) is not None
assert artifact_path.exists()
assert later_content.canonical_import_batch_id == later_batch.id
```

This test must not expect a new `DataConflict` row because a rejected permanent delete leaves the target batch graph unchanged.

- [ ] **Step 5: Write a failing rollback-boundary test**

In `test_account_data_transaction_boundaries.py`, exercise the real deletion service with the test database and real temporary artifact. Patch only `session.commit` to raise after quarantine:

```python
with pytest.raises(RuntimeError, match="commit failed"):
    await delete_batch_permanently(
        session,
        org_id=batch.org_id,
        account_id=batch.account_id,
        batch_id=batch.id,
        actor=lead,
    )

assert artifact_path.read_bytes() == b"source"
assert await session.get(DataImportBatch, batch.id) is not None
```

The break caught is loss of the physical file when the database transaction cannot commit.

Add a parametrized service test for every non-committed status so eligibility does not accidentally narrow later:

```python
@pytest.mark.parametrize(
    "batch_status",
    [
        ImportBatchStatus.UPLOADED,
        ImportBatchStatus.PREVIEW_READY,
        ImportBatchStatus.FAILED,
        ImportBatchStatus.REVOKED,
    ],
)
@pytest.mark.asyncio
async def test_permanent_delete_accepts_every_non_committed_batch_status(
    session, admin, account_access_setup, batch_status, monkeypatch, tmp_path
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]
    batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=account_access_setup["lead"].id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=batch_status,
        template_code="douyin_work_list_v1",
        content_sha256=batch_status.value.encode().hex().ljust(64, "0")[:64],
        revoked_at=datetime.now(UTC) if batch_status is ImportBatchStatus.REVOKED else None,
    )
    session.add(batch)
    await session.commit()

    await delete_batch_permanently(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=batch.id,
        actor=account_access_setup["lead"],
    )

    assert await session.get(DataImportBatch, batch.id) is None
```

- [ ] **Step 6: Implement artifact quarantine helpers**

Add a private immutable move record and three focused helpers:

```python
@dataclass(frozen=True, slots=True)
class QuarantinedArtifact:
    original: Path
    quarantined: Path


def _quarantine_artifacts(storage_keys: list[str]) -> list[QuarantinedArtifact]:
    moved: list[QuarantinedArtifact] = []
    try:
        for storage_key in storage_keys:
            original = storage.resolve(storage_key)
            if not original.exists():
                continue
            quarantined = original.with_name(f".{original.name}.{uuid4().hex}.deleting")
            os.replace(original, quarantined)
            moved.append(QuarantinedArtifact(original=original, quarantined=quarantined))
    except Exception:
        _restore_quarantined_artifacts(moved)
        raise
    return moved


def _restore_quarantined_artifacts(items: list[QuarantinedArtifact]) -> None:
    for item in reversed(items):
        if item.quarantined.exists():
            item.original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(item.quarantined, item.original)


def _purge_quarantined_artifacts(items: list[QuarantinedArtifact]) -> None:
    for item in items:
        item.quarantined.unlink(missing_ok=True)
```

Keep quarantine files in the same directory so `os.replace` remains an atomic same-filesystem move.

- [ ] **Step 7: Implement the deletion service**

Add `DataImportDeleteConflictError` and:

```python
async def delete_batch_permanently(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
    actor: User,
) -> None:
    batch = await _load_mutation_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )
    _assert_batch_scope(batch=batch, user=actor)
    if batch.status is ImportBatchStatus.COMMITTED:
        conflicts = await _find_revoke_conflicts(session=session, batch=batch)
        if conflicts:
            raise DataImportDeleteConflictError("batch contains superseded projections")
        for row in batch.rows:
            await _delete_row_targets(session=session, batch=batch, row=row)

    storage_keys = [artifact.storage_key for artifact in batch.artifacts]
    quarantined = _quarantine_artifacts(storage_keys)
    try:
        await session.delete(batch)
        await session.commit()
    except Exception:
        await session.rollback()
        _restore_quarantined_artifacts(quarantined)
        raise

    try:
        _purge_quarantined_artifacts(quarantined)
    except OSError:
        logger.exception("failed to purge quarantined account data artifacts")
```

Add module logging. Extend the PostgreSQL branch of `_load_mutation_batch` to lock and populate `DataArtifact` rows so `batch.artifacts` is available without an unsafe lazy load:

```python
artifacts = list(
    await session.scalars(
        select(DataArtifact)
        .where(
            DataArtifact.org_id == org_id,
            DataArtifact.account_id == account_id,
            DataArtifact.batch_id == batch_id,
        )
        .order_by(DataArtifact.id)
        .with_for_update()
    )
)
set_committed_value(batch, "artifacts", artifacts)
```

- [ ] **Step 8: Add the FastAPI route**

Import `Response`, the new exception, and service. Add:

```python
@router.delete(
    "/{account_id}/imports/{batch_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_import_batch(
    account_id: int,
    batch_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    account = await require_account_access(session, user, account_id, roles=REVOKE_ROLES)
    try:
        await delete_batch_permanently(
            session,
            org_id=user.org_id,
            account_id=account.id,
            batch_id=batch_id,
            actor=user,
        )
    except DataImportBatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail="import batch does not exist") from exc
    except DataImportDeleteConflictError as exc:
        raise _bad_request(str(exc), status_code=status.HTTP_409_CONFLICT) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 9: Run backend deletion tests and verify GREEN**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_account_data_import_api.py tests/test_account_data_permissions.py tests/test_account_data_transaction_boundaries.py -k "delete" -q
uv run ruff check app/api/account_data.py app/services/data_import/service.py tests/test_account_data_import_api.py tests/test_account_data_permissions.py tests/test_account_data_transaction_boundaries.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 10: Commit the backend deletion slice**

```powershell
git add backend/app/api/account_data.py backend/app/services/data_import/service.py backend/tests/test_account_data_import_api.py backend/tests/test_account_data_permissions.py backend/tests/test_account_data_transaction_boundaries.py
git diff --cached --check -- backend/app/api/account_data.py backend/app/services/data_import/service.py backend/tests/test_account_data_import_api.py backend/tests/test_account_data_permissions.py backend/tests/test_account_data_transaction_boundaries.py
git commit --only backend/app/api/account_data.py backend/app/services/data_import/service.py backend/tests/test_account_data_import_api.py backend/tests/test_account_data_permissions.py backend/tests/test_account_data_transaction_boundaries.py -m "feat: permanently delete account data imports"
```

---

### Task 2: Frontend API contract and destructive history interaction

**Files:**
- Modify: `frontend/src/api/accountData.ts`
- Modify: `frontend/src/api/accountData.test.ts`
- Modify: `frontend/src/components/account-data/ImportBatchHistory.tsx`
- Create: `frontend/src/components/account-data/ImportBatchHistory.test.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.test.tsx`
- Modify: `frontend/src/styles/account-data-center.css`

**Interfaces:**
- Produces: `deleteAccountDataImportBatch(accountId: number, batchId: number): Promise<void>`.
- Extends `ImportBatchHistory` with `deletingBatchId: number | null` and `onDelete(batchId: number): void`.
- Consumes: backend `204`, `403`, `404`, and `409` responses.

- [ ] **Step 1: Write and run a failing API client test**

Add `delete: vi.fn()` to the mocked client, import the new helper, and test:

```typescript
it("permanently deletes one scoped import batch", async () => {
  apiDelete.mockResolvedValueOnce({ data: undefined });

  await deleteAccountDataImportBatch(9, 12);

  expect(apiDelete).toHaveBeenCalledWith("/account-data/9/imports/12");
});
```

Run:

```powershell
Set-Location frontend
pnpm test -- src/api/accountData.test.ts
```

Expected: FAIL because the helper and mocked `api.delete` contract do not exist.

- [ ] **Step 2: Implement the API helper and verify GREEN**

```typescript
export async function deleteAccountDataImportBatch(
  accountId: number,
  batchId: number,
): Promise<void> {
  await api.delete(`/account-data/${accountId}/imports/${batchId}`);
}
```

Re-run `pnpm test -- src/api/accountData.test.ts`; expect PASS.

- [ ] **Step 3: Write failing page tests for confirmation and selection repair**

Add the helper to the page module mock. Cover a committed batch:

```typescript
fireEvent.click(await screen.findByRole("button", { name: "永久删除批次 81" }));
expect(screen.getByText("将先撤销该批次产生的数据，再永久删除原文件和历史记录。"))
  .toBeInTheDocument();
expect(deleteAccountDataImportBatch).not.toHaveBeenCalled();

fireEvent.click(screen.getByRole("button", { name: "确认永久删除批次 81" }));
await waitFor(() => expect(deleteAccountDataImportBatch).toHaveBeenCalledWith(42, 81));
await waitFor(() => expect(listAccountDataImports).toHaveBeenCalledTimes(2));
await waitFor(() => expect(getAccountDataStatus).toHaveBeenCalledTimes(2));
expect(screen.queryByText("批次 81")).not.toBeInTheDocument();
```

Return an empty history from the second `listAccountDataImports` call. Add a second test where the second history contains batch `82` and assert its “查看预览” action remains available after deleting active batch `81`. Add a `409` test and assert the backend detail remains visible while batch `81` remains rendered.

Add a `404` replay test: reject the delete helper with `{ response: { status: 404, data: { detail: "import batch does not exist" } } }`, return an empty refreshed history, and assert the card disappears without an error alert. This proves a repeated delete converges to the already-deleted state.

In the new focused component test, render one item for each status and assert every card has its own permanent-delete action:

```typescript
it.each(["uploaded", "preview_ready", "failed", "revoked", "committed"] as const)(
  "offers permanent deletion for %s batches",
  (status) => {
    render(
      <ImportBatchHistory
        items={[buildSummary(status)]}
        detailsById={new Map()}
        activeBatchId={null}
        revokingBatchId={null}
        deletingBatchId={null}
        revokeError={null}
        onOpenBatch={vi.fn()}
        onDownloadArtifact={vi.fn()}
        onRevoke={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "永久删除批次 81" })).toBeEnabled();
  },
);
```

- [ ] **Step 4: Run page tests and verify RED**

Run:

```powershell
Set-Location frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx
```

Expected: FAIL because the permanent delete button and mutation do not exist.

- [ ] **Step 5: Implement the history-card interaction**

Import `DeleteOutlined`. Add a separate `confirmingDeleteBatchId` state so revoke and delete confirmations do not overwrite each other. Render a danger button for every item:

```tsx
<Button
  size="small"
  danger
  icon={<DeleteOutlined />}
  disabled={deletingBatchId === item.id}
  aria-label={`永久删除批次 ${item.id}`}
  onClick={() => setConfirmingDeleteBatchId(item.id)}
>
  永久删除
</Button>
```

The inline confirmation copy is status-aware:

```tsx
<span>
  {item.status === "committed"
    ? "将先撤销该批次产生的数据，再永久删除原文件和历史记录。"
    : "将永久删除原文件、预览数据和历史记录，且无法恢复。"}
</span>
```

The confirm button uses `danger`, `loading={deletingBatchId === item.id}`, and `aria-label={`确认永久删除批次 ${item.id}`}`. Disable view, download, revoke, and delete actions only for the item being deleted.

Use a shared `.account-data-destructive-confirm` CSS class for both revoke and delete confirmation rows; retain the existing spacing, wrapping, and 12px helper text.

- [ ] **Step 6: Implement page mutation and cache repair**

Extract one refresh helper so success and an already-missing `404` use the same state repair:

```typescript
async function refreshAfterBatchDeletion(batchId: number) {
  queryClient.removeQueries({
    queryKey: ["account-data-import", routeAccountId, batchId],
    exact: true,
  });
  const [historyResult] = await Promise.all([
    historyQuery.refetch(),
    statusQuery.refetch(),
  ]);
  if (!isMountedRef.current) return;
  setHistoryError(null);
  setFlowFeedback({
    tone: "success",
    title: "导入批次已永久删除",
    description: "原文件、批次记录及其产生的数据已完成清理。",
  });
  if (activeBatchId === batchId) {
    const items = historyResult.data?.items ?? [];
    const next = items.find((item) => item.status === "preview_ready") ?? items[0] ?? null;
    setActiveBatchId(next?.id ?? null);
    setDraftBatch(null);
    if (next) {
      setEntryMode(
        next.source_kind === "manual_entry" || next.source_kind === "screenshot_verified"
          ? "manual"
          : "file",
      );
    }
  }
}

const deleteMutation = useMutation({
  mutationFn: (batchId: number) =>
    deleteAccountDataImportBatch(routeAccountId, batchId),
  onSuccess: async (_result, batchId) => {
    await refreshAfterBatchDeletion(batchId);
  },
  onError: async (error, batchId) => {
    if (!isMountedRef.current) return;
    const response = (error as { response?: { status?: number } }).response;
    if (response?.status === 404) {
      await refreshAfterBatchDeletion(batchId);
      return;
    }
    setHistoryError(
      readErrorDetail(error)
        ?? presentApiError(error, "永久删除当前批次失败，请稍后重试。").message,
    );
  },
});
```

Pass `deletingBatchId` and `onDelete` into `ImportBatchHistory`. Clear prior history errors before mutating.

- [ ] **Step 7: Run frontend tests, type checking, and lint**

Run:

```powershell
Set-Location frontend
pnpm test -- src/api/accountData.test.ts src/pages/AccountDataCenter.test.tsx
pnpm exec tsc --noEmit
pnpm exec eslint src/api/accountData.ts src/api/accountData.test.ts src/components/account-data/ImportBatchHistory.tsx src/components/account-data/ImportBatchHistory.test.tsx src/pages/AccountDataCenter.tsx src/pages/AccountDataCenter.test.tsx
```

Expected: all tests pass with no TypeScript or ESLint errors.

- [ ] **Step 8: Commit the frontend slice**

```powershell
git add frontend/src/api/accountData.ts frontend/src/api/accountData.test.ts frontend/src/components/account-data/ImportBatchHistory.tsx frontend/src/components/account-data/ImportBatchHistory.test.tsx frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx frontend/src/styles/account-data-center.css
git diff --cached --check -- frontend/src/api/accountData.ts frontend/src/api/accountData.test.ts frontend/src/components/account-data/ImportBatchHistory.tsx frontend/src/components/account-data/ImportBatchHistory.test.tsx frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx frontend/src/styles/account-data-center.css
git commit --only frontend/src/api/accountData.ts frontend/src/api/accountData.test.ts frontend/src/components/account-data/ImportBatchHistory.tsx frontend/src/components/account-data/ImportBatchHistory.test.tsx frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx frontend/src/styles/account-data-center.css -m "feat: add permanent import deletion controls"
```

---

### Task 3: End-to-end acceptance and full verification

**Files:**
- Modify: `frontend/e2e/account-data-import.spec.ts`

**Interfaces:**
- Consumes: the UI and API contract from Tasks 1–2.
- Produces: browser-level proof that a committed batch can be permanently deleted and its history returns to an empty state.

- [ ] **Step 1: Extend the mock API with deletion**

Add:

```typescript
if (method === "DELETE" && path === "/account-data/1/imports/81") {
  batch = null;
  return route.fulfill({ status: 204, body: "" });
}
```

- [ ] **Step 2: Add the destructive-flow acceptance assertions**

After the existing commit assertions:

```typescript
await page.getByRole("button", { name: "永久删除批次 81" }).click();
await expect(
  page.getByText("将先撤销该批次产生的数据，再永久删除原文件和历史记录。"),
).toBeVisible();
await page.getByRole("button", { name: "确认永久删除批次 81" }).click();
await expect(page.getByText("导入批次已永久删除")).toBeVisible();
await expect(page.getByText("暂无导入历史")).toBeVisible();
```

Move the existing review-provenance assertions before deletion, because the mocked deleted batch should no longer contribute coverage after deletion.

- [ ] **Step 3: Run the targeted Playwright test**

Run:

```powershell
Set-Location frontend
pnpm exec playwright test e2e/account-data-import.spec.ts
```

Expected: PASS with no browser console errors or horizontal overflow.

- [ ] **Step 4: Run the complete affected backend and frontend suites**

Run:

```powershell
Set-Location backend
uv run pytest tests/test_account_data_import_api.py tests/test_account_data_permissions.py tests/test_account_data_transaction_boundaries.py -q
uv run ruff check app/api/account_data.py app/services/data_import/service.py tests/test_account_data_import_api.py tests/test_account_data_permissions.py tests/test_account_data_transaction_boundaries.py

Set-Location ..\frontend
pnpm test -- src/api/accountData.test.ts src/pages/AccountDataCenter.test.tsx
pnpm build
```

Expected: all tests pass; Ruff, TypeScript, ESLint through the build, and Vite complete without errors.

- [ ] **Step 5: Review the final diff and commit acceptance coverage**

```powershell
Set-Location ..
git diff --check
git diff --stat
git add frontend/e2e/account-data-import.spec.ts
git diff --cached --check -- frontend/e2e/account-data-import.spec.ts
git commit --only frontend/e2e/account-data-import.spec.ts -m "test: cover permanent import deletion flow"
```

Confirm that no unrelated pre-existing workspace changes were added to any commit.
