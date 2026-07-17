// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getUserAccessCatalog,
  getUserDetail,
  listUsers,
  updateUser,
  updateUserAccess,
} from "../api/auth";
import Users from "./Users";

const users = [
  { id: 1, email: "admin@tzxai.top", display_name: "系统管理员", role: "admin" as const, is_active: true },
  { id: 2, email: "operator@tzxai.top", display_name: "运营同事", role: "user" as const, is_active: true },
];

const memberDetail = {
  ...users[1],
  has_global_access: false,
  client_memberships: [
    { client_id: 10, client_name: "数码品牌", role: "operator" as const },
  ],
  project_memberships: [
    {
      project_id: 20,
      project_name: "七月增长",
      client_id: 10,
      client_name: "数码品牌",
      role: "reviewer" as const,
    },
  ],
};

const adminDetail = {
  ...users[0],
  has_global_access: true,
  client_memberships: [],
  project_memberships: [],
};

const catalog = {
  clients: [
    { id: 10, name: "数码品牌", status: "active" as const },
    { id: 11, name: "生活方式品牌", status: "active" as const },
  ],
  projects: [
    { id: 20, client_id: 10, name: "七月增长", status: "active" as const },
    { id: 21, client_id: 10, name: "新品发布", status: "active" as const },
  ],
};

vi.mock("../api/auth", () => ({
  listUsers: vi.fn(async () => users),
  getUserDetail: vi.fn(async (id: number) => (id === 1 ? adminDetail : memberDetail)),
  getUserAccessCatalog: vi.fn(async () => catalog),
  createUser: vi.fn(async () => users[1]),
  updateUser: vi.fn(async (_id: number, input: Record<string, unknown>) => ({
    ...users[1],
    ...input,
  })),
  updateUserAccess: vi.fn(async () => memberDetail),
}));

describe("Users", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
  afterEach(cleanup);

  function renderPage() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <AntApp><Users /></AntApp>
      </QueryClientProvider>,
    );
  }

  it("opens a dense member workspace with client roles and project overrides", async () => {
    renderPage();

    expect(await screen.findByText("成员与权限")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));

    expect(await screen.findByText("客户范围")).toBeInTheDocument();
    expect(screen.getAllByText("数码品牌").length).toBeGreaterThan(0);
    expect(screen.getByText("项目覆盖")).toBeInTheDocument();
    expect(screen.getByText("七月增长")).toBeInTheDocument();
    expect(getUserDetail).toHaveBeenCalledWith(2);
    expect(getUserAccessCatalog).toHaveBeenCalled();
  });

  it("keeps the member workspace recoverable when the directory fails to load", async () => {
    vi.mocked(listUsers).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("成员名册加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByRole("button", { name: /运营同事/ })).toBeInTheDocument();
  });

  it("keeps the roster visible when a selected member detail fails to load", async () => {
    vi.mocked(getUserDetail).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("成员详情加载失败");
    expect(screen.getByRole("button", { name: /系统管理员/ })).toBeInTheDocument();
    expect(screen.queryByText("客户范围")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("客户范围")).toBeInTheDocument();
  });

  it("isolates a failed access catalog from the selected member identity", async () => {
    vi.mocked(getUserAccessCatalog).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));

    expect(await screen.findByText("运营同事")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("授权资源加载失败");
    expect(screen.queryByText("未授权")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("客户范围")).toBeInTheDocument();
  });

  it("shows global access instead of misleading assignments for admins", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /系统管理员/ }));

    expect(await screen.findByText("全局访问权限")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存授权" })).not.toBeInTheDocument();
  });

  it("saves identity changes and atomically replaces access", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /运营同事/ }));
    await screen.findByText("客户范围");

    const nameInput = screen.getByLabelText("姓名");
    fireEvent.change(nameInput, { target: { value: "内容负责人" } });
    fireEvent.click(screen.getByRole("button", { name: /保存资料/ }));
    await waitFor(() => expect(updateUser).toHaveBeenCalledWith(2, {
      email: "operator@tzxai.top",
      display_name: "内容负责人",
      role: "user",
    }));

    fireEvent.click(screen.getByLabelText("生活方式品牌"));
    fireEvent.click(screen.getByRole("button", { name: /保存授权/ }));
    await waitFor(() => expect(updateUserAccess).toHaveBeenCalledWith(2, {
      clients: expect.arrayContaining([
        { client_id: 10, role: "operator" },
        { client_id: 11, role: "operator" },
      ]),
      projects: [{ project_id: 20, role: "reviewer" }],
    }));
  });
});
