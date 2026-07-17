import { describe, expect, it } from "vitest";

import { isAuthenticationError, presentApiError } from "./errors";

describe("presentApiError", () => {
  it("maps authorization errors to business language", () => {
    expect(presentApiError({ response: { status: 403 } })).toEqual({
      message: "你没有权限访问当前内容。",
      diagnostic: "HTTP 403",
    });
  });

  it("does not expose response payloads or credentials", () => {
    const result = presentApiError(
      {
        response: {
          status: 503,
          data: { detail: "DEEPSEEK_API_KEY=super-secret; stack trace follows" },
          headers: { "x-request-id": "req-42" },
        },
      },
      "模型服务暂时不可用。",
    );

    expect(result).toEqual({
      message: "模型服务暂时不可用。",
      diagnostic: "HTTP 503 · 请求 req-42",
    });
    expect(JSON.stringify(result)).not.toContain("super-secret");
    expect(JSON.stringify(result)).not.toContain("API_KEY");
  });
});

describe("isAuthenticationError", () => {
  it("only treats explicit authorization failures as an invalid session", () => {
    expect(isAuthenticationError({ response: { status: 401 } })).toBe(true);
    expect(isAuthenticationError({ response: { status: 403 } })).toBe(true);
    expect(isAuthenticationError({ response: { status: 503 } })).toBe(false);
    expect(isAuthenticationError({ code: "ECONNABORTED" })).toBe(false);
  });
});
