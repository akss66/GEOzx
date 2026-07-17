// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getMe } from "./api/auth";
import App from "./App";
import { useAuth } from "./stores/auth";

vi.mock("./api/auth", () => ({ getMe: vi.fn() }));
vi.mock("./components/AppShell", () => ({
  AppShell: () => <div>工作区已打开</div>,
}));
vi.mock("./pages/Login", () => ({ default: () => <div>登录页面</div> }));

const user = {
  id: 1,
  email: "admin@tzxai.top",
  display_name: "系统管理员",
  role: "admin" as const,
  is_active: true,
};

describe("App bootstrap", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("dyflow_token", "stored-token");
    useAuth.setState({ token: "stored-token", user: null });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("keeps the session and offers retry after a service failure", async () => {
    vi.mocked(getMe)
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce(user);

    renderApp();

    expect(await screen.findByRole("alert")).toHaveTextContent("工作区暂时无法打开");
    expect(useAuth.getState().token).toBe("stored-token");

    fireEvent.click(screen.getByRole("button", { name: "重新连接" }));

    expect(await screen.findByText("工作区已打开")).toBeInTheDocument();
    expect(useAuth.getState().user).toEqual(user);
  });

  it("clears the session only after an explicit authentication failure", async () => {
    vi.mocked(getMe).mockRejectedValueOnce({ response: { status: 401 } });

    renderApp();

    expect(await screen.findByText("登录页面")).toBeInTheDocument();
    expect(useAuth.getState().token).toBeNull();
  });
});

function renderApp() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <App />
    </MemoryRouter>,
  );
}
