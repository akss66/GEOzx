// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { App as AntApp } from "antd";
import { cleanup, render, screen } from "@testing-library/react";
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
});
