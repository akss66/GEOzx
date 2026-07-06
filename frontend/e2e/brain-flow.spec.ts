import { expect, test, type Page, type Route } from "@playwright/test";

const user = {
  id: 1,
  email: "admin@dyflow.local",
  display_name: "Admin",
  role: "admin",
  is_active: true,
};

const project = {
  id: 1,
  name: "新品冷启动",
  description: null,
  status: "active",
  created_at: "2026-07-01T00:00:00Z",
};

const accountGroup = {
  id: 1,
  name: "核心矩阵",
  dimension: "track",
  created_at: "2026-07-01T00:00:00Z",
};

const account = {
  id: 1,
  nickname: "主账号",
  platform: "douyin",
  group_id: 1,
  project_id: 1,
  status: "active",
  external_account_id: "douyin-1",
  integration_status: "connected",
  auth_status: "authorized",
  data_sync_status: "healthy",
  created_at: "2026-07-01T00:00:00Z",
};

const planSteps = [
  {
    id: "step-script",
    agent_code: "02-content-director",
    agent_name: "编导文案专家",
    phase: "脚本",
    intent: "生成脚本",
    status: "planned",
    depends_on: [],
    expected_output: "视频脚本",
    risk_level: "medium",
  },
  {
    id: "step-editing",
    agent_code: "05-editor",
    agent_name: "剪辑专家",
    phase: "剪辑",
    intent: "完成成片",
    status: "planned",
    depends_on: ["step-script"],
    expected_output: "成片",
    risk_level: "low",
  },
];

const draftTask = {
  id: 1003,
  content_item_id: 200,
  title: "7月新品冷启动",
  type: "content_creation",
  status: "pending_confirmation",
  brief: {
    goal: "7月新品冷启动内容",
    project_id: 1,
    project_name: project.name,
    account_group_id: 1,
    account_group_name: accountGroup.name,
    platforms: ["douyin"],
    account_ids: [1],
    cycle: "本周",
    budget: 3000,
    content_goal: "生成冷启动脚本和成片",
    risk_constraints: ["避免夸大承诺"],
    expected_outputs: ["视频脚本", "成片"],
    confirmation_actions: ["确认 Brief", "确认预算"],
  },
  plan: {
    id: 1,
    summary: "先脚本后剪辑",
    steps: planSteps,
    quality_gates: ["脚本合规", "发布前检查"],
    estimated_cost: 18.5,
    requires_human_confirmation: true,
  },
  progress: 0,
  current_focus: "等待确认 Brief",
  risk_count: 1,
  context_closed_at: null,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

const runningTask = {
  ...draftTask,
  status: "running",
  progress: 45,
  current_focus: "编导文案专家处理中",
};

const contentItem = {
  id: 200,
  project_id: 1,
  account_id: 1,
  title: "首条冷启动内容",
  current_stage: "content_direction",
  status: "blocked",
  created_at: "2026-07-01T00:00:00Z",
};

const acceptances = [
  {
    id: 501,
    task_id: 1003,
    deliverable_id: 701,
    agent_code: "02-content-director",
    agent_name: "编导文案专家",
    deliverable_type: "video_script",
    title: "脚本验收",
    version: 1,
    summary: "脚本已满足冷启动目标。",
    acceptance_items: [{ label: "合规", status: "pass", note: "无明显违规表达。" }],
    history_versions: [],
    status: "pending",
    reviewer_note: null,
    rerun_scope: null,
    brain_rejudge_summary: null,
    brain_rejudge_basis: [],
  },
  {
    id: 502,
    task_id: 1003,
    deliverable_id: 702,
    agent_code: "05-editor",
    agent_name: "剪辑专家",
    deliverable_type: "edited_video",
    title: "成片验收",
    version: 1,
    summary: "成片节奏需要再压缩。",
    acceptance_items: [{ label: "节奏", status: "fail", note: "前三秒钩子偏弱。" }],
    history_versions: [],
    status: "pending",
    reviewer_note: null,
    rerun_scope: null,
    brain_rejudge_summary: null,
    brain_rejudge_basis: [],
  },
];

const agents = [
  {
    code: "00-decision",
    name: "运营大脑",
    group: "control",
    one_liner: "拆解目标并调度专家团",
    model: "deepseek-chat",
    fallback_model: null,
    automation_level: "confirm",
    tools: ["plan"],
    typical_tasks: ["任务拆解"],
    standard_outputs: ["review_report"],
    current_task: {
      task_id: 1003,
      title: runningTask.title,
      project_name: project.name,
      account_group_name: accountGroup.name,
      platforms: ["douyin"],
      progress: 45,
      risk_level: "medium",
      blockers: ["脚本合规"],
      next_action: "等待验收",
      output_summary: "已生成 Brief 并开始调度。",
    },
  },
  {
    code: "02-content-director",
    name: "编导文案专家",
    group: "creative",
    one_liner: "负责选题和脚本",
    model: "deepseek-chat",
    fallback_model: null,
    automation_level: "confirm",
    tools: ["script"],
    typical_tasks: ["脚本"],
    standard_outputs: ["video_script"],
    current_task: null,
  },
];

test("login, create a brain brief, inspect agents, and accept/reject deliverables", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await installMockWebSocket(page);
  await mockApi(page);

  await loginAsAdmin(page);

  await expect(page.getByText("运营大脑").first()).toBeVisible();

  await page.getByPlaceholder(/写下目标/).fill("7月新品冷启动内容");
  await page.getByRole("button", { name: "生成 Brief" }).click();
  await expect(page.getByText("待确认 Brief", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "确认并执行" }).click();
  await expect(page.getByText("编导文案专家处理中", { exact: true })).toBeVisible();

  await page.goto("/agents");
  await expect(page.getByText("专家团").first()).toBeVisible();
  await expect(page.getByText("编导文案专家").first()).toBeVisible();

  await page.goto("/tasks");
  await expect(page.getByText("任务/验收").first()).toBeVisible();
  await page.getByText(contentItem.title).click();
  await expect(page.getByText("分项验收").first()).toBeVisible();

  const scriptPanel = page.locator("section").filter({ hasText: "脚本验收" }).first();
  await scriptPanel.getByRole("button", { name: "通过" }).click();
  await expect(scriptPanel.getByText("已通过")).toBeVisible();

  const videoPanel = page.locator("section").filter({ hasText: "成片验收" }).first();
  await videoPanel.getByPlaceholder(/写明打回原因/).fill("前三秒钩子偏弱，需要重剪。");
  await videoPanel.getByRole("button", { name: "打回" }).click();
  await expect(videoPanel.getByText("已请求重跑")).toBeVisible();

  expect(consoleErrors).toEqual([]);
});

test("core pages render cleanly at responsive widths", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await installMockWebSocket(page);
  await mockApi(page);
  await loginAsAdmin(page);

  for (const width of [320, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });

    await page.goto("/");
    await expect(page.getByText("运营大脑").first()).toBeVisible();

    await page.goto("/agents");
    await expect(page.getByText("专家团").first()).toBeVisible();

    await page.goto("/tasks");
    await expect(page.getByText("任务/验收").first()).toBeVisible();
  }

  expect(consoleErrors).toEqual([]);
});

test("trace flow can zoom, drag nodes, and keep nodes separated at 768 and 1440", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));

  await installMockWebSocket(page);
  await mockApi(page);
  await loginAsAdmin(page);

  await page.getByPlaceholder(/写下目标/).fill("7月新品冷启动内容");
  await page.getByRole("button", { name: "生成 Brief" }).click();
  await page.getByRole("button", { name: "确认并执行" }).click();
  await page.getByText("完整", { exact: true }).click();

  const node = page.locator('.react-flow__node[data-id="step-script"]');
  const stationaryNode = page.locator('.react-flow__node[data-id="step-editing"]');
  const viewport = page.locator(".react-flow__viewport").first();
  await expect(page.locator(".react-flow__node")).toHaveCount(6);

  for (const width of [768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await waitForStableTraceLayout(page);
    await expectTraceNodesSeparated(page);

    const beforeDrag = await node.boundingBox();
    const beforeDragPosition = await getTraceNodePosition(node);
    const beforeStationaryPosition = await getTraceNodePosition(stationaryNode);
    expect(beforeDrag).not.toBeNull();
    expect(beforeDragPosition).not.toBeNull();
    expect(beforeStationaryPosition).not.toBeNull();
    await node.click();
    await page.mouse.move(
      beforeDrag!.x + beforeDrag!.width / 2,
      beforeDrag!.y + beforeDrag!.height / 2,
    );
    await page.mouse.down();
    await page.mouse.move(
      beforeDrag!.x + beforeDrag!.width / 2 - 48,
      beforeDrag!.y + beforeDrag!.height / 2 - 24,
      { steps: 8 },
    );
    await page.mouse.up();
    const afterDragPosition = await getTraceNodePosition(node);
    const afterStationaryPosition = await getTraceNodePosition(stationaryNode);
    expect(afterDragPosition).not.toBeNull();
    expect(afterStationaryPosition).not.toBeNull();
    expect(
      Math.abs(afterDragPosition!.x - beforeDragPosition!.x) +
        Math.abs(afterDragPosition!.y - beforeDragPosition!.y),
    ).toBeGreaterThan(8);
    expect(
      Math.abs(afterStationaryPosition!.x - beforeStationaryPosition!.x) +
        Math.abs(afterStationaryPosition!.y - beforeStationaryPosition!.y),
    ).toBeLessThan(1);
    await expectTraceNodesSeparated(page);

    const beforeZoom = await viewport.evaluate((element) => getComputedStyle(element).transform);
    await page.locator(".react-flow__controls-zoomin").click();
    await expect
      .poll(() => viewport.evaluate((element) => getComputedStyle(element).transform))
      .not.toBe(beforeZoom);
    await page.locator(".react-flow__controls-zoomout").click();
  }

  expect(consoleErrors).toEqual([]);
});

async function loginAsAdmin(page: Page) {
  await page.goto("/login");
  await page.getByPlaceholder("admin@dyflow.local").fill(user.email);
  await page.getByPlaceholder("••••••••").fill("admin12345");
  await page.locator('form button[type="submit"]').click();
}

async function installMockWebSocket(page: Page) {
  await page.addInitScript(() => {
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
  });
}

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) {
      return route.continue();
    }
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "POST" && path === "/auth/login") {
      return json(route, { access_token: "test-token", token_type: "bearer", user });
    }
    if (method === "GET" && path === "/auth/me") return json(route, user);
    if (method === "GET" && path === "/projects") return json(route, [project]);
    if (method === "GET" && path === "/account-groups") return json(route, [accountGroup]);
    if (method === "GET" && path === "/accounts") return json(route, [account]);
    if (method === "GET" && path === "/brain/tasks") return json(route, []);
    if (method === "POST" && path === "/brain/tasks/draft") return json(route, draftTask);
    if (method === "POST" && path === "/brain/tasks/1003/confirm") return json(route, runningTask);
    if (method === "GET" && path === "/brain/tasks/1003/invocations") {
      return json(route, [
        {
          id: 1,
          task_id: 1003,
          agent_code: "02-content-director",
          agent_name: "编导文案专家",
          status: "done",
          input_summary: "冷启动目标",
          output_summary: "脚本已生成",
          model: "deepseek-chat",
          token_count: 1200,
          cost: 0.8,
          failure_reason: null,
          upstream: [],
          started_at: "2026-07-01T00:00:00Z",
          finished_at: "2026-07-01T00:02:00Z",
        },
      ]);
    }
    if (method === "GET" && path === "/brain/tasks/1003/acceptances") {
      return json(route, acceptances);
    }
    if (method === "POST" && path === "/brain/tasks/1003/accept") {
      return json(route, { ...acceptances[0], status: "approved" });
    }
    if (method === "POST" && path === "/brain/tasks/1003/rerun") {
      return json(route, {
        ...acceptances[1],
        status: "rerun_requested",
        reviewer_note: "前三秒钩子偏弱，需要重剪。",
        rerun_scope: "current_agent",
        brain_rejudge_summary: "建议重跑当前剪辑 Agent。",
        brain_rejudge_basis: ["验收项未通过"],
      });
    }
    if (method === "GET" && path === "/agents") return json(route, agents);
    if (method === "GET" && path.startsWith("/agents/")) {
      return json(route, agents.find((agent) => path.endsWith(agent.code)) ?? agents[0]);
    }
    if (method === "GET" && path === "/content-items") return json(route, [contentItem]);
    if (method === "GET" && path === "/content-items/200/deliverables") return json(route, []);

    return json(route, {});
  });
}

async function expectTraceNodesSeparated(page: Page) {
  const boxes = await page.locator(".react-flow__node").evaluateAll((nodes) =>
    nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return {
        id: node.getAttribute("data-id") ?? node.textContent ?? "unknown",
        x: rect.left,
        y: rect.top,
        width: rect.width,
        height: rect.height,
      };
    }),
  );

  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i];
      const b = boxes[j];
      const overlapX = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
      const overlapY = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
      expect(
        overlapX <= 1 || overlapY <= 1,
        `Trace nodes overlap: ${a.id} and ${b.id}`,
      ).toBe(true);
    }
  }
}

async function waitForStableTraceLayout(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        let previous = "";
        let stableSamples = 0;
        const sample = () => {
          const viewport = document.querySelector(".react-flow__viewport");
          const key = [
            viewport ? getComputedStyle(viewport).transform : "",
            ...Array.from(document.querySelectorAll(".react-flow__node")).map((node) => {
              const box = node.getBoundingClientRect();
              return `${Math.round(box.x)}:${Math.round(box.y)}:${Math.round(box.width)}:${Math.round(box.height)}`;
            }),
          ].join("|");
          stableSamples = key === previous ? stableSamples + 1 : 0;
          previous = key;
          if (stableSamples >= 4) {
            resolve();
            return;
          }
          window.setTimeout(sample, 50);
        };
        sample();
      }),
  );
}

async function getTraceNodePosition(locator: ReturnType<Page["locator"]>) {
  return locator.evaluate((element) => {
    const transform = (element as HTMLElement).style.transform;
    const match = transform.match(/translate\((-?\d+(?:\.\d+)?)px,\s*(-?\d+(?:\.\d+)?)px\)/);
    if (!match) return null;
    return { x: Number(match[1]), y: Number(match[2]) };
  });
}

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}
