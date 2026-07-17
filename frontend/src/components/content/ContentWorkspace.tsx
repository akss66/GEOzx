import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { useEffect, useMemo, useState } from "react";

import {
  createContentItem,
  createDeliverableRevision,
  getContentWorkspace,
  listContentItems,
  rerunStage,
  rollbackDeliverable,
  startPipeline,
} from "../../api/orchestrator";
import { presentApiError } from "../../api/errors";
import { OperationalState } from "../ui";
import { useEventStream } from "../../hooks/useEventStream";
import type { ContentItem, ContentStage, Deliverable } from "../../types";
import { ContentCanvas, type ContentInspectorMode } from "./ContentCanvas";
import { ContentInspector } from "./ContentInspector";
import { canOperateContent } from "./contentPresentation";
import { ContentRail } from "./ContentRail";

export function ContentWorkspaceView({
  projectId,
  accountId,
}: {
  projectId: number;
  accountId: number | null;
}) {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [inspectorMode, setInspectorMode] = useState<ContentInspectorMode | null>(null);
  const [editingDeliverable, setEditingDeliverable] = useState<Deliverable | null>(null);

  const itemsQuery = useQuery({
    queryKey: ["content-items", projectId],
    queryFn: () => listContentItems(projectId),
  });
  const items = useMemo(() => itemsQuery.data ?? [], [itemsQuery.data]);

  useEffect(() => {
    if (itemsQuery.isLoading) return;
    if (selectedId != null && items.some((item) => item.id === selectedId)) return;
    setSelectedId(items[0]?.id ?? null);
  }, [items, itemsQuery.isLoading, selectedId]);

  const workspaceQuery = useQuery({
    queryKey: ["content-workspace", selectedId],
    queryFn: () => getContentWorkspace(selectedId!),
    enabled: selectedId != null,
  });
  const canOperate = canOperateContent(
    workspaceQuery.data?.account?.id ?? null,
    accountId,
  );

  useEffect(() => {
    if (!canOperate) setEditingDeliverable(null);
  }, [canOperate]);

  useEventStream((event) => {
    if (event.content_item_id == null) return;
    qc.invalidateQueries({ queryKey: ["content-items", projectId] });
    qc.invalidateQueries({ queryKey: ["content-workspace", event.content_item_id] });
  });

  const invalidateSelected = () => {
    qc.invalidateQueries({ queryKey: ["content-items", projectId] });
    if (selectedId != null) {
      qc.invalidateQueries({ queryKey: ["content-workspace", selectedId] });
    }
  };

  const createMutation = useMutation({
    mutationFn: (title: string) =>
      createContentItem({ project_id: projectId, account_id: accountId, title }),
    onSuccess: (item) => {
      setSelectedId(item.id);
      qc.invalidateQueries({ queryKey: ["content-items", projectId] });
      message.success("内容工作区已创建");
    },
    onError: () => message.error("内容工作区创建失败"),
  });

  const startMutation = useMutation({
    mutationFn: () => startPipeline(selectedId!),
    onSuccess: () => {
      invalidateSelected();
      message.success("Agent 生产已启动");
    },
    onError: (error) =>
      message.error(presentApiError(error, "启动失败，请检查模型配置。").message),
  });

  const revisionMutation = useMutation({
    mutationFn: ({ deliverable, payload, note }: { deliverable: Deliverable; payload: Record<string, unknown>; note: string }) =>
      createDeliverableRevision(deliverable.id, { payload, note: note || null }),
    onSuccess: () => {
      setEditingDeliverable(null);
      invalidateSelected();
      message.success("新版本已保存，等待审核");
    },
    onError: (error) =>
      message.error(presentApiError(error, "修订保存失败，请稍后重试。").message),
  });

  const rerunMutation = useMutation({
    mutationFn: (stage: ContentStage) => rerunStage(selectedId!, stage),
    onSuccess: () => {
      invalidateSelected();
      message.success("已请求专家重做当前阶段");
    },
    onError: () => message.error("阶段重做失败"),
  });

  const rollbackMutation = useMutation({
    mutationFn: rollbackDeliverable,
    onSuccess: () => {
      invalidateSelected();
      message.success("已恢复所选历史版本");
    },
    onError: () => message.error("版本恢复失败"),
  });

  const selectItem = (item: ContentItem) => {
    setSelectedId(item.id);
    setInspectorMode(null);
    setEditingDeliverable(null);
  };

  if (itemsQuery.isError) {
    const failure = presentApiError(
      itemsQuery.error,
      "内容列表暂时不可用，请稍后重新加载。",
    );
    return (
      <div className="content-workspace content-workspace--state">
        <OperationalState
          kind="error"
          title="内容列表加载失败"
          description={`${failure.message} 当前项目和顶部账号选择均会保留。`}
          diagnostic={failure.diagnostic}
          actionLabel="重新加载"
          actionLoading={itemsQuery.isFetching}
          onAction={() => void itemsQuery.refetch()}
        />
      </div>
    );
  }

  const workspaceFailure = workspaceQuery.isError
    ? presentApiError(
        workspaceQuery.error,
        "当前内容暂时不可用，请稍后重新加载。",
      )
    : null;

  return (
    <div className={`content-workspace${inspectorMode || editingDeliverable ? " has-inspector" : ""}`}>
      <ContentRail
        items={items}
        selectedId={selectedId}
        loading={itemsQuery.isLoading}
        canCreate={accountId != null}
        creating={createMutation.isPending}
        onSelect={selectItem}
        onCreate={(title) => createMutation.mutate(title)}
      />
      {workspaceFailure ? (
        <OperationalState
          kind="error"
          compact
          title="内容工作区加载失败"
          description={`${workspaceFailure.message} 左侧内容选择和顶部账号选择均会保留。`}
          diagnostic={workspaceFailure.diagnostic}
          actionLabel="重新加载"
          actionLoading={workspaceQuery.isFetching}
          onAction={() => void workspaceQuery.refetch()}
        />
      ) : (
        <>
          <ContentCanvas
            workspace={workspaceQuery.data ?? null}
            loading={workspaceQuery.isLoading}
            starting={startMutation.isPending}
            canOperate={canOperate}
            inspectorMode={inspectorMode}
            onStart={() => {
              if (!canOperate) {
                message.warning("请先从顶部选择与这条内容一致的账号");
                return;
              }
              startMutation.mutate();
            }}
            onOpenInspector={(mode) => {
              setEditingDeliverable(null);
              setInspectorMode((current) => current === mode ? null : mode);
            }}
            onEdit={(deliverable) => {
              if (!canOperate) {
                message.warning("请先从顶部选择与这条内容一致的账号");
                return;
              }
              setInspectorMode(null);
              setEditingDeliverable(deliverable);
            }}
          />
          <ContentInspector
            mode={inspectorMode}
            workspace={workspaceQuery.data ?? null}
            canOperate={canOperate}
            editingDeliverable={editingDeliverable}
            saving={revisionMutation.isPending}
            actionLoading={rerunMutation.isPending || rollbackMutation.isPending}
            onClose={() => setInspectorMode(null)}
            onCancelEdit={() => setEditingDeliverable(null)}
            onSaveRevision={(payload, note) => {
              if (!editingDeliverable || !canOperate) return;
              revisionMutation.mutate({ deliverable: editingDeliverable, payload, note });
            }}
            onRerun={(stage) => {
              if (canOperate) rerunMutation.mutate(stage);
            }}
            onRollback={(id) => {
              if (canOperate) rollbackMutation.mutate(id);
            }}
          />
        </>
      )}
    </div>
  );
}
