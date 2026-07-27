// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  deleteAccount,
  getDouyinAccountCapabilities,
  listAccounts,
  replaceAccountAssignments,
} from "../api/workspace";
import Accounts from "./Accounts";

const workspaceMocks = vi.hoisted(() => ({
  setPlatform: vi.fn(),
  setAccountId: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => workspaceMocks.navigate,
  };
});

vi.mock("../api/workspace", () => ({
  batchUpdateAccounts: vi.fn(),
  createAccount: vi.fn(),
  createAccountGroup: vi.fn(),
  createClient: vi.fn(),
  createDouyinAuthorizeUrl: vi.fn(),
  createDouyinIncrementalAuthorizeUrl: vi.fn(),
  createDouyinScanAddUrl: vi.fn(),
  createDouyinTrialWhitelistUrl: vi.fn(),
  createProject: vi.fn(),
  deleteAccount: vi.fn(),
  getAccountMatrix: vi.fn(async () => ({ platforms: [] })),
  getDouyinAccountCapabilities: vi.fn(),
  listAccountGroups: vi.fn(async () => []),
  listAccounts: vi.fn(async () => []),
  listClients: vi.fn(async () => []),
  listPlatformIntegrations: vi.fn(async () => []),
  listProjects: vi.fn(async () => []),
  replaceAccountAssignments: vi.fn(),
  syncDouyinAccountMetrics: vi.fn(),
  updateAccountIntegration: vi.fn(),
  updateClient: vi.fn(),
  updatePlatformIntegration: vi.fn(),
  updateProject: vi.fn(),
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

  it("shows official app permission gaps for a supported Douyin capability", async () => {
    vi.mocked(listAccounts).mockResolvedValueOnce([
      {
        id: 9,
        nickname: "数码菌",
        platform: "douyin",
        group_id: null,
        project_id: null,
        status: "active",
        external_account_id: "douyin-9",
        integration_status: "connected",
        auth_status: "authorized",
        data_sync_status: "pending",
        publish_capability: "prepare_only",
        created_at: "2026-07-22T00:00:00Z",
      },
    ]);
    vi.mocked(getDouyinAccountCapabilities).mockResolvedValueOnce({
      account_id: 9,
      platform: "douyin",
      configured_app_scopes: ["user_info", "task.posting.create"],
      granted_account_scopes: ["user_info"],
      next_recommended: "posting_feedback",
      capabilities: [
        {
          key: "posting_feedback",
          label: "投流回收",
          description: "创建投流任务、绑定作品并查询基础信息，形成发布复盘闭环。",
          app_scopes: [
            "task.posting.create",
            "posting.behavior",
            "task.posting.user_verification",
          ],
          user_scopes: ["posting.behavior"],
          missing_app_scopes: ["posting.behavior", "task.posting.user_verification"],
          missing_user_scopes: ["posting.behavior"],
          status: "needs_app_permission",
        },
      ],
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "查看抖音能力 数码菌" }));
    expect(await screen.findByText("投流回收")).toBeInTheDocument();
    expect(screen.getByText("开放平台待开通")).toBeInTheDocument();
    expect(screen.getByText(/task.posting.user_verification/)).toBeInTheDocument();
  });

  it("opens the data center for the selected account and preserves workspace context", async () => {
    vi.mocked(listAccounts).mockResolvedValueOnce([
      {
        id: 3,
        nickname: "数码菌",
        platform: "douyin",
        group_id: null,
        project_id: null,
        status: "active",
        external_account_id: "douyin-3",
        integration_status: "connected",
        auth_status: "authorized",
        data_sync_status: "healthy",
        publish_capability: "prepare_only",
        created_at: "2026-07-22T00:00:00Z",
      },
    ]);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "打开数据中心 数码菌" }));

    expect(workspaceMocks.setPlatform).toHaveBeenCalledWith("douyin");
    expect(workspaceMocks.setAccountId).toHaveBeenCalledWith(3);
    expect(workspaceMocks.navigate).toHaveBeenCalledWith("/accounts/3/data");
  });

  it("saves multiple customer and project bindings with explicit defaults", async () => {
    const workspaceApi = await import("../api/workspace");
    vi.mocked(workspaceApi.listClients).mockResolvedValueOnce([
      { id: 10, name: "客户甲", status: "active" },
      { id: 11, name: "客户乙", status: "active" },
    ]);
    vi.mocked(workspaceApi.listProjects).mockResolvedValueOnce([
      { id: 20, client_id: 10, name: "项目甲", status: "active" },
      { id: 21, client_id: 11, name: "项目乙", status: "active" },
    ]);
    vi.mocked(listAccounts).mockResolvedValueOnce([
      {
        id: 5,
        nickname: "矩阵账号",
        platform: "douyin",
        client_id: 10,
        client_ids: [10, 11],
        project_id: 20,
        project_ids: [20, 21],
        group_id: null,
        status: "active",
        external_account_id: "douyin-5",
        integration_status: "connected",
        auth_status: "authorized",
        data_sync_status: "healthy",
        publish_capability: "prepare_only",
        created_at: "2026-07-22T00:00:00Z",
      },
    ]);
    vi.mocked(replaceAccountAssignments).mockResolvedValueOnce({
      id: 5,
      nickname: "矩阵账号",
      platform: "douyin",
      client_id: 10,
      client_ids: [10, 11],
      project_id: 20,
      project_ids: [20, 21],
      group_id: null,
      status: "active",
      external_account_id: "douyin-5",
      integration_status: "connected",
      auth_status: "authorized",
      data_sync_status: "healthy",
      publish_capability: "prepare_only",
      created_at: "2026-07-22T00:00:00Z",
    });

    renderPage();

    expect(await screen.findByText("矩阵账号")).toBeInTheDocument();
    const editAssignment = await screen.findByText("编辑归属");
    fireEvent.click(editAssignment.closest("button")!);
    expect(
      await screen.findByRole("dialog", { name: "客户与项目归属 · 矩阵账号" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存归属" }));

    await waitFor(() =>
      expect(replaceAccountAssignments).toHaveBeenCalledWith(5, {
        client_ids: [10, 11],
        project_ids: [20, 21],
        default_client_id: 10,
        default_project_id: 20,
      }),
    );
  });
});

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <Accounts />
      </AntApp>
    </QueryClientProvider>,
  );
}
