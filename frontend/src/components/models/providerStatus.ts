import type { ModelProviderDetail } from "../../types";

export interface ProviderStatusMeta {
  label: "未配置" | "待验证" | "可用" | "异常" | "停用";
  tone: "neutral" | "warning" | "success" | "danger" | "disabled";
  reason: string;
}

function verificationErrorLabel(errorCode: string | null): string {
  switch (errorCode) {
    case "authentication_failed":
      return "密钥认证失败，请检查写入的凭证。";
    case "endpoint_unreachable":
      return "端点不可达，请检查地址与网络可用性。";
    case "protocol_incompatible":
      return "端点协议不兼容，无法按 OpenAI 兼容格式调用。";
    case "timeout":
      return "验证请求超时，请稍后重试。";
    case "model_unavailable":
      return "默认模型不可用，请更新模型目录或改用其他端点。";
    default:
      return "最近一次验证返回异常，请查看验证结果后再继续。";
  }
}

export function getProviderStatusMeta(provider: ModelProviderDetail): ProviderStatusMeta {
  if (!provider.enabled) {
    return {
      label: "停用",
      tone: "disabled",
      reason: "已停用，不参与新的模型调用。",
    };
  }
  if (!provider.key_configured) {
    return {
      label: "未配置",
      tone: "neutral",
      reason: "缺少可用密钥，暂时无法验证。",
    };
  }
  if (provider.verification_status === "verified") {
    return {
      label: "可用",
      tone: "success",
      reason: "验证通过，可用于新的路由分配。",
    };
  }
  if (provider.verification_status === "error") {
    return {
      label: "异常",
      tone: "danger",
      reason: verificationErrorLabel(provider.verification_error_code),
    };
  }
  return {
    label: "待验证",
    tone: "warning",
    reason: "配置已变更，请先验证再投入路由。",
  };
}
