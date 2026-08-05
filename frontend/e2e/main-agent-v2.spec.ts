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

const secondaryAccount = {
  ...account,
  id: 2,
  nickname: "次账号",
  external_account_id: "douyin-2",
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
  presentation: {
    type_label: "账号诊断",
    completion_label: "已完成当前账号运营诊断",
    status_label: "待审核",
    detail_action_label: "查看账号诊断",
  },
  next_actions: [],
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

function accountAnalysisArtifact(turnId: number) {
  return {
    ...artifact,
    id: 700000 + turnId,
    turn_id: turnId,
    run_id: 800000 + turnId,
    skill_run_id: 900000 + turnId,
    artifact_type: "account_analysis_answer",
    presentation: {
      type_label: "账号数据分析",
      completion_label: "已根据当前账号数据回答你的问题",
      status_label: "已完成",
      detail_action_label: "查看完整分析",
    },
    title: "账号数据分析",
    status: "accepted" as const,
    summary: "近 30 天播放量增长，但互动效率下降。",
    sections: [
      { key: "conclusion", title: "核心结论", content: "近 30 天播放量增长 24%，但互动率下降 0.8 个百分点。" },
      {
        key: "key_facts",
        title: "关键事实",
        content: [{
          metric_code: "play",
          label: "播放量",
          unit: "次",
          current_value: 12400,
          previous_value: 10000,
          absolute_change: 2400,
          relative_change: 0.24,
          direction: "up",
          current_period: { days: 30, start: "2026-07-01", end: "2026-07-30" },
          comparison_period: { days: 30, start: "2026-06-01", end: "2026-06-30" },
          sample_count: 14,
          evidence_hashes: ["sha256:must-not-appear"],
        }],
      },
      { key: "interpretation", title: "数据解读", content: ["流量规模扩大，但互动承接变弱。"] },
      {
        key: "recommendations",
        title: "下一步建议",
        content: [{
          action: "连续 7 天测试强互动提问式结尾",
          rationale: "当前互动率较上一周期下降",
          validation_metric: "互动率",
          observation_days: 7,
        }],
      },
      { key: "data_limits", title: "数据限制", content: ["当前没有成交数据，不能判断商业转化。"] },
      { key: "next_action", title: "下一步", content: "先执行 7 天互动率提升实验。" },
      { key: "participating_experts", title: "参与专家", content: ["运营专家"] },
      { key: "critic", title: "质量审核", content: { passed: true, score: 94 } },
    ],
    evidence_refs: [
      { kind: "field_observation", id: 91, label: "content_hash=sha256:must-not-appear" },
      { kind: "field_observation", id: 92, label: "播放量 · 2026-07-01 至 2026-07-30" },
    ],
    evidence_summary: {
      total: 14,
      metric_count: 2,
      groups: [{
        kind: "field_observation",
        label: "账号数据字段",
        count: 14,
        metric_count: 2,
        period: "2026-07-01 至 2026-07-30",
      }],
    },
    quality: { score: 94, passed: true, issues: [] },
  };
}

test("account analysis finishes in place as a grounded readable answer", async ({ page }) => {
  await installBrowserState(page);
  const unexpectedApiCalls = await mockApi(page);
  await loginAsAdmin(page);

  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: account.nickname }).click();

  const prompt = "最近30天账号表现怎么样？";
  await page.getByLabel("运营大脑消息").fill(prompt);
  await page.getByRole("button", { name: "发送给运营大脑" }).click();

  const turn = page.getByTestId("work-turn").filter({ hasText: prompt });
  await expect(turn).toHaveCount(1);
  await expect(turn).toHaveAttribute("data-turn-status", "completed");
  await expect(turn.getByRole("heading", { name: "账号数据分析" })).toBeVisible();
  await expect(turn).toContainText("近 30 天播放量增长 24%");
  await expect(turn).toContainText("下一步建议");
  await expect(turn).not.toContainText(/采用成果|正式成果|sha256|content_hash/);

  await turn.getByRole("button", { name: "查看分析依据" }).click();
  await expect(turn).toContainText("已核验 2 类指标、14 条数据记录");
  await turn.getByRole("button", { name: "查看已完成过程" }).click();
  await expect(turn.getByRole("region", { name: "调用专家摘要" })).toContainText("运营专家");
  await expect(turn).not.toContainText(/sha256|content_hash/);

  const turnId = await turn.getAttribute("data-turn-id");
  await page.reload();
  await expect(page.locator(`[data-turn-id="${turnId}"]`)).toHaveCount(1);
  await expect(page.locator(`[data-turn-id="${turnId}"]`).getByRole("heading", {
    name: "账号数据分析",
  })).toBeVisible();
  expect(unexpectedApiCalls).toEqual([]);
});

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
  await expect(sourceTurn.locator(".tz-artifact-card")).toContainText(artifact.summary);

  await sourceTurn.getByRole("button", { name: /查看(已完成过程|分析过程)/ }).click();
  await sourceTurn.getByRole("button", { name: "技术详情" }).click();
  const technicalDetails = sourceTurn.locator(".tz-work-turn__technical-log");
  await expect(technicalDetails).toContainText(expertInvocation.agent_code);
  await expect(technicalDetails).not.toContainText(artifact.summary);

  await sourceTurn.locator(".tz-artifact-card__actions button").first().click();
  await expect(sourceTurn.locator(".tz-artifact-card__sections--remaining"))
    .toContainText("增加一个新选题测试");

  await page.getByRole("tab", { name: "方案与内容" }).click();
  const artifactRow = page.locator(".tz-artifact-center__row", { hasText: "账号诊断" }).first();
  await expect(artifactRow).toBeVisible();
  await artifactRow.getByRole("button").click();

  const artifactDetail = page.getByRole("region", { name: "方案与内容详情" });
  await expect(artifactDetail.locator(".tz-artifact-card")).toContainText(artifact.summary);
  await artifactDetail.getByRole("button", { name: "返回来源对话" }).click();

  await expect(sourceTurn).toBeVisible();
  await expect(sourceTurn).toBeFocused();

  const laterGreeting = "你好，继续普通对话";
  await page.getByLabel("运营大脑消息").fill(laterGreeting);
  await page.getByRole("button", { name: "发送给运营大脑" }).click();

  const followUpTurn = page.locator(`[data-turn-id="${followUpTurnId}"]`);
  await expect(followUpTurn).toBeVisible();
  await expect(followUpTurn).toContainText(laterGreeting);
  await expect(sourceTurn.locator(".tz-artifact-card")).toContainText(artifact.summary);
  await expect(followUpTurn.locator(".tz-artifact-card")).toHaveCount(0);

  expect(unexpectedApiCalls).toEqual([]);
  expect(
    consoleErrors.filter((message) => !message.includes("There may be circular references")),
  ).toEqual([]);
});

test("main agent v4 restores and isolates one streamed WorkTurn", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await installBrowserState(page);
  const scenario = await mockCodexInteractionApi(page);
  await loginAsAdmin(page);

  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: account.nickname }).click();

  const prompt = "分析这个账号最近30天的数据";
  const composer = page.getByLabel("运营大脑消息");
  await composer.fill(prompt);
  await page.getByRole("button", { name: "发送给运营大脑" }).click();

  const target = page.getByTestId("work-turn").filter({ hasText: prompt });
  await expect(target).toHaveCount(1);
  await expect(target.locator(".tz-work-turn__user")).toHaveText(prompt);
  await expect(target.locator(
    '.tz-work-turn__operator[data-thinking="true"] .tz-work-turn__avatar',
  )).toHaveCount(1);
  await expect(target.locator(".tz-work-turn__activity")).toHaveCount(1);

  scenario.releaseInitialTurn();
  await expect(target).toHaveAttribute("data-turn-id", String(scenario.targetTurnId));
  await expect(composer).toBeEnabled();
  await expect(page.getByRole("button", { name: "停止当前任务" })).toBeEnabled();
  await expect.poll(() => page.evaluate(() => (
    window as typeof window & { __turnEventStreamRequests?: unknown[] }
  ).__turnEventStreamRequests?.length ?? 0)).toBeGreaterThan(0);

  const firstPartial = "已读取近 30 天数据";
  scenario.setPartialAssistant(firstPartial);
  await emitRuntimeFrame(
    page,
    scenario.threadId,
    scenario.targetTurnId,
    scenario.targetClientMessageId,
    "brain.runtime.message_start",
    0,
  );
  await emitRuntimeFrame(
    page,
    scenario.threadId,
    scenario.targetTurnId,
    scenario.targetClientMessageId,
    "brain.runtime.message_delta",
    1,
    firstPartial,
  );
  const response = target.locator(".tz-work-turn__response");
  await expect(response).toHaveText(firstPartial);
  await response.evaluate((node) => node.setAttribute("data-same-node-probe", "true"));

  const supplement = "补充：不要生成长期策略";
  await composer.fill(supplement);
  await page.getByRole("button", { name: "补充或排队" }).click();
  await expect(page.getByTestId("work-turn").filter({ hasText: supplement })).toHaveCount(1);
  await emitDurableTurnEvent(page, {
    id: 1,
    sequence: 1,
    type: "turn.steered",
    payload: {
      message: "已收到补充要求。",
      metadata: { category: "steering", label: "supplement", source_id: scenario.steeringTurnId },
    },
    thread_id: scenario.threadId,
    turn_id: scenario.targetTurnId,
    run_id: 5101,
    skill_run_id: null,
    created_at: createdAt,
  });
  scenario.recordSteeringEvent();
  await expect(target.getByRole("note", { name: "任务调整" })).toContainText("已补充要求");

  const secondPartial = "，正在核对异常。";
  scenario.setPartialAssistant(`${firstPartial}${secondPartial}`);
  await emitRuntimeFrame(
    page,
    scenario.threadId,
    scenario.targetTurnId,
    scenario.targetClientMessageId,
    "brain.runtime.message_delta",
    2,
    secondPartial,
  );
  await expect(response).toHaveText(`${firstPartial}${secondPartial}`);
  await expect(response).toHaveAttribute("data-same-node-probe", "true");
  await expect(target.locator(".tz-work-turn__response")).toHaveCount(1);

  const restoredTurnId = await target.getAttribute("data-turn-id");
  await page.reload();
  const restored = page.locator(`[data-turn-id="${restoredTurnId}"]`);
  await expect(restored).toHaveCount(1);
  await expect(restored.locator(".tz-work-turn__response"))
    .toHaveText(`${firstPartial}${secondPartial}`);
  await expect(restored.getByRole("note", { name: "任务调整" })).toContainText("已补充要求");
  await expect.poll(() => page.evaluate(() => (
    window as typeof window & {
      __turnEventStreamRequests?: Array<{ threadId: number; afterId: number }>;
    }
  ).__turnEventStreamRequests ?? [])).toContainEqual({
    threadId: scenario.threadId,
    afterId: 1,
  });

  await page.getByRole("button", { name: "停止当前任务" }).click();
  await expect(restored).toHaveAttribute("data-turn-status", "stopped");
  await expect(restored.getByRole("region", { name: "执行步骤" })).toContainText("读取账号数据");
  await expect(restored.locator('[data-step-state="done"]')).toContainText("读取账号数据");
  await expect(restored).not.toContainText(/手动刷新|刷新页面/);

  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(restored.locator(".tz-work-turn__user")).toHaveCount(1);
    await expect(page.getByLabel("运营大脑消息")).toBeVisible();
    await expect(restored.getByRole("button", { name: "查看分析过程" })).toBeVisible();
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await restored.getByRole("button", { name: "查看分析过程" }).click();
  await expect(restored.getByRole("button", { name: "技术详情" })).toBeVisible();
  await restored.getByRole("button", { name: "技术详情" }).click();
  await expect(restored).toContainText(`消息编号：${scenario.targetTurnId}`);

  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: secondaryAccount.nickname }).click();
  await expect(page.getByRole("heading", { name: "今天，想推进什么？" })).toBeVisible();
  await expect(page.getByText(prompt)).toHaveCount(0);
  await expect(page.getByText(supplement)).toHaveCount(0);
  await expect(page.locator(`[data-turn-id="${scenario.targetTurnId}"]`)).toHaveCount(0);
  await expect.poll(() => page.evaluate((threadId) => (
    window as typeof window & { __abortedTurnStreams?: number[] }
  ).__abortedTurnStreams?.includes(threadId) ?? false, scenario.threadId)).toBe(true);

  expect(scenario.unexpectedApiCalls).toEqual([]);
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
  await expect(sourceTurn.locator(".tz-artifact-card")).toContainText(artifact.summary);

  for (const width of [320, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const processButton = sourceTurn.getByRole("button", { name: /查看(已完成过程|分析过程)/ });
    if ((await processButton.getAttribute("aria-expanded")) !== "true") {
      await processButton.click();
    }
    const technicalButton = sourceTurn.getByRole("button", { name: "技术详情" });
    if ((await technicalButton.getAttribute("aria-expanded")) !== "true") {
      await technicalButton.click();
    }
    const technicalDetails = sourceTurn.locator(".tz-work-turn__technical-log");
    await expect(technicalDetails).toContainText(expertInvocation.agent_code);
    await expect(technicalDetails).toBeInViewport();

    await expect(page.getByLabel("运营大脑消息")).toBeVisible();
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
  await expect(page.getByLabel("运营大脑消息")).toBeVisible();
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
    await page.getByLabel("运营大脑消息").fill(prompt);
    await page.getByRole("button", { name: "发送给运营大脑" }).click();
    await expect(page.getByTestId("work-turn")).toHaveCount(
      cases.findIndex(([item]) => item === prompt) + 1,
    );
    const turn = page.getByTestId("work-turn").last();
    await expect(turn).toContainText(prompt);
    await expect(turn).toHaveAttribute("data-turn-status", status);
    await turn.getByRole("button", { name: /查看(已完成过程|分析过程)/ }).click();
    await turn.getByRole("button", { name: "技术详情" }).click();
    await expect(turn).toContainText(`路由：${mode}`);
    await expect(turn).not.toContainText(/provider body|idempotency|Traceback|sk-secret/i);
  }

  const approvalTurn = page.getByTestId("work-turn").nth(7);
  await expect(approvalTurn).toContainText("任务已暂停，等待你确认");
  const failureTurn = page.getByTestId("work-turn").nth(8);
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
    if (sessionStorage.getItem("main-agent-e2e-state-ready") !== "true") {
      localStorage.removeItem("tongzhouxing_current_workspace");
      localStorage.removeItem("tongzhouxing_brain_active_conversation_threads");
      localStorage.setItem(
        "tongzhouxing_brain_active_tasks",
        JSON.stringify({ version: 1, accounts: { 1: taskId } }),
      );
      sessionStorage.setItem("main-agent-e2e-state-ready", "true");
    }

    type BrowserSocket = MockWebSocket & { receive: (payload: unknown) => void };
    const socketWindow = window as typeof window & {
      __mockSockets?: BrowserSocket[];
      __emitRuntimeEvent?: (payload: unknown) => void;
      __emitTurnEvent?: (payload: {
        id: number;
        type: string;
        thread_id: number;
      }) => void;
      __turnEventStreamRequests?: Array<{ threadId: number; afterId: number }>;
      __abortedTurnStreams?: number[];
    };
    socketWindow.__mockSockets = [];

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
      readonly url: string;

      constructor(url: string | URL) {
        super();
        this.url = String(url);
        socketWindow.__mockSockets?.push(this as BrowserSocket);
        window.setTimeout(() => {
          this.readyState = MockWebSocket.OPEN;
          const event = new Event("open");
          this.onopen?.(event);
          this.dispatchEvent(event);
        }, 0);
      }

      send(data: string) {
        try {
          const payload = JSON.parse(data) as { type?: string; thread_id?: number; account_id?: number };
          if (payload.type !== "authenticate") return;
          window.queueMicrotask(() => this.receive({
            type: "authenticated",
            ...(payload.thread_id != null ? { thread_id: payload.thread_id } : {}),
            ...(payload.account_id != null ? { account_id: payload.account_id } : {}),
          }));
        } catch {
          // Tests deliberately ignore non-JSON socket writes.
        }
      }

      receive(payload: unknown) {
        const event = new MessageEvent("message", { data: JSON.stringify(payload) });
        this.onmessage?.(event);
        this.dispatchEvent(event);
      }

      close() {
        this.readyState = MockWebSocket.CLOSED;
        const event = new Event("close");
        this.onclose?.(event);
        this.dispatchEvent(event);
      }
    }

    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    socketWindow.__emitRuntimeEvent = (payload: unknown) => {
      const socket = socketWindow.__mockSockets
        ?.filter((candidate) => candidate.url.endsWith("/ws/conversation-runtime"))
        .at(-1);
      if (!socket) throw new Error("No conversation runtime socket is connected");
      socket.receive(payload);
    };

    const nativeFetch = window.fetch.bind(window);
    const turnStreams = new Map<number, ReadableStreamDefaultController<Uint8Array>>();
    const encoder = new TextEncoder();
    socketWindow.__turnEventStreamRequests = [];
    socketWindow.__abortedTurnStreams = [];
    socketWindow.__emitTurnEvent = (payload) => {
      const controller = turnStreams.get(payload.thread_id);
      if (!controller) throw new Error(`No durable Turn stream for Thread ${payload.thread_id}`);
      controller.enqueue(encoder.encode(
        `id: ${payload.id}\nevent: ${payload.type}\ndata: ${JSON.stringify(payload)}\n\n`,
      ));
    };
    window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      const requestUrl = new URL(
        typeof input === "string" || input instanceof URL ? String(input) : input.url,
        window.location.origin,
      );
      const match = requestUrl.pathname.match(/^\/api\/conversation-threads\/(\d+)\/event-stream$/);
      if (!match) return nativeFetch(input, init);
      const threadId = Number(match[1]);
      const afterId = Number(requestUrl.searchParams.get("after_id") ?? 0);
      socketWindow.__turnEventStreamRequests?.push({ threadId, afterId });
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          turnStreams.set(threadId, controller);
          init?.signal?.addEventListener("abort", () => {
            if (turnStreams.get(threadId) !== controller) return;
            turnStreams.delete(threadId);
            socketWindow.__abortedTurnStreams?.push(threadId);
            try {
              controller.close();
            } catch {
              // The reader may already have cancelled this stream.
            }
          }, { once: true });
        },
        cancel() {
          turnStreams.delete(threadId);
          socketWindow.__abortedTurnStreams?.push(threadId);
        },
      });
      return Promise.resolve(new Response(stream, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }));
    }) as typeof window.fetch;
  }, { taskId: inspectionTaskId });
}

async function mockApi(page: Page) {
  let sourceTurnSubmitted = false;
  const unexpectedApiCalls: string[] = [];
  const turns: Array<Record<string, unknown>> = [];
  const analysisArtifacts: Array<ReturnType<typeof accountAnalysisArtifact>> = [];
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
      if (body.message === "最近30天账号表现怎么样？") {
        analysisArtifacts.push(accountAnalysisArtifact(turnId));
      }
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
      sourceTurnSubmitted = sourceTurnSubmitted || isSourceTurn;
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
    if (method === "GET" && /^\/conversation-threads\/\d+\/events$/.test(path)) {
      return json(route, { data: [] });
    }
    if (method === "GET" && path === "/artifacts") {
      const data = [
        ...(sourceTurnSubmitted ? [artifact] : []),
        ...analysisArtifacts,
      ];
      return json(route, {
        data,
        pagination: { page: 1, page_size: 20, total: data.length, pages: data.length ? 1 : 0 },
      });
    }
    if (method === "GET" && path === `/artifacts/${artifact.id}`) {
      return json(route, artifact);
    }
    const analysisArtifactMatch = path.match(/^\/artifacts\/(\d+)$/);
    if (method === "GET" && analysisArtifactMatch) {
      const selected = analysisArtifacts.find((item) => item.id === Number(analysisArtifactMatch[1]));
      if (selected) return json(route, selected);
    }

    unexpectedApiCalls.push(`${method} ${path}`);
    return json(route, {});
  });

  return unexpectedApiCalls;
}

async function mockCodexInteractionApi(page: Page) {
  const threadId = 4001;
  const targetTurnId = 4101;
  const steeringTurnId = 4102;
  const unexpectedApiCalls: string[] = [];
  const durableEvents: Array<Record<string, unknown>> = [];
  let releaseInitialTurn!: () => void;
  const initialTurnGate = new Promise<void>((resolve) => {
    releaseInitialTurn = resolve;
  });
  let initialTurnReleased = false;
  let threadState: Record<string, unknown> | null = null;
  let targetTurn: Record<string, unknown> | null = null;
  let targetClientMessageId = "";

  const makeThread = (selectedAccount: typeof account) => ({
    id: selectedAccount.id === account.id ? threadId : threadId + selectedAccount.id,
    org_id: 1,
    created_by_id: user.id,
    client_id: null,
    project_id: null,
    account_id: selectedAccount.id,
    title: "Codex interaction recovery",
    turns: [] as Array<Record<string, unknown>>,
    created_at: createdAt,
    updated_at: createdAt,
  });
  const runFor = (turn: Record<string, unknown>, status: string) => ({
    id: Number(turn.id) + 5000,
    org_id: 1,
    requested_by_id: user.id,
    task_id: null,
    thread_id: turn.thread_id,
    turn_id: turn.id,
    client_message_id: turn.client_message_id,
    status,
    phase: status === "completed" ? "completed" : "execute",
    created_at: createdAt,
    updated_at: createdAt,
  });

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
    if (method === "GET" && path === "/accounts") return json(route, [account, secondaryAccount]);
    if (method === "GET" && path === "/clients") return json(route, []);
    if (method === "GET" && path === "/skills") return json(route, { data: publicSkills });
    if (method === "GET" && path === "/brain/tasks") return json(route, []);
    if (method === "GET" && path === "/notifications") return json(route, []);
    if (method === "GET" && path === "/notifications/unread-count") return json(route, { count: 0 });
    if (method === "GET" && /^\/accounts\/\d+\/pending-work$/.test(path)) {
      const selectedAccountId = Number(path.split("/")[2]);
      return json(route, { account_id: selectedAccountId, groups: [] });
    }
    if (method === "GET" && path === "/workspace-context") {
      return json(route, {
        clients: [],
        selected_client: null,
        projects: [],
        selected_project: null,
        accounts: [account, secondaryAccount],
      });
    }
    if (method === "GET" && path === "/brain/conversations") {
      const selectedAccountId = Number(url.searchParams.get("account_id"));
      const data = threadState && Number(threadState.account_id) === selectedAccountId
        ? [{
            id: threadState.id,
            account_id: threadState.account_id,
            title: threadState.title,
            turn_count: (threadState.turns as unknown[]).length,
            last_message: null,
            created_at: threadState.created_at,
            updated_at: threadState.updated_at,
          }]
        : [];
      return json(route, { data });
    }
    if (method === "POST" && path === "/brain/conversations") {
      const body = (await request.postDataJSON()) as { account_id: number };
      const selectedAccount = body.account_id === account.id ? account : secondaryAccount;
      threadState = makeThread(selectedAccount);
      return json(route, threadState);
    }
    if (method === "GET" && path === `/brain/conversations/${threadId}`) {
      return json(route, threadState);
    }
    if (method === "POST" && path === `/brain/conversations/${threadId}/turns`) {
      const body = (await request.postDataJSON()) as {
        client_message_id: string;
        message: string;
        target_turn_id: number | null;
      };
      if (body.target_turn_id === targetTurnId) {
        const steeringTurn = {
          id: steeringTurnId,
          thread_id: threadId,
          org_id: 1,
          created_by_id: user.id,
          client_message_id: body.client_message_id,
          user_input: body.message,
          assistant_response: "已补充到当前任务。",
          target_turn_id: targetTurnId,
          steering_mode: "supplement",
          intent: { mode: "answer", route_source: "deterministic", skill_code: null },
          status: "completed",
          projections: [],
          created_at: createdAt,
          updated_at: createdAt,
        };
        (threadState?.turns as Array<Record<string, unknown>>).push(steeringTurn);
        return json(route, {
          turn: steeringTurn,
          run: runFor(steeringTurn, "completed"),
          task_id: null,
          projections: [],
          steering_explanation: "已补充到当前任务的要求中。",
        });
      }
      targetTurn = {
        id: targetTurnId,
        thread_id: threadId,
        org_id: 1,
        created_by_id: user.id,
        client_message_id: body.client_message_id,
        user_input: body.message,
        assistant_response: null,
        intent: { mode: "skill", route_source: "deterministic", skill_code: "performance_review" },
        status: "running",
        turn_phase: "reading_data",
        projections: [{
          type: "progress",
          turn_id: targetTurnId,
          skill_run_id: 6101,
          stages: [
            { code: "read_data", name: "读取账号数据", status: "completed" },
            { code: "quality_review", name: "质量审核", status: "running" },
          ],
        }],
        created_at: createdAt,
        updated_at: createdAt,
      };
      targetClientMessageId = body.client_message_id;
      (threadState?.turns as Array<Record<string, unknown>>).push(targetTurn);
      await initialTurnGate;
      return json(route, {
        turn: targetTurn,
        run: runFor(targetTurn, "running"),
        task_id: null,
        projections: targetTurn.projections,
      });
    }
    if (method === "POST" && path === `/brain/conversations/${threadId}/turns/${targetTurnId}/stop`) {
      if (targetTurn) targetTurn.status = "stopped";
      return json(route, {
        thread_id: threadId,
        turn_id: targetTurnId,
        run_id: 9101,
        stopped: true,
        dispatch_deferred: false,
      });
    }
    if (method === "GET" && path === `/conversation-threads/${threadId}/events`) {
      const afterId = Number(url.searchParams.get("after_id") ?? 0);
      return json(route, { data: durableEvents.filter((event) => Number(event.id) > afterId) });
    }
    if (method === "GET" && path === "/artifacts") {
      return json(route, {
        data: [],
        pagination: { page: 1, page_size: 20, total: 0, pages: 0 },
      });
    }

    unexpectedApiCalls.push(`${method} ${path}`);
    return json(route, {});
  });

  return {
    threadId,
    targetTurnId,
    steeringTurnId,
    unexpectedApiCalls,
    get targetClientMessageId() {
      return targetClientMessageId;
    },
    releaseInitialTurn() {
      if (initialTurnReleased) return;
      initialTurnReleased = true;
      releaseInitialTurn();
    },
    setPartialAssistant(value: string) {
      if (targetTurn) targetTurn.assistant_response = value;
    },
    recordSteeringEvent() {
      durableEvents.push({
        id: 1,
        sequence: 1,
        type: "turn.steered",
        payload: {
          message: "已收到补充要求。",
          metadata: { category: "steering", label: "supplement", source_id: steeringTurnId },
        },
        thread_id: threadId,
        turn_id: targetTurnId,
        run_id: 5101,
        skill_run_id: null,
        created_at: createdAt,
      });
    },
  };
}

async function emitRuntimeFrame(
  page: Page,
  threadId: number,
  turnId: number,
  clientMessageId: string,
  type: string,
  streamSequence: number,
  delta?: string,
) {
  await expect.poll(() => page.evaluate(() => {
    const sockets = (window as typeof window & {
      __mockSockets?: Array<{ url: string; readyState: number }>;
    }).__mockSockets ?? [];
    return sockets.some((socket) =>
      socket.url.endsWith("/ws/conversation-runtime") && socket.readyState === WebSocket.OPEN
    );
  })).toBe(true);
  await page.evaluate(({ eventType, thread, turn, clientId, sequence, content }) => {
    const emit = (window as typeof window & {
      __emitRuntimeEvent?: (payload: unknown) => void;
    }).__emitRuntimeEvent;
    if (!emit) throw new Error("Runtime event emitter is unavailable");
    emit({
      type: eventType,
      payload: {
        thread_id: thread,
        turn_id: turn,
        client_message_id: clientId,
        message_id: `${turn}:00-decision:1`,
        agent_code: "00-decision",
        stream_seq: sequence,
        ...(content != null ? { delta: content } : {}),
      },
    });
  }, {
    eventType: type,
    thread: threadId,
    turn: turnId,
    clientId: clientMessageId,
    sequence: streamSequence,
    content: delta,
  });
}

async function emitDurableTurnEvent(page: Page, event: Record<string, unknown>) {
  await expect.poll(() => page.evaluate((threadId) => (
    window as typeof window & {
      __turnEventStreamRequests?: Array<{ threadId: number }>;
    }
  ).__turnEventStreamRequests?.some((request) => request.threadId === threadId) ?? false,
  Number(event.thread_id))).toBe(true);
  await page.evaluate((payload) => {
    const emit = (window as typeof window & {
      __emitTurnEvent?: (event: typeof payload & { id: number; type: string; thread_id: number }) => void;
    }).__emitTurnEvent;
    if (!emit) throw new Error("Durable Turn event emitter is unavailable");
    emit(payload as typeof payload & { id: number; type: string; thread_id: number });
  }, event);
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
  if (message === "最近30天账号表现怎么样？") {
    const analysis = accountAnalysisArtifact(turnId);
    return {
      mode: "skill",
      status: "completed",
      skillCode: "account_data_analysis",
      response: "账号数据分析已完成，已给出结论、依据和下一步建议。",
      projections: [
        {
          ...baseSummary("account_data_analysis", "completed"),
          tools: [{
            id: turnId + 8000,
            tool_code: "account.metrics_analysis",
            tool_name: "账号指标分析",
            status: "completed",
            retry_count: 0,
          }],
          artifact_ids: [analysis.id],
          evidence_ids: [91, 92],
        },
        {
          type: "progress",
          turn_id: turnId,
          skill_run_id: analysis.skill_run_id,
          stages: [
            { code: "metrics", name: "计算账号指标", status: "completed" },
            { code: "grounding", name: "核对结论与依据", status: "completed" },
          ],
        },
        {
          type: "artifact",
          turn_id: turnId,
          artifact_id: analysis.id,
          artifact_type: analysis.artifact_type,
          skill_run_id: analysis.skill_run_id,
          account_id: account.id,
          report: { summary: analysis.summary },
        },
      ],
    };
  }
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
