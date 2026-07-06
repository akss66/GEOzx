// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AdminRoute, ProtectedRoute } from "./RouteGuards";
import { useAuth } from "../stores/auth";
import type { User } from "../types";

const member: User = {
  id: 2,
  email: "member@example.com",
  display_name: "Member",
  role: "user",
  is_active: true,
};

const admin: User = {
  ...member,
  id: 1,
  email: "admin@example.com",
  display_name: "Admin",
  role: "admin",
};

function setAuthState(token: string | null, user: User | null) {
  useAuth.setState({ token, user });
}

describe("RouteGuards", () => {
  afterEach(() => {
    cleanup();
    setAuthState(null, null);
    localStorage.clear();
  });

  it("redirects unauthenticated users to login", () => {
    setAuthState(null, null);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<div>Private workspace</div>} />
          </Route>
          <Route path="/login" element={<div>Login page</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("Login page")).toBeInTheDocument();
    expect(screen.queryByText("Private workspace")).not.toBeInTheDocument();
  });

  it("renders protected content for authenticated users", () => {
    setAuthState("token", member);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<div>Private workspace</div>} />
          </Route>
          <Route path="/login" element={<div>Login page</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("Private workspace")).toBeInTheDocument();
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
  });

  it("renders admin-only routes for admins", () => {
    setAuthState("token", admin);

    render(
      <MemoryRouter initialEntries={["/config"]}>
        <Routes>
          <Route path="/" element={<div>Home page</div>} />
          <Route element={<AdminRoute />}>
            <Route path="/config" element={<div>Config page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("Config page")).toBeInTheDocument();
  });

  it("redirects non-admin users away from admin-only routes", () => {
    setAuthState("token", member);

    render(
      <MemoryRouter initialEntries={["/config"]}>
        <Routes>
          <Route path="/" element={<div>Home page</div>} />
          <Route element={<AdminRoute />}>
            <Route path="/config" element={<div>Config page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText("Home page")).toBeInTheDocument();
    expect(screen.queryByText("Config page")).not.toBeInTheDocument();
  });
});
