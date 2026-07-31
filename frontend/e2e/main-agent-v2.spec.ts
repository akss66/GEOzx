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
const publicSkills = [
  inspectionSkill,
  {
    ...inspectionSkill,
    code: "performance_review",
    name: "运营复盘",
    description: "复盘账号表现并生成正式复盘成果",
  },
  {
    ...inspectionSkill,
    code: "topic_planning",
    name: "选题策划",
    description: "生成可执行选题成果",
  },
  {
    ...inspectionSkill,
    code: "publishing_preparation",
    name: "发布准备",
    description: "生成发布准备包但不真实发布",
  },
];

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

  await sourceTurn.getByText("技术日志").click();
  const technicalDetails = sourceTurn.locator(".tz-conversation-turn__technical");
  await expect(technicalDetails).toContainText(expertInvocation.agent_code);
  await expect(technicalDetails).not.toContainText(artifact.summary);

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

test("main agent v2 remains usable with runtime details at responsive widths", async ({
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

  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: account.nickname }).click();
  await page.locator(".dy-brain-capability-trigger").click();
  await page.getByRole("menuitem", { name: new RegExp(inspectionSkill.name) }).click();

  const sourceTurn = page.locator(`[data-turn-id="${sourceTurnId}"]`);
  await expect(sourceTurn.locator(".tz-artifact-card")).toContainText(artifact.title);

  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const technicalDetails = sourceTurn.locator(".tz-conversation-turn__technical");
    if ((await technicalDetails.getAttribute("open")) == null) {
      await sourceTurn.getByText("技术日志").click();
    }
    await expect(technicalDetails).toContainText(expertInvocation.agent_code);
    await expect(technicalDetails).toBeInViewport();

    await expect(page.locator(".dy-brain-input textarea")).toBeVisible();
  }

  expect(unexpectedApiCalls).toEqual([]);
  expect(
    consoleErrors.filter((message) => !message.includes("There may be circular references")),
  ).toEqual([]);
});

test("empty main agent workspace keeps actions compact and content above the fold", async ({
  page,
}) => {
  await installBrowserState(page);
  await page.addInitScript(() => {
    localStorage.setItem(
      "tongzhouxing_brain_active_tasks",
      JSON.stringify({ version: 1, accounts: {} }),
    );
  });
  await mockApi(page);
  await loginAsAdmin(page);

  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: account.nickname }).click();
  await expect(page.getByRole("heading", { name: "今天，想推进什么？" })).toBeVisible();

  const actionsBox = await page.locator(".tz-brain-empty-actions").boundingBox();
  const stageBox = await page.locator(".tz-brain-stage").boundingBox();

  expect(actionsBox).not.toBeNull();
  expect(stageBox).not.toBeNull();
  expect(actionsBox!.height).toBeLessThanOrEqual(64);
  expect(stageBox!.y).toBeLessThanOrEqual(130);
  await expect(page.locator(".dy-brain-input textarea")).toBeVisible();
});

test("Turn UI renders ten mocked presentation states as a frontend-only contract", async ({
  page,
}) => {
  await installBrowserState(page);
  const unexpectedApiCalls = await mockApi(page);
  await loginAsAdmin(page);
  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: account.nickname }).click();

  const cases = [
    ["你好", "answer", "completed"],
    ["你能做什么", "answer", "completed"],
    ["只查询账号数据，不生成策略", "query", "completed"],
    ["一键账号体检", "skill", "completed"],
    ["performance_review", "skill", "completed"],
    ["topic_planning", "skill", "completed"],
    ["publishing_preparation", "skill", "completed"],
    ["现在真实发布", "action", "waiting_permission"],
    ["强制专家失败", "skill", "failed"],
    ["继续刚才的账号，不要发布", "answer", "completed"],
  ] as const;

  for (const [prompt, mode, status] of cases) {
    await page.locator(".dy-brain-input textarea").fill(prompt);
    await page.locator(".dy-brain-send-button").click();
    await expect.poll(async () =>
      page.locator(".tz-conversation-turn").count()
    ).toBe(cases.findIndex(([item]) => item === prompt) + 1);
    const turn = page.locator(".tz-conversation-turn").last();
    await expect(turn).toContainText(prompt);
    await expect(turn).toHaveAttribute("data-turn-status", status);
    await turn.getByText("技术日志").click();
    await expect(turn).toContainText(`路由：${mode}`);
    await expect(turn).not.toContainText(/provider body|idempotency|Traceback|sk-secret/i);
  }

  const approvalTurn = page.locator(".tz-conversation-turn").nth(7);
  await expect(approvalTurn).toContainText("任务已暂停，等待你确认");
  const failureTurn = page.locator(".tz-conversation-turn").nth(8);
  await expect(failureTurn).toContainText("执行失败，未生成伪造成果");
  expect(unexpectedApiCalls).toEqual([]);
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
    if (method === "GET" && path === "/skills") return json(route, { data: publicSkills });
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
      const isSourceTurn = !sourceTurnSubmitted && body.requested_skill_code === inspectionSkill.code;
      const turnId = sourceTurnId + turns.length;
      const capability = mockedUiContractTurn(body.message, turnId);
      const projections = isSourceTurn ? sourceTurnProjections() : capability.projections;
      const turn = {
        id: turnId,
        thread_id: threadBase.id,
        org_id: 1,
        created_by_id: user.id,
        client_message_id: body.client_message_id,
        user_input: body.message,
        assistant_response: isSourceTurn
          ? "账号体检已经完成，正式报告已生成。"
          : capability.response,
        intent: {
          mode: isSourceTurn ? "skill" : capability.mode,
          route_source: body.requested_skill_code ? "explicit" : "deterministic",
          skill_code: body.requested_skill_code ?? capability.skillCode,
        },
        status: isSourceTurn ? "completed" : capability.status,
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
          status: isSourceTurn ? "completed" : capability.status,
          phase: isSourceTurn ? "completed" : capability.status,
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

function mockedUiContractTurn(message: string, turnId: number) {
  const baseSummary = (skillCode: string | null, status: string) => ({
    type: "execution_summary",
    turn_id: turnId,
    run_id: turnId + 5000,
    mode: skillCode ? "skill" : "answer",
    route_source: "deterministic",
    skill_code: skillCode,
    skill_version: skillCode ? 1 : null,
    skill_run_id: skillCode ? turnId + 6000 : null,
    status,
    quality_score: skillCode ? 0.91 : null,
    experts: skillCode
      ? [{
          id: turnId + 7000,
          agent_code: "06-operator",
          agent_name: "运营专家",
          status,
          attempt: 0,
          duration_ms: 28,
        }]
      : [],
    tools: [],
    error_code: null,
    recovery_action: null,
    artifact_ids: [],
    evidence_ids: [],
  });
  if (message.includes("只查询")) {
    return {
      mode: "query",
      status: "completed",
      skillCode: "account_data_query",
      response: "已读取当前账号数据，未生成策略。",
      projections: [{
        type: "account_data",
        turn_id: turnId,
        account_id: account.id,
        skill_code: "account_data_query",
        skill_run_id: turnId + 6000,
        data: {},
      }],
    };
  }
  if (message === "一键账号体检") {
    return {
      mode: "skill",
      status: "completed",
      skillCode: "account_inspection",
      response: "账号体检完成。",
      projections: [baseSummary("account_inspection", "completed")],
    };
  }
  if (["performance_review", "topic_planning", "publishing_preparation"].includes(message)) {
    return {
      mode: "skill",
      status: "completed",
      skillCode: message,
      response: `${message} 成果已生成，未执行真实发布。`,
      projections: [baseSummary(message, "completed")],
    };
  }
  if (message === "现在真实发布") {
    return {
      mode: "action",
      status: "waiting_permission",
      skillCode: null,
      response: "任务已暂停，等待你确认；审批前不会真实发布。",
      projections: [],
    };
  }
  if (message === "强制专家失败") {
    return {
      mode: "skill",
      status: "failed",
      skillCode: "account_inspection",
      response: "执行失败，未生成伪造成果。",
      projections: [{
        type: "execution_blocked",
        turn_id: turnId,
        skill_run_id: turnId + 6000,
        code: "EXPERT_EXECUTION_FAILED",
        recovery_action: "稍后重试。",
      }],
    };
  }
  return {
    mode: "answer",
    status: "completed",
    skillCode: null,
    response: message.includes("继续刚才")
      ? `继续使用当前账号 ${account.nickname}；本轮不会发布。`
      : "你好，我是运营大脑。",
    projections: [],
  };
}

function sourceTurnProjections() {
  return [
    {
      type: "execution_summary",
      turn_id: sourceTurnId,
      run_id: 8001,
      mode: "skill",
      route_source: "explicit",
      skill_code: inspectionSkill.code,
      skill_version: inspectionSkill.version,
      skill_run_id: artifact.skill_run_id,
      status: "completed",
      quality_score: 0.92,
      experts: [{
        id: expertInvocation.id,
        agent_code: expertInvocation.agent_code,
        agent_name: expertInvocation.agent_name,
        status: expertInvocation.status,
        attempt: 0,
        duration_ms: 25,
      }],
      tools: [],
      error_code: null,
      recovery_action: null,
      artifact_ids: [artifact.id],
      evidence_ids: [],
    },
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
