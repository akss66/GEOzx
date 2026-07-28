import { expect, test, type Page, type Route } from "@playwright/test";

const createdAt = "2026-07-01T00:00:00Z";
const sourceTurnId = 3001;
const followUpTurnId = 3002;
const inspectionTaskId = 8101;

const user = {
  id: 1,
  email: "admin@dyflow.local",
  display_name: "Admin",
  role: "admin",
  is_active: true,
};

const account = {
  id: 1,
  client_id: null,
  client_ids: [],
  nickname: "主账号",
  platform: "douyin",
  group_id: null,
  project_id: null,
  project_ids: [],
  status: "active",
  external_account_id: "douyin-1",
  integration_status: "connected",
  auth_status: "authorized",
  data_sync_status: "healthy",
  avatar_url: null,
  created_at: createdAt,
};

const inspectionSkill = {
  code: "account_inspection",
  version: 1,
  name: "一键账号体检",
  description: "检查账号健康度并生成正式报告",
  category: "quick_operations" as const,
  icon: "inspection",
  requires_account: true,
  is_available: true,
  unavailable_reason: null,
};

const expertInvocation = {
  id: 9101,
  task_id: inspectionTaskId,
  agent_code: "06-operator",
  agent_name: "数据分析专家",
  status: "done",
  input_summary: "读取近七天账号数据",
  output_summary: "账号数据分析完成",
  model: "test-model",
  token_count: 120,
  cost: 0.01,
  failure_reason: null,
  upstream: [],
  started_at: createdAt,
  finished_at: createdAt,
};

const inspectionTask = {
  id: inspectionTaskId,
  content_item_id: null,
  title: "账号体检",
  type: "account_diagnosis",
  status: "completed",
  brief: {
    goal: "一键账号体检",
    project_id: null,
    project_name: null,
    account_group_id: null,
    account_group_name: null,
    platforms: ["douyin"],
    account_ids: [account.id],
    cycle: "本轮",
    budget: null,
    content_goal: "识别账号问题并给出建议",
    risk_constraints: [],
    expected_outputs: ["account_inspection_report"],
    confirmation_actions: [],
  },
  plan: {
    id: 8201,
    summary: "读取数据后完成账号体检",
    steps: [
      {
        id: "data",
        agent_code: "06-operator",
        agent_name: expertInvocation.agent_name,
        phase: "数据分析",
        intent: "读取账号数据并识别异常",
        status: "done",
        depends_on: [],
        expected_output: "账号健康度结论",
        risk_level: "low",
        execution_kind: "account_diagnosis",
        human_gate: false,
        tool_codes: ["account.data_context"],
      },
    ],
    quality_gates: [],
    estimated_cost: 0.01,
    requires_human_confirmation: false,
  },
  progress: 100,
  current_focus: "账号体检已完成",
  risk_count: 0,
  runtime_mode: "skill",
  thread_id: "brain-task-8101",
  context_closed_at: null,
  created_at: createdAt,
  updated_at: createdAt,
};

const inspectionRuntime = {
  task: inspectionTask,
  thread_id: inspectionTask.thread_id,
  status: "completed",
  timeline: [],
  invocations: [expertInvocation],
  tool_calls: [],
  acceptances: [],
  pending_permissions: [],
  next_actions: [],
};

const threadBase = {
  id: 1001,
  org_id: 1,
  created_by_id: user.id,
  client_id: null,
  project_id: null,
  account_id: account.id,
  title: "Main Agent V2 test thread",
  turns: [],
  created_at: createdAt,
  updated_at: createdAt,
};

const artifact = {
  id: 7001,
  account_id: account.id,
  thread_id: threadBase.id,
  turn_id: sourceTurnId,
  run_id: 8001,
  skill_run_id: 9001,
  task_id: null,
  artifact_type: "account_inspection_report",
  title: "账号体检报告",
  version: 1,
  status: "ready_for_review" as const,
  summary: "近七天数据稳定，建议保持发布节奏并测试新的内容角度。",
  sections: [
    {
      key: "summary",
      title: "体检结论",
      content: "账号整体健康，互动率高于上一周期。",
    },
    {
      key: "highlights",
      title: "优化建议",
      content: "保持每周三次发布，并增加一个新选题测试。",
    },
  ],
  evidence_refs: [],
  quality: { score: 0.92, passed: true, issues: [] },
  created_at: createdAt,
};

test("main agent v2 preserves the completed Artifact on its source Turn after later chat", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await installBrowserState(page);
  const unexpectedApiCalls = await mockApi(page);
  await loginAsAdmin(page);

  const accountTrigger = page.locator(".tz-account-trigger");
  await expect(accountTrigger).toBeVisible();
  await accountTrigger.click();
  await page.locator(".tz-account-panel button", { hasText: account.nickname }).click();
  await expect(page.locator(".dy-brain-context-strip")).toContainText(account.nickname);

  await page.locator(".dy-brain-capability-trigger").click();
  await page.getByRole("menuitem", { name: new RegExp(inspectionSkill.name) }).click();

  const sourceTurn = page.locator(`[data-turn-id="${sourceTurnId}"]`);
  await expect(sourceTurn).toBeVisible();
  await expect(sourceTurn).toContainText(inspectionSkill.name);
  await expect(sourceTurn.locator(".tz-artifact-card")).toContainText(artifact.title);

  await page.locator(".tz-brain-toolbar-actions button").nth(1).click();
  const detailsDrawer = page.locator(".tz-brain-details-drawer");
  await expect(detailsDrawer).toContainText("专家接力");
  await expect(detailsDrawer).toContainText(expertInvocation.agent_name);
  await detailsDrawer.locator(".ant-drawer-close").click();

  await sourceTurn.locator(".tz-artifact-card__actions button").first().click();
  await expect(sourceTurn.locator(".tz-artifact-card__sections--remaining"))
    .toContainText("增加一个新选题测试");

  await page.locator(".tz-brain-mode-switch button").nth(1).click();
  const artifactRow = page.locator(".tz-artifact-center__row", { hasText: artifact.title }).first();
  await expect(artifactRow).toBeVisible();
  await artifactRow.getByRole("button").click();

  const artifactDetail = page.locator("section[aria-label='Artifact detail']");
  await expect(artifactDetail.locator(".tz-artifact-card")).toContainText(artifact.title);
  await artifactDetail.locator("button").last().click();

  await expect(sourceTurn).toBeVisible();
  await expect(sourceTurn).toBeFocused();

  const laterGreeting = "你好，继续普通对话";
  await page.locator(".dy-brain-input textarea").fill(laterGreeting);
  await page.locator(".dy-brain-send-button").click();

  const followUpTurn = page.locator(`[data-turn-id="${followUpTurnId}"]`);
  await expect(followUpTurn).toBeVisible();
  await expect(followUpTurn).toContainText(laterGreeting);
  await expect(sourceTurn.locator(".tz-artifact-card")).toContainText(artifact.title);
  await expect(followUpTurn.locator(".tz-artifact-card")).toHaveCount(0);

  expect(unexpectedApiCalls).toEqual([]);
  expect(
    consoleErrors.filter((message) => !message.includes("There may be circular references")),
  ).toEqual([]);
});

async function loginAsAdmin(page: Page) {
  await page.goto("/login");
  await page.locator('input[autocomplete="email"]').fill(user.email);
  await page.locator('input[autocomplete="current-password"]').fill("admin12345");
  await page.locator('form button[type="submit"]').click();
}

async function installBrowserState(page: Page) {
  await page.addInitScript(({ taskId }) => {
    localStorage.removeItem("tongzhouxing_current_workspace");
    localStorage.removeItem("tongzhouxing_brain_active_conversation_threads");
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: { 1: taskId } }),
    );

    class MockWebSocket extends EventTarget {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;

      readyState = MockWebSocket.CONNECTING;
      onopen: ((event: Event) => void) | null = null;
      onclose: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor() {
        super();
        window.setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          const event = new Event("open");
          this.onopen?.(event);
          this.dispatchEvent(event);
        }, 0);
      }

      send() {}

      close() {
        this.readyState = MockWebSocket.CLOSED;
        const event = new Event("close");
        this.onclose?.(event);
        this.dispatchEvent(event);
      }
    }

    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  }, { taskId: inspectionTaskId });
}

async function mockApi(page: Page) {
  let sourceTurnSubmitted = false;
  const unexpectedApiCalls: string[] = [];
  const turns: Array<Record<string, unknown>> = [];
  const threadState = { ...threadBase, turns };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "POST" && path === "/auth/login") {
      return json(route, { access_token: "test-token", token_type: "bearer", user });
    }
    if (method === "GET" && path === "/auth/me") return json(route, user);
    if (method === "GET" && path === "/projects") return json(route, []);
    if (method === "GET" && path === "/account-groups") return json(route, []);
    if (method === "GET" && path === "/accounts") return json(route, [account]);
    if (method === "GET" && path === "/clients") return json(route, []);
    if (method === "GET" && path === "/skills") return json(route, { data: [inspectionSkill] });
    if (method === "GET" && path === "/brain/tasks") return json(route, [inspectionTask]);
    if (method === "GET" && path === `/brain/tasks/${inspectionTaskId}/runtime`) {
      return json(route, inspectionRuntime);
    }
    if (method === "GET" && path === "/notifications") return json(route, []);
    if (method === "GET" && path === "/notifications/unread-count") {
      return json(route, { count: 0 });
    }
    if (method === "GET" && path === "/workspace-context") {
      return json(route, {
        clients: [],
        selected_client: null,
        projects: [],
        selected_project: null,
        accounts: [account],
      });
    }
    if (method === "POST" && path === "/brain/conversations") {
      turns.splice(0);
      sourceTurnSubmitted = false;
      return json(route, threadState);
    }
    if (method === "GET" && /^\/brain\/conversations\/\d+$/.test(path)) {
      return json(route, threadState);
    }
    if (method === "POST" && /^\/brain\/conversations\/\d+\/turns$/.test(path)) {
      const body = (await request.postDataJSON()) as {
        client_message_id: string;
        message: string;
        requested_skill_code?: string;
      };
      const isSourceTurn = !sourceTurnSubmitted;
      const turnId = isSourceTurn ? sourceTurnId : followUpTurnId;
      const projections = isSourceTurn
        ? sourceTurnProjections()
        : [];
      const turn = {
        id: turnId,
        thread_id: threadBase.id,
        org_id: 1,
        created_by_id: user.id,
        client_message_id: body.client_message_id,
        user_input: body.message,
        assistant_response: isSourceTurn
          ? "账号体检已经完成，正式报告已生成。"
          : "你好，我们继续聊。",
        intent: {
          mode: isSourceTurn ? "skill" : "answer",
          status: "completed",
          requested_skill_code: body.requested_skill_code ?? null,
        },
        projections,
        created_at: createdAt,
        updated_at: createdAt,
      };
      turns.push(turn);
      sourceTurnSubmitted = true;
      return json(route, {
        turn,
        run: {
          id: turnId + 5000,
          org_id: 1,
          requested_by_id: user.id,
          task_id: null,
          thread_id: threadBase.id,
          turn_id: turnId,
          client_message_id: body.client_message_id,
          status: "completed",
          phase: "execution",
          created_at: createdAt,
          updated_at: createdAt,
        },
        task_id: null,
        projections,
      });
    }
    if (method === "GET" && path === "/artifacts") {
      return json(route, {
        data: [artifact],
        pagination: { page: 1, page_size: 20, total: 1, pages: 1 },
      });
    }
    if (method === "GET" && path === `/artifacts/${artifact.id}`) {
      return json(route, artifact);
    }

    unexpectedApiCalls.push(`${method} ${path}`);
    return json(route, {});
  });

  return unexpectedApiCalls;
}

function sourceTurnProjections() {
  return [
    {
      type: "progress",
      turn_id: sourceTurnId,
      skill_run_id: artifact.skill_run_id,
      stages: [{ code: "data", name: "数据分析", status: "completed" }],
    },
    {
      type: "expert",
      turn_id: sourceTurnId,
      invocation: expertInvocation,
    },
    {
      type: "artifact",
      turn_id: sourceTurnId,
      artifact_id: artifact.id,
      artifact_type: artifact.artifact_type,
      skill_run_id: artifact.skill_run_id,
      account_id: account.id,
      report: { summary: artifact.summary },
    },
  ];
}

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}
