// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deleteAccount, listAccounts } from "../api/workspace";
import Accounts from "./Accounts";

const workspaceMocks = vi.hoisted(() => ({
  setPlatform: vi.fn(),
  setAccountId: vi.fn(),
}));

vi.mock("../api/workspace", () => ({
  batchUpdateAccounts: vi.fn(),
  createAccount: vi.fn(),
  createAccountGroup: vi.fn(),
  createDouyinAuthorizeUrl: vi.fn(),
  createDouyinScanAddUrl: vi.fn(),
  createDouyinTrialWhitelistUrl: vi.fn(),
  deleteAccount: vi.fn(),
  getAccountMatrix: vi.fn(async () => ({ platforms: [] })),
  listAccountGroups: vi.fn(async () => []),
  listAccounts: vi.fn(async () => []),
  listPlatformIntegrations: vi.fn(async () => []),
  listProjects: vi.fn(async () => []),
  syncDouyinAccountMetrics: vi.fn(),
  updateAccountIntegration: vi.fn(),
  updatePlatformIntegration: vi.fn(),
}));

vi.mock("../stores/auth", () => ({
  useAuth: vi.fn((selector: (state: { user: { role: string } }) => unknown) =>
    selector({ user: { role: "admin" } }),
  ),
}));

vi.mock("../stores/currentWorkspace", () => {
  const state = {
    accountId: 1,
    setPlatform: workspaceMocks.setPlatform,
    setAccountId: workspaceMocks.setAccountId,
  };
  return {
    useCurrentWorkspace: vi.fn((selector: (value: typeof state) => unknown) => selector(state)),
  };
});

vi.mock("../stores/accountMatrixPreferences", () => ({
  loadAccountMatrixPreferences: vi.fn(() => ({
    view: "table",
    projectId: null,
    dimension: "all",
    platform: "all",
    groupId: null,
  })),
  saveAccountMatrixPreferences: vi.fn(),
}));

describe("Accounts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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

  it("does not present a failed account query as an empty matrix", async () => {
    vi.mocked(listAccounts).mockRejectedValueOnce({
      response: { status: 503, headers: { "x-request-id": "accounts-1" } },
    });

    renderPage();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("账号矩阵加载失败");
    expect(screen.queryByText("当前筛选下暂无账号")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("账号明细 · 0")).toBeInTheDocument();
  });

  it("lets an admin confirm deletion and clears the current account context", async () => {
    vi.mocked(listAccounts).mockResolvedValueOnce([
      {
        id: 1,
        nickname: "阿桑",
        platform: "douyin",
        group_id: null,
        project_id: null,
        status: "active",
        external_account_id: "aksss60",
        integration_status: "connected",
        auth_status: "authorized",
        data_sync_status: "pending",
        publish_capability: "prepare_only",
        created_at: "2026-07-17T00:00:00Z",
      },
    ]);
    vi.mocked(deleteAccount).mockResolvedValueOnce();

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "删除账号 阿桑" }));
    expect(screen.getByText("删除账号“阿桑”？")).toBeInTheDocument();
    expect(screen.getByText("不会删除抖音平台上的账号本身")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(deleteAccount).toHaveBeenCalledWith(1));
    expect(workspaceMocks.setAccountId).toHaveBeenCalledWith(null);
  });
});

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp><Accounts /></AntApp>
    </QueryClientProvider>,
  );
}
