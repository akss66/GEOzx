// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createUser,
  getSecondaryPasswordStatus,
  getUserAccessCatalog,
  getUserDetail,
  listUsers,
  permanentlyDeleteUser,
  previewUserDeletion,
  resetUserPassword,
  setSecondaryPassword,
  updateUser,
  updateUserAccess,
} from "../api/auth";
import { useAuth } from "../stores/auth";
import type {
  SecondaryPasswordStatus,
  User,
  UserAccessCatalog,
  UserDeletionPreview,
  UserDetail,
} from "../types";
import Users from "./Users";

vi.mock("../api/auth", () => ({
  listUsers: vi.fn(),
  createUser: vi.fn(),
  getUserDetail: vi.fn(),
  getUserAccessCatalog: vi.fn(),
  updateUser: vi.fn(),
  updateUserAccess: vi.fn(),
  setSecondaryPassword: vi.fn(),
  getSecondaryPasswordStatus: vi.fn(),
  resetUserPassword: vi.fn(),
  previewUserDeletion: vi.fn(),
  permanentlyDeleteUser: vi.fn(),
}));

const adminUser: User = {
  id: 1,
  email: "admin@tzx.ai",
  display_name: "系统管理员",
  role: "admin",
  is_active: true,
};

const operatorUser: User = {
  id: 2,
  email: "ops@tzx.ai",
  display_name: "运营同事",
  role: "user",
  is_active: true,
};

const dormantUser: User = {
  id: 3,
  email: "idle@tzx.ai",
  display_name: "停用成员",
  role: "user",
  is_active: false,
};

const accessCatalog: UserAccessCatalog = {
  clients: [
    { id: 10, name: "数码品牌", status: "active" },
    { id: 11, name: "生活方式品牌", status: "active" },
  ],
  projects: [
    { id: 20, client_id: 10, name: "七月增长", status: "active" },
    { id: 21, client_id: 11, name: "内容升级", status: "paused" },
  ],
  accounts: [
    {
      id: 101,
      client_id: 10,
      project_ids: [20],
      nickname: "数码品牌主号",
      platform: "douyin",
      status: "active",
    },
    {
      id: 102,
      client_id: 11,
      project_ids: [21],
      nickname: "生活方式副号",
      platform: "xiaohongshu",
      status: "active",
    },
  ],
};

let roster: User[] = [];
let detailMap: Record<number, UserDetail> = {};
let secondaryStatus: SecondaryPasswordStatus;
let previewCounter = 0;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function resetFixtures() {
  roster = [clone(adminUser), clone(operatorUser), clone(dormantUser)];
  detailMap = {
    1: {
      ...clone(adminUser),
      has_global_access: true,
      account_scope_mode: "all_accessible",
      account_ids: [],
      client_memberships: [],
      project_memberships: [],
    },
    2: {
      ...clone(operatorUser),
      has_global_access: false,
      account_scope_mode: "all_accessible",
      account_ids: [],
      client_memberships: [
        { client_id: 10, client_name: "数码品牌", role: "operator" },
      ],
      project_memberships: [
        {
          project_id: 20,
          project_name: "七月增长",
          client_id: 10,
          client_name: "数码品牌",
          role: "reviewer",
        },
      ],
    },
    3: {
      ...clone(dormantUser),
      has_global_access: false,
      account_scope_mode: "selected",
      account_ids: [],
      client_memberships: [],
      project_memberships: [],
    },
  };
  secondaryStatus = {
    configured: false,
    deletion_available: false,
    delete_available_at: null,
    locked_until: null,
  };
  previewCounter = 0;
}

function configureAuthMocks() {
  vi.mocked(listUsers).mockImplementation(async () => clone(roster));
  vi.mocked(createUser).mockImplementation(async (input) => {
    const nextUser: User = {
      id: roster.length + 10,
      email: input.email,
      display_name: input.display_name,
      role: input.role,
      is_active: true,
    };
    roster = [...roster, nextUser];
    detailMap[nextUser.id] = {
      ...nextUser,
      has_global_access: input.role === "admin",
      account_scope_mode: "all_accessible",
      account_ids: [],
      client_memberships: [],
      project_memberships: [],
    };
    return clone(nextUser);
  });
  vi.mocked(getUserDetail).mockImplementation(async (userId: number) => clone(detailMap[userId]));
  vi.mocked(getUserAccessCatalog).mockImplementation(async () => clone(accessCatalog));
  vi.mocked(updateUser).mockImplementation(async (userId: number, input) => {
    const current = detailMap[userId];
    const next: UserDetail = { ...current, ...input };
    detailMap[userId] = next;
    roster = roster.map((user) => (user.id === userId ? { ...user, ...input } : user));
    return clone(roster.find((user) => user.id === userId)!);
  });
  vi.mocked(updateUserAccess).mockImplementation(async (userId: number, input) => {
    const current = detailMap[userId];
    const next: UserDetail = {
      ...current,
      client_memberships: input.clients.map((item) => ({
        client_id: item.client_id,
        client_name: accessCatalog.clients.find((client) => client.id === item.client_id)?.name ?? `客户 ${item.client_id}`,
        role: item.role,
      })),
      project_memberships: input.projects.map((item) => {
        const project = accessCatalog.projects.find((entry) => entry.id === item.project_id);
        return {
          project_id: item.project_id,
          project_name: project?.name ?? `项目 ${item.project_id}`,
          client_id: project?.client_id ?? null,
          client_name: accessCatalog.clients.find((client) => client.id === project?.client_id)?.name ?? null,
          role: item.role,
        };
      }),
      account_scope_mode: input.account_scope_mode ?? current.account_scope_mode,
      account_ids: input.account_ids ?? current.account_ids,
    };
    detailMap[userId] = next;
    return clone(next);
  });
  vi.mocked(setSecondaryPassword).mockImplementation(async () => {
    secondaryStatus = {
      configured: true,
      deletion_available: false,
      delete_available_at: "2026-07-20T10:10:00Z",
      locked_until: null,
    };
    return clone(secondaryStatus);
  });
  vi.mocked(getSecondaryPasswordStatus).mockImplementation(async () => clone(secondaryStatus));
  vi.mocked(resetUserPassword).mockImplementation(async () => undefined);
  vi.mocked(previewUserDeletion).mockImplementation(async (userId: number) => {
    previewCounter += 1;
    return clone<UserDeletionPreview>({
      target_user_id: userId,
      target_email: detailMap[userId].email,
      counts: {
        users: 1,
        brain_tasks: userId === 2 ? 3 : 0,
        content_items: userId === 2 ? 1 : 0,
      },
      preview_token: `preview-${userId}-${previewCounter}`,
      expires_at: "2026-07-20T10:20:00Z",
      allowed: true,
      blockers: [],
    });
  });
  vi.mocked(permanentlyDeleteUser).mockImplementation(async (userId: number) => {
    const removed = detailMap[userId];
    roster = roster.filter((user) => user.id !== userId);
    delete detailMap[userId];
    return {
      operation_id: `delete-${userId}`,
      deleted_at: "2026-07-20T10:12:00Z",
      counts: {
        users: 1,
        brain_tasks: removed.email === operatorUser.email ? 3 : 0,
      },
    };
  });
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <Users />
      </AntApp>
    </QueryClientProvider>,
  );

  return { queryClient };
}

function setInputValue(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function storageSnapshot() {
  const local = Object.entries(localStorage).map(([key, value]) => `${key}:${value}`).join("|");
  const session = Object.entries(sessionStorage).map(([key, value]) => `${key}:${value}`).join("|");
  return `${local}|${session}`;
}

describe("Users", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    cleanup();
    localStorage.clear();
    sessionStorage.clear();
    resetFixtures();
    configureAuthMocks();
    useAuth.setState({ token: "test-token", user: clone(adminUser) });
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    sessionStorage.clear();
    useAuth.setState({ token: null, user: null });
  });

  it("auto-selects the first member and exposes the four governance tabs", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "成员与权限" })).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "概览" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "资源权限" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "安全与登录" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "操作记录" })).toBeInTheDocument();
    expect(screen.queryByText("请选择成员")).not.toBeInTheDocument();

    await waitFor(() => expect(
      screen.getByRole("button", { name: /系统管理员/ }),
    ).toHaveAttribute("aria-pressed", "true"));

    fireEvent.click(screen.getByRole("tab", { name: "操作记录" }));
    expect(await screen.findByText("成员级操作记录暂不可用")).toBeInTheDocument();
  });

  it("filters the roster by search, role, anomaly, and status", async () => {
    renderPage();
    await screen.findByDisplayValue("系统管理员");

    setInputValue("搜索成员", "停用");
    expect(screen.getByRole("button", { name: /停用成员/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /运营同事/ })).not.toBeInTheDocument();

    setInputValue("搜索成员", "");
    fireEvent.change(screen.getByLabelText("系统角色筛选"), { target: { value: "admin" } });
    expect(screen.getByRole("button", { name: /系统管理员/ })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("系统角色筛选"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("授权异常筛选"), { target: { value: "anomalies" } });
    expect(screen.getByRole("button", { name: /停用成员/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /运营同事/ })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("状态筛选"), { target: { value: "active" } });
    expect(screen.queryByRole("button", { name: /停用成员/ })).not.toBeInTheDocument();
    expect(screen.getByText("没有符合条件的成员")).toBeInTheDocument();
  });

  it("saves identity changes and translates enable-disable business errors", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));
    await screen.findByDisplayValue("运营同事");

    setInputValue("显示名称", "内容负责人");
    setInputValue("登录邮箱", "owner@tzx.ai");
    fireEvent.change(screen.getByLabelText("系统身份"), { target: { value: "admin" } });
    fireEvent.click(screen.getByRole("button", { name: "保存成员资料" }));

    await waitFor(() => expect(updateUser).toHaveBeenCalledWith(2, {
      display_name: "内容负责人",
      email: "owner@tzx.ai",
      role: "admin",
    }));

    vi.mocked(updateUser).mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            code: "USER_LAST_ACTIVE_ADMIN_REQUIRED",
            message: "last admin required",
          },
        },
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "禁用成员" }));
    expect(await screen.findByText("至少保留一位启用中的管理员。")).toBeInTheDocument();
  });

  it("toggles account scope, persists selected accounts, and explains when no accounts are visible", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));
    fireEvent.click(screen.getByRole("tab", { name: "资源权限" }));
    await screen.findByLabelText("最终生效账号");

    fireEvent.click(screen.getByLabelText("仅指定账号"));
    fireEvent.click(screen.getByLabelText("数码品牌主号"));
    fireEvent.click(screen.getByRole("button", { name: "保存资源权限" }));

    await waitFor(() => expect(updateUserAccess).toHaveBeenCalledWith(2, expect.objectContaining({
      account_scope_mode: "selected",
      account_ids: [101],
    })));

    fireEvent.click(screen.getByRole("button", { name: /停用成员/ }));
    await waitFor(() => expect(
      screen.getByRole("button", { name: /停用成员/ }),
    ).toHaveAttribute("aria-pressed", "true"));
    fireEvent.click(screen.getByRole("tab", { name: "资源权限" }));
    fireEvent.click(screen.getByLabelText("仅指定账号"));
    expect(await screen.findByText("无账号可见")).toBeInTheDocument();
  });

  it("sets the current admin secondary password and resets the selected member password", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));
    fireEvent.click(screen.getByRole("tab", { name: "安全与登录" }));
    await screen.findByText("当前登录管理员的二级密码");

    setInputValue("当前登录密码", "admin-pw-123");
    setInputValue("新的二级密码", "secondary-pass-123");
    fireEvent.click(screen.getByRole("button", { name: "设置二级密码" }));

    await waitFor(() => expect(vi.mocked(setSecondaryPassword).mock.calls[0]?.[0]).toEqual({
      current_password: "admin-pw-123",
      secondary_password: "secondary-pass-123",
    }));

    setInputValue("新的登录密码", "reset-pass-123");
    fireEvent.click(screen.getByRole("button", { name: "重置该成员登录密码" }));

    await waitFor(() => expect(resetUserPassword).toHaveBeenCalledWith(2, {
      new_password: "reset-pass-123",
    }));
  });

  it("recovers from stale delete previews and clears sensitive destructive inputs", async () => {
    const { queryClient } = renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));
    fireEvent.click(screen.getByRole("tab", { name: "安全与登录" }));
    await screen.findByRole("button", { name: "获取删除预览" });

    fireEvent.click(screen.getByRole("button", { name: "获取删除预览" }));
    await screen.findByText("不可逆影响预览");

    setInputValue("确认目标邮箱", "ops@tzx.ai");
    setInputValue("执行人二级密码", "delete-pass-123");

    vi.mocked(permanentlyDeleteUser).mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            code: "USER_DELETION_PREVIEW_STALE",
            message: "stale preview",
          },
        },
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "确认永久删除" }));

    expect(await screen.findByText("成员数据已变化，请重新获取最新影响预览。")).toBeInTheDocument();
    expect(screen.queryByLabelText("确认目标邮箱")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("执行人二级密码")).not.toBeInTheDocument();

    const queryKeys = JSON.stringify(queryClient.getQueryCache().getAll().map((entry) => entry.queryKey));
    expect(queryKeys).not.toContain("preview-2-1");
    expect(queryKeys).not.toContain("ops@tzx.ai");
    expect(queryKeys).not.toContain("delete-pass-123");
    expect(storageSnapshot()).not.toContain("preview-2-1");
    expect(storageSnapshot()).not.toContain("ops@tzx.ai");
    expect(storageSnapshot()).not.toContain("delete-pass-123");
  });

  it("clears destructive inputs when the delete flow closes", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));
    fireEvent.click(screen.getByRole("tab", { name: "安全与登录" }));
    fireEvent.click(await screen.findByRole("button", { name: "获取删除预览" }));
    await screen.findByText("不可逆影响预览");

    setInputValue("确认目标邮箱", "ops@tzx.ai");
    setInputValue("执行人二级密码", "delete-pass-123");
    fireEvent.click(screen.getByRole("button", { name: "关闭删除流程" }));

    fireEvent.click(await screen.findByRole("button", { name: /获取删除预览/ }));
    await screen.findByText("不可逆影响预览");
    expect(screen.getByLabelText("确认目标邮箱")).toHaveValue("");
    expect(screen.getByLabelText("执行人二级密码")).toHaveValue("");
  });

  it("removes a permanently deleted member from the roster and selects the next member", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));
    fireEvent.click(screen.getByRole("tab", { name: "安全与登录" }));
    fireEvent.click(await screen.findByRole("button", { name: "获取删除预览" }));
    await screen.findByText("不可逆影响预览");

    setInputValue("确认目标邮箱", "ops@tzx.ai");
    setInputValue("执行人二级密码", "delete-pass-123");
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除" }));

    await waitFor(() => expect(permanentlyDeleteUser).toHaveBeenCalledWith(2, {
      preview_token: "preview-2-1",
      target_email: "ops@tzx.ai",
      secondary_password: "delete-pass-123",
    }));

    await waitFor(() => expect(screen.queryByRole("button", { name: /运营同事/ })).not.toBeInTheDocument());
    expect(await screen.findByDisplayValue("停用成员")).toBeInTheDocument();

    const rosterList = screen.getByRole("list", { name: "成员名册列表" });
    expect(within(rosterList).getByRole("button", { name: /停用成员/ })).toHaveAttribute("aria-pressed", "true");
  });
});
