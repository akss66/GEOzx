import { expect, test, type Page, type Route } from "@playwright/test";

const createdAt = "2026-08-12T09:00:00Z";
const user = {
  id: 1,
  email: "wechat-flow@example.test",
  display_name: "WeChat Flow Admin",
  role: "admin",
  is_active: true,
};

const primaryAccount = {
  id: 1,
  client_id: null,
  client_ids: [],
  nickname: "公众号主账号",
  platform: "wechat",
  group_id: null,
  project_id: null,
  project_ids: [],
  status: "active",
  external_account_id: "wechat-primary",
  integration_status: "connected",
  auth_status: "authorized",
  data_sync_status: "healthy",
  avatar_url: null,
  created_at: createdAt,
};

const secondaryAccount = {
  ...primaryAccount,
  id: 2,
  nickname: "公众号次账号",
  external_account_id: "wechat-secondary",
};

const threadId = 4001;
const turnId = 4101;
const articleId = 42;

test("wechat article handoff stays in one WorkTurn and survives refresh plus account switching", async ({
  page,
}) => {
  await installBrowserState(page);
  const scenario = await mockWechatArticleApi(page);
  await loginAsAdmin(page);

  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: primaryAccount.nickname }).click();

  const composer = page.getByLabel("运营大脑消息");
  await composer.fill("写一篇关于夏季门店陈列的公众号文章");
  await page.getByRole("button", { name: "发送给运营大脑" }).click();

  const turn = page.getByTestId("work-turn").filter({ hasText: "写一篇关于夏季门店陈列的公众号文章" });
  await expect(turn).toHaveCount(1);
  await expect(turn).toContainText("文章初稿已生成");
  const workspaceLink = turn.getByRole("link", { name: "打开文章工作台" });
  await expect(workspaceLink).toHaveAttribute("href", `/wechat-articles/${articleId}`);

  await page.reload();
  const restoredTurn = page.locator(`[data-turn-id="${turnId}"]`);
  await expect(restoredTurn).toHaveCount(1);
  await expect(restoredTurn.getByRole("link", { name: "打开文章工作台" }))
    .toHaveAttribute("href", `/wechat-articles/${articleId}`);

  await restoredTurn.getByRole("link", { name: "打开文章工作台" }).click();
  await expect(page).toHaveURL(new RegExp(`/wechat-articles/${articleId}$`));

  await page.getByRole("button", { name: "一键生成全部配图" }).click();
  await expect.poll(() => scenario.generateAllCalls).toBe(1);
  await expect(page.getByText("已选图")).toBeVisible();

  await page.getByRole("button", { name: "获取提示词" }).click();
  await expect(page.getByText("适合门店橱窗海报的夏季陈列主视觉提示词")).toBeVisible();

  const titleInput = page.getByLabel("标题");
  await titleInput.fill("夏季门店陈列新标题");
  await page.waitForTimeout(2200);
  await expect(page.getByRole("button", { name: "查看差异" })).toBeVisible();

  await page.getByRole("button", { name: "预览" }).click();
  await expect(page.getByRole("heading", { name: "公众号预览" })).toBeVisible();

  await page.getByRole("button", { name: "创建版本并同步到公众号草稿箱" }).click();
  const dialog = await page.getByRole("dialog", { name: "同步确认" });
  await expect(dialog).toContainText("品牌公众号");
  await dialog.getByRole("button", { name: "确认同步到公众号「品牌公众号」草稿箱" }).click();
  await expect.poll(() => scenario.syncCalls).toBe(1);

  await page.goto("/");
  await page.locator(".tz-account-trigger").click();
  await page.locator(".tz-account-panel button", { hasText: secondaryAccount.nickname }).click();
  await expect(page.getByText("写一篇关于夏季门店陈列的公众号文章")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "打开文章工作台" })).toHaveCount(0);
  await expect(page.locator(`[data-turn-id="${turnId}"]`)).toHaveCount(0);

  expect(scenario.unexpectedApiCalls).toEqual([]);
});

async function loginAsAdmin(page: Page) {
  await page.goto("/login");
  await page.locator('input[autocomplete="email"]').fill(user.email);
  await page.locator('input[autocomplete="current-password"]').fill("admin12345");
  await page.locator('form button[type="submit"]').click();
}

async function installBrowserState(page: Page) {
  await page.addInitScript(() => {
    sessionStorage.clear();
    localStorage.removeItem("tongzhouxing_current_workspace");
    localStorage.removeItem("tongzhouxing_brain_active_conversation_threads");
    localStorage.removeItem("tongzhouxing_brain_active_tasks");
  });
}

async function mockWechatArticleApi(page: Page) {
  const unexpectedApiCalls: string[] = [];
  const threadByAccountId = new Map<number, Record<string, unknown>>();
  let workingCopyLockVersion = 7;
  let selectedMaterialId: number | null = null;
  let generateAllCalls = 0;
  let syncCalls = 0;
  let conflictRaised = false;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "POST" && path === "/auth/login") {
      return json(route, { access_token: "wechat-flow-token", token_type: "bearer", user });
    }
    if (method === "GET" && path === "/auth/me") return json(route, user);
    if (method === "GET" && path === "/projects") return json(route, []);
    if (method === "GET" && path === "/account-groups") return json(route, []);
    if (method === "GET" && path === "/clients") return json(route, []);
    if (method === "GET" && path === "/notifications") return json(route, []);
    if (method === "GET" && path === "/notifications/unread-count") return json(route, { count: 0 });
    if (method === "GET" && path === "/skills") return json(route, { data: [] });
    if (method === "GET" && path === "/brain/tasks") return json(route, []);
    if (method === "GET" && path === "/accounts") return json(route, [primaryAccount, secondaryAccount]);
    if (method === "GET" && /^\/accounts\/\d+\/pending-work$/.test(path)) {
      const accountId = Number(path.split("/")[2]);
      return json(route, { account_id: accountId, groups: [] });
    }
    if (method === "GET" && path === "/workspace-context") {
      return json(route, {
        clients: [],
        selected_client: null,
        projects: [],
        selected_project: null,
        accounts: [primaryAccount, secondaryAccount],
      });
    }
    if (method === "GET" && path === "/brain/conversations") {
      const accountId = Number(url.searchParams.get("account_id"));
      const thread = threadByAccountId.get(accountId);
      return json(route, {
        data: thread ? [{
          id: thread.id,
          account_id: thread.account_id,
          title: thread.title,
          turn_count: Array.isArray(thread.turns) ? thread.turns.length : 0,
          last_message: null,
          created_at: thread.created_at,
          updated_at: thread.updated_at,
        }] : [],
      });
    }
    if (method === "POST" && path === "/brain/conversations") {
      const body = (await request.postDataJSON()) as { account_id: number };
      const accountId = body.account_id;
      const nextThread = {
        id: threadId + (accountId === primaryAccount.id ? 0 : accountId),
        org_id: 1,
        created_by_id: user.id,
        client_id: null,
        project_id: null,
        account_id: accountId,
        title: "WeChat article flow",
        turns: [] as Array<Record<string, unknown>>,
        created_at: createdAt,
        updated_at: createdAt,
      };
      threadByAccountId.set(accountId, nextThread);
      return json(route, nextThread);
    }
    if (method === "GET" && /^\/brain\/conversations\/\d+$/.test(path)) {
      const targetThreadId = Number(path.split("/").at(-1));
      const thread = [...threadByAccountId.values()].find((item) => Number(item.id) === targetThreadId);
      return json(route, thread ?? {});
    }
    if (method === "POST" && /^\/brain\/conversations\/\d+\/turns$/.test(path)) {
      const body = (await request.postDataJSON()) as { client_message_id: string; message: string };
      const thread = threadByAccountId.get(primaryAccount.id);
      if (!thread) return json(route, {});
      const turn = wechatArticleTurn(body.message, body.client_message_id);
      thread.turns = [turn];
      thread.updated_at = createdAt;
      return json(route, {
        turn,
        run: { id: 5101, status: "waiting_user", phase: "waiting_approval" },
        task_id: null,
        projections: turn.projections,
      });
    }
    if (method === "GET" && /^\/conversation-threads\/\d+\/events$/.test(path)) {
      return json(route, { data: [] });
    }
    if (method === "GET" && path === "/artifacts") {
      return json(route, { data: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } });
    }
    if (method === "GET" && path === `/wechat-articles/${articleId}/working-copy`) {
      return json(route, workingCopyPayload({ lockVersion: workingCopyLockVersion, selectedMaterialId }));
    }
    if (method === "PATCH" && path === `/wechat-articles/${articleId}/working-copy`) {
      if (!conflictRaised) {
        conflictRaised = true;
        return route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            error: {
              code: "ARTICLE_VERSION_CONFLICT",
              details: { currentLockVersion: 8 },
            },
          }),
        });
      }
      const body = (await request.postDataJSON()) as { document: Record<string, unknown> };
      workingCopyLockVersion += 1;
      return json(route, {
        ...workingCopyPayload({ lockVersion: workingCopyLockVersion, selectedMaterialId }),
        document: body.document,
      });
    }
    if (method === "GET" && path === `/wechat-articles/${articleId}/preview`) {
      return json(route, {
        articleId,
        document: workingCopyPayload({ lockVersion: workingCopyLockVersion, selectedMaterialId }).document,
        renderedHtml: "<h1>公众号预览</h1><p>适合门店橱窗海报的夏季陈列方案。</p>",
      });
    }
    if (method === "GET" && path === `/wechat-articles/${articleId}/image-slots/7/prompt`) {
      return json(route, { prompt: "适合门店橱窗海报的夏季陈列主视觉提示词" });
    }
    if (method === "POST" && path === `/wechat-articles/${articleId}/image-generations`) {
      generateAllCalls += 1;
      selectedMaterialId = 101;
      return json(route, { requestedSlotIds: [7], materialIds: [101], failedSlotIds: [] });
    }
    if (method === "POST" && path === `/wechat-articles/${articleId}/versions`) {
      return json(route, {
        id: 12,
        articleId,
        version: 4,
        trigger: "manual",
        document: workingCopyPayload({ lockVersion: workingCopyLockVersion, selectedMaterialId }).document,
      });
    }
    if (method === "GET" && path === `/wechat-articles/${articleId}/draft-sync-context`) {
      return json(route, {
        targetAccount: { id: 31, name: "品牌公众号" },
        articleTitle: "夏季门店陈列",
        articleVersionId: 12,
        imageCount: selectedMaterialId ? 1 : 0,
        readiness: {
          canSync: true,
          blockers: [],
          warnings: [],
          unresolvedClaimCount: 0,
        },
        remote: {
          status: "wechat_synced",
          remoteHash: "hash-1",
          updatedAt: createdAt,
          errorCode: null,
          operationType: "draft_sync",
        },
      });
    }
    if (method === "POST" && path === `/wechat-articles/${articleId}/draft-syncs`) {
      syncCalls += 1;
      return json(route, {
        id: 88,
        accountId: 31,
        articleId,
        articleVersionId: 12,
        status: "queued",
        conflictStrategy: "fail",
        externalMediaId: null,
        expectedRemoteHash: "hash-1",
        observedRemoteHash: null,
        retryable: false,
        errorCode: null,
        createdAt: createdAt,
        updatedAt: createdAt,
      });
    }

    unexpectedApiCalls.push(`${method} ${path}`);
    return json(route, {});
  });

  return {
    unexpectedApiCalls,
    get generateAllCalls() {
      return generateAllCalls;
    },
    get syncCalls() {
      return syncCalls;
    },
  };
}

function wechatArticleTurn(message: string, clientMessageId: string) {
  return {
    id: turnId,
    thread_id: threadId,
    org_id: 1,
    created_by_id: user.id,
    client_message_id: clientMessageId,
    user_input: message,
    assistant_response: "raw runtime response should not replace the handoff",
    intent: {
      mode: "skill",
      route_source: "deterministic",
      skill_code: "wechat_article_production",
    },
    status: "waiting_permission",
    turn_phase: "waiting_approval",
    projections: [
      {
        type: "execution_summary",
        turn_id: turnId,
        run_id: 5101,
        skill_code: "wechat_article_production",
        skill_run_id: 6101,
        status: "waiting_user",
        quality_score: null,
        experts: [],
        tools: [],
      },
      {
        type: "artifact",
        artifact_id: 9001,
        artifact_type: "wechat_article",
        skill_run_id: 6101,
        account_id: primaryAccount.id,
        turn_id: turnId,
        report: {
          article_id: articleId,
          current_immutable_version: 3,
          readiness: { status: "waiting_user" },
          explicit_user_decisions: [
            { action: "generate_images", status: "not_requested" },
            { action: "sync_draft", status: "not_requested" },
          ],
        },
      },
    ],
    pending_interrupt: {
      id: 77,
      account_id: primaryAccount.id,
      thread_id: threadId,
      turn_id: turnId,
      run_id: 5101,
      kind: "article_action",
      status: "pending",
      public_message: "Choose the next article action.",
      action_label: "Open article workspace",
      response_schema: {},
      version: 1,
      resolved_at: null,
      created_at: createdAt,
      updated_at: createdAt,
    },
    created_at: createdAt,
    updated_at: createdAt,
  };
}

function workingCopyPayload(overrides: {
  lockVersion: number;
  selectedMaterialId: number | null;
}) {
  return {
    articleId,
    accountId: 31,
    accountName: "品牌公众号",
    lockVersion: overrides.lockVersion,
    basedOnDeliverableId: 11,
    document: {
      title: "夏季门店陈列",
      digest: "适合品牌公众号的门店橱窗陈列建议。",
      author: "运营团队",
      blocks: [
        { type: "paragraph", blockId: "intro", text: "先用门店橱窗主画面建立夏季氛围，再落到陈列动线。" },
      ],
      claims: [
        { claimId: "c-1", blockId: "intro", kind: "product", text: "主画面需要围绕门店主推商品。", citationIds: [] },
      ],
    },
    imageSlots: [
      {
        id: 7,
        stableKey: "cover",
        purpose: "封面图",
        aspectRatio: "2.35:1",
        visualBrief: "门店橱窗海报主视觉",
        status: overrides.selectedMaterialId ? "ready" : "pending",
        selectedMaterialId: overrides.selectedMaterialId,
        lockVersion: 2,
        hasPrompt: true,
      },
    ],
  };
}

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}
