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
        "accounts/:accountId/data",
        "knowledge",
        "wechat-articles/:articleId",
      ]),
    );
  });

  it("keeps privileged system routes admin-only", () => {
    const adminPaths = APP_ROUTES.filter((route) => route.adminOnly).map((route) => route.path);

    expect(adminPaths).toEqual(["config", "models", "users"]);
  });

  it("does not register duplicate path routes", () => {
    const paths = APP_ROUTES.map((route) => route.path ?? "index");

    expect(new Set(paths).size).toBe(paths.length);
  });

  it("keeps placeholder and legacy aliases out of the page registry", () => {
    const paths = APP_ROUTES.map((route) => route.path ?? "index");

    expect(paths).not.toContain("customer-service");
    expect(paths).not.toContain("advertising");
    expect(paths).not.toContain("pipeline");
  });
});
