import {
  CloudUploadOutlined,
  ReloadOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Skeleton } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import {
  commitAccountDataImportBatch,
  downloadAccountDataArtifact,
  getAccountDataImportBatch,
  getAccountDataStatus,
  listAccountDataImports,
  resolveAccountDataImportRow,
  revokeAccountDataImportBatch,
  uploadAccountDataImport,
  type AccountDataImportArtifact,
  type AccountDataImportBatch,
  type AccountDataImportRow,
} from "../api/accountData";
import { presentApiError } from "../api/errors";
import { getBatchStatusLabel } from "../components/account-data/statusMeta";
import { getWorkspaceContext } from "../api/shell";
import { PlatformTag, OperationalState, PageHeader } from "../components/ui";
import { FileImportFlow } from "../components/account-data/FileImportFlow";
import { ImportBatchHistory } from "../components/account-data/ImportBatchHistory";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import "../styles/account-data-center.css";

type FlowFeedback = {
  tone: "error" | "success";
  title: string;
  description: string;
} | null;

const COVERAGE_LABEL: Record<string, string> = {
  account_metrics: "账号概览",
  content_metrics: "作品表现",
  benchmarks: "对标基准",
};

const STATUS_LABEL = {
  active: "正常",
  inactive: "停用",
  banned: "封禁",
} as const;

function formatDateTime(value: string | null) {
  if (!value) return "尚未确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "尚未确认";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function countBlockingRows(batch: AccountDataImportBatch | null) {
  if (!batch) return 0;
  return batch.rows.filter((row) => row.status === "invalid" || row.status === "needs_resolution")
    .length;
}

function rowNeedsWork(row: AccountDataImportRow) {
  return row.status === "invalid" || row.status === "needs_resolution";
}

function presentImportError(error: unknown): FlowFeedback {
  const detail = readErrorDetail(error);
  if (detail === "Unknown or unsupported template") {
    return {
      tone: "error",
      title: "无法识别导入模板",
      description: "请改用已支持的抖音导出模板后重新上传。",
    };
  }
  if (detail) {
    return {
      tone: "error",
      title: "导入未完成",
      description: detail,
    };
  }
  const failure = presentApiError(error, "导入文件暂时无法处理，请稍后重试。");
  return {
    tone: "error",
    title: "导入未完成",
    description: failure.message,
  };
}

function presentRevokeError(error: unknown) {
  const detail = readErrorDetail(error);
  if (detail) return detail;
  return presentApiError(error, "撤销当前批次失败，请稍后重试。").message;
}

function presentDownloadError(error: unknown) {
  const detail = readErrorDetail(error);
  if (detail) return detail;
  return presentApiError(error, "下载原文件失败，请稍后重试。").message;
}

function readErrorDetail(error: unknown) {
  if (!error || typeof error !== "object") return null;
  const response = (error as { response?: { data?: unknown } }).response;
  const detail = (response?.data as { detail?: unknown } | undefined)?.detail;
  return typeof detail === "string" ? detail : null;
}

export default function AccountDataCenter() {
  const params = useParams<{ accountId: string }>();
  const routeAccountId = Number(params.accountId);

  if (!Number.isFinite(routeAccountId)) {
    return (
      <OperationalState
        kind="blocked"
        title="账号地址无效"
        description="当前链接没有有效的账号编号，请返回账号矩阵重新选择。"
      />
    );
  }

  return <AccountDataCenterWorkspace key={routeAccountId} routeAccountId={routeAccountId} />;
}

function AccountDataCenterWorkspace({ routeAccountId }: { routeAccountId: number }) {
  const workspace = useCurrentWorkspace();
  const queryClient = useQueryClient();
  const [activeBatchId, setActiveBatchId] = useState<number | null>(null);
  const [draftBatch, setDraftBatch] = useState<AccountDataImportBatch | null>(null);
  const [flowFeedback, setFlowFeedback] = useState<FlowFeedback>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const contextQuery = useQuery({
    queryKey: ["workspace-context", workspace.clientId, workspace.projectId],
    queryFn: () => getWorkspaceContext(workspace.clientId, workspace.projectId),
  });

  const account = useMemo(() => {
    if (!Number.isFinite(routeAccountId) || !contextQuery.data) return null;
    return contextQuery.data.accounts.find((item) => item.id === routeAccountId) ?? null;
  }, [contextQuery.data, routeAccountId]);

  useEffect(() => {
    if (!account) return;
    if (workspace.platform !== account.platform) workspace.setPlatform(account.platform);
    if (workspace.accountId !== account.id) workspace.setAccountId(account.id);
  }, [account, workspace]);

  const statusQuery = useQuery({
    enabled: Boolean(account),
    queryKey: ["account-data-status", routeAccountId],
    queryFn: () => getAccountDataStatus(routeAccountId),
  });

  const historyQuery = useQuery({
    enabled: Boolean(account),
    queryKey: ["account-data-history", routeAccountId],
    queryFn: () => listAccountDataImports(routeAccountId),
  });

  const detailQueries = useQueries({
    queries: (historyQuery.data?.items ?? []).map((item) => ({
      queryKey: ["account-data-import", routeAccountId, item.id],
      queryFn: () => getAccountDataImportBatch(routeAccountId, item.id),
      enabled: Boolean(account),
      retry: false,
    })),
  });

  const detailsById = useMemo(() => {
    const map = new Map<number, AccountDataImportBatch>();
    detailQueries.forEach((query) => {
      if (query.data) map.set(query.data.id, query.data);
    });
    if (draftBatch) map.set(draftBatch.id, draftBatch);
    return map;
  }, [detailQueries, draftBatch]);

  useEffect(() => {
    if (activeBatchId != null || !historyQuery.data?.items.length) return;
    const next = historyQuery.data.items.find((item) => item.status === "preview_ready")
      ?? historyQuery.data.items[0];
    setActiveBatchId(next.id);
  }, [activeBatchId, historyQuery.data]);

  const activeBatch = useMemo(() => {
    if (draftBatch && (activeBatchId == null || draftBatch.id === activeBatchId)) {
      return draftBatch;
    }
    if (activeBatchId == null) return draftBatch;
    return detailsById.get(activeBatchId) ?? draftBatch;
  }, [activeBatchId, detailsById, draftBatch]);

  const blockingRowCount = countBlockingRows(activeBatch);
  const canCommit = Boolean(
    activeBatch
    && activeBatch.status === "preview_ready"
    && activeBatch.rows.length > 0
    && activeBatch.rows.every((row) => !rowNeedsWork(row)),
  );

  const conflictCount = useMemo(() => {
    let total = 0;
    detailsById.forEach((batch) => {
      total += batch.conflicts.length;
      total += batch.rows.filter((row) => row.status === "needs_resolution").length;
    });
    return total;
  }, [detailsById]);

  async function refreshBatchWorkspace(batchId: number) {
    const [batch] = await Promise.all([
      queryClient.fetchQuery({
        queryKey: ["account-data-import", routeAccountId, batchId],
        queryFn: () => getAccountDataImportBatch(routeAccountId, batchId),
      }),
      historyQuery.refetch(),
      statusQuery.refetch(),
    ]);
    return batch;
  }

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadAccountDataImport(routeAccountId, file),
    onSuccess: (batch) => {
      if (!isMountedRef.current) return;
      setFlowFeedback({
        tone: "success",
        title: "导入预览已生成",
        description: "请先核对行级数据，确认无误后再正式写入当前账号。",
      });
      setHistoryError(null);
      setActiveBatchId(batch.id);
      setDraftBatch(batch);
      queryClient.setQueryData(["account-data-import", routeAccountId, batch.id], batch);
      void historyQuery.refetch();
    },
    onError: (error) => {
      if (!isMountedRef.current) return;
      setFlowFeedback(presentImportError(error));
    },
  });

  const resolveMutation = useMutation({
    mutationFn: ({
      batchId,
      rowNumber,
      selectedContentId,
    }: {
      batchId: number;
      rowNumber: number;
      selectedContentId: number;
    }) =>
      resolveAccountDataImportRow(
        routeAccountId,
        batchId,
        rowNumber,
        selectedContentId,
      ),
    onSuccess: async (_row, variables) => {
      try {
        const batch = await refreshBatchWorkspace(variables.batchId);
        if (!isMountedRef.current) return;
        setHistoryError(null);
        setActiveBatchId(variables.batchId);
        setDraftBatch(batch);
      } catch (error) {
        if (!isMountedRef.current) return;
        setFlowFeedback(presentImportError(error));
      }
    },
    onError: (error) => {
      if (!isMountedRef.current) return;
      setFlowFeedback(presentImportError(error));
    },
  });

  const commitMutation = useMutation({
    mutationFn: (batchId: number) => commitAccountDataImportBatch(routeAccountId, batchId),
    onSuccess: async (batch) => {
      if (!isMountedRef.current) return;
      setDraftBatch(batch);
      setFlowFeedback({
        tone: "success",
        title: "导入已确认",
        description: "当前批次已经写入账号数据中心，头部状态与历史记录已刷新。",
      });
      setHistoryError(null);
      queryClient.setQueryData(["account-data-import", routeAccountId, batch.id], batch);
      await Promise.all([historyQuery.refetch(), statusQuery.refetch()]);
    },
    onError: (error) => {
      if (!isMountedRef.current) return;
      setFlowFeedback(presentImportError(error));
    },
  });

  const revokeMutation = useMutation({
    mutationFn: (batchId: number) => revokeAccountDataImportBatch(routeAccountId, batchId),
    onSuccess: async (batch) => {
      if (!isMountedRef.current) return;
      setHistoryError(null);
      setDraftBatch((current) => current?.id === batch.id ? batch : current);
      queryClient.setQueryData(["account-data-import", routeAccountId, batch.id], batch);
      await Promise.all([historyQuery.refetch(), statusQuery.refetch()]);
    },
    onError: (error) => {
      if (!isMountedRef.current) return;
      setHistoryError(presentRevokeError(error));
    },
  });

  if (contextQuery.isLoading) {
    return (
      <div className="account-data-center account-data-center--loading" aria-label="账号数据中心加载中">
        <Skeleton active paragraph={{ rows: 2 }} />
        <div className="account-data-skeleton-grid">
          <Skeleton active paragraph={{ rows: 8 }} />
          <Skeleton active paragraph={{ rows: 8 }} />
        </div>
      </div>
    );
  }

  if (contextQuery.isError) {
    const failure = presentApiError(contextQuery.error, "当前账号上下文暂时不可用。");
    return (
      <OperationalState
        kind="error"
        title="账号上下文加载失败"
        description={failure.message}
        diagnostic={failure.diagnostic}
        actionLabel="重新加载"
        onAction={() => void contextQuery.refetch()}
      />
    );
  }

  if (!account) {
    return (
      <OperationalState
        kind="blocked"
        title="找不到当前账号的数据中心"
        description="系统不会自动切换到其他账号。"
        actionLabel="返回后重试"
        onAction={() => void contextQuery.refetch()}
      />
    );
  }

  if (statusQuery.isError || historyQuery.isError) {
    const failure = presentApiError(
      statusQuery.error ?? historyQuery.error,
      "当前账号的数据中心暂时无法打开。",
    );
    return (
      <OperationalState
        kind="error"
        title="数据中心加载失败"
        description={failure.message}
        diagnostic={failure.diagnostic}
        actionLabel="重新加载"
        onAction={() => {
          void Promise.all([statusQuery.refetch(), historyQuery.refetch()]);
        }}
      />
    );
  }

  return (
    <div className="account-data-center">
      <PageHeader
        title="账号数据中心"
        subtitle="围绕当前账号完成导出导入、冲突确认、来源留痕与撤销，不在这里静默切换账号。"
        extra={(
          <Button
            icon={<ReloadOutlined />}
            loading={statusQuery.isFetching || historyQuery.isFetching}
            onClick={() => {
              void Promise.all([statusQuery.refetch(), historyQuery.refetch()]);
            }}
          >
            重新同步页面
          </Button>
        )}
      />

      <section className="account-data-hero">
        <div className="account-data-account">
          {account.avatar_url ? (
            <img src={account.avatar_url} alt="" className="account-data-avatar" />
          ) : (
            <span className="account-data-avatar account-data-avatar--fallback">
              {account.nickname.slice(0, 1)}
            </span>
          )}
          <div>
            <span>当前账号</span>
            <strong>{account.nickname}</strong>
            <div className="account-data-account-meta">
              <PlatformTag platform={account.platform} />
              <span>{STATUS_LABEL[account.status]}</span>
            </div>
          </div>
        </div>
        <div className="account-data-hero-metrics">
          <article>
            <span>最近确认时间</span>
            <strong>{formatDateTime(statusQuery.data?.latest_confirmed_at ?? null)}</strong>
          </article>
          <article>
            <span>来源覆盖</span>
            <strong>{Object.values(statusQuery.data?.coverage ?? {}).filter((item) => item !== "missing").length} / 3</strong>
          </article>
          <article>
            <span>冲突数量</span>
            <strong>{conflictCount}</strong>
          </article>
          <article>
            <span>当前批次</span>
            <strong>{activeBatch ? getBatchStatusLabel(activeBatch.status) : "未开始"}</strong>
          </article>
        </div>
      </section>

      <section className="account-data-coverage">
        {(Object.entries(statusQuery.data?.coverage ?? {}) as Array<[string, string]>).map(([key, value]) => (
          <article key={key} className={`account-data-coverage-item is-${value}`}>
            <span>{COVERAGE_LABEL[key] ?? key}</span>
            <strong>{value === "available" ? "已覆盖" : value === "partial" ? "部分覆盖" : "待补齐"}</strong>
          </article>
        ))}
      </section>

      {blockingRowCount > 0 ? (
        <div className="account-data-warning" role="status">
          <WarningOutlined />
          <span>{`${blockingRowCount} 行仍需校验或人工确认，正式写入前不会放行。`}</span>
        </div>
      ) : null}

      <div className="account-data-layout">
        <FileImportFlow
          batch={activeBatch ?? null}
          feedback={flowFeedback}
          uploading={uploadMutation.isPending}
          resolvingRowNumber={resolveMutation.isPending ? resolveMutation.variables?.rowNumber ?? null : null}
          committing={commitMutation.isPending}
          canCommit={canCommit}
          onFileSelected={(file) => {
            setFlowFeedback(null);
            setHistoryError(null);
            uploadMutation.mutate(file);
          }}
          onResolveRow={(rowNumber, selectedContentId) => {
            if (!activeBatch) return;
            setFlowFeedback(null);
            setHistoryError(null);
            resolveMutation.mutate({ batchId: activeBatch.id, rowNumber, selectedContentId });
          }}
          onCommit={() => {
            if (!activeBatch) return;
            setFlowFeedback(null);
            setHistoryError(null);
            commitMutation.mutate(activeBatch.id);
          }}
        />

        <ImportBatchHistory
          items={historyQuery.data?.items ?? []}
          detailsById={detailsById}
          activeBatchId={activeBatchId}
          revokingBatchId={revokeMutation.isPending ? revokeMutation.variables ?? null : null}
          revokeError={historyError}
          onOpenBatch={(batchId) => {
            setActiveBatchId(batchId);
            setDraftBatch(detailsById.get(batchId) ?? null);
          }}
          onDownloadArtifact={(artifact: AccountDataImportArtifact) => {
            setHistoryError(null);
            void Promise.resolve(downloadAccountDataArtifact(artifact)).catch((error) => {
              if (!isMountedRef.current) return;
              setHistoryError(presentDownloadError(error));
            });
          }}
          onRevoke={(batchId) => revokeMutation.mutate(batchId)}
        />
      </div>

      {!historyQuery.isLoading && (historyQuery.data?.items.length ?? 0) === 0 ? (
        <section className="account-data-empty-block">
          <CloudUploadOutlined />
          <div>
            <strong>当前账号还没有确认过导入批次</strong>
            <p>先在左侧上传平台导出的原始文件，再在页面内完成校验和确认写入。</p>
          </div>
        </section>
      ) : null}
    </div>
  );
}
