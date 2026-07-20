import { expect, test, type Page, type Route } from "@playwright/test";

const admin = {
  id: 1,
  email: "admin@dyflow.local",
  display_name: "系统管理员",
  role: "admin",
  is_active: true,
};

test("member governance fits the 1440px desktop viewport without document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockUsersApi(page);
  await page.addInitScript(() => localStorage.setItem("dyflow_token", "test-token"));
  await page.goto("/users");

  await expect(page.getByRole("heading", { name: "成员与权限" })).toBeVisible();
  await expect(page.locator(".tz-user-page-header .ant-btn")).toBeAttached();

  const layout = await page.evaluate(() => {
    const button = document.querySelector(".tz-user-page-header .ant-btn")?.getBoundingClientRect();
    const inspector = document.querySelector(".tz-member-inspector")?.getBoundingClientRect();
    return {
      viewportWidth: window.innerWidth,
      documentClientWidth: document.documentElement.clientWidth,
      documentScrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      buttonRight: button?.right ?? Number.POSITIVE_INFINITY,
      inspectorRight: inspector?.right ?? Number.POSITIVE_INFINITY,
    };
  });

  expect(layout.documentScrollWidth).toBeLessThanOrEqual(layout.documentClientWidth);
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.documentClientWidth);
  expect(layout.buttonRight).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.inspectorRight).toBeLessThanOrEqual(layout.viewportWidth);
});

async function mockUsersApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (!pathname.startsWith("/api/")) return route.continue();
    const path = pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "POST" && path === "/auth/login") {
      return json(route, { access_token: "test-token", token_type: "bearer", user: admin });
    }
    if (method === "GET" && path === "/auth/me") return json(route, admin);
    if (method === "GET" && path === "/workspace-context") {
      return json(route, {
        clients: [],
        selected_client: null,
        projects: [],
        selected_project: null,
        accounts: [],
      });
    }
    if (method === "GET" && path === "/notifications/unread-count") return json(route, { count: 0 });
    if (method === "GET" && path === "/users") return json(route, [admin]);
    if (method === "GET" && path === "/users/access-catalog") {
      return json(route, { clients: [], projects: [], accounts: [] });
    }
    if (method === "GET" && path === "/users/me/secondary-password/status") {
      return json(route, {
        configured: false,
        deletion_available: false,
        delete_available_at: null,
        locked_until: null,
      });
    }
    if (method === "GET" && path === "/users/1") {
      return json(route, {
        ...admin,
        has_global_access: true,
        account_scope_mode: "all_accessible",
        account_ids: [],
        client_memberships: [],
        project_memberships: [],
      });
    }
    if (method === "GET" && path === "/projects") return json(route, []);
    if (method === "GET" && path === "/account-groups") return json(route, []);
    if (method === "GET" && path === "/accounts") return json(route, []);

    return json(route, []);
  });
}

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}
