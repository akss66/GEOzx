import { Button, Input } from "antd";
import { useEffect, useMemo, useState } from "react";

import type {
  ModelProviderDetail,
  ModelProviderDiscoveryResult,
  ModelProviderVerifyResult,
} from "../../types";
import { getProviderStatusMeta } from "./providerStatus";

type Notice = {
  tone: "neutral" | "error";
  text: string;
};

function formatDateTime(value: string | null): string {
  if (!value) return "尚未验证";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function verificationErrorMessage(errorCode: string | null): string {
  switch (errorCode) {
    case "authentication_failed":
      return "密钥认证失败。";
    case "endpoint_unreachable":
      return "端点不可达。";
    case "protocol_incompatible":
      return "端点协议不兼容。";
    case "timeout":
      return "验证请求超时。";
    case "model_unavailable":
      return "默认模型不可用。";
    default:
      return "验证服务返回异常。";
  }
}

function verificationOutcome(provider: ModelProviderDetail): string {
  if (!provider.key_configured) {
    return "尚未写入可用密钥。";
  }
  if (provider.verification_status === "verified") {
    return "连接验证成功，可参与新的路由分配。";
  }
  if (provider.verification_status === "error") {
    return verificationErrorMessage(provider.verification_error_code);
  }
  return "配置已变更，等待验证。";
}

function modelsToText(models: string[] | null): string {
  return (models ?? []).join("\n");
}

function parseModels(value: string): string[] {
  return Array.from(new Set(
    value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean),
  ));
}

export default function ProviderVerification({
  provider,
  latestVerification,
  verifying,
  discovering,
  savingModels,
  onVerify,
  onDiscover,
  onSaveModels,
}: {
  provider: ModelProviderDetail;
  latestVerification: ModelProviderVerifyResult | null;
  verifying: boolean;
  discovering: boolean;
  savingModels: boolean;
  onVerify: (providerId: number) => Promise<ModelProviderVerifyResult>;
  onDiscover: (providerId: number) => Promise<ModelProviderDiscoveryResult>;
  onSaveModels: (providerId: number, models: string[]) => Promise<void>;
}) {
  const [modelsText, setModelsText] = useState(modelsToText(provider.models));
  const [modelsTouched, setModelsTouched] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const status = getProviderStatusMeta(provider);

  useEffect(() => {
    setModelsText(modelsToText(provider.models));
    setModelsTouched(false);
    setNotice(null);
  }, [provider.id, provider.models]);

  const modelCount = useMemo(
    () => parseModels(modelsText).length,
    [modelsText],
  );
  const modelError = modelsTouched && modelCount === 0
    ? "模型目录至少需要一个模型名称。"
    : null;

  return (
    <section className="provider-editor__section">
      <header>
        <h3>验证与模型目录</h3>
        <p>验证结果只显示安全摘要。自动发现失败时不会覆盖手工维护的模型目录。</p>
      </header>

      <div className="provider-verification__stats" aria-label="验证摘要">
        <div>
          <span>当前状态</span>
          <strong className={`provider-status provider-status--${status.tone}`}>{status.label}</strong>
        </div>
        <div>
          <span>验证结果</span>
          <strong>{verificationOutcome(provider)}</strong>
        </div>
        <div>
          <span>延迟</span>
          <strong>{latestVerification ? `${latestVerification.latency_ms} ms` : "尚未记录"}</strong>
        </div>
        <div>
          <span>模型数量</span>
          <strong>{modelCount}</strong>
        </div>
        <div>
          <span>上次验证</span>
          <strong>{formatDateTime(provider.verified_at)}</strong>
        </div>
      </div>

      <div className="provider-editor__actions">
        <Button
          type="primary"
          loading={verifying}
          disabled={!provider.enabled || !provider.key_configured}
          onClick={async () => {
            const result = await onVerify(provider.id);
            if (result.verification_status === "verified") {
              setNotice({
                tone: "neutral",
                text: "连接验证成功，可参与新的路由分配。",
              });
              return;
            }
            setNotice({
              tone: "error",
              text: `连接验证未通过：${verificationErrorMessage(result.verification_error_code)}`,
            });
          }}
        >
          验证连接
        </Button>
        <Button
          loading={discovering}
          disabled={!provider.enabled || !provider.key_configured}
          onClick={async () => {
            const result = await onDiscover(provider.id);
            if (result.error_code === "discovery_unsupported") {
              setNotice({
                tone: "neutral",
                text: "当前端点不支持自动发现，已保留手工模型目录。",
              });
              return;
            }
            if (result.error_code) {
              setNotice({
                tone: "error",
                text: "自动发现未完成，请保留现有模型目录后稍后重试。",
              });
              return;
            }
            setModelsText(modelsToText(result.models));
            setModelsTouched(true);
            setNotice({ tone: "neutral", text: `已更新 ${result.models.length} 个模型。` });
          }}
        >
          自动发现模型
        </Button>
      </div>

      <label className="provider-editor__field provider-editor__field--stacked">
        <span>模型目录</span>
        <Input.TextArea
          aria-label="模型目录"
          aria-invalid={Boolean(modelError)}
          autoSize={{ minRows: 6, maxRows: 12 }}
          value={modelsText}
          placeholder="每行一个模型名称"
          onChange={(event) => {
            setModelsTouched(true);
            setModelsText(event.target.value);
          }}
        />
      </label>

      {modelError ? (
        <p role="alert" className="provider-editor__validation">{modelError}</p>
      ) : null}
      {notice ? (
        <p
          role={notice.tone === "error" ? "alert" : undefined}
          className={`provider-editor__notice is-${notice.tone}`}
        >
          {notice.text}
        </p>
      ) : null}

      <div className="provider-editor__actions">
        <Button
          loading={savingModels}
          disabled={modelCount === 0}
          onClick={async () => {
            const models = parseModels(modelsText);
            await onSaveModels(provider.id, models);
            setModelsTouched(false);
            setNotice({ tone: "neutral", text: `已保存 ${models.length} 个模型。` });
          }}
        >
          保存模型目录
        </Button>
      </div>
    </section>
  );
}
