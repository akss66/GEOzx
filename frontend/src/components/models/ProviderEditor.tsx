import { Button, Input, Switch } from "antd";
import { useEffect, useMemo, useState } from "react";

import type {
  ModelProviderDetail,
  ModelProviderDiscoveryResult,
  ModelProviderVerifyResult,
  PatchModelProviderInput,
} from "../../types";
import ProviderCredentialPanel from "./ProviderCredentialPanel";
import ProviderVerification from "./ProviderVerification";
import { getProviderStatusMeta } from "./providerStatus";

function buildPatch(
  provider: ModelProviderDetail,
  displayName: string,
  baseUrl: string,
  enabled: boolean,
): PatchModelProviderInput | null {
  const nextDisplayName = displayName.trim();
  const nextBaseUrl = baseUrl.trim();
  const patch: Partial<PatchModelProviderInput> = {};

  if (nextDisplayName !== provider.display_name) {
    patch.display_name = nextDisplayName;
  }
  if ((provider.base_url ?? "") !== nextBaseUrl) {
    patch.base_url = nextBaseUrl;
  }
  if (provider.enabled !== enabled) {
    patch.enabled = enabled;
  }

  return Object.keys(patch).length ? patch as PatchModelProviderInput : null;
}

export default function ProviderEditor({
  provider,
  saving,
  deleting,
  replacingCredential,
  removingCredential,
  verifying,
  discovering,
  savingModels,
  deleteConflictNames,
  latestVerification,
  onSave,
  onDelete,
  onReplaceCredential,
  onRemoveCredential,
  onVerify,
  onDiscover,
  onSaveModels,
}: {
  provider: ModelProviderDetail;
  saving: boolean;
  deleting: boolean;
  replacingCredential: boolean;
  removingCredential: boolean;
  verifying: boolean;
  discovering: boolean;
  savingModels: boolean;
  deleteConflictNames: string[];
  latestVerification: ModelProviderVerifyResult | null;
  onSave: (providerId: number, input: PatchModelProviderInput) => Promise<void>;
  onDelete: (providerId: number) => Promise<void>;
  onReplaceCredential: (providerId: number, apiKey: string) => Promise<void>;
  onRemoveCredential: (providerId: number) => Promise<void>;
  onVerify: (providerId: number) => Promise<ModelProviderVerifyResult>;
  onDiscover: (providerId: number) => Promise<ModelProviderDiscoveryResult>;
  onSaveModels: (providerId: number, models: string[]) => Promise<void>;
}) {
  const [displayName, setDisplayName] = useState(provider.display_name);
  const [baseUrl, setBaseUrl] = useState(provider.base_url ?? "");
  const [enabled, setEnabled] = useState(provider.enabled);
  const status = getProviderStatusMeta(provider);

  useEffect(() => {
    setDisplayName(provider.display_name);
    setBaseUrl(provider.base_url ?? "");
    setEnabled(provider.enabled);
  }, [provider.id, provider.display_name, provider.base_url, provider.enabled]);

  const patch = useMemo(
    () => buildPatch(provider, displayName, baseUrl, enabled),
    [baseUrl, displayName, enabled, provider],
  );

  return (
    <section className="provider-editor">
      <header className="provider-editor__header">
        <div>
          <span>DETAIL EDITOR</span>
          <h2>{provider.display_name}</h2>
          <p>{provider.provider_type === "custom_openai" ? "自定义兼容端点" : `内置模板 · ${provider.template_code ?? provider.code}`}</p>
        </div>
        <div className="provider-editor__headline">
          <span className={`provider-status provider-status--${status.tone}`}>{status.label}</span>
          <small>{status.reason}</small>
        </div>
      </header>

      <section className="provider-editor__section">
        <header>
          <h3>基础配置</h3>
          <p>启停、名称和端点地址都按组织级配置管理。变更后需要重新验证。</p>
        </header>

        <label className="provider-editor__switch">
          <div>
            <strong>启用提供商</strong>
            <small>停用后不会参与新的模型路由，但不会改写历史调用账本。</small>
          </div>
          <Switch checked={enabled} onChange={setEnabled} />
        </label>

        <label className="provider-editor__field">
          <span>显示名称</span>
          <Input
            aria-label="显示名称"
            maxLength={80}
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </label>

        <label className="provider-editor__field">
          <span>基础地址</span>
          <Input
            aria-label="基础地址"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
          />
        </label>

        <div className="provider-editor__actions">
          <Button
            type="primary"
            loading={saving}
            disabled={!patch}
            onClick={async () => {
              if (!patch) return;
              await onSave(provider.id, patch);
            }}
          >
            保存提供商
          </Button>
          <Button danger loading={deleting} onClick={() => onDelete(provider.id)}>
            删除提供商
          </Button>
        </div>

        {deleteConflictNames.length ? (
          <div className="provider-editor__conflict" role="status">
            <strong>以下专家仍在使用该提供商，请先迁移路由。</strong>
            <ul>
              {deleteConflictNames.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      <ProviderCredentialPanel
        provider={provider}
        replacing={replacingCredential}
        removing={removingCredential}
        onReplace={onReplaceCredential}
        onRemove={onRemoveCredential}
      />

      <ProviderVerification
        provider={provider}
        latestVerification={latestVerification}
        verifying={verifying}
        discovering={discovering}
        savingModels={savingModels}
        onVerify={onVerify}
        onDiscover={onDiscover}
        onSaveModels={onSaveModels}
      />
    </section>
  );
}
