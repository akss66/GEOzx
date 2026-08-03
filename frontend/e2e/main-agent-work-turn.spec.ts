import { expect, test, type CDPSession, type Page, type Route } from "@playwright/test";

const user = {
  id: 1,
  email: "work-turn@example.test",
  display_name: "E2E 管理员",
  role: "admin",
  is_active: true,
};

const account = {
  id: 1,
  client_id: null,
  nickname: "Work Turn 验收账号",
  platform: "douyin",
  group_id: null,
  project_id: null,
  project_ids: [],
  status: "active",
  external_account_id: "work-turn-e2e",
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
  artifact_type: "video_script",
  presentation_format: "spoken",
  title: "五条夏日口播拍摄稿",
  version: 1,
  status: "ready_for_review",
  summary: "已围绕账号受众整理五条可直接拍摄的口播稿。",
  sections: [
    { key: "core_conclusion", title: "拍摄重点", content: "先用前三秒抛出痛点，再给出可执行步骤。" },
    { key: "spoken_script", title: "口播拍摄稿", content: "第一条：用真实场景开场。" },
  ],
  evidence_refs: [],
  quality: { score: 94, passed: true, issues: [] },
  created_at: "2026-08-04T00:00:00Z",
};

test("work turn remains readable, actionable, and responsive with local mocked data", async ({ page }, testInfo) => {
  const consoleDiagnostics = await captureConsoleDiagnostics(page);

  await mockWorkTurnApi(page);
  await login(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/brain");
  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: account.nickname }).click();

  const input = page.locator(".dy-brain-input textarea");
  await expect(input).toBeVisible();
  await input.fill("请生成五条夏日口播拍摄稿");
  await page.locator(".dy-brain-send-button").click();

  const turn = page.getByTestId("work-turn");
  await expect(turn).toHaveCount(1);
  await expect(turn).toContainText("请生成五条夏日口播拍摄稿");
  await expect(turn.getByText("运营大脑", { exact: true })).toBeVisible();
  await expect(turn.getByRole("button", { name: "查看口播拍摄稿" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "方案与内容" })).toBeVisible();

  await expect(turn.locator(".tz-work-turn__user")).toHaveCSS("border-left-width", "0px");
  await expect(turn.locator(".tz-work-turn__operator")).toHaveCSS("border-width", "0px");
  await expect(turn.locator(".tz-work-turn__progress")).toHaveCSS("border-top-width", "1px");

  const processToggle = turn.getByRole("button", { name: "查看过程" });
  await expect(processToggle).toHaveAttribute("aria-expanded", "false");
  const processContentId = await processToggle.getAttribute("aria-controls");
  expect(processContentId).toBeTruthy();
  await processToggle.focus();
  await expect(processToggle).toBeFocused();
  await processToggle.press("Enter");
  await expect(processToggle).toHaveAttribute("aria-expanded", "true");
  await expect(turn.locator(`[id="${processContentId}"]`)).toBeVisible();
  const technicalToggle = turn.getByRole("button", { name: "技术日志" });
  await expect(technicalToggle).toHaveAttribute("aria-expanded", "false");
  const technicalContentId = await technicalToggle.getAttribute("aria-controls");
  expect(technicalContentId).toBeTruthy();
  await technicalToggle.focus();
  await expect(technicalToggle).toBeFocused();
  await technicalToggle.press("Space");
  await expect(technicalToggle).toHaveAttribute("aria-expanded", "true");
  await expect(turn.locator(`[id="${technicalContentId}"]`)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("work-turn-desktop.png"), fullPage: true });

  await page.getByRole("tab", { name: "方案与内容" }).click();
  await expect(page.getByRole("region", { name: "方案与内容" })).toBeVisible();
  await expect(page.getByText("口播拍摄稿", { exact: true }).last()).toBeVisible();

  await page.getByRole("tab", { name: "对话" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(turn).toBeVisible();
  await expect(input).toBeVisible();
  const mobileColumns = await turn.evaluate((element) =>
    getComputedStyle(element).gridTemplateColumns.trim().split(/\s+/),
  );
  expect(mobileColumns).toHaveLength(1);
  const hasOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(hasOverflow).toBe(false);
  await page.screenshot({ path: testInfo.outputPath("work-turn-mobile.png"), fullPage: true });
  expect(consoleDiagnostics.warnings).toEqual([]);
  expect(consoleDiagnostics.errors).toEqual([]);
});

async function captureConsoleDiagnostics(page: Page) {
  const diagnostics: { warnings: Array<Record<string, unknown>>; errors: Array<Record<string, unknown>> } = {
    warnings: [],
    errors: [],
  };
  const session: CDPSession = await page.context().newCDPSession(page);
  await session.send("Runtime.enable");
  session.on("Runtime.consoleAPICalled", (event) => {
    if (event.type !== "warning" && event.type !== "error") return;
    diagnostics[event.type === "warning" ? "warnings" : "errors"].push({
      type: event.type,
      args: event.args.map((argument) => ({
        type: argument.type,
        value: argument.value,
        description: argument.description,
      })),
      location: event.stackTrace?.callFrames[0],
      stack: event.stackTrace?.callFrames.map((frame) => ({
        functionName: frame.functionName,
        url: frame.url,
        lineNumber: frame.lineNumber,
        columnNumber: frame.columnNumber,
      })),
    });
  });
  return diagnostics;
}

async function login(page: Page) {
  await page.goto("/login");
  await page.locator('input[autocomplete="email"]').fill(user.email);
  await page.locator('input[autocomplete="current-password"]').fill("test-password");
  await page.locator('form button[type="submit"]').click();
  await expect(page).toHaveURL(/\/$/);
}

async function mockWorkTurnApi(page: Page) {
  const thread = {
    id: 101,
    org_id: 1,
    created_by_id: user.id,
    client_id: null,
    project_id: null,
    account_id: account.id,
    title: "Work turn E2E",
    turns: [] as Array<Record<string, unknown>>,
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "POST" && path === "/auth/login") return json(route, { access_token: "e2e-token", token_type: "bearer", user });
    if (method === "GET" && path === "/auth/me") return json(route, user);
    if (method === "GET" && ["/projects", "/account-groups", "/clients", "/notifications", "/brain/tasks"].includes(path)) return json(route, []);
    if (method === "GET" && path === "/accounts") return json(route, [account]);
    if (method === "GET" && path === "/notifications/unread-count") return json(route, { count: 0 });
    if (method === "GET" && path === "/skills") return json(route, { data: [] });
    if (method === "GET" && path === "/workspace-context") {
      return json(route, {
        clients: [], selected_client: null, projects: [], selected_project: null, accounts: [account],
      });
    }
    if (method === "POST" && path === "/brain/conversations") return json(route, thread);
    if (method === "GET" && path === "/brain/conversations/101") return json(route, thread);
    if (method === "POST" && path === "/brain/conversations/101/turns") {
      const body = await request.postDataJSON() as { client_message_id: string; message: string };
      const turn = {
        id: 201,
        thread_id: thread.id,
        org_id: 1,
        created_by_id: user.id,
        client_message_id: body.client_message_id,
        user_input: body.message,
        assistant_response: "五条口播拍摄稿已经整理完成，可直接查看和确认。",
        intent: { mode: "skill", route_source: "deterministic", skill_code: "topic_planning" },
        status: "completed",
        projections: [
          {
            type: "progress",
            turn_id: 201,
            skill_run_id: 401,
            stages: [{ code: "outline", name: "整理拍摄结构", status: "completed" }],
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
        created_at: artifact.created_at,
        updated_at: artifact.created_at,
      };
      thread.turns = [turn];
      return json(route, { turn, run: { id: 301, status: "completed", phase: "completed" }, task_id: null, projections: turn.projections });
    }
    if (method === "GET" && path === "/artifacts") return json(route, { data: [artifact], pagination: { page: 1, page_size: 20, total: 1, pages: 1 } });
    if (method === "GET" && path === `/artifacts/${artifact.id}`) return json(route, artifact);
    return json(route, {});
  });
}

async function json(route: Route, body: unknown) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
}
