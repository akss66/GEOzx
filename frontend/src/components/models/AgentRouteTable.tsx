import { Button, InputNumber } from "antd";
import { useEffect, useMemo, useState } from "react";

import type { ModelProviderDetail, ModelRoute, UpdateModelRouteInput } from "../../types";
import { getProviderStatusMeta } from "./providerStatus";

type RouteDraft = Omit<UpdateModelRouteInput, "primary_provider_id" | "primary_model"> & {
  primary_provider_id: number | null;
  primary_model: string | null;
};

function encodeTarget(providerId: number | null, model: string | null): string {
  return providerId == null || !model ? "" : `${providerId}::${model}`;
}

function decodeTarget(value: string): { providerId: number | null; model: string | null } {
  if (!value) {
    return { providerId: null, model: null };
  }
  const [providerId, ...rest] = value.split("::");
  return {
    providerId: Number(providerId),
    model: rest.join("::") || null,
  };
}

function routeToDraft(route: ModelRoute): RouteDraft {
  return {
    primary_provider_id: route.primary_provider_id,
    primary_model: route.primary_model,
    fallback_provider_id: route.fallback_provider_id,
    fallback_model: route.fallback_model,
    temperature: route.temperature,
    max_tokens: route.max_tokens,
    timeout_seconds: route.timeout_seconds,
  };
}

function routeOptions(providers: ModelProviderDetail[]) {
  return providers.map((provider) => {
    const status = getProviderStatusMeta(provider);
    return {
      label: provider.display_name,
      options: (provider.models ?? []).map((model) => {
        const disabled = status.label !== "可用";
        const suffix = disabled ? `（${status.label}，不可分配）` : "";
        return {
          value: encodeTarget(provider.id, model),
          label: `${provider.display_name} · ${model}${suffix}`,
          disabled,
        };
      }),
    };
  });
}

function routeChanged(route: ModelRoute, draft: RouteDraft): boolean {
  return JSON.stringify(routeToDraft(route)) !== JSON.stringify(draft);
}

function providerName(providers: ModelProviderDetail[], providerId: number | null): string {
  return providers.find((provider) => provider.id === providerId)?.display_name ?? "未知供应商";
}

export default function AgentRouteTable({
  routes,
  providers,
  savingAgentCode,
  onSave,
}: {
  routes: ModelRoute[];
  providers: ModelProviderDetail[];
  savingAgentCode: string | null;
  onSave: (agentCode: string, input: UpdateModelRouteInput) => Promise<void>;
}) {
  const options = useMemo(() => routeOptions(providers), [providers]);
  const [drafts, setDrafts] = useState<Record<string, RouteDraft>>({});

  useEffect(() => {
    setDrafts(Object.fromEntries(routes.map((route) => [route.agent_code, routeToDraft(route)])));
  }, [routes]);

  return (
    <section className="route-table">
      <header className="route-table__header">
        <div>
          <span>AGENT ROUTES</span>
          <h2>专家路由</h2>
          <p>路由目标按供应商分组。未配置、待验证、异常或停用的目标会被禁用并附带原因。</p>
        </div>
      </header>

      <div className="route-table__wrap">
        <table>
          <thead>
            <tr>
              <th>专家</th>
              <th>主路由</th>
              <th>兜底路由</th>
              <th>温度</th>
              <th>Max Tokens</th>
              <th>超时（秒）</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {routes.map((route) => {
              const draft = drafts[route.agent_code] ?? routeToDraft(route);
              const dirty = routeChanged(route, draft);
              const availableTargets = new Set(
                options.flatMap((group) => group.options.map((option) => option.value)),
              );
              const primaryTarget = encodeTarget(
                draft.primary_provider_id,
                draft.primary_model,
              );
              const fallbackTarget = encodeTarget(
                draft.fallback_provider_id,
                draft.fallback_model,
              );
              const primaryInvalid = Boolean(primaryTarget) && !availableTargets.has(primaryTarget);
              const fallbackInvalid = Boolean(fallbackTarget) && !availableTargets.has(fallbackTarget);

              return (
                <tr key={route.agent_code}>
                  <td>
                    <strong>{route.agent_name}</strong>
                    <small>{route.agent_code}</small>
                  </td>
                  <td>
                    <label>
                      <span className="visually-hidden">{`${route.agent_name}主路由`}</span>
                      <select
                        aria-label={`${route.agent_name}主路由`}
                        value={encodeTarget(draft.primary_provider_id, draft.primary_model)}
                        onChange={(event) => {
                          const next = decodeTarget(event.target.value);
                          setDrafts((current) => ({
                            ...current,
                            [route.agent_code]: {
                              ...draft,
                              primary_provider_id: next.providerId,
                              primary_model: next.model ?? "",
                            },
                          }));
                        }}
                      >
                        <option value="" disabled>选择主路由</option>
                        {primaryInvalid ? (
                          <option value={primaryTarget} disabled>
                            {`当前配置已失效：${providerName(providers, draft.primary_provider_id)} · ${draft.primary_model}`}
                          </option>
                        ) : null}
                        {options.map((group) => (
                          <optgroup key={group.label} label={group.label}>
                            {group.options.map((option) => (
                              <option key={option.value} value={option.value} disabled={option.disabled}>
                                {option.label}
                              </option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                      {primaryInvalid ? (
                        <small role="alert">当前主路由已失效，请重新选择并保存。</small>
                      ) : null}
                    </label>
                  </td>
                  <td>
                    <label>
                      <span className="visually-hidden">{`${route.agent_name}兜底路由`}</span>
                      <select
                        aria-label={`${route.agent_name}兜底路由`}
                        value={encodeTarget(draft.fallback_provider_id, draft.fallback_model)}
                        onChange={(event) => {
                          const next = decodeTarget(event.target.value);
                          setDrafts((current) => ({
                            ...current,
                            [route.agent_code]: {
                              ...draft,
                              fallback_provider_id: next.providerId,
                              fallback_model: next.model,
                            },
                          }));
                        }}
                      >
                        <option value="">不设置</option>
                        {fallbackInvalid ? (
                          <option value={fallbackTarget} disabled>
                            {`当前配置已失效：${providerName(providers, draft.fallback_provider_id)} · ${draft.fallback_model}`}
                          </option>
                        ) : null}
                        {options.map((group) => (
                          <optgroup key={group.label} label={group.label}>
                            {group.options.map((option) => (
                              <option key={option.value} value={option.value} disabled={option.disabled}>
                                {option.label}
                              </option>
                            ))}
                          </optgroup>
                        ))}
                      </select>
                      {fallbackInvalid ? (
                        <small role="alert">当前兜底路由已失效，请重新选择并保存。</small>
                      ) : null}
                    </label>
                  </td>
                  <td>
                    <InputNumber
                      aria-label={`${route.agent_name}温度`}
                      min={0}
                      max={2}
                      step={0.1}
                      value={draft.temperature}
                      onChange={(value) => {
                        setDrafts((current) => ({
                          ...current,
                          [route.agent_code]: {
                            ...draft,
                            temperature: Number(value ?? 0),
                          },
                        }));
                      }}
                    />
                  </td>
                  <td>
                    <InputNumber
                      aria-label={`${route.agent_name}Max Tokens`}
                      min={256}
                      max={32768}
                      step={256}
                      value={draft.max_tokens}
                      onChange={(value) => {
                        setDrafts((current) => ({
                          ...current,
                          [route.agent_code]: {
                            ...draft,
                            max_tokens: Number(value ?? 256),
                          },
                        }));
                      }}
                    />
                  </td>
                  <td>
                    <InputNumber
                      aria-label={`${route.agent_name}超时（秒）`}
                      min={5}
                      max={300}
                      step={5}
                      value={draft.timeout_seconds}
                      onChange={(value) => {
                        setDrafts((current) => ({
                          ...current,
                          [route.agent_code]: {
                            ...draft,
                            timeout_seconds: Number(value ?? 5),
                          },
                        }));
                      }}
                    />
                  </td>
                  <td>
                    <Button
                      type="primary"
                      loading={savingAgentCode === route.agent_code}
                      disabled={!dirty || !draft.primary_provider_id || !draft.primary_model}
                      onClick={() => {
                        if (draft.primary_provider_id == null || !draft.primary_model) {
                          return;
                        }
                        void onSave(route.agent_code, {
                          ...draft,
                          primary_provider_id: draft.primary_provider_id,
                          primary_model: draft.primary_model,
                        });
                      }}
                    >
                      保存路由设置
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
