import { expect, test, type APIRequestContext } from "@playwright/test";

const enabled = process.env.REAL_BACKEND_SMOKE === "1";
const apiOrigin = process.env.REAL_API_ORIGIN ?? "http://127.0.0.1:8000";
const email = process.env.REAL_SMOKE_EMAIL ?? "ci-admin@example.invalid";
const password = process.env.REAL_SMOKE_PASSWORD ?? "ci-main-agent-password";

test.describe("Operations Brain real API smoke", () => {
  test.skip(!enabled, "Set REAL_BACKEND_SMOKE=1 to run against backend + worker services.");

  test("frontend and real backend complete greeting, query, and approval flows", async ({
    page,
    request,
  }) => {
    await page.goto("/");
    await expect(page.locator("#root")).toBeAttached();

    const token = await login(request);
    const headers = { Authorization: `Bearer ${token}` };
    const unique = `${Date.now()}-${test.info().parallelIndex}`;
    const accountResponse = await request.post(`${apiOrigin}/accounts`, {
      headers,
      data: {
        nickname: `V3 real smoke ${unique}`,
        platform: "douyin",
      },
    });
    expect(accountResponse.status()).toBe(201);
    const account = await accountResponse.json() as { id: number };
    const threadResponse = await request.post(`${apiOrigin}/brain/conversations`, {
      headers,
      data: {
        account_id: account.id,
        title: `V3 real smoke ${unique}`,
      },
    });
    expect(threadResponse.status()).toBe(201);
    const thread = await threadResponse.json() as { id: number };

    const greeting = await submitAndPoll(request, headers, {
      threadId: thread.id,
      key: `real-greeting-${unique}`,
      message: "你好",
    });
    expect(greeting.status).toBe("completed");
    expect(greeting.intent?.mode).toBe("answer");
    expect(greeting.model_call_count).toBe(1);
    expect(greeting.assistant_response).toContain("Operations Brain CI answer");

    const query = await submitAndPoll(request, headers, {
      threadId: thread.id,
      key: `real-query-${unique}`,
      message: "只查询账号数据，不生成策略",
    });
    expect(query.status).toBe("completed");
    expect(query.intent?.mode).toBe("query");
    expect(query.model_call_count).toBe(0);
    expect(query.projections).toContainEqual(
      expect.objectContaining({ type: "account_data", account_id: account.id }),
    );

    const approval = await submitAndPoll(request, headers, {
      threadId: thread.id,
      key: `real-approval-${unique}`,
      message: "现在发布到外部平台",
    });
    expect(approval.intent?.mode).toBe("action");
    expect(approval.status).toBe("waiting_permission");
    expect(approval.projections).toContainEqual(
      expect.objectContaining({
        type: "approval",
        approval: expect.objectContaining({ status: "waiting_approval" }),
      }),
    );
  });
});

async function login(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${apiOrigin}/auth/login`, {
    data: { email, password },
  });
  expect(response.status()).toBe(200);
  const body = await response.json() as { access_token: string };
  return body.access_token;
}

async function submitAndPoll(
  request: APIRequestContext,
  headers: Record<string, string>,
  input: {
    threadId: number;
    key: string;
    message: string;
  },
) {
  const submitted = await request.post(
    `${apiOrigin}/brain/conversations/${input.threadId}/turns`,
    {
      headers,
      data: {
        client_message_id: input.key,
        message: input.message,
      },
    },
  );
  expect(submitted.status()).toBe(202);
  const body = await submitted.json() as { turn: { id: number } };
  await expect.poll(
    async () => {
      const turn = await request.get(`${apiOrigin}/brain/turns/${body.turn.id}`, {
        headers,
      });
      expect(turn.status()).toBe(200);
      return await turn.json() as {
        status: string;
        intent: { mode: string } | null;
        model_call_count: number | null;
        assistant_response: string | null;
        projections: Array<Record<string, unknown>>;
      };
    },
    {
      message: `Turn ${body.turn.id} did not reach a terminal or paused state`,
      timeout: 60_000,
      intervals: [100, 250, 500, 1_000],
    },
  ).toMatchObject({
    status: expect.stringMatching(
      /^(blocked|completed|failed|waiting_decision|waiting_permission|waiting_user)$/,
    ),
  });
  const turn = await request.get(`${apiOrigin}/brain/turns/${body.turn.id}`, { headers });
  expect(turn.status()).toBe(200);
  return await turn.json() as {
    status: string;
    intent: { mode: string } | null;
    model_call_count: number | null;
    assistant_response: string | null;
    projections: Array<Record<string, unknown>>;
  };
}
