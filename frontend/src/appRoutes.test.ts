import { describe, expect, it } from "vitest";

import { APP_ROUTES, PUBLIC_ROUTES } from "./appRoutes";

describe("app routes", () => {
  it("keeps the login route public", () => {
    expect(PUBLIC_ROUTES).toEqual([{ path: "/login", page: "login" }]);
  });

  it("registers the core authenticated routes", () => {
    const paths = APP_ROUTES.map((route) => route.path ?? "index");

    expect(paths).toEqual(
      expect.arrayContaining([
        "index",
        "brain",
        "agents",
        "tasks",
        "approvals",
        "review",
        "cost",
        "risks",
        "accounts",
        "knowledge",
      ]),
    );
  });

  it("keeps privileged system routes admin-only", () => {
    const adminPaths = APP_ROUTES.filter((route) => route.adminOnly).map((route) => route.path);

    expect(adminPaths).toEqual(["config", "users"]);
  });

  it("does not register duplicate path routes", () => {
    const paths = APP_ROUTES.map((route) => route.path ?? "index");

    expect(new Set(paths).size).toBe(paths.length);
  });
});
