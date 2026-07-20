import { ApiOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Empty, Input } from "antd";
import { useEffect, useMemo, useState } from "react";

import type { ModelProviderDetail, ModelProviderTemplate } from "../../types";
import { getProviderStatusMeta } from "./providerStatus";

function providerSubtitle(provider: ModelProviderDetail): string {
  if (provider.provider_type === "custom_openai") {
    return "自定义 OpenAI 兼容端点";
  }
  return provider.template_code ? `内置模板 · ${provider.template_code}` : "内置供应商";
}

export default function ProviderRegistry({
  providers,
  templates,
  selectedProviderId,
  creatingTemplateCode,
  creatingCustom,
  onSelect,
  onCreateTemplate,
  onCreateCustom,
}: {
  providers: ModelProviderDetail[];
  templates: ModelProviderTemplate[];
  selectedProviderId: number | null;
  creatingTemplateCode: string | null;
  creatingCustom: boolean;
  onSelect: (providerId: number) => void;
  onCreateTemplate: (templateCode: string) => void;
  onCreateCustom: (displayName: string, baseUrl: string) => void;
}) {
  const [customOpen, setCustomOpen] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

  useEffect(() => {
    if (!creatingCustom) {
      setDisplayName("");
      setBaseUrl("");
    }
  }, [creatingCustom]);

  const sortedTemplates = useMemo(
    () => [...templates].sort((left, right) => left.display_name.localeCompare(right.display_name, "zh-CN")),
    [templates],
  );

  return (
    <section className="provider-registry">
      <header className="provider-registry__header">
        <div>
          <span>PROVIDER REGISTRY</span>
          <h2>供应商目录</h2>
          <p>组织级模型供应商列表。目录只显示安全元数据，不渲染任何完整密钥。</p>
        </div>
        <Button
          aria-label="新建兼容端点"
          icon={<PlusOutlined />}
          onClick={() => setCustomOpen((current) => !current)}
        >
          新建兼容端点
        </Button>
      </header>

      <div className="provider-registry__template-strip" aria-label="内置供应商模板">
        {sortedTemplates.map((template) => (
          <Button
            key={template.code}
            loading={creatingTemplateCode === template.code}
            onClick={() => onCreateTemplate(template.code)}
          >
            {`添加 ${template.display_name}`}
          </Button>
        ))}
      </div>

      {customOpen ? (
        <form
          className="provider-registry__custom-form"
          onSubmit={(event) => {
            event.preventDefault();
            onCreateCustom(displayName.trim(), baseUrl.trim());
          }}
        >
          <label>
            <span>提供商名称</span>
            <Input
              aria-label="提供商名称"
              value={displayName}
              maxLength={80}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </label>
          <label>
            <span>兼容端点地址</span>
            <Input
              aria-label="兼容端点地址"
              value={baseUrl}
              placeholder="https://example.com/v1"
              onChange={(event) => setBaseUrl(event.target.value)}
            />
          </label>
          <div className="provider-registry__custom-actions">
            <Button type="text" onClick={() => setCustomOpen(false)}>
              取消
            </Button>
            <Button
              htmlType="submit"
              type="primary"
              loading={creatingCustom}
              disabled={!displayName.trim() || !baseUrl.trim()}
            >
              创建自定义提供商
            </Button>
          </div>
        </form>
      ) : null}

      {providers.length === 0 ? (
        <div className="provider-registry__empty">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="还没有组织级模型供应商，请先从模板添加或创建兼容端点。"
          />
        </div>
      ) : (
        <div className="provider-registry__list" role="list" aria-label="供应商目录">
          {providers.map((provider) => {
            const status = getProviderStatusMeta(provider);
            const isActive = provider.id === selectedProviderId;

            return (
              <div key={provider.id} role="listitem">
                <button
                  type="button"
                  className={`provider-registry__row${isActive ? " is-selected" : ""}`}
                  aria-label={`编辑 ${provider.display_name}`}
                  onClick={() => onSelect(provider.id)}
                >
                  <span className="provider-registry__icon" aria-hidden="true">
                    <ApiOutlined />
                  </span>
                  <div className="provider-registry__copy">
                    <div className="provider-registry__title">
                      <strong>{provider.display_name}</strong>
                      <em>{providerSubtitle(provider)}</em>
                    </div>
                    <small>{provider.base_url ?? "由服务器环境提供内置端点"}</small>
                  </div>
                  <div className="provider-registry__meta">
                    <span className={`provider-status provider-status--${status.tone}`}>{status.label}</span>
                    <small>{provider.key_configured ? `尾号 ${provider.key_last_four ?? "已配置"}` : "未写入密钥"}</small>
                  </div>
                </button>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
