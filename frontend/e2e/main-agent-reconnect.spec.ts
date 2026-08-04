import { expect, test, type Page, type Route } from "@playwright/test";

const user = {
  id: 1,
  email: "reconnect@example.test",
  display_name: "Realtime operator",
  role: "admin",
  is_active: true,
};

const account = {
  id: 1,
  client_id: null,
  nickname: "Realtime account A",
  platform: "douyin",
  group_id: null,
  project_id: null,
  project_ids: [],
  status: "active",
  external_account_id: "realtime-a",
  integration_status: "connected",
  auth_status: "authorized",
  data_sync_status: "healthy",
  avatar_url: null,
  risk_count: 0,
  created_at: "2026-08-04T00:00:00Z",
};

const artifact = {
  id: 501,
  account_id: account.id,
  thread_id: 101,
  turn_id: 201,
  run_id: 301,
  skill_run_id: 401,
  task_id: null,
  artifact_type: "account_inspection_report",
  presentation_format: "document",
  title: "Recovered account report",
  version: 1,
  status: "ready_for_review",
  summary: "The recovered deliverable is visible exactly once.",
  sections: [{ key: "summary", title: "Summary", content: "Recovered from durable events." }],
  evidence_refs: [],
  quality: { score: 0.9, passed: true, issues: [] },
  created_at: "2026-08-04T00:00:00Z",
};

const thread = {
  id: 101,
  org_id: 1,
  created_by_id: user.id,
  client_id: null,
  project_id: null,
  account_id: account.id,
  title: "Reconnect contract",
  created_at: "2026-08-04T00:00:00Z",
  updated_at: "2026-08-04T00:00:00Z",
  turns: [{
    id: 201,
    thread_id: 101,
    org_id: 1,
    created_by_id: user.id,
    client_message_id: "reconnect-turn",
    user_input: "Inspect and recover this account",
    assistant_response: "Working through the third step.",
    intent: { mode: "skill", route_source: "explicit", skill_code: "account_inspection" },
    status: "running",
    projections: [
      {
        type: "progress",
        turn_id: 201,
        skill_run_id: 401,
        stages: [
          { code: "read_data", name: "Read account data", status: "completed" },
          { code: "specialist_work", name: "Specialist analysis", status: "completed" },
          { code: "quality_review", name: "Quality review", status: "running" },
        ],
      },
      {
        type: "artifact",
        turn_id: 201,
        artifact_id: artifact.id,
        artifact_type: artifact.artifact_type,
        skill_run_id: artifact.skill_run_id,
        account_id: account.id,
        report: { summary: artifact.summary },
      },
    ],
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
  }],
};

test("durable reconnect resumes one work turn without duplicate or foreign projection", async ({ page }) => {
  await installBrowserState(page);
  const telemetry = await mockReconnectApi(page);
  await page.goto("/brain");

  const turn = page.getByTestId("work-turn");
  await expect(turn).toHaveCount(1);
  await expect(turn.locator(".tz-work-turn__progress")).toBeVisible();
  await expect(turn.locator(".tz-work-turn__progress li")).toHaveCount(3);
  await expect(turn.getByText("Read account data", { exact: true })).toHaveCount(1);
  await expect(turn.getByText("Specialist analysis", { exact: true })).toHaveCount(1);
  await expect(turn.getByText("Quality review", { exact: true })).toHaveCount(1);

  await expect(turn).toHaveAttribute("data-turn-status", "completed");
  await expect(turn.locator('[data-projection-key="artifact-501"]')).toHaveCount(1);
  await expect(turn).not.toContainText("Account B private event");
  expect(telemetry.recoveryRequests).toBeGreaterThanOrEqual(2);
  expect(telemetry.streamRequests).toBeGreaterThanOrEqual(2);
  expect(telemetry.authorizations).toEqual(
    expect.arrayContaining(["Bearer reconnect-token"]),
  );
  expect(telemetry.urls.join(" ")).not.toContain("reconnect-token");
  const navigationKinds = await page.evaluate(() => performance.getEntriesByType("navigation")
    .map((entry) => (entry as PerformanceNavigationTiming).type));
  expect(navigationKinds).not.toContain("reload");
});

async function installBrowserState(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem(
      "tongzhouxing_brain_active_conversation_threads",
      JSON.stringify({ version: 1, accounts: { 1: 101 } }),
    );
    localStorage.setItem("dyflow_token", "reconnect-token");
    localStorage.setItem(
      "tongzhouxing_current_workspace",
      JSON.stringify({
        version: 2,
        clientId: null,
        projectId: null,
        platform: "douyin",
        accountId: 1,
      }),
    );
  });
}

async function mockReconnectApi(page: Page) {
  const telemetry = {
    authorizations: [] as string[],
    recoveryRequests: 0,
    streamRequests: 0,
    urls: [] as string[],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();
    telemetry.urls.push(request.url());
    if (path.startsWith("/conversation-threads/101/")) {
      telemetry.authorizations.push(request.headers().authorization ?? "");
    }
    if (method === "POST" && path === "/auth/login") return json(route, { access_token: "reconnect-token", token_type: "bearer", user });
    if (method === "GET" && path === "/auth/me") return json(route, user);
    if (method === "GET" && ["/projects", "/account-groups", "/clients", "/notifications", "/brain/tasks"].includes(path)) return json(route, []);
    if (method === "GET" && path === "/accounts") return json(route, [account]);
    if (method === "GET" && path === "/notifications/unread-count") return json(route, { count: 0 });
    if (method === "GET" && path === "/skills") return json(route, { data: [] });
    if (method === "GET" && path === "/workspace-context") return json(route, { clients: [], selected_client: null, projects: [], selected_project: null, accounts: [account] });
    if (method === "GET" && path === "/brain/conversations/101") return json(route, thread);
    if (method === "GET" && path === "/artifacts/501") return json(route, artifact);
    if (method === "GET" && path === "/conversation-threads/101/events") {
      telemetry.recoveryRequests += 1;
      if (telemetry.recoveryRequests === 1) return json(route, { data: [] });
      return json(route, { data: [
        event(10, 1, "step.completed", { step: "quality_review", status: "completed" }),
        event(11, 2, "deliverable.updated", { deliverable_id: 501 }),
        event(12, 3, "turn.completed", { status: "completed" }),
        { ...event(13, 1, "step.started", { step: "foreign", message: "Account B private event" }), thread_id: 202, turn_id: 202 },
      ] });
    }
    if (method === "GET" && path === "/conversation-threads/101/event-stream") {
      telemetry.streamRequests += 1;
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    }
    return json(route, {});
  });
  return telemetry;
}

function event(id: number, sequence: number, type: string, payload: Record<string, unknown>) {
  return {
    id,
    sequence,
    type,
    payload,
    thread_id: 101,
    turn_id: 201,
    run_id: 301,
    skill_run_id: 401,
    created_at: "2026-08-04T00:00:00Z",
  };
}

async function json(route: Route, body: unknown) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}
