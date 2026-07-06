const CALLBACK_PATH = "/platform-integrations/douyin/oauth/callback";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return json({ status: "ok", service: "dyflow-douyin-callback" });
    }

    if (!url.pathname.startsWith(CALLBACK_PATH)) {
      return new Response("Not found", { status: 404 });
    }

    if (request.method !== "GET") {
      return new Response("Method not allowed", {
        status: 405,
        headers: { allow: "GET" },
      });
    }

    const code = url.searchParams.get("code") || "";
    const state = url.searchParams.get("state") || "";
    const scope = url.searchParams.get("scope") || url.searchParams.get("scopes") || "";

    if (!code || !state) {
      return html(
        renderCallbackPage({
          ok: false,
          title: "授权回调缺少参数",
          message: "没有收到抖音返回的 code 或 state，请重新发起授权。",
          code,
          state,
          scope,
        }),
        400,
      );
    }

    const completion = await completeOAuth({ env, code, state, callbackUrl: url.href });
    return html(
      renderCallbackPage({
        ok: completion.ok,
        title: completion.ok ? "抖音账号授权成功" : "抖音账号授权未完成",
        message: completion.message,
        code,
        state,
        scope,
        accountId: completion.accountId,
        externalOpenId: completion.externalOpenId,
      }),
      completion.ok ? 200 : 502,
    );
  },
};

async function completeOAuth({ env, code, state, callbackUrl }) {
  if (!env.DYFLOW_BACKEND_COMPLETE_URL || !env.DYFLOW_OAUTH_WORKER_SECRET) {
    return {
      ok: false,
      message: "Worker 缺少后端完成地址或桥接密钥配置。",
    };
  }

  try {
    const response = await fetch(env.DYFLOW_BACKEND_COMPLETE_URL, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "authorization": `Bearer ${env.DYFLOW_OAUTH_WORKER_SECRET}`,
      },
      body: JSON.stringify({ code, state, callback_url: callbackUrl }),
    });
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      return {
        ok: false,
        message: payload.detail || `后端完成授权失败：HTTP ${response.status}`,
      };
    }

    return {
      ok: true,
      message: "账号已经写入同舟行，可以关闭此页面并回到系统查看账号矩阵。",
      accountId: payload.account_id,
      externalOpenId: payload.external_open_id,
    };
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "转发授权回调时发生异常。",
    };
  }
}

function renderCallbackPage({ ok, title, message, code, state, scope, accountId, externalOpenId }) {
  const color = ok ? "#047857" : "#b45309";
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>${escapeHtml(title)}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root { color-scheme: light; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f7f3ec;
      color: #111315;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      width: min(520px, calc(100vw - 40px));
      border-radius: 28px;
      background: rgba(255, 255, 255, 0.84);
      border: 1px solid rgba(17, 19, 21, 0.08);
      box-shadow: 0 24px 80px rgba(17, 19, 21, 0.12);
      padding: 34px;
    }
    h1 { margin: 0 0 12px; font-size: 28px; letter-spacing: 0; }
    p { margin: 0 0 18px; line-height: 1.7; }
    .status { color: ${color}; font-weight: 750; }
    dl { display: grid; gap: 10px; margin: 24px 0 0; }
    div { min-width: 0; }
    dt { font-size: 12px; color: #525252; margin-bottom: 4px; }
    dd { margin: 0; word-break: break-all; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
  </style>
</head>
<body>
  <main>
    <h1>${escapeHtml(title)}</h1>
    <p class="status">${ok ? "已完成" : "需要处理"}</p>
    <p>${escapeHtml(message)}</p>
    <dl>
      <div><dt>account_id</dt><dd>${escapeHtml(String(accountId || ""))}</dd></div>
      <div><dt>open_id</dt><dd>${escapeHtml(String(externalOpenId || ""))}</dd></div>
      <div><dt>scope</dt><dd>${escapeHtml(scope)}</dd></div>
      <div><dt>code</dt><dd>${escapeHtml(code)}</dd></div>
      <div><dt>state</dt><dd>${escapeHtml(state)}</dd></div>
    </dl>
  </main>
</body>
</html>`;
}

function html(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[char];
  });
}
