# Account Data Center Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current stretched two-column account data page with a truthful three-view workspace for overview, full-width import validation, and dense import history.

**Architecture:** Keep `AccountDataCenter` as the account-scoped orchestration boundary, but move each user job into a focused component and load only the active batch detail. Add an account-scoped paginated row endpoint so the validation table can show 50 rows at a time without rendering a 10,000-row batch. Reuse the existing import, manual-entry, commit, revoke, delete, and download mutations.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Pydantic v2, React 18, TypeScript, TanStack Query, Ant Design, Vitest, Testing Library, Playwright, CSS.

## Global Constraints

- The page has exactly three primary views: `数据概览`, `导入与补录`, and `导入记录`.
- Import preview and import history must never return to a side-by-side layout at any breakpoint.
- `preview_ready` means pending confirmation and must not increase confirmed coverage.
- Technical template codes, parser versions, hashes, and raw headers stay out of the default operator view.
- The import table defaults to 50 rows per page and must not mount 10,000 DOM rows.
- Account ID remains part of every query key, mutation, and API authorization check.
- Existing file size limits remain 10 MiB for import files and 5 MiB for manual-entry screenshots.
- Existing revoke, permanent-delete, and workspace-role permissions do not change.
- No new frontend or backend dependency is introduced.
- Production deployment is out of scope until localhost verification is complete and the user explicitly approves release.

---

## File Structure

### Backend

- Modify `backend/app/schemas/account_data.py`
  - Define the paginated row response, row-view filter, and history creator fields.
- Modify `backend/app/services/data_import/service.py`
  - Add the account-scoped row page query, count aggregation, and history creator projection.
- Modify `backend/app/api/account_data.py`
  - Expose `GET /account-data/{account_id}/imports/{batch_id}/rows`.
- Modify `backend/tests/test_account_data_import_api.py`
  - Cover paging, filtering, counts, authorization, and account isolation.

### Frontend API and presentation metadata

- Modify `frontend/src/api/accountData.ts`
  - Add row page types and `getAccountDataImportRows`.
- Modify `frontend/src/api/accountData.test.ts`
  - Verify query parameters and account-scoped endpoint construction.
- Modify `frontend/src/components/account-data/statusMeta.ts`
  - Centralize business labels for templates, sources, batches, and coverage.
- Create `frontend/src/components/account-data/statusMeta.test.ts`
  - Lock operator-facing names and prevent technical-code regressions.

### Frontend views

- Modify `frontend/src/pages/AccountDataCenter.tsx`
  - Own tab state, current preview batch, lazy detail query, and shared mutations only.
- Modify `frontend/src/pages/AccountDataCenter.test.tsx`
  - Cover orchestration, account isolation, and cross-view refresh.
- Create `frontend/src/components/account-data/AccountDataHeader.tsx`
  - Render compact account and confirmed-data status.
- Create `frontend/src/components/account-data/AccountDataTabs.tsx`
  - Render accessible primary tabs.
- Create `frontend/src/components/account-data/DataCoverageOverview.tsx`
  - Render the truthful coverage conclusion and domain list.
- Create `frontend/src/components/account-data/ImportWorkspace.tsx`
  - Coordinate file/manual entry and preview state.
- Create `frontend/src/components/account-data/ImportProgress.tsx`
  - Render completed/current/upcoming/error steps.
- Create `frontend/src/components/account-data/ImportSummary.tsx`
  - Render operator-facing file, period, and row counts.
- Replace `frontend/src/components/account-data/ImportPreviewTable.tsx`
  - Render template-aware paginated rows and row-level resolution.
- Create `frontend/src/components/account-data/ImportCommitBar.tsx`
  - Keep blocking counts and the confirm action visible.
- Replace `frontend/src/components/account-data/ImportBatchHistory.tsx`
  - Render the dense history table and per-row action menu.
- Retain `frontend/src/components/account-data/ManualDataEntry.tsx`
  - Integrate it as a secondary method within the import view.
- Modify `frontend/src/styles/account-data-center.css`
  - Implement full-width layouts, responsive behavior, and sticky confirmation.
- Modify `frontend/e2e/account-data-import.spec.ts`
  - Cover the approved layout and end-to-end mocked import flow.

---

### Task 1: Add the account-scoped paginated import-row endpoint

**Files:**
- Modify: `backend/app/schemas/account_data.py`
- Modify: `backend/app/services/data_import/service.py`
- Modify: `backend/app/api/account_data.py`
- Test: `backend/tests/test_account_data_import_api.py`

**Interfaces:**
- Produces:
  - `ImportRowPageOut`
  - `ImportRowView = Literal["all", "ready", "needs_work"]`
  - `load_scoped_import_rows(session, *, org_id, account_id, batch_id, page, page_size, view)`
  - `GET /account-data/{account_id}/imports/{batch_id}/rows?page=1&page_size=50&view=all`
  - `ImportBatchSummaryOut.created_by_id`
  - `ImportBatchSummaryOut.created_by_name`
- Consumes:
  - Existing `DataImportBatch`, `DataImportRow`, `ImportRowStatus`, and `require_account_access`.

- [ ] **Step 1: Write failing API tests for paging and counts**

Add tests that create one batch with 61 rows:

```python
response = await client.get(
    f"/account-data/{account.id}/imports/{batch.id}/rows",
    headers=_auth(operator_token),
    params={"page": 2, "page_size": 50, "view": "all"},
)

assert response.status_code == 200
assert response.json()["page"] == 2
assert response.json()["page_size"] == 50
assert response.json()["total_count"] == 61
assert response.json()["filtered_count"] == 61
assert response.json()["ready_count"] == 59
assert response.json()["blocking_count"] == 2
assert len(response.json()["items"]) == 11
```

Add a second assertion for `view=needs_work`:

```python
blocked = await client.get(
    f"/account-data/{account.id}/imports/{batch.id}/rows",
    headers=_auth(operator_token),
    params={"page": 1, "page_size": 50, "view": "needs_work"},
)

assert blocked.status_code == 200
assert blocked.json()["filtered_count"] == 2
assert {item["status"] for item in blocked.json()["items"]} == {
    "invalid",
    "needs_resolution",
}
```

- [ ] **Step 2: Write failing tests for validation and isolation**

Cover:

```python
assert (
    await client.get(
        f"/account-data/{account.id}/imports/{batch.id}/rows",
        headers=_auth(operator_token),
        params={"page_size": 201},
    )
).status_code == 422

assert (
    await client.get(
        f"/account-data/{other_account.id}/imports/{batch.id}/rows",
        headers=_auth(operator_token),
    )
).status_code == 404
```

Also assert an outsider without account access receives the existing concealment response `404`.

- [ ] **Step 3: Write a failing history-creator projection test**

After listing imports, assert:

```python
history = await client.get(
    f"/account-data/{account.id}/imports",
    headers=_auth(operator_token),
)

assert history.status_code == 200
assert history.json()["items"][0]["created_by_id"] == account_access_setup["operator"].id
assert history.json()["items"][0]["created_by_name"] == "Operator"
```

This test prevents the redesigned history table from displaying internal user IDs as operator names.

- [ ] **Step 4: Run the focused backend tests and confirm failure**

Run:

```powershell
cd backend
uv run pytest tests/test_account_data_import_api.py -k "row_page or paginated_rows" -v
```

Expected: FAIL because the collection route and response schema do not exist.

- [ ] **Step 5: Add the response schema**

Add to `backend/app/schemas/account_data.py`:

```python
ImportRowView = Literal["all", "ready", "needs_work"]


class ImportRowPageOut(BaseModel):
    items: list[ImportRowOut] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    total_count: int = Field(ge=0)
    filtered_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    blocking_count: int = Field(ge=0)
    total_pages: int = Field(ge=0)
```

Extend `ImportBatchSummaryOut`:

```python
created_by_id: int | None = None
created_by_name: str | None = None
```

- [ ] **Step 6: Implement the scoped query**

In `backend/app/services/data_import/service.py`, add a frozen result dataclass:

```python
@dataclass(frozen=True, slots=True)
class ImportRowPage:
    items: list[DataImportRow]
    total_count: int
    filtered_count: int
    ready_count: int
    blocking_count: int
```

Implement `load_scoped_import_rows` with these rules:

```python
READY_ROW_STATUSES = {ImportRowStatus.READY, ImportRowStatus.COMMITTED}
BLOCKING_ROW_STATUSES = {
    ImportRowStatus.INVALID,
    ImportRowStatus.NEEDS_RESOLUTION,
}
```

The function must:

1. Verify the batch by `org_id`, `account_id`, and `batch_id`.
2. Count all rows, ready rows, and blocking rows for that same scope.
3. Apply the optional view filter.
4. Order by `DataImportRow.row_number.asc()`.
5. Apply `offset=(page - 1) * page_size` and `limit=page_size`.
6. Raise `DataImportBatchNotFoundError` when the batch is outside scope.

- [ ] **Step 7: Project the batch creator in history**

Change `list_scoped_batches` to return a focused dataclass:

```python
@dataclass(frozen=True, slots=True)
class ImportBatchListItem:
    batch: DataImportBatch
    created_by_name: str | None
```

Select `DataImportBatch` plus `User.display_name` using an outer join on
`DataImportBatch.created_by_id == User.id`. Preserve the existing organization and
account filters and descending batch order. Update `_batch_summary_out` to accept
`created_by_name` and return both creator fields.

- [ ] **Step 8: Add the GET route**

Add before the existing row PATCH route:

```python
@router.get(
    "/{account_id}/imports/{batch_id}/rows",
    response_model=ImportRowPageOut,
)
async def list_import_rows(
    account_id: int,
    batch_id: int,
    user: CurrentUser,
    session: SessionDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    view: ImportRowView = "all",
) -> ImportRowPageOut:
    account = await require_account_access(session, user, account_id)
    result = await load_scoped_import_rows(
        session,
        org_id=user.org_id,
        account_id=account.id,
        batch_id=batch_id,
        page=page,
        page_size=page_size,
        view=view,
    )
    total_pages = (
        math.ceil(result.filtered_count / page_size)
        if result.filtered_count
        else 0
    )
    return ImportRowPageOut(
        items=[_row_out(item) for item in result.items],
        page=page,
        page_size=page_size,
        total_count=result.total_count,
        filtered_count=result.filtered_count,
        ready_count=result.ready_count,
        blocking_count=result.blocking_count,
        total_pages=total_pages,
    )
```

Import `math`, `Query`, `ImportRowPageOut`, `ImportRowView`, and `load_scoped_import_rows`.

- [ ] **Step 9: Run backend tests, lint, and type checks**

Run:

```powershell
cd backend
uv run pytest tests/test_account_data_import_api.py -v
uv run ruff check app/api/account_data.py app/schemas/account_data.py app/services/data_import/service.py tests/test_account_data_import_api.py
uv run mypy app/api/account_data.py app/schemas/account_data.py app/services/data_import/service.py
```

Expected: all commands exit `0`.

- [ ] **Step 10: Commit the endpoint**

```powershell
git add -- backend/app/api/account_data.py backend/app/schemas/account_data.py backend/app/services/data_import/service.py backend/tests/test_account_data_import_api.py
git commit -m "feat: paginate account import rows"
```

---

### Task 2: Add frontend row paging and business presentation metadata

**Files:**
- Modify: `frontend/src/api/accountData.ts`
- Modify: `frontend/src/api/accountData.test.ts`
- Modify: `frontend/src/components/account-data/statusMeta.ts`
- Create: `frontend/src/components/account-data/statusMeta.test.ts`

**Interfaces:**
- Consumes: Task 1 `ImportRowPageOut` and `ImportRowView`.
- Produces:
  - `AccountDataImportRowView`
  - `AccountDataImportRowPage`
  - `getAccountDataImportRows(accountId, batchId, options)`
  - `getTemplateLabel(templateCode)`
  - `getCoverageLabel(domain)`
  - `getSourceKindLabel(sourceKind)`

- [ ] **Step 1: Write failing API-client tests**

Add:

```typescript
await getAccountDataImportRows(9, 12, {
  page: 2,
  pageSize: 50,
  view: "needs_work",
});

expect(apiGet).toHaveBeenCalledWith(
  "/account-data/9/imports/12/rows",
  {
    params: {
      page: 2,
      page_size: 50,
      view: "needs_work",
    },
  },
);
```

Also verify the defaults send `{ page: 1, page_size: 50, view: "all" }`.

- [ ] **Step 2: Write failing metadata tests**

Create `statusMeta.test.ts` with:

```typescript
expect(getTemplateLabel("douyin_daily_play_v1")).toBe("抖音日播放数据");
expect(getTemplateLabel("douyin_single_content_v1")).toBe("抖音单作品分析");
expect(getTemplateLabel("douyin_period_aggregate_v1")).toBe("抖音阶段汇总");
expect(getTemplateLabel("douyin_work_list_v1")).toBe("抖音作品列表");
expect(getTemplateLabel("custom_future_template")).toBe("其他账号数据");
```

Verify source and coverage labels in the same file.

- [ ] **Step 3: Run the tests and confirm failure**

Run:

```powershell
cd frontend
pnpm test -- src/api/accountData.test.ts src/components/account-data/statusMeta.test.ts
```

Expected: FAIL because the exported interfaces and helpers do not exist.

- [ ] **Step 4: Add the frontend row-page contract**

Add to `accountData.ts`:

```typescript
export type AccountDataImportRowView = "all" | "ready" | "needs_work";

export interface AccountDataImportRowPage {
  items: AccountDataImportRow[];
  page: number;
  page_size: number;
  total_count: number;
  filtered_count: number;
  ready_count: number;
  blocking_count: number;
  total_pages: number;
}

export async function getAccountDataImportRows(
  accountId: number,
  batchId: number,
  options: {
    page?: number;
    pageSize?: number;
    view?: AccountDataImportRowView;
  } = {},
): Promise<AccountDataImportRowPage> {
  const { data } = await api.get<AccountDataImportRowPage>(
    `/account-data/${accountId}/imports/${batchId}/rows`,
    {
      params: {
        page: options.page ?? 1,
        page_size: options.pageSize ?? 50,
        view: options.view ?? "all",
      },
    },
  );
  return data;
}
```

- [ ] **Step 5: Add creator fields to the frontend batch summary**

Extend `AccountDataImportBatchSummary`:

```typescript
created_by_id: number | null;
created_by_name: string | null;
```

Update API fixtures to use human-readable creator names such as `运营人员`.

- [ ] **Step 6: Implement operator-facing metadata**

In `statusMeta.ts`, use explicit records:

```typescript
const TEMPLATE_LABELS: Record<string, string> = {
  douyin_daily_play_v1: "抖音日播放数据",
  douyin_single_content_v1: "抖音单作品分析",
  douyin_period_aggregate_v1: "抖音阶段汇总",
  douyin_work_list_v1: "抖音作品列表",
};

export function getTemplateLabel(templateCode: string) {
  return TEMPLATE_LABELS[templateCode] ?? "其他账号数据";
}
```

Add equivalent helpers for source kinds and the four coverage domains. Do not return the unknown technical code as fallback text.

- [ ] **Step 7: Run tests, lint, and type checks**

Run:

```powershell
cd frontend
pnpm test -- src/api/accountData.test.ts src/components/account-data/statusMeta.test.ts
pnpm lint
pnpm build
```

Expected: all commands exit `0`.

- [ ] **Step 8: Commit the client contract**

```powershell
git add -- frontend/src/api/accountData.ts frontend/src/api/accountData.test.ts frontend/src/components/account-data/statusMeta.ts frontend/src/components/account-data/statusMeta.test.ts
git commit -m "feat: add paged import row client"
```

---

### Task 3: Create the shared account header, tabs, and truthful data overview

**Files:**
- Create: `frontend/src/components/account-data/AccountDataHeader.tsx`
- Create: `frontend/src/components/account-data/AccountDataTabs.tsx`
- Create: `frontend/src/components/account-data/DataCoverageOverview.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.test.tsx`
- Modify: `frontend/src/styles/account-data-center.css`

**Interfaces:**
- Produces:
  - `AccountDataView = "overview" | "import" | "history"`
  - `<AccountDataHeader account status pendingBatch onUpdateData />`
  - `<AccountDataTabs value onChange />`
  - `<DataCoverageOverview status onImportDomain onAnalyze />`
- Consumes:
  - Existing account response and `AccountDataStatus`.
  - Task 2 presentation helpers.

- [ ] **Step 1: Write failing page tests for the three primary views**

Add assertions:

```typescript
expect(await screen.findByRole("tab", { name: "数据概览" })).toHaveAttribute(
  "aria-selected",
  "true",
);
expect(screen.getByRole("tab", { name: "导入与补录" })).toBeInTheDocument();
expect(screen.getByRole("tab", { name: "导入记录" })).toBeInTheDocument();
expect(screen.getByText("当前账号已有部分可用数据")).toBeInTheDocument();
```

Click `导入与补录` and assert the overview panel is hidden and the import panel is visible.

- [ ] **Step 2: Write failing truthfulness tests**

Mock `latest_confirmed_at: null`, all coverage values `missing`, and one `preview_ready` batch. Assert:

```typescript
expect(screen.getByText("暂无已确认数据")).toBeInTheDocument();
expect(screen.getByText("有 1 个批次等待确认")).toBeInTheDocument();
expect(screen.queryByText("已有可用数据")).not.toBeInTheDocument();
```

- [ ] **Step 3: Run tests and confirm failure**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx
```

Expected: FAIL because the tabs and new overview copy are absent.

- [ ] **Step 4: Implement `AccountDataTabs`**

Use native buttons with:

```tsx
<div className="account-data-tabs" role="tablist" aria-label="账号数据中心视图">
  {items.map((item) => (
    <button
      key={item.value}
      id={`account-data-tab-${item.value}`}
      type="button"
      role="tab"
      aria-selected={value === item.value}
      aria-controls={`account-data-panel-${item.value}`}
      onClick={() => onChange(item.value)}
    >
      {item.label}
    </button>
  ))}
</div>
```

- [ ] **Step 5: Implement the compact header**

`AccountDataHeader` must show:

- Avatar, nickname, platform, and account status.
- Latest confirmed date or `暂无已确认数据`.
- A badge only when a preview is pending.
- One primary action `更新数据`.

Remove the existing hero metrics for source count, conflict count, and current selected history batch.

- [ ] **Step 6: Implement the coverage overview**

Build the conclusion from confirmed `status.coverage` only. Render four rows with label, state, freshness, source summary, and one contextual action. Use `status.sources` to show the latest source per domain.

When no domain is confirmed, render:

```text
当前账号还没有可供运营分析的数据。
```

with one action `导入第一份数据`.

- [ ] **Step 7: Wire view state into the page**

Initialize:

```typescript
const pendingBatch = history.items.find((item) => item.status === "preview_ready") ?? null;
const [activeView, setActiveView] = useState<AccountDataView>(
  pendingBatch ? "import" : "overview",
);
```

Because history loads asynchronously, use a one-time ref to auto-open import only when the initial response contains a pending preview. Never switch tabs again after the user has made a choice.

- [ ] **Step 8: Add the initial responsive styles**

Remove `.account-data-hero`, `.account-data-coverage`, and the two-column `.account-data-layout` from the active page structure. Add:

- Compact account strip.
- Underlined primary tabs.
- Full-width tab panels.
- Domain rows that become two columns and then one column at narrower widths.

- [ ] **Step 9: Run page tests and build**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx
pnpm lint
pnpm build
```

Expected: all commands exit `0`.

- [ ] **Step 10: Commit the shell and overview**

```powershell
git add -- frontend/src/components/account-data/AccountDataHeader.tsx frontend/src/components/account-data/AccountDataTabs.tsx frontend/src/components/account-data/DataCoverageOverview.tsx frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx frontend/src/styles/account-data-center.css
git commit -m "feat: restructure account data center views"
```

---

### Task 4: Remove eager batch-detail loading and isolate active account state

**Files:**
- Modify: `frontend/src/pages/AccountDataCenter.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.test.tsx`

**Interfaces:**
- Produces:
  - One `activeBatchId`.
  - One active batch detail query.
  - Query keys that always include `routeAccountId`.
- Consumes:
  - Existing `getAccountDataImportBatch`.
  - Task 3 view state.

- [ ] **Step 1: Write a failing lazy-loading test**

Return three history summaries and assert:

```typescript
await screen.findByRole("tab", { name: "导入记录" });
expect(getAccountDataImportBatch).not.toHaveBeenCalled();

fireEvent.click(screen.getByRole("tab", { name: "导入记录" }));
fireEvent.click(screen.getByRole("button", { name: "查看批次 81" }));

await waitFor(() =>
  expect(getAccountDataImportBatch).toHaveBeenCalledTimes(1),
);
expect(getAccountDataImportBatch).toHaveBeenCalledWith(42, 81);
```

If the initial list has one `preview_ready` batch, assert exactly that batch is loaded once for the import view.

- [ ] **Step 2: Write an account-switch isolation test**

Navigate from account 42 to account 99. Assert:

- `activeBatchId` does not retain account 42’s batch.
- The row query and detail query use account 99.
- No account 42 preview remains visible while account 99 loads.

- [ ] **Step 3: Run tests and confirm the eager-query failure**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx -t "lazy|account switch"
```

Expected: FAIL because `useQueries` currently loads every history item.

- [ ] **Step 4: Replace `useQueries` with one active query**

Remove:

- `useQueries`
- `detailsById`
- conflict aggregation across every batch

Add:

```typescript
const activeBatchQuery = useQuery({
  enabled: activeBatchId != null,
  queryKey: ["account-data-import", routeAccountId, activeBatchId],
  queryFn: () => getAccountDataImportBatch(routeAccountId, activeBatchId!),
  retry: false,
});
```

Store the just-created preview in the same query key with `queryClient.setQueryData`, then set its ID active.

- [ ] **Step 5: Reset account-scoped UI state**

The existing keyed workspace remount remains the primary reset. Also ensure:

- Active view derives again for the new account.
- Row page and filter start at page 1 / `all`.
- Flow feedback and history errors clear.
- No cross-account cache key omits `routeAccountId`.

- [ ] **Step 6: Run the full page tests**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx
```

Expected: PASS and detail calls equal the number of explicitly opened batches.

- [ ] **Step 7: Commit lazy batch loading**

```powershell
git add -- frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx
git commit -m "perf: load account import details on demand"
```

---

### Task 5: Build the full-width import validation workspace

**Files:**
- Create: `frontend/src/components/account-data/ImportWorkspace.tsx`
- Create: `frontend/src/components/account-data/ImportProgress.tsx`
- Create: `frontend/src/components/account-data/ImportSummary.tsx`
- Replace: `frontend/src/components/account-data/ImportPreviewTable.tsx`
- Create: `frontend/src/components/account-data/ImportCommitBar.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.test.tsx`
- Modify: `frontend/src/styles/account-data-center.css`

**Interfaces:**
- Consumes:
  - Task 2 `getAccountDataImportRows`.
  - Active batch metadata from Task 4.
  - Existing upload, resolve-row, and commit mutations.
- Produces:
  - A full-width import view with paged rows.
  - `ImportProgressState = "select" | "recognizing" | "review" | "commit" | "failed"`.
  - Row filter `all | ready | needs_work`.

- [ ] **Step 1: Write failing tests for progress semantics**

For a preview-ready batch, assert:

```typescript
expect(screen.getByText("选择文件").closest("li")).toHaveClass("is-complete");
expect(screen.getByText("自动识别").closest("li")).toHaveClass("is-complete");
expect(screen.getByText("校验数据").closest("li")).toHaveClass("is-current");
expect(screen.getByText("确认写入").closest("li")).toHaveClass("is-upcoming");
```

For an upload error, assert the identifying step has `is-error` and includes a retry action.

- [ ] **Step 2: Write failing tests for paged rows and filters**

Mock:

```typescript
vi.mocked(getAccountDataImportRows).mockResolvedValue({
  items: rows.slice(0, 50),
  page: 1,
  page_size: 50,
  total_count: 68,
  filtered_count: 68,
  ready_count: 67,
  blocking_count: 1,
  total_pages: 2,
});
```

Assert:

- Exactly 50 body rows render.
- `下一页` requests page 2.
- `需处理 1` requests page 1 with `view=needs_work`.
- The active query key includes account ID and batch ID.

- [ ] **Step 3: Write failing tests for the commit bar**

For `blocking_count=1`:

```typescript
expect(screen.getByRole("button", { name: "确认写入 68 条" })).toBeDisabled();
expect(screen.getByText("仍有 1 条需要处理")).toBeInTheDocument();
```

For `blocking_count=0`, assert the button is enabled and calls the existing commit mutation.

- [ ] **Step 4: Run the focused tests and confirm failure**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx -t "progress|paged rows|commit bar"
```

Expected: FAIL because the new workspace components do not exist.

- [ ] **Step 5: Implement import progress and summary**

`ImportProgress` renders four semantic list items with distinct classes and icons. `ImportSummary` shows:

- Original filename.
- Operator-facing template label.
- Period.
- Total / ready / blocking counts.

Put parser version, technical code, and original headers inside:

```tsx
<details className="account-data-technical-details">
  <summary>技术校验详情</summary>
  ...
</details>
```

- [ ] **Step 6: Implement the template-aware row table**

Create a column registry inside `ImportPreviewTable.tsx`:

```typescript
const TEMPLATE_COLUMNS: Record<string, ImportColumn[]> = {
  douyin_daily_play_v1: [
    { key: "stat_date", label: "日期" },
    { key: "play", label: "播放量", numeric: true },
  ],
  douyin_single_content_v1: [
    { key: "title", label: "作品" },
    { key: "published_at", label: "发布时间" },
    { key: "play", label: "播放量", numeric: true },
  ],
  douyin_period_aggregate_v1: [
    { key: "period_start", label: "开始日期" },
    { key: "period_end", label: "结束日期" },
    { key: "publish_count", label: "发布数", numeric: true },
    { key: "median_play", label: "播放中位数", numeric: true },
  ],
  douyin_work_list_v1: [
    { key: "title", label: "作品" },
    { key: "published_at", label: "发布时间" },
    { key: "play", label: "播放量", numeric: true },
  ],
};
```

Keep status and validation-result columns for every template. Raw values and candidate resolution stay in row details.

- [ ] **Step 7: Implement row paging and filter state**

In `ImportWorkspace`:

```typescript
const [rowPage, setRowPage] = useState(1);
const [rowView, setRowView] = useState<AccountDataImportRowView>("all");

const rowsQuery = useQuery({
  enabled: batch?.id != null,
  queryKey: [
    "account-data-import-rows",
    accountId,
    batch?.id,
    rowPage,
    rowView,
  ],
  queryFn: () => getAccountDataImportRows(
    accountId,
    batch!.id,
    { page: rowPage, pageSize: 50, view: rowView },
  ),
});
```

Changing filter resets the page to `1`. Resolving a row invalidates the current row-page query and active batch detail.

- [ ] **Step 8: Implement the commit bar**

Render it after the table and make it sticky within the application content viewport. It must:

- Show total and blocking counts.
- Explain why confirmation is disabled.
- Use `确认写入 {total_count} 条` as the primary label.
- Leave enough bottom padding that the last table row remains visible.

- [ ] **Step 9: Connect file upload and success state**

After upload:

- Set the returned batch active.
- Switch to the import view.
- Reset row filter/page.
- Fetch row page 1.

After commit:

- Invalidate account status, history, active detail, and row page.
- Show the write count and period.
- Offer `查看数据概览`.
- Offer `交给运营大脑分析` by using `useNavigate` and the existing Brain draft contract:

```typescript
navigate("/", {
  state: {
    agentDraft: `分析账号“${account.nickname}”刚确认写入的${getTemplateLabel(batch.template_code)}，先总结数据变化，再告诉我下一步最值得做什么。`,
    agentMode: "discuss",
  },
});
```

The current workspace store already carries the selected account ID, so the handoff must not
create or switch account context on its own.

- [ ] **Step 10: Apply the full-width responsive CSS**

Remove the old two-column rules. Ensure:

- The import workspace uses `width: 100%`.
- Table numeric columns align right.
- At 1280px, core columns fit without horizontal scrolling.
- At narrow widths, the table has contained horizontal scrolling.
- The commit bar does not cover the last row.
- Reduced-motion mode disables nonessential transitions.

- [ ] **Step 11: Run component tests, lint, and build**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx
pnpm lint
pnpm build
```

Expected: all commands exit `0`.

- [ ] **Step 12: Commit the import workspace**

```powershell
git add -- frontend/src/components/account-data/ImportWorkspace.tsx frontend/src/components/account-data/ImportProgress.tsx frontend/src/components/account-data/ImportSummary.tsx frontend/src/components/account-data/ImportPreviewTable.tsx frontend/src/components/account-data/ImportCommitBar.tsx frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx frontend/src/styles/account-data-center.css
git commit -m "feat: add full-width account import review"
```

---

### Task 6: Replace import-history cards with a dense table

**Files:**
- Replace: `frontend/src/components/account-data/ImportBatchHistory.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.test.tsx`
- Modify: `frontend/src/styles/account-data-center.css`

**Interfaces:**
- Consumes:
  - Batch summaries only for the first render.
  - Existing open, download, revoke, and delete callbacks.
  - Task 2 business labels.
- Produces:
  - `<ImportBatchHistory ... />` as the `导入记录` tab panel.

- [x] **Step 1: Write failing history-table tests**

Assert:

```typescript
expect(screen.getByRole("table", { name: "导入记录" })).toBeInTheDocument();
expect(screen.getByRole("columnheader", { name: "数据类型" })).toBeInTheDocument();
expect(screen.getByText("抖音作品列表")).toBeInTheDocument();
expect(screen.queryByText("douyin_work_list_v1")).not.toBeInTheDocument();
```

Assert a row has one explicit `查看` action and one `更多` menu trigger, while destructive actions are not visible until `更多` opens.

- [x] **Step 2: Write failing revoke/delete flow tests**

Cover:

- A committed batch says permanent deletion will revoke written data first.
- A preview batch says it will delete preview rows and source files.
- Second confirmation is still required.
- Successful deletion refreshes history and status and removes the row.

- [x] **Step 3: Run tests and confirm failure**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx -t "history table|permanent delete|revoke"
```

Expected: FAIL because history is still rendered as cards.

- [x] **Step 4: Implement the table**

Use an accessible table with these columns:

```text
批次 | 数据类型 | 来源 | 数据周期 | 记录数 | 状态 | 创建时间 | 操作
```

Use `getTemplateLabel`, `getSourceKindLabel`, and `getBatchStatusLabel`. Show `—` for a missing period, not `窗口 — —`.
Render `created_by_name ?? "已删除成员"` in the creator column; never display a
raw user ID as the primary operator label.

- [x] **Step 5: Implement the row action menu**

Use Ant Design `Dropdown` or the project’s existing menu primitive. Menu items:

- Download original file, when an artifact exists.
- Revoke import, only for committed batches.
- Permanently delete.

Loading the artifact list is allowed only after the user opens or selects that batch. Do not restore eager detail queries for every row.

- [x] **Step 6: Preserve inline confirmations**

Do not introduce a modal as the first interaction. Expand a confirmation row beneath the selected batch, with:

- Consequence text.
- Confirm action.
- Cancel action.

Only one confirmation can be open at once.

- [x] **Step 7: Style density and responsiveness**

- Stable compact row height.
- Quiet table header.
- No card border around every batch.
- On mobile, each row becomes a labeled batch summary with the same action menu.

- [x] **Step 8: Run tests and build**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx
pnpm lint
pnpm build
```

Expected: all commands exit `0`.

- [ ] **Step 9: Commit the history table**

```powershell
git add -- frontend/src/components/account-data/ImportBatchHistory.tsx frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx frontend/src/styles/account-data-center.css
git commit -m "feat: add dense account import history"
```

---

### Task 7: Integrate manual entry, loading, errors, and accessible responsive behavior

**Files:**
- Modify: `frontend/src/components/account-data/ManualDataEntry.tsx`
- Modify: `frontend/src/components/account-data/ImportWorkspace.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.tsx`
- Modify: `frontend/src/pages/AccountDataCenter.test.tsx`
- Modify: `frontend/src/styles/account-data-center.css`

**Interfaces:**
- Consumes: existing manual preview, manual confirmation, and commit mutations.
- Produces: one import view with `文件导入` as the primary method and `人工补录` as the secondary method.

- [ ] **Step 1: Write failing method-switch tests**

Assert:

- The import view starts in file mode.
- `其他补录方式` reveals `人工补录`.
- Switching methods does not lose an existing active file preview without confirmation.
- A manual preview switches the progress state to review and uses the same commit bar counts.

- [ ] **Step 2: Write failing loading and error tests**

Cover:

- Account/status/history loading preserves header and tab skeletons.
- Row-page loading preserves the table header.
- Unknown templates show file name, no-write statement, and retry.
- Empty/header-only files never enable confirm.
- A detail failure does not blank the data overview.

- [ ] **Step 3: Write failing accessibility tests**

Verify:

- Tab and panel relationships.
- File input has a visible label.
- Statuses include text.
- Errors use `role="alert"`.
- Confirmation bar is reachable after the table in DOM order.
- More-menu and confirmation controls have accessible names containing the batch ID.

- [ ] **Step 4: Run tests and confirm failure**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx
```

Expected: at least the new method, skeleton, and accessibility assertions fail.

- [ ] **Step 5: Integrate manual entry into `ImportWorkspace`**

Use a secondary method selector inside the import view. Reuse `ManualDataEntry`; remove its duplicated outer section header and commit action when embedded. It should emit the same active-batch update shape as file upload.

- [ ] **Step 6: Implement state-specific skeletons and errors**

Keep:

- Shared header skeleton.
- Tab skeleton.
- Table-header skeleton.

Use `OperationalState` only for account-level blocking errors. Use inline retry feedback for file, rows, and history-detail failures.

- [ ] **Step 7: Finish responsive and reduced-motion rules**

Test CSS at:

- 1440px
- 1024px
- 768px
- 390px

At 390px, transform history rows into stacked summaries and keep import rows in a contained horizontal table. Do not create a squeezed two-column page.

- [ ] **Step 8: Run the page suite, lint, and build**

Run:

```powershell
cd frontend
pnpm test -- src/pages/AccountDataCenter.test.tsx
pnpm lint
pnpm build
```

Expected: all commands exit `0`.

- [ ] **Step 9: Commit hardening**

```powershell
git add -- frontend/src/components/account-data/ManualDataEntry.tsx frontend/src/components/account-data/ImportWorkspace.tsx frontend/src/pages/AccountDataCenter.tsx frontend/src/pages/AccountDataCenter.test.tsx frontend/src/styles/account-data-center.css
git commit -m "fix: harden account data center states"
```

---

### Task 8: Update browser acceptance coverage and run the full release candidate gate

**Files:**
- Modify: `frontend/e2e/account-data-import.spec.ts`
- Verify: `backend/`
- Verify: `frontend/`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces: a locally verified release candidate; no production deployment.

- [ ] **Step 1: Update mocked E2E routes**

Add:

```typescript
if (
  method === "GET"
  && path === "/account-data/1/imports/81/rows"
) {
  const page = Number(url.searchParams.get("page") ?? "1");
  const view = url.searchParams.get("view") ?? "all";
  await route.fulfill({
    json: buildRowPage({ page, view }),
  });
  return;
}
```

Update selectors from card classes to roles and operator-facing labels.

- [ ] **Step 2: Add the approved visual acceptance scenario**

The scenario must verify:

1. The overview opens without any batch-detail requests.
2. Upload switches to `导入与补录`.
3. The validation workspace spans the page and history is absent.
4. The page shows 30 total, 30 ready, and 0 blocking.
5. `确认写入 30 条` remains visible after scrolling the table.
6. The API technical code is not visible.
7. History renders as a table after switching tabs.

- [ ] **Step 3: Add responsive browser assertions**

At desktop and mobile widths, assert:

```typescript
await expect(page.locator(".account-data-layout")).toHaveCount(0);
await expect(page.getByRole("tabpanel", { name: "导入与补录" })).toBeVisible();
```

At mobile width, assert the page has no document-level horizontal overflow:

```typescript
const hasOverflow = await page.evaluate(
  () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
);
expect(hasOverflow).toBe(false);
```

- [ ] **Step 4: Run the focused E2E test**

Run:

```powershell
cd frontend
pnpm test:e2e -- e2e/account-data-import.spec.ts
```

Expected: all scenarios pass.

- [ ] **Step 5: Run backend quality gates**

Run:

```powershell
cd backend
uv run pytest tests/test_account_data_import_api.py tests/test_manual_account_data_api.py tests/test_account_data_permissions.py tests/test_account_data_transaction_boundaries.py -v
uv run ruff check .
uv run mypy app
```

Expected: all commands exit `0`.

- [ ] **Step 6: Run frontend quality gates**

Run:

```powershell
cd frontend
pnpm test
pnpm lint
pnpm build
pnpm test:e2e -- e2e/account-data-import.spec.ts
```

Expected: all commands exit `0`; existing bundle-size warnings may remain, but no new error is accepted.

- [ ] **Step 7: Inspect the final diff**

Run:

```powershell
git diff --check master...HEAD
git status --short
git log --oneline master..HEAD
```

Confirm:

- No secret, real credential, `.env`, build output, or downloaded production file is included.
- Every behavior change has a test.
- The branch contains only account-data-center redesign work.

- [ ] **Step 8: Commit E2E acceptance changes**

```powershell
git add -- frontend/e2e/account-data-import.spec.ts
git commit -m "test: cover account data center workflow"
```

- [ ] **Step 9: Stop at the release approval gate**

Report:

- Implemented tasks and commits.
- Backend, frontend, and browser verification results.
- Any non-blocking warnings.
- Exact files changed.

Do not merge to `master` and do not deploy until the user explicitly approves the verified result.
