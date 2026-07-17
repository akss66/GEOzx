export type PresentedApiError = {
  message: string;
  diagnostic: string | null;
};

type ErrorResponse = {
  status?: unknown;
  headers?: unknown;
};

type ErrorLike = {
  code?: unknown;
  response?: ErrorResponse;
};

const STATUS_MESSAGE: Record<number, string> = {
  400: "提交的信息不完整或格式不正确。",
  401: "登录状态已失效，请重新登录。",
  403: "你没有权限访问当前内容。",
  404: "请求的内容不存在或已被移除。",
  409: "当前内容已发生变化，请刷新后重试。",
  422: "提交的信息未通过校验，请检查后重试。",
  429: "操作过于频繁，请稍后重试。",
};

export function presentApiError(
  error: unknown,
  fallback = "服务暂时不可用，请稍后重试。",
): PresentedApiError {
  const value = asErrorLike(error);
  const status = numericStatus(value?.response?.status);
  const requestId = responseRequestId(value?.response?.headers);

  if (!status) {
    return {
      message: "网络连接中断，请检查连接后重试。",
      diagnostic: value?.code === "ECONNABORTED" ? "请求超时" : null,
    };
  }

  return {
    message: STATUS_MESSAGE[status] ?? fallback,
    diagnostic: `HTTP ${status}${requestId ? ` · 请求 ${requestId}` : ""}`,
  };
}

export function isAuthenticationError(error: unknown) {
  const status = numericStatus(asErrorLike(error)?.response?.status);
  return status === 401 || status === 403;
}

function asErrorLike(error: unknown): ErrorLike | null {
  if (!error || typeof error !== "object") return null;
  return error as ErrorLike;
}

function numericStatus(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function responseRequestId(headers: unknown): string | null {
  if (!headers || typeof headers !== "object") return null;
  const source = headers as Record<string, unknown>;
  const value = source["x-request-id"] ?? source["x-trace-id"];
  return typeof value === "string" && /^[a-zA-Z0-9._:-]{1,96}$/.test(value) ? value : null;
}
