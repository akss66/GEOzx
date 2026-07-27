import { expect, test, type Page, type Route } from "@playwright/test";

const admin = {
  id: 1,
  email: "admin@tzxai.top",
  display_name: "系统管理员",
  role: "admin",
  is_active: true,
};

const account = {
  id: 1,
  client_id: 1,
  nickname: "抖音数据验收账号",
  platform: "douyin",
  group_id: null,
  project_id: 11,
  project_ids: [11],
  status: "active",
  external_account_id: "douyin-e2e-1",
  integration_status: "connected",
  auth_status: "authorized",
  data_sync_status: "manual",
  avatar_url: null,
  risk_count: 0,
  created_at: "2026-07-23T00:00:00Z",
};

const rows = Array.from({ length: 68 }, (_, index) => ({
  id: 1000 + index,
  row_number: index + 2,
  status: "ready",
  raw_values: {
    title: `作品 ${index + 1}`,
    published_at: "2026-07-18 14:11:20",
    play_count: 81 + index,
  },
  normalized_values: {
    title: `作品 ${index + 1}`,
    published_at: "2026-07-18T14:11:20",
    play_count: 81 + index,
    completion_rate: 0.0875,
  },
  field_errors: [],
  warnings: [],
  candidate_content_ids: [],
  projected_target_ids: [],
  platform_content_record_id: null,
  resolution_outcome: null,
  resolved_by_id: null,
  resolved_at: null,
}));

function buildBatch(status: "preview_ready" | "committed") {
  return {
    id: 81,
    status,
    source_kind: "platform_export",
    template_code: "douyin_work_list_v1",
    row_count: 68,
    period_start: "2026-07-18",
    period_end: "2026-07-18",
    committed_at: status === "committed" ? "2026-07-23T08:00:00Z" : null,
    revoked_at: null,
    created_at: "2026-07-23T07:58:00Z",
    artifacts: [
      {
        id: 501,
        filename: "douyin-work-list.csv",
        content_type: "text/csv",
        byte_size: 4096,
        sha256: "a".repeat(64),
        download_url: "/account-data/1/imports/81/artifacts/501",
      },
    ],
    conflicts: [],
    rows: rows.map((row) => ({
      ...row,
      status: status === "committed" ? "committed" : "ready",
    })),
  };
}

test("imports a Douyin work list and exposes its provenance to review", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await mockAccountDataApi(page);
  await page.addInitScript(() => {
    localStorage.setItem("dyflow_token", "test-token");
    localStorage.setItem(
      "tongzhouxing_current_workspace",
      JSON.stringify({
        version: 2,
        clientId: 1,
        projectId: 11,
        platform: "douyin",
        accountId: 1,
      }),
    );
  });

  await page.goto("/accounts/1/data");
  await expect(page.getByText("账号数据中心", { exact: true })).toBeVisible();

  const header = "作品名称,发布时间,体裁,审核状态,播放量,完播率,5s完播率,封面点击率,2s跳出率,平均播放时长,点赞量,分享量,评论量,收藏量,主页访问量,粉丝增量";
  const csvRows = Array.from(
    { length: 68 },
    (_, index) => `作品 ${index + 1},2026-07-18 14:11:20,1min-视频,公开,${81 + index},0.0875,0.375,,0.375,9.53,6,0,3,0,3,0`,
  );
  await page.getByLabel("选择导入文件").setInputFiles({
    name: "douyin-work-list.csv",
    mimeType: "text/csv",
    buffer: Buffer.from([header, ...csvRows].join("\n"), "utf8"),
  });

  await expect(page.getByText("导入预览已生成")).toBeVisible();
  await expect(page.getByText("douyin_work_list_v1", { exact: true })).toBeVisible();
  await expect(page.locator(".account-data-preview-summary").getByText("68", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "确认导入" }).click();
  await expect(page.getByText("导入已确认", { exact: true })).toBeVisible();
  await expect(page.getByText(/已写入/).first()).toBeVisible();

  await page.goto("/review");
  await expect(page.getByText("平台导出", { exact: true })).toBeVisible();
  await expect(page.getByText("数据证据")).toBeVisible();

  await page.goto("/accounts/1/data");
  await page.getByRole("button", { name: "\u6c38\u4e45\u5220\u9664\u6279\u6b21 81" }).click();
  await expect(
    page.getByText("\u5c06\u5148\u64a4\u9500\u8be5\u6279\u6b21\u4ea7\u751f\u7684\u6570\u636e\uff0c\u518d\u6c38\u4e45\u5220\u9664\u539f\u6587\u4ef6\u548c\u5386\u53f2\u8bb0\u5f55\u3002"),
  ).toBeVisible();
  await page.getByRole("button", { name: "\u786e\u8ba4\u6c38\u4e45\u5220\u9664\u6279\u6b21 81" }).click();
  await expect(page.getByText("\u5bfc\u5165\u6279\u6b21\u5df2\u6c38\u4e45\u5220\u9664")).toBeVisible();
  await expect(page.getByText("\u6682\u65e0\u5bfc\u5165\u5386\u53f2")).toBeVisible();

  const layout = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
  }));
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
  expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.clientWidth);
  expect(consoleErrors).toEqual([]);
});

async function mockAccountDataApi(page: Page) {
  let batch: ReturnType<typeof buildBatch> | null = null;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "GET" && path === "/auth/me") return json(route, admin);
    if (method === "GET" && path === "/accounts/1") return json(route, account);
    if (method === "GET" && path === "/notifications/unread-count") {
      return json(route, { count: 0 });
    }
    if (method === "GET" && path === "/workspace-context") {
      return json(route, {
        clients: [{ id: 1, name: "默认客户", status: "active", created_at: "2026-07-01T00:00:00Z" }],
        selected_client: { id: 1, name: "默认客户", status: "active", created_at: "2026-07-01T00:00:00Z" },
        projects: [{ id: 11, client_id: 1, name: "数据验收项目", description: null, status: "active", created_at: "2026-07-01T00:00:00Z" }],
        selected_project: { id: 11, client_id: 1, name: "数据验收项目", description: null, status: "active", created_at: "2026-07-01T00:00:00Z" },
        accounts: [account],
      });
    }
    if (method === "GET" && path === "/account-data/1/status") {
      return json(route, {
        account_id: 1,
        latest_confirmed_at: batch?.committed_at ?? null,
        coverage: {
          account_metrics: "missing",
          content_metrics: batch?.status === "committed" ? "available" : "missing",
          audience_profiles: "missing",
          benchmarks: "missing",
        },
        sources: batch?.status === "committed"
          ? [{
              batch_id: 81,
              source_kind: "platform_export",
              template_code: "douyin_work_list_v1",
              data_domain: "content_metrics",
              committed_at: batch.committed_at,
              period_start: batch.period_start,
              period_end: batch.period_end,
            }]
          : [],
      });
    }
    if (method === "GET" && path === "/account-data/1/imports") {
      return json(route, {
        items: batch
          ? [{
              id: batch.id,
              status: batch.status,
              source_kind: batch.source_kind,
              template_code: batch.template_code,
              row_count: batch.row_count,
              period_start: batch.period_start,
              period_end: batch.period_end,
              committed_at: batch.committed_at,
              revoked_at: batch.revoked_at,
              created_at: batch.created_at,
            }]
          : [],
      });
    }
    if (method === "POST" && path === "/account-data/1/imports") {
      batch = buildBatch("preview_ready");
      return json(route, batch);
    }
    if (method === "GET" && path === "/account-data/1/imports/81") {
      return json(route, batch ?? buildBatch("preview_ready"));
    }
    if (method === "POST" && path === "/account-data/1/imports/81/commit") {
      batch = buildBatch("committed");
      return json(route, batch);
    }
    if (method === "DELETE" && path === "/account-data/1/imports/81") {
      batch = null;
      return route.fulfill({ status: 204 });
    }
    if (method === "GET" && path === "/metrics/review-workspace") {
      return json(route, buildReviewWorkspace());
    }

    return json(route, []);
  });
}

function buildReviewWorkspace() {
  return {
    account: {
      id: 1,
      nickname: account.nickname,
      platform: "douyin",
      auth_status: "authorized",
      data_sync_status: "manual",
    },
    period: {
      days: 30,
      current_start: "2026-06-24",
      current_end: "2026-07-23",
      previous_start: "2026-05-25",
      previous_end: "2026-06-23",
    },
    data_status: {
      has_data: true,
      sources: ["platform_export"],
      latest_stat_date: "2026-07-18",
      latest_synced_at: "2026-07-23T08:00:00Z",
      latest_confirmed_at: "2026-07-23T08:00:00Z",
      days_since_observed: 5,
      days_since_confirmed: 0,
      coverage: {
        account_metrics: "missing",
        content_metrics: "available",
        content_identity: "available",
        audience: "missing",
        benchmarks: "missing",
      },
      conflict_count: 0,
      source_summary: [{
        batch_id: 81,
        source_kind: "platform_export",
        data_domains: ["content_metrics"],
        confirmed_at: "2026-07-23T08:00:00Z",
        period_start: "2026-07-18",
        period_end: "2026-07-18",
      }],
      missing_reasons: ["账号概览待人工补录", "粉丝画像待截图核验"],
    },
    goal: {
      id: null,
      period_days: 30,
      target_play: null,
      target_completion_rate: null,
      target_follower_delta: null,
      status: "not_configured",
      achievement_percent: null,
      components: [],
      summary: "尚未设置周期目标",
    },
    conclusion: "当前作品数据已入库，可基于 68 条平台导出记录开始复盘。",
    totals: {
      play: 7786,
      exposure: 0,
      avg_completion_rate: 0.0875,
      avg_engagement_rate: 0.04,
      follower_delta: 0,
    },
    changes: [{
      metric: "play",
      label: "播放量",
      current: 7786,
      previous: null,
      delta_percent: null,
      direction: "baseline",
      summary: "当前周期已建立播放量基线",
    }],
    trend: [{ date: "07/18", play: 7786, exposure: 0 }],
    engagement: [{ date: "07/18", completion_rate: 0.0875, like_rate: 0.04 }],
    attributions: [{
      content_item_id: 1,
      title: "作品 68",
      play: 148,
      completion_rate: 0.0875,
      engagement_rate: 0.04,
      role: "driver",
      reason: "当前导入批次播放贡献最高",
    }],
    evidence: [{
      id: 1,
      content_item_id: 1,
      account_id: 1,
      source: "douyin",
      stat_date: "2026-07-18",
      title: "作品 68",
      play: 148,
      exposure: 0,
      completion_rate: 0.0875,
      like_rate: 0.04,
      comment_rate: 0.01,
      share_rate: 0,
      follower_delta: 0,
      completion_rate_5s: 0.375,
      bounce_rate_2s: 0.375,
      profile_visit_count: 3,
      created_at: "2026-07-23T08:00:00Z",
    }],
    suggestions: [],
  };
}

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}
