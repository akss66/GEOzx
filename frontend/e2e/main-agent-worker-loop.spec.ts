import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const enabled = process.env.REAL_WORKER_LOOP === "1";
const apiOrigin = process.env.REAL_API_ORIGIN ?? "http://127.0.0.1:8000";
const email = process.env.REAL_SMOKE_EMAIL ?? "ci-admin@example.invalid";
const password = process.env.REAL_SMOKE_PASSWORD ?? "ci-main-agent-password";
const weeklyRequest = "结合最近数据和对标内容，规划并制作下周抖音内容";
const steering = "第一条不要讲价格";

test.describe("Operations Brain complete worker loop", () => {
  test.skip(!enabled, "Set REAL_WORKER_LOOP=1 to run the real backend + worker contract.");

  test("plans, accepts steering, hands off manual publishing, and reconnects once", async ({
    page,
    request,
  }) => {
    test.setTimeout(120_000);
    const browserProblems: string[] = [];
    page.on("console", (message) => {
      if (["warning", "error"].includes(message.type())) browserProblems.push(message.text());
    });
    page.on("pageerror", (error) => browserProblems.push(error.message));

    const fixture = await createFixture(request);
    await installSession(page, fixture.token, fixture.account.id);
    await page.goto("/brain");

    await expect(page.locator(".tz-account-trigger .tz-account-avatar")).toBeVisible();
    await expect(page.locator(".dy-brain-context-strip")).toContainText(fixture.account.nickname);

    const composer = page.getByRole("textbox", { name: "运营大脑消息" });
    await composer.fill(weeklyRequest);
    await composer.press("Enter");

    const sourceTurn = page.getByTestId("work-turn").filter({ hasText: weeklyRequest });
    await expect(sourceTurn).toHaveCount(1);
    await expect(sourceTurn.locator(".tz-work-turn__progress li").first()).toBeVisible();
    await expect(sourceTurn).toContainText(/读取.*数据|分析.*对标/);
    expect(await navigationTypes(page)).not.toContain("reload");

    // The composer must remain a steering surface while the source WorkTurn is
    // active; this is a visible UI action, not an API-side test shortcut.
    await composer.fill(steering);
    await composer.press("Enter");
    await expect(sourceTurn.getByRole("status", { name: "任务调整" })).toContainText(
      /已加入当前任务|已补充到当前任务/,
    );

    await expect(sourceTurn).toHaveAttribute("data-turn-status", "completed", {
      timeout: 90_000,
    });
    await expect(sourceTurn.locator(".tz-artifact-card")).toContainText("5 个选题");
    await expect(sourceTurn.locator(".tz-artifact-card")).toContainText(/5 条.*口播.*拍摄稿/);
    await expect(sourceTurn.locator(".tz-artifact-card")).toContainText(/7 天.*发布安排/);
    await expect(sourceTurn.locator(".tz-artifact-card")).toContainText("不要讲价格");
    await expect(sourceTurn.getByText("查看生成依据")).toBeVisible();

    const turnsBeforeReload = await page.getByTestId("work-turn").count();
    const artifactsBeforeReload = await sourceTurn.locator(".tz-artifact-card").count();
    await page.reload();
    const recoveredSource = page.getByTestId("work-turn").filter({ hasText: weeklyRequest });
    await expect(recoveredSource).toHaveCount(1);
    await expect(page.getByTestId("work-turn")).toHaveCount(turnsBeforeReload);
    await expect(recoveredSource.locator(".tz-artifact-card")).toHaveCount(
      artifactsBeforeReload,
    );

    const tabs = page.getByRole("tablist", { name: "运营工作区" });
    await tabs.getByRole("tab", { name: "对话" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(tabs.getByRole("tab", { name: "方案与内容" })).toBeFocused();
    await expect(page.locator(".tz-artifact-center__row")).toContainText(/5 个选题/);

    await page.keyboard.press("ArrowRight");
    await expect(tabs.getByRole("tab", { name: "待处理" })).toBeFocused();
    const manualGroup = page.locator(".tz-pending-work__group", { hasText: "待手动发布" });
    await expect(manualGroup).toBeVisible();
    const manualItem = manualGroup.locator(".tz-pending-work__item").first();
    await expect(manualItem).toContainText("排期内容等待在抖音手动发布");
    await manualItem.getByRole("button", { name: "记录已发布" }).click();
    await expect(manualItem).toHaveCount(0);
    await expect(page.locator(".tz-pending-work__group", { hasText: "待补录数据" }))
      .toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    const overflow = await page.evaluate(() => ({
      body: document.body.scrollWidth - document.body.clientWidth,
      root: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    }));
    expect(overflow.body).toBeLessThanOrEqual(1);
    expect(overflow.root).toBeLessThanOrEqual(1);
    expect(browserProblems).toEqual([]);
  });
});

async function createFixture(request: APIRequestContext) {
  const login = await request.post(`${apiOrigin}/auth/login`, {
    data: { email, password },
  });
  expect(login.status()).toBe(200);
  const { access_token: token } = await login.json() as { access_token: string };
  const headers = { Authorization: `Bearer ${token}` };
  const unique = `${Date.now()}-${test.info().parallelIndex}`;
  const created = await request.post(`${apiOrigin}/accounts`, {
    headers,
    data: {
      nickname: `Worker loop ${unique}`,
      platform: "douyin",
    },
  });
  expect(created.status()).toBe(201);
  const account = await created.json() as { id: number; nickname: string };
  return { token, account };
}

async function installSession(page: Page, token: string, accountId: number) {
  await page.addInitScript(({ accessToken, selectedAccountId }) => {
    localStorage.setItem("dyflow_token", accessToken);
    localStorage.setItem(
      "tongzhouxing_current_workspace",
      JSON.stringify({
        version: 2,
        clientId: null,
        projectId: null,
        platform: "douyin",
        accountId: selectedAccountId,
      }),
    );
  }, { accessToken: token, selectedAccountId: accountId });
}

async function navigationTypes(page: Page) {
  return await page.evaluate(() => performance.getEntriesByType("navigation")
    .map((entry) => (entry as PerformanceNavigationTiming).type));
}
