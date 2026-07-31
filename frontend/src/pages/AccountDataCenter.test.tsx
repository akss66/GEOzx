// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import {
  MemoryRouter,
  Route,
  RouterProvider,
  Routes,
  createMemoryRouter,
  useNavigate,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  confirmManualAccountDataRow,
  createManualAccountDataPreview,
  deleteAccountDataImportBatch,
  downloadAccountDataArtifact,
  type AccountDataImportArtifact,
  type AccountDataImportBatch,
  type AccountDataImportBatchSummary,
  type AccountDataImportRow,
  type AccountDataImportRowPage,
  type AccountDataStatus,
  commitAccountDataImportBatch,
  getAccountDataImportBatch,
  getAccountDataImportRows,
  getAccountDataStatus,
  listAccountDataImports,
  resolveAccountDataImportRow,
  revokeAccountDataImportBatch,
  uploadAccountDataImport,
} from "../api/accountData";
import { getWorkspaceContext } from "../api/shell";
import { getAccount } from "../api/workspace";
import AccountDataCenter from "./AccountDataCenter";

const workspaceMocks = vi.hoisted(() => ({
  clientId: 1 as number | null,
  projectId: 11 as number | null,
  platform: "douyin" as const,
  accountId: 7 as number | null,
  setPlatform: vi.fn(),
  setAccountId: vi.fn(),
  hydrate: vi.fn(),
}));

vi.mock("../api/accountData", () => ({
  getAccountDataStatus: vi.fn(),
  listAccountDataImports: vi.fn(),
  getAccountDataImportBatch: vi.fn(),
  getAccountDataImportRows: vi.fn(),
  uploadAccountDataImport: vi.fn(),
  createManualAccountDataPreview: vi.fn(),
  confirmManualAccountDataRow: vi.fn(),
  resolveAccountDataImportRow: vi.fn(),
  commitAccountDataImportBatch: vi.fn(),
  revokeAccountDataImportBatch: vi.fn(),
  deleteAccountDataImportBatch: vi.fn(),
  downloadAccountDataArtifact: vi.fn(),
}));

vi.mock("../api/shell", () => ({
  getWorkspaceContext: vi.fn(),
}));

vi.mock("../api/workspace", () => ({
  getAccount: vi.fn(),
}));

vi.mock("../stores/currentWorkspace", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../stores/currentWorkspace")>();
  return {
    ...actual,
    useCurrentWorkspace: vi.fn((selector?: (state: typeof workspaceMocks) => unknown) =>
      typeof selector === "function" ? selector(workspaceMocks) : workspaceMocks,
    ),
  };
});

function buildAccount(id = 42) {
  return {
    id,
    nickname: "数码菌",
    platform: "douyin" as const,
    client_id: 1,
    client_ids: [1],
    group_id: null,
    project_id: 11,
    project_ids: [11],
    status: "active" as const,
    external_account_id: "douyin-42",
    integration_status: "connected" as const,
    auth_status: "authorized" as const,
    data_sync_status: "healthy" as const,
    avatar_url: "https://cdn.example.com/avatar.png",
    risk_count: 1,
    created_at: "2026-07-22T00:00:00Z",
  };
}

function buildStatus(overrides: Partial<AccountDataStatus> = {}): AccountDataStatus {
  return {
    account_id: 42,
    latest_confirmed_at: "2026-07-22T08:10:00Z",
    coverage: {
      account_metrics: "available",
      content_metrics: "partial",
      audience_profiles: "missing",
      benchmarks: "missing",
    },
    sources: [
      {
        batch_id: 81,
        source_kind: "platform_export",
        template_code: "douyin_work_list_v1",
        data_domain: "content_metrics",
        committed_at: "2026-07-22T08:10:00Z",
        period_start: "2026-07-01",
        period_end: "2026-07-22",
      },
    ],
    ...overrides,
  };
}

function buildArtifact(
  overrides: Partial<AccountDataImportArtifact> = {},
): AccountDataImportArtifact {
  return {
    id: 501,
    filename: "works.xlsx",
    content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    byte_size: 2048,
    sha256: "a".repeat(64),
    download_url: "/account-data/42/imports/81/artifacts/501",
    ...overrides,
  };
}

function buildBatchSummary(
  overrides: Partial<AccountDataImportBatchSummary> = {},
): AccountDataImportBatchSummary {
  return {
    id: 81,
    status: "preview_ready",
    source_kind: "platform_export",
    template_code: "douyin_work_list_v1",
    row_count: 1,
    period_start: "2026-07-01",
    period_end: "2026-07-22",
    committed_at: null,
    revoked_at: null,
    created_at: "2026-07-22T08:00:00Z",
    ...overrides,
    created_by_id: overrides.created_by_id ?? 1,
    created_by_name: overrides.created_by_name ?? "Operator",
  };
}

function buildPreviewBatch(overrides: Partial<AccountDataImportBatch> = {}): AccountDataImportBatch {
  const base: AccountDataImportBatch = {
    id: 81,
    status: "preview_ready",
    source_kind: "platform_export",
    template_code: "douyin_work_list_v1",
    row_count: 1,
    period_start: "2026-07-01",
    period_end: "2026-07-22",
    committed_at: null,
    revoked_at: null,
    created_by_id: 1,
    created_by_name: "Operator",
    created_at: "2026-07-22T08:00:00Z",
    artifacts: [buildArtifact()],
    conflicts: [
      {
        id: 701,
        row_number: 2,
        status: "open",
        field_name: "platform_content_record_id",
        conflict_code: "ambiguous_match",
        message: "同一发布时间存在多个候选作品，请人工确认。",
        candidate_content_ids: [91, 92],
        resolved_by_id: null,
        resolved_at: null,
      },
    ],
    rows: [
      {
        id: 601,
        row_number: 2,
        status: "needs_resolution",
        raw_values: { title: "作品 A", published_at: "2026-07-18 14:11:20" },
        normalized_values: { title: "作品 A", published_at: "2026-07-18T14:11:20" },
        field_errors: [],
        warnings: [],
        candidate_content_ids: [91, 92],
        projected_target_ids: [],
        platform_content_record_id: null,
        resolution_outcome: null,
        resolved_by_id: null,
        resolved_at: null,
      },
    ],
  };
  return {
    ...base,
    ...overrides,
    artifacts: overrides.artifacts ?? base.artifacts,
    conflicts: overrides.conflicts ?? base.conflicts,
    rows: overrides.rows ?? base.rows,
  };
}

function buildImportRow(
  rowNumber: number,
  overrides: Partial<AccountDataImportRow> = {},
): AccountDataImportRow {
  return {
    id: 600 + rowNumber,
    row_number: rowNumber,
    status: "ready",
    raw_values: {
      title: `作品 ${rowNumber}`,
      published_at: "2026-07-18 14:11:20",
      play: rowNumber * 100,
    },
    normalized_values: {
      title: `作品 ${rowNumber}`,
      published_at: "2026-07-18T14:11:20",
      play: rowNumber * 100,
    },
    field_errors: [],
    warnings: [],
    candidate_content_ids: [],
    projected_target_ids: [],
    platform_content_record_id: rowNumber,
    resolution_outcome: null,
    resolved_by_id: null,
    resolved_at: null,
    ...overrides,
  };
}

function buildRowPage(
  overrides: Partial<AccountDataImportRowPage> = {},
): AccountDataImportRowPage {
  const items = overrides.items ?? [buildImportRow(1)];
  return {
    items,
    page: 1,
    page_size: 50,
    total_count: items.length,
    filtered_count: items.length,
    ready_count: items.filter((row) => row.status === "ready").length,
    blocking_count: items.filter(
      (row) => row.status === "invalid" || row.status === "needs_resolution",
    ).length,
    total_pages: 1,
    ...overrides,
  };
}

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderPage(route = "/accounts/42/data", queryClient = createTestQueryClient()) {
  const router = createMemoryRouter(
    [{ path: "/accounts/:accountId/data", element: <AccountDataCenter /> }],
    { initialEntries: [route] },
  );
  return {
    queryClient,
    router,
    ...render(
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <RouterProvider router={router} />
        </AntApp>
      </QueryClientProvider>
    ),
  };
}

function RouteSwitchHarness() {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate("/accounts/99/data")}>
        切换到账号 99
      </button>
      <Routes>
        <Route path="/accounts/:accountId/data" element={<AccountDataCenter />} />
      </Routes>
    </>
  );
}

function renderMountedRouteSwitcher(
  route = "/accounts/2/data",
  queryClient = createTestQueryClient(),
) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <RouteSwitchHarness />
        </AntApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

async function openAccountDataView(name: "数据概览" | "导入与补录" | "导入记录") {
  fireEvent.click(await screen.findByRole("tab", { name }));
}

async function openBatchMoreMenu(batchId: number) {
  const trigger = await screen.findByRole("button", {
    name: `更多操作 批次 ${batchId}`,
  });
  fireEvent.click(trigger);
}

describe("AccountDataCenter", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getAccount).mockResolvedValue(buildAccount());
    vi.mocked(getAccountDataImportRows).mockResolvedValue(
      buildRowPage({
        items: [buildPreviewBatch().rows[0]],
        total_count: 1,
        filtered_count: 1,
        ready_count: 0,
        blocking_count: 1,
      }),
    );
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  afterEach(cleanup);

  it("shows an explicit account error instead of silently falling back", async () => {
    vi.mocked(getAccount).mockRejectedValueOnce({
      response: { status: 404, data: { detail: "account not found" } },
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount(7)],
    });

    renderPage();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(getAccountDataStatus).not.toHaveBeenCalled();
    expect(workspaceMocks.hydrate).not.toHaveBeenCalled();
  });

  it("opens an unbound account without clearing the current client and project", async () => {
    vi.mocked(getAccount).mockResolvedValueOnce({
      ...buildAccount(),
      client_id: null,
      client_ids: [],
      project_id: null,
      project_ids: [],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({ items: [] });

    renderPage();

    await waitFor(() =>
      expect(workspaceMocks.hydrate).toHaveBeenCalledWith({
        clientId: 1,
        projectId: 11,
        platform: "douyin",
        accountId: 42,
      }),
    );
    expect(await screen.findByText("账号数据中心")).toBeInTheDocument();
  });

  it("separates overview, import, and history into three primary views", async () => {
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({ items: [] });

    renderPage();

    expect(await screen.findByRole("tab", { name: "数据概览" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "导入与补录" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "导入记录" })).toBeInTheDocument();
    expect(await screen.findByText("当前账号已有部分可用数据")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "导入与补录" }));

    expect(screen.queryByText("当前账号已有部分可用数据")).not.toBeInTheDocument();
    expect(await screen.findByLabelText("选择导入文件")).toBeInTheDocument();
  });

  it("does not present pending preview data as confirmed account data", async () => {
    const pending = buildPreviewBatch();
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(
      buildStatus({
        latest_confirmed_at: null,
        coverage: {
          account_metrics: "missing",
          content_metrics: "missing",
          audience_profiles: "missing",
          benchmarks: "missing",
        },
        sources: [],
      }),
    );
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [buildBatchSummary()],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(pending);

    renderPage();

    expect(await screen.findByText("暂无已确认数据")).toBeInTheDocument();
    expect(await screen.findByText("有 1 个批次等待确认")).toBeInTheDocument();
    expect(screen.queryByText("已有可用数据")).not.toBeInTheDocument();
  });

  it("shows a recoverable unknown-template error inline", async () => {
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValue({ items: [] });
    vi.mocked(uploadAccountDataImport).mockRejectedValueOnce({
      response: {
        status: 422,
        data: { detail: "Unknown or unsupported template" },
      },
    });

    renderPage();

    await openAccountDataView("导入与补录");
    const fileInput = await screen.findByLabelText("选择导入文件");
    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["bad"], "unknown.xlsx", {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          }),
        ],
      },
    });

    expect(await screen.findByText("无法识别导入模板")).toBeInTheDocument();
    expect(screen.getByText("请改用已支持的抖音导出模板后重新上传。")).toBeInTheDocument();
  });

  it("keeps the uploaded preview in React StrictMode", async () => {
    const preview = buildPreviewBatch({
      id: 81,
      template_code: "douyin_daily_play_v1",
    });
    vi.mocked(getWorkspaceContext).mockResolvedValue({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValue(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValue({ items: [] });
    vi.mocked(uploadAccountDataImport).mockResolvedValueOnce(preview);

    const queryClient = createTestQueryClient();
    const router = createMemoryRouter(
      [{ path: "/accounts/:accountId/data", element: <AccountDataCenter /> }],
      { initialEntries: ["/accounts/42/data"] },
    );
    render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <AntApp>
            <RouterProvider router={router} />
          </AntApp>
        </QueryClientProvider>
      </StrictMode>,
    );

    await openAccountDataView("导入与补录");
    const fileInput = await screen.findByLabelText("选择导入文件");
    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["valid"], "daily.xlsx", {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          }),
        ],
      },
    });

    expect(await screen.findByText("导入预览已生成")).toBeInTheDocument();
    expect(screen.getByText("douyin_daily_play_v1")).toBeInTheDocument();
  });

  it("creates a manual account-period preview through the page workspace", async () => {
    const preview = buildPreviewBatch({
      id: 91,
      source_kind: "manual_entry",
      template_code: "manual_account_period_v1",
      conflicts: [],
      rows: [
        {
          ...buildPreviewBatch().rows[0],
          row_number: 1,
          status: "ready",
          raw_values: {},
          normalized_values: { follower_count: 1200, total_play: 8900 },
          candidate_content_ids: [],
        },
      ],
      artifacts: [],
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValue({ items: [] });
    vi.mocked(createManualAccountDataPreview).mockResolvedValueOnce(preview);

    renderPage();

    await openAccountDataView("导入与补录");
    fireEvent.click(await screen.findByRole("tab", { name: "人工录入" }));
    fireEvent.change(screen.getByLabelText("统计日期"), { target: { value: "2026-07-22" } });
    fireEvent.change(screen.getByLabelText("粉丝总数"), { target: { value: "1200" } });
    fireEvent.change(screen.getByLabelText("播放量"), { target: { value: "8900" } });
    fireEvent.click(screen.getByRole("button", { name: "生成录入预览" }));

    await waitFor(() => expect(createManualAccountDataPreview).toHaveBeenCalledWith(
      42,
      expect.objectContaining({
        data_domain: "account_period_totals",
        stat_date: "2026-07-22",
        account_metrics: expect.objectContaining({
          follower_count: 1200,
          total_play: 8900,
          total_exposure: null,
        }),
      }),
      null,
    ));
    expect(await screen.findByText("人工数据预览已生成")).toBeInTheDocument();
    expect(screen.getByText("manual_account_period_v1")).toBeInTheDocument();
    expect(confirmManualAccountDataRow).not.toHaveBeenCalled();
  });

  it("blocks commit while one row is invalid", async () => {
    const preview = buildPreviewBatch({
      rows: [
        {
          ...buildPreviewBatch().rows[0],
          status: "invalid",
          candidate_content_ids: [],
          field_errors: [{ message: "播放量不能为空" }],
        },
      ],
      conflicts: [],
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [buildBatchSummary()],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(preview);
    vi.mocked(getAccountDataImportRows).mockResolvedValueOnce(
      buildRowPage({
        items: preview.rows,
        total_count: 1,
        filtered_count: 1,
        ready_count: 0,
        blocking_count: 1,
      }),
    );

    renderPage();

    expect(await screen.findByRole("button", { name: "确认写入 1 条" })).toBeDisabled();
    expect(await screen.findByText("播放量不能为空")).toBeInTheDocument();
    expect(commitAccountDataImportBatch).not.toHaveBeenCalled();
  });

  it("shows a four-step import progress and business summary", async () => {
    const preview = buildPreviewBatch({
      template_code: "douyin_work_list_v1",
      row_count: 68,
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [buildBatchSummary({ row_count: 68 })],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(preview);
    vi.mocked(getAccountDataImportRows).mockResolvedValueOnce(
      buildRowPage({
        items: Array.from({ length: 50 }, (_, index) => buildImportRow(index + 1)),
        total_count: 68,
        filtered_count: 68,
        ready_count: 67,
        blocking_count: 1,
        total_pages: 2,
      }),
    );

    renderPage();

    await screen.findByRole("heading", { name: "核对本次导入" });
    const progress = screen.getByRole("list", { name: "导入进度" });
    expect(progress.children[0]).toHaveClass("is-complete");
    expect(progress.children[1]).toHaveClass("is-complete");
    expect(progress.children[2]).toHaveClass("is-current");
    expect(progress.children[3]).toHaveClass("is-upcoming");
    expect(screen.getByText("抖音作品列表")).toBeInTheDocument();
    expect(screen.getByText("douyin_work_list_v1").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("works.xlsx")).toBeInTheDocument();
  });

  it("pages import rows and resets to page one when filtering needs-work rows", async () => {
    const preview = buildPreviewBatch({ row_count: 68 });
    const pageOne = buildRowPage({
      items: Array.from({ length: 50 }, (_, index) => buildImportRow(index + 1)),
      total_count: 68,
      filtered_count: 68,
      ready_count: 67,
      blocking_count: 1,
      total_pages: 2,
    });
    const pageTwo = buildRowPage({
      items: Array.from({ length: 18 }, (_, index) => buildImportRow(index + 51)),
      page: 2,
      total_count: 68,
      filtered_count: 68,
      ready_count: 67,
      blocking_count: 1,
      total_pages: 2,
    });
    const needsWork = buildRowPage({
      items: [
        buildImportRow(68, {
          status: "invalid",
          field_errors: [{ message: "播放量不能为空" }],
        }),
      ],
      total_count: 68,
      filtered_count: 1,
      ready_count: 67,
      blocking_count: 1,
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [buildBatchSummary({ row_count: 68 })],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(preview);
    vi.mocked(getAccountDataImportRows).mockImplementation(
      async (_accountId, _batchId, query) => {
        if (query?.view === "needs_work") return needsWork;
        return query?.page === 2 ? pageTwo : pageOne;
      },
    );

    renderPage();

    const table = await screen.findByRole("table", { name: "导入数据校验表" });
    expect(table.querySelectorAll("tbody > tr")).toHaveLength(50);
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() =>
      expect(getAccountDataImportRows).toHaveBeenCalledWith(
        42,
        81,
        { page: 2, pageSize: 50, view: "all" },
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "需处理 1" }));
    await waitFor(() =>
      expect(getAccountDataImportRows).toHaveBeenCalledWith(
        42,
        81,
        { page: 1, pageSize: 50, view: "needs_work" },
      ),
    );
    expect(await screen.findByText("播放量不能为空")).toBeInTheDocument();
  });

  it("uses server row counts to block or allow import confirmation", async () => {
    const preview = buildPreviewBatch({ row_count: 68 });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [buildBatchSummary({ row_count: 68 })],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(preview);
    vi.mocked(getAccountDataImportRows).mockResolvedValueOnce(
      buildRowPage({
        items: Array.from({ length: 50 }, (_, index) => buildImportRow(index + 1)),
        total_count: 68,
        filtered_count: 68,
        ready_count: 67,
        blocking_count: 1,
        total_pages: 2,
      }),
    );

    renderPage();

    expect(await screen.findByText("仍有 1 条需要处理")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认写入 68 条" })).toBeDisabled();
  });

  it("blocks commit until one ambiguous row is resolved", async () => {
    const preview = buildPreviewBatch();
    const resolvedRow = {
      ...preview.rows[0],
      status: "ready" as const,
      platform_content_record_id: 91,
      resolution_outcome: "selected_existing_content",
      candidate_content_ids: [91, 92],
    };
    const refreshedBatch = buildPreviewBatch({
      rows: [resolvedRow],
      conflicts: [],
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus)
      .mockResolvedValueOnce(buildStatus())
      .mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports)
      .mockResolvedValueOnce({
        items: [buildBatchSummary()],
      })
      .mockResolvedValueOnce({
        items: [buildBatchSummary()],
      });
    vi.mocked(getAccountDataImportBatch)
      .mockResolvedValueOnce(preview)
      .mockResolvedValueOnce(refreshedBatch);
    vi.mocked(getAccountDataImportRows)
      .mockResolvedValueOnce(
        buildRowPage({
          items: preview.rows,
          total_count: 1,
          filtered_count: 1,
          ready_count: 0,
          blocking_count: 1,
        }),
      )
      .mockResolvedValue(
        buildRowPage({
          items: refreshedBatch.rows,
          total_count: 1,
          filtered_count: 1,
          ready_count: 1,
          blocking_count: 0,
        }),
      );
    vi.mocked(resolveAccountDataImportRow).mockResolvedValueOnce(resolvedRow);

    renderPage();

    expect(await screen.findByRole("button", { name: "确认写入 1 条" })).toBeDisabled();
    fireEvent.click(await screen.findByRole("button", { name: "选用候选作品 #91" }));

    await waitFor(() =>
      expect(resolveAccountDataImportRow).toHaveBeenCalledWith(42, 81, 2, 91),
    );
    await waitFor(() => expect(getAccountDataImportBatch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(listAccountDataImports).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getAccountDataStatus).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认写入 1 条" })).toBeEnabled(),
    );
  });

  it("renders uploaded and failed batch statuses truthfully", async () => {
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus({ latest_confirmed_at: null }));
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [
        buildBatchSummary({ id: 82, status: "uploaded", committed_at: null }),
        buildBatchSummary({ id: 83, status: "failed", committed_at: null }),
      ],
    });
    vi.mocked(getAccountDataImportBatch)
      .mockResolvedValueOnce(
        buildPreviewBatch({
          id: 82,
          status: "uploaded",
          rows: [],
          conflicts: [],
          artifacts: [buildArtifact({ download_url: "/account-data/42/imports/82/artifacts/501" })],
        }),
      )
      .mockResolvedValueOnce(
        buildPreviewBatch({
          id: 83,
          status: "failed",
          rows: [],
          conflicts: [],
          artifacts: [buildArtifact({ id: 502, download_url: "/account-data/42/imports/83/artifacts/502" })],
        }),
      );

    renderPage();

    await openAccountDataView("导入记录");
    expect((await screen.findAllByText("已上传")).length).toBeGreaterThan(0);
    expect(await screen.findByText("导入失败")).toBeInTheDocument();
  });

  it("renders a dense import history table with business labels", async () => {
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [
        buildBatchSummary({
          created_by_id: 7,
          created_by_name: "张运营",
        }),
      ],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(
      buildPreviewBatch({
        created_by_id: 7,
        created_by_name: "张运营",
      }),
    );

    renderPage();

    await openAccountDataView("导入记录");

    const table = await screen.findByRole("table", { name: "导入记录" });
    expect(table).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "数据类型" })).toBeInTheDocument();
    expect(screen.getByText("抖音作品列表")).toBeInTheDocument();
    expect(screen.getByText("张运营")).toBeInTheDocument();
    expect(screen.queryByText("douyin_work_list_v1")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看批次 81" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "更多操作 批次 81" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "永久删除批次 81" })).not.toBeInTheDocument();
  });

  it("commits successfully and refreshes status plus history", async () => {
    const preview = buildPreviewBatch();
    const committed: AccountDataImportBatch = {
      ...preview,
      status: "committed",
      committed_at: "2026-07-22T08:25:00Z",
      conflicts: [],
      rows: preview.rows.map((row) => ({
        ...row,
        status: "committed" as const,
        platform_content_record_id: 91,
      })),
    };
    const pendingStatus: AccountDataStatus = {
      ...buildStatus(),
      latest_confirmed_at: null,
    };
    const refreshedPreview: AccountDataImportBatch = {
      ...preview,
      rows: preview.rows.map((row) => ({ ...row, status: "ready" as const })),
      conflicts: [],
    };
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus)
      .mockResolvedValueOnce(pendingStatus)
      .mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports)
      .mockResolvedValueOnce({
        items: [
          {
            id: 81,
            status: "preview_ready",
            source_kind: "platform_export",
            template_code: "douyin_work_list_v1",
            row_count: 1,
            period_start: "2026-07-01",
            period_end: "2026-07-22",
            committed_at: null,
            revoked_at: null,
            created_by_id: 1,
            created_by_name: "Operator",
            created_at: "2026-07-22T08:00:00Z",
          },
        ],
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: 81,
            status: "committed",
            source_kind: "platform_export",
            template_code: "douyin_work_list_v1",
            row_count: 1,
            period_start: "2026-07-01",
            period_end: "2026-07-22",
            committed_at: "2026-07-22T08:25:00Z",
            revoked_at: null,
            created_by_id: 1,
            created_by_name: "Operator",
            created_at: "2026-07-22T08:00:00Z",
          },
        ],
      });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(refreshedPreview);
    vi.mocked(getAccountDataImportRows).mockResolvedValueOnce(
      buildRowPage({
        items: refreshedPreview.rows,
        total_count: 1,
        filtered_count: 1,
        ready_count: 1,
        blocking_count: 0,
      }),
    );
    vi.mocked(commitAccountDataImportBatch).mockResolvedValueOnce(committed);

    renderPage();

    const commitButton = await screen.findByRole("button", { name: "确认写入 1 条" });
    await waitFor(() => expect(commitButton).toBeEnabled());
    fireEvent.click(commitButton);

    await waitFor(() =>
      expect(commitAccountDataImportBatch).toHaveBeenCalledWith(42, 81),
    );
    await waitFor(() => expect(listAccountDataImports).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getAccountDataStatus).toHaveBeenCalledTimes(2));
    expect(await screen.findByLabelText("数据确认状态")).toHaveTextContent("最近确认");
  });

  it("requires an explicit revoke confirmation before revoking one batch", async () => {
    const committed: AccountDataImportBatchSummary = buildBatchSummary({
      status: "committed",
      committed_at: "2026-07-22T08:25:00Z",
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(listAccountDataImports)
      .mockResolvedValueOnce({ items: [committed] })
      .mockResolvedValueOnce({
        items: [
          {
            ...committed,
            status: "revoked",
            revoked_at: "2026-07-22T08:45:00Z",
          },
        ],
      });
    vi.mocked(getAccountDataImportBatch)
      .mockResolvedValueOnce({
        ...buildPreviewBatch(),
        status: "committed",
        committed_at: committed.committed_at,
        conflicts: [],
        rows: buildPreviewBatch().rows.map((row) => ({
          ...row,
          status: "committed" as const,
          platform_content_record_id: 91,
        })),
      })
      .mockResolvedValueOnce({
        ...buildPreviewBatch(),
        status: "revoked",
        committed_at: committed.committed_at,
        revoked_at: "2026-07-22T08:45:00Z",
        rows: buildPreviewBatch().rows.map((row) => ({
          ...row,
          status: "revoked" as const,
          platform_content_record_id: 91,
        })),
      });
    vi.mocked(getAccountDataStatus)
      .mockResolvedValueOnce(buildStatus())
      .mockResolvedValueOnce(buildStatus());
    vi.mocked(revokeAccountDataImportBatch).mockResolvedValueOnce({
      ...buildPreviewBatch(),
      status: "revoked",
      committed_at: committed.committed_at,
      revoked_at: "2026-07-22T08:45:00Z",
    });

    renderPage();

    await openAccountDataView("导入记录");
    await openBatchMoreMenu(81);
    fireEvent.click(await screen.findByRole("menuitem", { name: /撤销写入/ }));
    expect(screen.getByText("确认撤销这次写入？")).toBeInTheDocument();
    expect(revokeAccountDataImportBatch).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认撤销批次 81" }));

    await waitFor(() =>
      expect(revokeAccountDataImportBatch).toHaveBeenCalledWith(42, 81),
    );
  });

  it("permanently deletes a committed batch after explicit confirmation", async () => {
    const committed = buildBatchSummary({
      status: "committed",
      committed_at: "2026-07-22T08:25:00Z",
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus)
      .mockResolvedValueOnce(buildStatus())
      .mockResolvedValueOnce(buildStatus({
        latest_confirmed_at: null,
        sources: [],
      }));
    vi.mocked(listAccountDataImports)
      .mockResolvedValueOnce({ items: [committed] })
      .mockResolvedValueOnce({ items: [] });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(
      buildPreviewBatch({
        status: "committed",
        committed_at: committed.committed_at,
        conflicts: [],
        rows: buildPreviewBatch().rows.map((row) => ({
          ...row,
          status: "committed" as const,
        })),
      }),
    );
    vi.mocked(deleteAccountDataImportBatch).mockResolvedValueOnce();

    renderPage();

    await openAccountDataView("导入记录");
    await openBatchMoreMenu(81);
    fireEvent.click(await screen.findByRole("menuitem", { name: /永久删除/ }));
    expect(
      screen.getByText("将先撤销该批次产生的数据，再永久删除原文件和历史记录。"),
    ).toBeInTheDocument();
    expect(deleteAccountDataImportBatch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除批次 81" }));

    await waitFor(() =>
      expect(deleteAccountDataImportBatch).toHaveBeenCalledWith(42, 81),
    );
    await waitFor(() => expect(listAccountDataImports).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getAccountDataStatus).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("导入批次已永久删除")).toBeInTheDocument();
    expect(await screen.findByText("暂无导入历史")).toBeInTheDocument();
  });

  it("selects the next preview batch after permanently deleting the active batch", async () => {
    const current = buildBatchSummary();
    const replacement = buildBatchSummary({ id: 82 });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus)
      .mockResolvedValueOnce(buildStatus())
      .mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports)
      .mockResolvedValueOnce({ items: [current] })
      .mockResolvedValueOnce({ items: [replacement] });
    vi.mocked(getAccountDataImportBatch)
      .mockResolvedValueOnce(buildPreviewBatch())
      .mockResolvedValueOnce(
        buildPreviewBatch({
          id: 82,
          artifacts: [
            buildArtifact({
              id: 502,
              download_url: "/account-data/42/imports/82/artifacts/502",
            }),
          ],
        }),
      );
    vi.mocked(deleteAccountDataImportBatch).mockResolvedValueOnce();

    const { container } = renderPage();

    await openAccountDataView("导入记录");
    await openBatchMoreMenu(81);
    fireEvent.click(await screen.findByRole("menuitem", { name: /永久删除/ }));
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除批次 81" }));

    expect(await screen.findByText("批次 82")).toBeInTheDocument();
    await waitFor(() =>
      expect(container.querySelector(".account-data-history-row.is-active"))
        .toHaveTextContent("批次 82"),
    );
    expect(screen.getByRole("button", { name: "查看批次 82" })).toBeEnabled();
  });

  it("keeps a batch visible when permanent deletion has a later-data conflict", async () => {
    const committed = buildBatchSummary({
      status: "committed",
      committed_at: "2026-07-22T08:25:00Z",
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({ items: [committed] });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(
      buildPreviewBatch({
        status: "committed",
        committed_at: committed.committed_at,
        conflicts: [],
      }),
    );
    vi.mocked(deleteAccountDataImportBatch).mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: "该批次已被后续数据引用，不能永久删除。" },
      },
    });

    renderPage();

    await openAccountDataView("导入记录");
    await openBatchMoreMenu(81);
    fireEvent.click(await screen.findByRole("menuitem", { name: /永久删除/ }));
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除批次 81" }));

    expect(
      await screen.findByText("该批次已被后续数据引用，不能永久删除。"),
    ).toBeInTheDocument();
    expect(screen.getByText("批次 81")).toBeInTheDocument();
  });

  it("treats an already-missing batch as successfully deleted", async () => {
    const preview = buildBatchSummary();
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus)
      .mockResolvedValueOnce(buildStatus())
      .mockResolvedValueOnce(buildStatus({
        latest_confirmed_at: null,
        sources: [],
      }));
    vi.mocked(listAccountDataImports)
      .mockResolvedValueOnce({ items: [preview] })
      .mockResolvedValueOnce({ items: [] });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(buildPreviewBatch());
    vi.mocked(deleteAccountDataImportBatch).mockRejectedValueOnce({
      response: {
        status: 404,
        data: { detail: "import batch does not exist" },
      },
    });

    renderPage();

    await openAccountDataView("导入记录");
    await openBatchMoreMenu(81);
    fireEvent.click(await screen.findByRole("menuitem", { name: /永久删除/ }));
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除批次 81" }));

    expect(await screen.findByText("导入批次已永久删除")).toBeInTheDocument();
    expect(await screen.findByText("暂无导入历史")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("downloads artifacts through the authenticated api helper", async () => {
    const committed = buildBatchSummary({
      status: "committed",
      committed_at: "2026-07-22T08:25:00Z",
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({ items: [committed] });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(
      buildPreviewBatch({
        status: "committed",
        committed_at: committed.committed_at,
        conflicts: [],
        rows: buildPreviewBatch().rows.map((row) => ({
          ...row,
          status: "committed" as const,
          platform_content_record_id: 91,
        })),
      }),
    );

    renderPage();

    await openAccountDataView("导入记录");
    fireEvent.click(await screen.findByRole("button", { name: "查看批次 81" }));
    await openBatchMoreMenu(81);
    fireEvent.click(await screen.findByRole("menuitem", { name: /下载原文件/ }));

    await waitFor(() =>
      expect(downloadAccountDataArtifact).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 501,
          filename: "works.xlsx",
          download_url: "/account-data/42/imports/81/artifacts/501",
        }),
      ),
    );
  });

  it("shows a clear revoke permission denial", async () => {
    const committed = buildBatchSummary({
      status: "committed",
      committed_at: "2026-07-22T08:25:00Z",
    });
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({ items: [committed] });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(
      buildPreviewBatch({
        status: "committed",
        committed_at: committed.committed_at,
        conflicts: [],
        rows: buildPreviewBatch().rows.map((row) => ({
          ...row,
          status: "committed" as const,
          platform_content_record_id: 91,
        })),
      }),
    );
    vi.mocked(revokeAccountDataImportBatch).mockRejectedValueOnce({
      response: {
        status: 403,
        data: { detail: "只有负责人可以撤销已确认批次。" },
      },
    });

    renderPage();

    await openAccountDataView("导入记录");
    await openBatchMoreMenu(81);
    fireEvent.click(await screen.findByRole("menuitem", { name: /撤销写入/ }));
    fireEvent.click(screen.getByRole("button", { name: "确认撤销批次 81" }));

    await waitFor(() =>
      expect(revokeAccountDataImportBatch).toHaveBeenCalledWith(42, 81),
    );
    expect(await screen.findByText("只有负责人可以撤销已确认批次。")).toBeInTheDocument();
  });

  it("lazy loads only the history batch an operator explicitly opens", async () => {
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [
        buildBatchSummary({ id: 81, status: "committed" }),
        buildBatchSummary({ id: 82, status: "committed" }),
        buildBatchSummary({ id: 83, status: "committed" }),
      ],
    });
    vi.mocked(getAccountDataImportBatch).mockImplementation(async (_accountId, batchId) =>
      buildPreviewBatch({ id: batchId, status: "committed" }),
    );

    renderPage();

    await waitFor(() => expect(listAccountDataImports).toHaveBeenCalledWith(42));
    expect(getAccountDataImportBatch).not.toHaveBeenCalled();

    await openAccountDataView("导入记录");
    fireEvent.click((await screen.findAllByRole("button", { name: /查看批次/ }))[0]);

    await waitFor(() => expect(getAccountDataImportBatch).toHaveBeenCalledTimes(1));
    expect(getAccountDataImportBatch).toHaveBeenCalledWith(42, 81);
  });

  it("isolates active batch state after same-mounted account switch", async () => {
    const account2Batch = buildPreviewBatch({
      id: 201,
      artifacts: [
        buildArtifact({
          id: 601,
          filename: "account-2.xlsx",
          download_url: "/account-data/2/imports/201/artifacts/601",
        }),
      ],
      rows: buildPreviewBatch().rows.map((row) => ({
        ...row,
        status: "ready" as const,
        candidate_content_ids: [],
      })),
      conflicts: [],
    });
    const account99Batch = buildPreviewBatch({
      id: 990,
      artifacts: [
        buildArtifact({
          id: 602,
          filename: "account-99.xlsx",
          download_url: "/account-data/99/imports/990/artifacts/602",
        }),
      ],
      rows: buildPreviewBatch().rows.map((row) => ({
        ...row,
        status: "ready" as const,
        title: undefined,
        candidate_content_ids: [],
      })),
      conflicts: [],
    });
    vi.mocked(getWorkspaceContext).mockResolvedValue({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount(2), buildAccount(99)],
    });
    vi.mocked(getAccount).mockImplementation(async (accountId) => buildAccount(accountId));
    vi.mocked(getAccountDataStatus).mockImplementation(async (accountId) =>
      buildStatus({
        account_id: accountId,
        latest_confirmed_at: accountId === 2 ? null : "2026-07-22T08:10:00Z",
      }),
    );
    vi.mocked(listAccountDataImports).mockImplementation(async (accountId) =>
      accountId === 2
        ? { items: [buildBatchSummary({ id: 201 })] }
        : { items: [buildBatchSummary({ id: 990, row_count: 1 })] },
    );
    vi.mocked(getAccountDataImportBatch).mockImplementation(async (accountId, batchId) =>
      accountId === 2 && batchId === 201 ? account2Batch : account99Batch,
    );
    vi.mocked(getAccountDataImportRows).mockImplementation(async (accountId) =>
      buildRowPage({
        items: accountId === 2 ? account2Batch.rows : account99Batch.rows,
        total_count: 1,
        filtered_count: 1,
        ready_count: 1,
        blocking_count: 0,
      }),
    );
    vi.mocked(commitAccountDataImportBatch).mockImplementation(async (accountId, batchId) => ({
      ...(accountId === 99 && batchId === 990 ? account99Batch : account2Batch),
      status: "committed",
      committed_at: "2026-07-22T08:25:00Z",
      rows: (accountId === 99 && batchId === 990 ? account99Batch : account2Batch).rows.map((row) => ({
        ...row,
        status: "committed" as const,
        platform_content_record_id: 91,
      })),
    }));

    renderMountedRouteSwitcher();

    await waitFor(() =>
      expect(screen.getByText("account-2.xlsx")).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认写入 1 条" })).toBeEnabled(),
    );

    fireEvent.click(screen.getByRole("button", { name: "切换到账号 99" }));

    await waitFor(() =>
      expect(screen.getByText("account-99.xlsx")).toBeInTheDocument(),
    );
    expect(screen.queryByText("account-2.xlsx")).not.toBeInTheDocument();
    expect(getAccountDataImportBatch).toHaveBeenCalledWith(2, 201);
    expect(getAccountDataImportBatch).toHaveBeenCalledWith(99, 990);

    const account99CommitButton = screen.getByRole("button", { name: "确认写入 1 条" });
    await waitFor(() => expect(account99CommitButton).toBeEnabled());
    fireEvent.click(account99CommitButton);

    await waitFor(() =>
      expect(commitAccountDataImportBatch).toHaveBeenCalledWith(99, 990),
    );
    expect(commitAccountDataImportBatch).not.toHaveBeenCalledWith(2, 201);
  });
});
