import { Button, Input } from "antd";
import { useEffect, useState } from "react";

import type { ModelProviderDetail } from "../../types";

export default function ProviderCredentialPanel({
  provider,
  replacing,
  removing,
  onReplace,
  onRemove,
}: {
  provider: ModelProviderDetail;
  replacing: boolean;
  removing: boolean;
  onReplace: (providerId: number, apiKey: string) => Promise<void>;
  onRemove: (providerId: number) => Promise<void>;
}) {
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    setApiKey("");
  }, [provider.id]);

  return (
    <section className="provider-editor__section">
      <header>
        <h3>写入式凭证</h3>
        <p>密钥只允许写入或替换。当前页面只显示配置状态与尾号，不显示原文。</p>
      </header>

      <div className="provider-credential">
        <div className="provider-credential__state">
          <strong>{provider.key_configured ? `已配置 · 尾号 ${provider.key_last_four ?? "已写入"}` : "未配置"}</strong>
          <small>{provider.credential_source === "environment" ? "服务器环境变量" : "组织加密密钥"}</small>
        </div>

        <label className="provider-editor__field">
          <span>API Key</span>
          <Input
            aria-label="API Key"
            autoComplete="new-password"
            type="password"
            value={apiKey}
            placeholder="输入新密钥后点击替换"
            onChange={(event) => setApiKey(event.target.value)}
          />
        </label>

        <div className="provider-editor__actions">
          <Button
            loading={removing}
            disabled={!provider.key_configured}
            onClick={async () => {
              await onRemove(provider.id);
              setApiKey("");
            }}
          >
            移除密钥
          </Button>
          <Button
            type="primary"
            loading={replacing}
            disabled={!apiKey.trim()}
            onClick={async () => {
              const nextKey = apiKey.trim();
              await onReplace(provider.id, nextKey);
              setApiKey("");
            }}
          >
            替换密钥
          </Button>
        </div>
      </div>
    </section>
  );
}
