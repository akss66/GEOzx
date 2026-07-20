import { DatabaseOutlined } from "@ant-design/icons";
import { App as AntApp, Skeleton, Tabs } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  ModelProviderDeleteConflictError,
  createModelProvider,
  deleteModelProvider,
  discoverModelProviderModels,
  getModelInfrastructure,
  getModelProvider,
  getModelProviderTemplates,
  listModelCalls,
  listModelProviders,
  removeModelProviderCredential,
  replaceModelProviderCredential,
  updateModelProviderDetails,
  updateModelProviderModels,
  updateModelRoute,
  verifyModelProvider,
} from "../api/modelInfrastructure";
import { presentApiError } from "../api/errors";
import AgentRouteTable from "../components/models/AgentRouteTable";
import ProviderEditor from "../components/models/ProviderEditor";
import ProviderRegistry from "../components/models/ProviderRegistry";
import { OperationalState } from "../components/ui";
import type {
  CreateModelProviderInput,
  ModelCallStatus,
  ModelInfrastructureOverview,
  ModelProviderVerifyResult,
  PatchModelProviderInput,
  UpdateModelRouteInput,
} from "../types";

type SectionKey = "providers" | "routes" | "calls";
type CallFilter = "all" | ModelCallStatus;

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function normalizeAffectedAgentNames(affectedAgents: unknown[]): string[] {
  return affectedAgents
    .map((item) => {
      if (typeof item === "string") {
        return item;
      }
      if (item && typeof item === "object" && "agent_name" in item) {
        const name = item.agent_name;
        return typeof name === "string" ? name : null;
      }
      return null;
    })
    .filter((item): item is string => Boolean(item));
}

async function invalidateProviderQueries(queryClient: ReturnType<typeof useQueryClient>, providerId: number) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["model-infrastructure"] }),
    queryClient.invalidateQueries({ queryKey: ["model-providers"] }),
    queryClient.invalidateQueries({ queryKey: ["model-provider", providerId] }),
  ]);
}

function InfrastructureSummary({ overview }: { overview: ModelInfrastructureOverview }) {
  const { summary } = overview;

  return (
    <dl className="model-infra__summary" aria-label="模型基础设施概览">
      <div>
        <dt>就绪供应商</dt>
        <dd>{summary.providers_ready}<small> / {summary.providers_total}</small></dd>
      </div>
      <div>
        <dt>专家路由</dt>
        <dd>{summary.routes_total}</dd>
      </div>
      <div>
        <dt>24h 调用</dt>
        <dd>{summary.calls_24h}</dd>
      </div>
      <div className={summary.failures_24h > 0 ? "is-alert" : ""}>
        <dt>24h 失败</dt>
        <dd>{summary.failures_24h}</dd>
      </div>
    </dl>
  );
}

function CallLedger({
  filter,
  onFilter,
  loading,
  retrying,
  error,
  onRetry,
  calls,
  total,
}: {
  filter: CallFilter;
  onFilter: (filter: CallFilter) => void;
  loading: boolean;
  retrying: boolean;
  error: ReturnType<typeof presentApiError> | null;
  onRetry: () => void;
  calls: Awaited<ReturnType<typeof listModelCalls>>["items"];
  total: number;
}) {
  return (
    <section className="call-ledger">
      <header className="call-ledger__header">
        <div>
          <span>READ-ONLY LEDGER</span>
          <h2>调用账本</h2>
          <p>最近 {total} 条不可变调用记录。过滤只影响当前视图，不改写历史数据。</p>
        </div>
        <div className="call-ledger__filter" role="radiogroup" aria-label="调用状态">
          {(["all", "ok", "error"] as const).map((value) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={filter === value}
              onClick={() => onFilter(value)}
            >
              {value === "all" ? "全部" : value === "ok" ? "成功" : "失败"}
            </button>
          ))}
        </div>
      </header>

      {loading ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : error ? (
        <OperationalState
          kind="error"
          title="调用账本加载失败"
          description={`${error.message} 当前筛选条件不会被修改。`}
          diagnostic={error.diagnostic}
          actionLabel="重新加载"
          actionLoading={retrying}
          onAction={onRetry}
        />
      ) : calls.length === 0 ? (
        <div className="call-ledger__empty">
          <DatabaseOutlined />
          <strong>没有符合条件的调用记录</strong>
          <span>产生真实模型调用后，这里会显示路由、耗时、Token 与成本。</span>
        </div>
      ) : (
        <div className="call-ledger__table-wrap">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>调用者</th>
                <th>供应商 / 模型</th>
                <th>Token</th>
                <th>耗时</th>
                <th>成本</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {calls.map((call) => (
                <tr key={call.id}>
                  <td>{formatTimestamp(call.created_at)}</td>
                  <td>
                    <strong>{call.agent_name}</strong>
                    <small>{call.agent_code ?? "system"}</small>
                  </td>
                  <td>
                    <strong>{call.provider}</strong>
                    <small>{call.model}</small>
                  </td>
                  <td>{call.total_tokens.toLocaleString()}</td>
                  <td>{call.latency_ms.toLocaleString()} ms</td>
                  <td>${call.cost_usd.toFixed(4)}</td>
                  <td>
                    <span className={`call-ledger__state is-${call.status}`}>{call.status === "ok" ? "成功" : "失败"}</span>
                    {call.error_summary ? (
                      <small className="call-ledger__error">{call.error_summary}</small>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function ModelInfrastructure() {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const [section, setSection] = useState<SectionKey>("providers");
  const [selectedProviderId, setSelectedProviderId] = useState<number | null>(null);
  const [callFilter, setCallFilter] = useState<CallFilter>("all");
  const [deleteConflicts, setDeleteConflicts] = useState<Record<number, string[]>>({});
  const [latestVerification, setLatestVerification] = useState<Record<number, ModelProviderVerifyResult>>({});

  const overviewQuery = useQuery({
    queryKey: ["model-infrastructure"],
    queryFn: getModelInfrastructure,
  });
  const templatesQuery = useQuery({
    queryKey: ["model-provider-templates"],
    queryFn: getModelProviderTemplates,
  });
  const providersQuery = useQuery({
    queryKey: ["model-providers"],
    queryFn: listModelProviders,
  });

  const providers = useMemo(() => providersQuery.data ?? [], [providersQuery.data]);

  useEffect(() => {
    if (!providers.length) {
      if (selectedProviderId !== null) {
        setSelectedProviderId(null);
      }
      return;
    }
    if (selectedProviderId == null || !providers.some((provider) => provider.id === selectedProviderId)) {
      setSelectedProviderId(providers[0].id);
    }
  }, [providers, selectedProviderId]);

  const selectedProviderQuery = useQuery({
    queryKey: ["model-provider", selectedProviderId],
    queryFn: () => getModelProvider(selectedProviderId as number),
    enabled: selectedProviderId != null,
  });

  const selectedProvider = selectedProviderQuery.data
    ?? providers.find((provider) => provider.id === selectedProviderId)
    ?? null;

  const createProviderMutation = useMutation({
    mutationFn: (input: CreateModelProviderInput) => createModelProvider(input),
    onSuccess: async (provider) => {
      setSelectedProviderId(provider.id);
      setDeleteConflicts((current) => {
        const next = { ...current };
        delete next[provider.id];
        return next;
      });
      queryClient.setQueryData(["model-provider", provider.id], provider);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-providers"] }),
        queryClient.invalidateQueries({ queryKey: ["model-infrastructure"] }),
      ]);
      message.success("供应商已创建。");
    },
    onError: () => message.error("创建供应商失败，请检查模板或端点地址。"),
  });

  const patchProviderMutation = useMutation({
    mutationFn: ({ providerId, input }: { providerId: number; input: PatchModelProviderInput }) =>
      updateModelProviderDetails(providerId, input),
    onSuccess: async (provider) => {
      queryClient.setQueryData(["model-provider", provider.id], provider);
      await invalidateProviderQueries(queryClient, provider.id);
      message.success("供应商设置已保存。");
    },
    onError: () => message.error("保存供应商失败，请稍后重试。"),
  });

  const replaceCredentialMutation = useMutation({
    mutationFn: ({ providerId, apiKey }: { providerId: number; apiKey: string }) =>
      replaceModelProviderCredential(providerId, apiKey),
    onSuccess: async (provider) => {
      queryClient.setQueryData(["model-provider", provider.id], provider);
      await invalidateProviderQueries(queryClient, provider.id);
      message.success("密钥已替换，原文不会回传到前端。");
    },
    onError: () => message.error("替换密钥失败，请稍后再试。"),
  });

  const removeCredentialMutation = useMutation({
    mutationFn: (providerId: number) => removeModelProviderCredential(providerId),
    onSuccess: async (provider) => {
      queryClient.setQueryData(["model-provider", provider.id], provider);
      await invalidateProviderQueries(queryClient, provider.id);
      message.success("密钥已移除。");
    },
    onError: () => message.error("移除密钥失败，请稍后再试。"),
  });

  const verifyMutation = useMutation({
    mutationFn: (providerId: number) => verifyModelProvider(providerId),
    onSuccess: async (result) => {
      setLatestVerification((current) => ({ ...current, [result.provider_id]: result }));
      await invalidateProviderQueries(queryClient, result.provider_id);
      if (result.verification_status === "verified") {
        message.success("连接验证成功。");
      } else {
        message.error("连接验证未通过，请根据错误摘要检查配置。");
      }
    },
    onError: () => message.error("验证连接失败，请检查密钥和端点地址。"),
  });

  const discoverMutation = useMutation({
    mutationFn: (providerId: number) => discoverModelProviderModels(providerId),
    onSuccess: async (result) => {
      await invalidateProviderQueries(queryClient, result.provider_id);
      if (!result.error_code) {
        message.success("模型目录已刷新。");
      }
    },
    onError: () => message.error("自动发现模型失败，请保留现有模型目录后重试。"),
  });

  const saveModelsMutation = useMutation({
    mutationFn: ({ providerId, models }: { providerId: number; models: string[] }) =>
      updateModelProviderModels(providerId, models),
    onSuccess: async (provider) => {
      queryClient.setQueryData(["model-provider", provider.id], provider);
      await invalidateProviderQueries(queryClient, provider.id);
      message.success("模型目录已保存。");
    },
    onError: () => message.error("保存模型目录失败，请稍后再试。"),
  });

  const deleteProviderMutation = useMutation({
    mutationFn: (providerId: number) => deleteModelProvider(providerId),
    onSuccess: async (_, providerId) => {
      setDeleteConflicts((current) => {
        const next = { ...current };
        delete next[providerId];
        return next;
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-providers"] }),
        queryClient.invalidateQueries({ queryKey: ["model-infrastructure"] }),
      ]);
      message.success("供应商已删除。");
    },
    onError: (error, providerId) => {
      if (error instanceof ModelProviderDeleteConflictError) {
        setDeleteConflicts((current) => ({
          ...current,
          [providerId]: normalizeAffectedAgentNames(error.affectedAgents as unknown[]),
        }));
        return;
      }
      message.error("删除供应商失败，请稍后再试。");
    },
  });

  const routeMutation = useMutation({
    mutationFn: ({ agentCode, input }: { agentCode: string; input: UpdateModelRouteInput }) =>
      updateModelRoute(agentCode, input),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["model-infrastructure"] });
      message.success(`${saved.agent_name} 路由已保存。`);
    },
    onError: () => message.error("保存路由设置失败，请检查供应商状态和模型目录。"),
  });

  const callsQuery = useQuery({
    queryKey: ["model-infrastructure-calls", callFilter],
    queryFn: () => listModelCalls(callFilter === "all" ? null : callFilter, 50),
    enabled: section === "calls",
  });

  const pageFailure = useMemo(() => {
    const error = overviewQuery.error ?? templatesQuery.error ?? providersQuery.error;
    return error ? presentApiError(error, "模型基础设施暂时不可用。") : null;
  }, [overviewQuery.error, providersQuery.error, templatesQuery.error]);

  if (pageFailure) {
    return (
      <main className="model-infra">
        <OperationalState
          kind="error"
          title="模型基础设施加载失败"
          description={pageFailure.message}
          diagnostic={pageFailure.diagnostic}
          actionLabel="重新加载"
          onAction={() => {
            void overviewQuery.refetch();
            void templatesQuery.refetch();
            void providersQuery.refetch();
          }}
        />
      </main>
    );
  }

  if (overviewQuery.isLoading || providersQuery.isLoading || templatesQuery.isLoading || !overviewQuery.data) {
    return (
      <div className="model-infra-loading">
        <Skeleton active paragraph={{ rows: 14 }} />
      </div>
    );
  }

  const selectedProviderFailure = selectedProviderQuery.error
    ? presentApiError(selectedProviderQuery.error, "供应商详情暂时不可用。")
    : null;

  return (
    <main className="model-infra">
      <header className="model-infra__masthead">
        <div>
          <span>模型运行</span>
          <h1>模型基础设施</h1>
          <p>桌面工作台用于管理模型供应商、专家路由和只读调用账本。完整密钥始终留在服务器侧。</p>
        </div>
        <InfrastructureSummary overview={overviewQuery.data} />
      </header>

      <div className="model-infra__tabs">
        <Tabs
          activeKey={section}
          onChange={(key) => setSection(key as SectionKey)}
          items={[
            { key: "providers", label: "供应商" },
            { key: "routes", label: "专家路由" },
            { key: "calls", label: "调用账本" },
          ]}
        />
      </div>

      {section === "providers" ? (
        <div className="model-infra__workspace">
          <ProviderRegistry
            providers={providers}
            templates={templatesQuery.data ?? []}
            selectedProviderId={selectedProviderId}
            creatingTemplateCode={
              createProviderMutation.isPending && createProviderMutation.variables
                && "template_code" in createProviderMutation.variables
                ? createProviderMutation.variables.template_code ?? null
                : null
            }
            creatingCustom={
              createProviderMutation.isPending
              && Boolean(createProviderMutation.variables && "provider_type" in createProviderMutation.variables)
            }
            onSelect={(providerId) => {
              setSelectedProviderId(providerId);
              setDeleteConflicts((current) => {
                const next = { ...current };
                delete next[providerId];
                return next;
              });
            }}
            onCreateTemplate={(templateCode) => {
              createProviderMutation.mutate({
                template_code: templateCode,
                enabled: true,
              });
            }}
            onCreateCustom={(displayName, baseUrl) => {
              createProviderMutation.mutate({
                provider_type: "custom_openai",
                display_name: displayName,
                base_url: baseUrl,
                enabled: true,
              });
            }}
          />

          <div className="model-infra__detail">
            {selectedProviderId && selectedProviderQuery.isLoading && !selectedProvider ? (
              <div className="model-infra__detail-skeleton">
                <Skeleton active paragraph={{ rows: 10 }} />
              </div>
            ) : selectedProviderFailure ? (
              <OperationalState
                kind="error"
                title="供应商详情加载失败"
                description={selectedProviderFailure.message}
                diagnostic={selectedProviderFailure.diagnostic}
                actionLabel="重新加载"
                onAction={() => void selectedProviderQuery.refetch()}
              />
            ) : selectedProvider ? (
              <ProviderEditor
                provider={selectedProvider}
                saving={patchProviderMutation.isPending}
                deleting={deleteProviderMutation.isPending}
                replacingCredential={replaceCredentialMutation.isPending}
                removingCredential={removeCredentialMutation.isPending}
                verifying={verifyMutation.isPending}
                discovering={discoverMutation.isPending}
                savingModels={saveModelsMutation.isPending}
                deleteConflictNames={deleteConflicts[selectedProvider.id] ?? []}
                latestVerification={latestVerification[selectedProvider.id] ?? null}
                onSave={async (providerId, input) => {
                  await patchProviderMutation.mutateAsync({ providerId, input });
                }}
                onDelete={async (providerId) => {
                  try {
                    await deleteProviderMutation.mutateAsync(providerId);
                  } catch {
                    // Inline conflict guidance is handled by mutation onError.
                  }
                }}
                onReplaceCredential={async (providerId, apiKey) => {
                  await replaceCredentialMutation.mutateAsync({ providerId, apiKey });
                }}
                onRemoveCredential={async (providerId) => {
                  await removeCredentialMutation.mutateAsync(providerId);
                }}
                onVerify={(providerId) => verifyMutation.mutateAsync(providerId)}
                onDiscover={(providerId) => discoverMutation.mutateAsync(providerId)}
                onSaveModels={async (providerId, models) => {
                  await saveModelsMutation.mutateAsync({ providerId, models });
                }}
              />
            ) : (
              <OperationalState
                kind="empty"
                title="还没有供应商"
                description="先从左侧模板添加内置供应商，或创建一个自定义 OpenAI 兼容端点。"
              />
            )}
          </div>
        </div>
      ) : null}

      {section === "routes" ? (
        <div className="model-infra__routes">
          <AgentRouteTable
            routes={overviewQuery.data.routes}
            providers={providers}
            savingAgentCode={routeMutation.isPending && routeMutation.variables
              ? routeMutation.variables.agentCode
              : null}
            onSave={async (agentCode, input) => {
              await routeMutation.mutateAsync({ agentCode, input });
            }}
          />
        </div>
      ) : null}

      {section === "calls" ? (
        <CallLedger
          filter={callFilter}
          onFilter={setCallFilter}
          loading={callsQuery.isLoading}
          retrying={callsQuery.isFetching}
          error={callsQuery.isError
            ? presentApiError(callsQuery.error, "调用账本暂时不可用，请稍后重试。")
            : null}
          onRetry={() => void callsQuery.refetch()}
          calls={callsQuery.data?.items ?? []}
          total={callsQuery.data?.total ?? 0}
        />
      ) : null}
    </main>
  );
}
