// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import Login from "./Login";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

vi.mock("../api/auth", () => ({
  login: vi.fn(),
}));

vi.mock("../stores/auth", () => ({
  useAuth: () => ({
    token: null,
    setAuth: vi.fn(),
  }),
}));

afterEach(cleanup);

describe("Login", () => {
  it("does not prefill demo credentials", () => {
    render(
      <MemoryRouter>
        <AntApp>
          <Login />
        </AntApp>
      </MemoryRouter>,
    );

    expect(screen.getByLabelText("邮箱")).toHaveValue("");
    expect(screen.getByLabelText("密码")).toHaveValue("");
    expect(document.body).not.toHaveTextContent("admin@qq.com");
    expect(document.querySelector('input[value="admin123"]')).not.toBeInTheDocument();
    expect(screen.queryByText("其他方式登录")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /通过微信登录|通过QQ登录/ }))
      .not.toBeInTheDocument();
  });

  it("clears a required-field error through normal change validation", async () => {
    render(
      <MemoryRouter>
        <AntApp>
          <Login />
        </AntApp>
      </MemoryRouter>,
    );

    const email = screen.getByLabelText("邮箱");
    fireEvent.submit(email.closest("form") as HTMLFormElement);
    await expect(screen.findByText("邮箱不能为空")).resolves.toBeInTheDocument();

    fireEvent.change(email, { target: { value: "operator@example.test" } });
    await waitFor(() => expect(screen.queryByText("邮箱不能为空")).not.toBeInTheDocument());
  });
});
