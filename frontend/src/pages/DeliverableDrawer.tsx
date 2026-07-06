import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  FileDoneOutlined,
  HistoryOutlined,
  LoadingOutlined,
  ReloadOutlined,
  RobotOutlined,
  RollbackOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App as AntApp,
  Button,
  Checkbox,
  Collapse,
  Divider,
  Drawer,
  Empty,
  Input,
  Radio,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import {
  approveDeliverableAcceptance,
  closeTaskMemory,
  listDeliverableAcceptances,
  rejectDeliverableAcceptance,
  rejudgeDeliverableAcceptance,
} from "../api/brain";
import { listMaterials } from "../api/materials";
import {
  checkPublishReadiness,
  listPublishCapabilities,
  listDeliverableHistory,
  rerunStage,
  rollbackDeliverable,
} from "../api/orchestrator";
import { API_BASE } from "../api/client";
import { silverTagStyle } from "../theme/styles";
import type {
  ContentItem,
  ContentStage,
  Deliverable,
  DeliverableAcceptance,
  DeliverableStatus,
  DeliverableType,
  MaterialAsset,
  PublishCapability,
  PublishReadiness,
  PublishReadinessInput,
  RerunScope,
} from "../types";

const TYPE_LABEL: Record<DeliverableType, string> = {
  positioning_strategy: "定位策略",
  topic_plan: "选题方案",
  publish_calendar: "发布日历",
  video_script: "视频脚本",
  art_prompt: "美术提示词",
  video_asset: "视频素材",
  edited_video: "成片",
  review_report: "复盘报告",
  ad_plan: "投放计划",
  cs_record: "客服记录",
};

const TYPE_TO_STAGE: Partial<Record<DeliverableType, ContentStage>> = {
  positioning_strategy: "positioning",
  video_script: "content_direction",
  art_prompt: "art_direction",
  video_asset: "video_creation",
  edited_video: "editing",
  review_report: "operation",
  ad_plan: "advertising",
  cs_record: "customer_service",
};

const STATUS_TAG: Record<DeliverableStatus, { color: string; label: string }> = {
  draft: { color: "silver", label: "草稿" },
  pending_review: { color: "gold", label: "待审" },
  approved: { color: "green", label: "生效" },
  rejected: { color: "red", label: "打回" },
  superseded: { color: "default", label: "已被取代" },
};

const ACCEPTANCE_TAG: Record<
  DeliverableAcceptance["status"],
  { color: string; label: string; icon: React.ReactNode }
> = {
  pending: { color: "gold", label: "待验收", icon: <SafetyCertificateOutlined /> },
  approved: { color: "green", label: "已通过", icon: <CheckCircleOutlined /> },
  rejected: { color: "red", label: "已打回", icon: <CloseCircleOutlined /> },
  rerun_requested: { color: "processing", label: "已请求重跑", icon: <ReloadOutlined /> },
};

const RERUN_SCOPE_LABEL: Record<RerunScope, string> = {
  current_agent: "当前 Agent",
  upstream: "上游依赖",
  downstream: "下游依赖",
  full_chain: "全链路",
};

type RejectDraft = {
  reason: string;
  rerunScope: RerunScope;
  askBrainRejudge: boolean;
};

type PublishReadinessForm = {
  title: string;
  body: string;
  topics: string;
  materialIds: number[];
  coverMaterialId: number | null;
  scheduledAt: string;
  visibility: "public" | "friends" | "private";
  allowComment: boolean;
};

function getDemoTaskId(item: ContentItem): number {
  return item.id >= 1000 ? item.id : 1003;
}

export function DeliverableDrawer({
  item,
  onClose,
}: {
  item: ContentItem | null;
  onClose: () => void;
}) {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const [acceptances, setAcceptances] = useState<DeliverableAcceptance[]>([]);
  const [rejectDrafts, setRejectDrafts] = useState<Record<number, RejectDraft>>({});
  const [publishForm, setPublishForm] = useState<PublishReadinessForm>({
    title: "",
    body: "",
    topics: "",
    materialIds: [],
    coverMaterialId: null,
    scheduledAt: "",
    visibility: "public",
    allowComment: true,
  });
  const [publishReadiness, setPublishReadiness] = useState<PublishReadiness | null>(null);

  const taskId = item ? getDemoTaskId(item) : null;

  const historyQuery = useQuery({
    queryKey: ["deliverable-history", item?.id],
    queryFn: () => listDeliverableHistory(item!.id),
    enabled: item != null,
    retry: false,
  });

  const acceptanceQuery = useQuery({
    queryKey: ["deliverable-acceptances", taskId],
    queryFn: async () => {
      const rows = await listDeliverableAcceptances(taskId!);
      if (rows.length > 0) return rows;
      return listDeliverableAcceptances(1003);
    },
    enabled: taskId != null,
  });

  const materialsQuery = useQuery({
    queryKey: ["materials", item?.id],
    queryFn: () => listMaterials({ contentItemId: item!.id }),
    enabled: item != null,
  });

  const publishCapabilitiesQuery = useQuery({
    queryKey: ["publish-capabilities"],
    queryFn: listPublishCapabilities,
    enabled: item != null,
  });

  useEffect(() => {
    setAcceptances(acceptanceQuery.data ?? []);
  }, [acceptanceQuery.data]);

  useEffect(() => {
    if (!item) return;
    setPublishForm({
      title: item.title,
      body: "",
      topics: "",
      materialIds: [],
      coverMaterialId: null,
      scheduledAt: "",
      visibility: "public",
      allowComment: true,
    });
    setPublishReadiness(null);
  }, [item?.id, item?.title]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["deliverable-history", item?.id] });
    qc.invalidateQueries({ queryKey: ["content-items"] });
    qc.invalidateQueries({ queryKey: ["deliverable-acceptances", taskId] });
  };

  const updateAcceptance = (next: DeliverableAcceptance) => {
    setAcceptances((rows) => rows.map((row) => (row.id === next.id ? next : row)));
  };

  const rollbackMutation = useMutation({
    mutationFn: rollbackDeliverable,
    onSuccess: () => {
      message.success("已回滚到该版本");
      invalidate();
    },
    onError: () => message.error("回滚失败"),
  });

  const rerunMutation = useMutation({
    mutationFn: (stage: ContentStage) => rerunStage(item!.id, stage),
    onSuccess: () => {
      message.success("已请求重跑，生成后会进入分项验收");
      invalidate();
    },
    onError: () => message.error("重跑失败"),
  });

  const approveMutation = useMutation({
    mutationFn: (acceptance: DeliverableAcceptance) =>
      approveDeliverableAcceptance(acceptance),
    onSuccess: (next) => {
      updateAcceptance(next);
      message.success("已通过该交付物");
    },
  });

  const rejectMutation = useMutation({
    mutationFn: rejectDeliverableAcceptance,
    onSuccess: (next) => {
      updateAcceptance(next);
      message.success("已打回并记录重跑范围");
    },
  });

  const rejudgeMutation = useMutation({
    mutationFn: rejudgeDeliverableAcceptance,
    onSuccess: (next) => {
      updateAcceptance(next);
      message.success("运营大脑已给出重判建议");
    },
  });

  const closeMemoryMutation = useMutation({
    mutationFn: () => closeTaskMemory(taskId!),
    onSuccess: () => message.success("本次任务记忆已关闭"),
  });

  const publishReadinessMutation = useMutation({
    mutationFn: () => checkPublishReadiness(item!.id, buildPublishReadinessInput(publishForm)),
    onSuccess: (next) => {
      setPublishReadiness(next);
      if (next.risk === "block") {
        message.error("发布准备检查未通过");
      } else if (next.risk === "warn") {
        message.warning("发布准备检查存在风险，已进入人工确认");
      } else {
        message.success("发布准备检查通过，已进入人工确认");
      }
      qc.invalidateQueries({ queryKey: ["pending-tool-call-approvals"] });
    },
    onError: () => message.error("发布准备检查失败"),
  });

  const historyByType = useMemo(
    () =>
      (historyQuery.data ?? []).reduce<Record<string, Deliverable[]>>((acc, d) => {
        (acc[d.type] ??= []).push(d);
        return acc;
      }, {}),
    [historyQuery.data],
  );

  const allApproved =
    acceptances.length > 0 && acceptances.every((row) => row.status === "approved");

  const setRejectDraft = (id: number, patch: Partial<RejectDraft>) => {
    const currentDraft = currentRejectDraft(rejectDrafts[id]);
    setRejectDrafts((current) => ({
      ...current,
      [id]: {
        ...currentDraft,
        ...patch,
      },
    }));
  };

  const submitReject = (acceptance: DeliverableAcceptance) => {
    const draft = rejectDrafts[acceptance.id] ?? {
      reason: "",
      rerunScope: "current_agent" as RerunScope,
      askBrainRejudge: true,
    };
    if (draft.reason.trim().length === 0) {
      message.warning("打回前需要填写原因");
      return;
    }
    rejectMutation.mutate({
      acceptance,
      reason: draft.reason.trim(),
      rerun_scope: draft.rerunScope,
      ask_brain_rejudge: draft.askBrainRejudge,
    });
  };

  return (
    <Drawer
      title={item ? `分项验收 · #${item.id}` : "分项验收"}
      open={item != null}
      onClose={onClose}
      width={720}
    >
      {item && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              当前任务
            </Typography.Text>
            <Typography.Title level={5} style={{ margin: "2px 0 0", fontWeight: 600 }}>
              {item.title}
            </Typography.Title>
          </div>

          <Alert
            type={allApproved ? "success" : "info"}
            showIcon
            message={allApproved ? "所有交付物已通过" : "逐项验收每个专家交付物"}
            description={
              allApproved
                ? "用户确认最终验收后，系统会关闭本次任务记忆。后续历史归档暂不启用。"
                : "可以只通过某个子 Agent 的交付物，也可以打回单项并选择当前、上游、下游或全链路重跑。"
            }
            action={
              <Button
                type={allApproved ? "primary" : "default"}
                disabled={!allApproved || taskId == null}
                loading={closeMemoryMutation.isPending}
                onClick={() => closeMemoryMutation.mutate()}
              >
                关闭本次任务记忆
              </Button>
            }
          />

          <PublishReadinessPanel
            form={publishForm}
            result={publishReadiness}
            materials={materialsQuery.data ?? []}
            capabilities={publishCapabilitiesQuery.data ?? []}
            materialsLoading={materialsQuery.isLoading}
            loading={publishReadinessMutation.isPending}
            onChange={(patch) =>
              setPublishForm((current) => ({
                ...current,
                ...patch,
              }))
            }
            onCheck={() => publishReadinessMutation.mutate()}
          />

          {acceptanceQuery.isLoading ? (
            <div style={{ display: "grid", placeItems: "center", padding: 40 }}>
              <Spin />
            </div>
          ) : acceptances.length === 0 ? (
            <Empty description="暂无需要验收的交付物" />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {acceptances.map((acceptance) => (
                <AcceptancePanel
                  key={acceptance.id}
                  acceptance={acceptance}
                  history={historyByType[acceptance.deliverable_type] ?? []}
                  rejectDraft={
                    rejectDrafts[acceptance.id] ??
                    currentRejectDraft({
                      reason: acceptance.reviewer_note ?? "",
                      rerunScope: acceptance.rerun_scope ?? "current_agent",
                      askBrainRejudge: acceptance.status === "rerun_requested",
                    })
                  }
                  onDraftChange={(patch) => setRejectDraft(acceptance.id, patch)}
                  onApprove={() => approveMutation.mutate(acceptance)}
                  onReject={() => submitReject(acceptance)}
                  onRejudge={() => rejudgeMutation.mutate(acceptance)}
                  onRerunStage={(stage) => rerunMutation.mutate(stage)}
                  onRollback={(deliverableId) => rollbackMutation.mutate(deliverableId)}
                  actionLoading={
                    approveMutation.isPending ||
                    rejectMutation.isPending ||
                    rejudgeMutation.isPending ||
                    rerunMutation.isPending ||
                    rollbackMutation.isPending
                  }
                />
              ))}
            </div>
          )}

          {historyQuery.isError && (
            <Alert
              type="warning"
              showIcon
              message="真实交付物历史暂不可用"
              description="当前仍可使用 mock 验收数据完成流程演示；后端接口恢复后会自动显示真实版本记录。"
            />
          )}
        </div>
      )}
    </Drawer>
  );
}

function currentRejectDraft(draft?: Partial<RejectDraft>): RejectDraft {
  return {
    reason: draft?.reason ?? "",
    rerunScope: draft?.rerunScope ?? "current_agent",
    askBrainRejudge: draft?.askBrainRejudge ?? true,
  };
}

function buildPublishReadinessInput(form: PublishReadinessForm): PublishReadinessInput {
  const scheduledDate = form.scheduledAt ? new Date(form.scheduledAt) : null;
  const scheduledAt =
    scheduledDate && !Number.isNaN(scheduledDate.getTime())
      ? scheduledDate.toISOString()
      : null;

  return {
    platform: "douyin",
    title: form.title.trim(),
    body: form.body.trim(),
    topics: form.topics
      .split(/[,\uFF0C\s]+/)
      .map((topic) => topic.trim())
      .filter(Boolean),
    scheduled_at: scheduledAt,
    material_ids: form.materialIds,
    cover_material_id: form.coverMaterialId,
    visibility: form.visibility,
    allow_comment: form.allowComment,
  };
}

function PublishReadinessPanel({
  form,
  result,
  materials,
  capabilities,
  materialsLoading,
  loading,
  onChange,
  onCheck,
}: {
  form: PublishReadinessForm;
  result: PublishReadiness | null;
  materials: MaterialAsset[];
  capabilities: PublishCapability[];
  materialsLoading: boolean;
  loading: boolean;
  onChange: (patch: Partial<PublishReadinessForm>) => void;
  onCheck: () => void;
}) {
  const riskMeta = result
    ? {
        pass: { color: "green", label: "可发布" },
        warn: { color: "gold", label: "需确认" },
        block: { color: "red", label: "阻塞" },
      }[result.risk]
    : null;
  const douyinCapability = capabilities.find((capability) => capability.platform === "douyin");

  return (
    <section
      style={{
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 14,
        background: "var(--dy-surface)",
        padding: 14,
      }}
    >
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          alignItems: "flex-start",
          marginBottom: 12,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <SendOutlined style={{ color: "var(--dy-accent)" }} />
            <Typography.Text strong>发布准备校验</Typography.Text>
            {riskMeta && (
              <Tag color={riskMeta.color} style={{ marginInlineEnd: 0 }}>
                {riskMeta.label}
              </Tag>
            )}
          </div>
          <Typography.Text style={{ display: "block", marginTop: 4, color: "var(--dy-text)" }}>
            将标题、话题、定时发布和素材状态交给运营 Agent 做最终确认。
          </Typography.Text>
        </div>
        <Button type="primary" icon={<SafetyCertificateOutlined />} loading={loading} onClick={onCheck}>
          开始校验
        </Button>
      </header>

      {douyinCapability && (
        <div
          style={{
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
            alignItems: "center",
            marginBottom: 12,
            padding: "10px 12px",
            border: "1px solid var(--dy-border-subtle)",
            borderRadius: 12,
            background: "var(--dy-elevated)",
          }}
        >
          <Tag style={{ marginInlineEnd: 0 }}>抖音</Tag>
          <Tag color="warning" style={{ marginInlineEnd: 0 }}>
            {executionModeLabel(douyinCapability.execution_mode)}
          </Tag>
          <Tag style={{ marginInlineEnd: 0 }}>
            {permissionStatusLabel(douyinCapability.permission_status)}
          </Tag>
          <Typography.Text style={{ color: "var(--dy-text)", fontSize: 12.5 }}>
            当前只生成发布包和人工清单，不启用浏览器自动发布。
          </Typography.Text>
        </div>
      )}

      <div style={{ display: "grid", gap: 10 }}>
        <Input
          value={form.title}
          onChange={(event) => onChange({ title: event.target.value })}
          placeholder="发布标题"
          prefix={<FileDoneOutlined />}
        />
        <Input.TextArea
          value={form.body}
          onChange={(event) => onChange({ body: event.target.value })}
          placeholder="发布正文 / 备注"
          autoSize={{ minRows: 2, maxRows: 4 }}
        />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <Input
            value={form.topics}
            onChange={(event) => onChange({ topics: event.target.value })}
            placeholder="话题，用逗号分隔"
          />
          <Input
            type="datetime-local"
            value={form.scheduledAt}
            onChange={(event) => onChange({ scheduledAt: event.target.value })}
            prefix={<ClockCircleOutlined />}
          />
        </div>
        <MaterialPicker
          materials={materials}
          loading={materialsLoading}
          selectedIds={form.materialIds}
          onChange={(materialIds) =>
            onChange({
              materialIds,
              coverMaterialId:
                form.coverMaterialId != null && materialIds.includes(form.coverMaterialId)
                  ? form.coverMaterialId
                  : null,
            })
          }
        />
        <div
          style={{
            display: "grid",
            gap: 10,
            padding: 12,
            border: "1px solid var(--dy-border-subtle)",
            borderRadius: 12,
            background: "var(--dy-elevated)",
          }}
        >
          <div style={{ display: "grid", gap: 6 }}>
            <Typography.Text strong>发布范围</Typography.Text>
            <Radio.Group
              value={form.visibility}
              onChange={(event) => onChange({ visibility: event.target.value })}
              optionType="button"
              buttonStyle="solid"
              options={[
                { label: "公开", value: "public" },
                { label: "朋友可见", value: "friends" },
                { label: "私密", value: "private" },
              ]}
            />
          </div>
          <Checkbox
            checked={form.allowComment}
            onChange={(event) => onChange({ allowComment: event.target.checked })}
          >
            允许评论
          </Checkbox>
          <div style={{ display: "grid", gap: 6 }}>
            <Typography.Text strong>封面素材</Typography.Text>
            <Radio.Group
              value={form.coverMaterialId ?? 0}
              onChange={(event) =>
                onChange({ coverMaterialId: event.target.value === 0 ? null : event.target.value })
              }
            >
              <Space size={[8, 8]} wrap>
                <Radio.Button value={0}>不指定</Radio.Button>
                {materials
                  .filter((material) => material.status === "ready" && form.materialIds.includes(material.id))
                  .map((material) => (
                    <Radio.Button key={material.id} value={material.id}>
                      #{material.id} {material.kind}
                    </Radio.Button>
                  ))}
              </Space>
            </Radio.Group>
          </div>
        </div>
      </div>

      {result && (
        <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
          <Alert
            type={result.risk === "block" ? "error" : result.risk === "warn" ? "warning" : "success"}
            showIcon
            message={
              result.ready
                ? `已生成工具调用 #${result.tool_call.id}，等待人工确认`
                : `工具调用 #${result.tool_call.id} 已被阻塞`
            }
          />
          <PublishPackageSummary result={result} />
          <div style={{ display: "grid", gap: 6 }}>
            {result.findings.map((finding) => (
              <div
                key={`${finding.code}-${finding.message}`}
                style={{
                  display: "grid",
                  gridTemplateColumns: "72px minmax(0, 1fr)",
                  gap: 8,
                  alignItems: "start",
                  padding: "8px 10px",
                  border: "1px solid var(--dy-border-subtle)",
                  borderRadius: 10,
                  background: "var(--dy-elevated)",
                }}
              >
                <Tag
                  color={
                    finding.level === "block" ? "red" : finding.level === "warn" ? "gold" : "green"
                  }
                  style={{ marginInlineEnd: 0, textAlign: "center" }}
                >
                  {finding.level}
                </Tag>
                <div>
                  <Typography.Text strong>{finding.code}</Typography.Text>
                  <div style={{ fontSize: 12.5, lineHeight: 1.55, color: "var(--dy-text)" }}>
                    {finding.message}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function PublishPackageSummary({ result }: { result: PublishReadiness }) {
  const pkg = result.package;
  const visibilityLabel = {
    public: "公开",
    friends: "朋友可见",
    private: "私密",
  }[pkg.visibility];

  return (
    <div
      style={{
        display: "grid",
        gap: 8,
        padding: "10px 12px",
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 12,
        background: "var(--dy-elevated)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Typography.Text strong>发布准备包</Typography.Text>
        <Tag style={{ marginInlineEnd: 0 }}>抖音</Tag>
        <Tag style={{ marginInlineEnd: 0 }}>{visibilityLabel}</Tag>
        <Tag color={pkg.allow_comment ? "green" : "default"} style={{ marginInlineEnd: 0 }}>
          {pkg.allow_comment ? "允许评论" : "关闭评论"}
        </Tag>
      </div>
      <div style={{ display: "grid", gap: 5, fontSize: 12.5, color: "var(--dy-text)" }}>
        <span>账号：{pkg.account_id ? `#${pkg.account_id}` : "未绑定内容账号"}</span>
        <span>类型：{contentTypeLabel(pkg.content_type)}</span>
        <span>执行：{executionModeLabel(pkg.execution_mode)}</span>
        <span>标题：{pkg.title}</span>
        <span>素材：{pkg.material_ids.length > 0 ? pkg.material_ids.map((id) => `#${id}`).join(" / ") : "未选择"}</span>
        <span>封面：{pkg.cover_material_id ? `#${pkg.cover_material_id}` : "未指定"}</span>
        <span>话题：{pkg.topics.length > 0 ? pkg.topics.join(" / ") : "未设置"}</span>
        <span>定时：{pkg.scheduled_at ? new Date(pkg.scheduled_at).toLocaleString() : "立即/人工确认后发布"}</span>
      </div>
      {pkg.manual_steps.length > 0 && (
        <div style={{ display: "grid", gap: 6, marginTop: 4 }}>
          <Typography.Text strong>人工发布清单</Typography.Text>
          {pkg.manual_steps.map((step, index) => (
            <div
              key={`${step}-${index}`}
              style={{
                display: "grid",
                gridTemplateColumns: "22px minmax(0, 1fr)",
                gap: 8,
                fontSize: 12.5,
                color: "var(--dy-text)",
              }}
            >
              <span className="dy-tabular" style={{ color: "var(--dy-accent)", fontWeight: 700 }}>
                {index + 1}
              </span>
              <span>{step}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function contentTypeLabel(value: "video" | "image_text"): string {
  return {
    video: "视频",
    image_text: "图文",
  }[value];
}

function executionModeLabel(
  value: "official_api" | "manual_checklist" | "browser_runner_disabled",
): string {
  return {
    official_api: "官方接口",
    manual_checklist: "人工发布",
    browser_runner_disabled: "浏览器自动化已关闭",
  }[value];
}

function permissionStatusLabel(
  value: "oauth_authorized" | "pending_review" | "prepare_only",
): string {
  return {
    oauth_authorized: "OAuth 已授权",
    pending_review: "发布权限待审核",
    prepare_only: "仅发布准备",
  }[value];
}

function MaterialPicker({
  materials,
  loading,
  selectedIds,
  onChange,
}: {
  materials: MaterialAsset[];
  loading: boolean;
  selectedIds: number[];
  onChange: (ids: number[]) => void;
}) {
  const toggle = (id: number, checked: boolean) => {
    if (checked) {
      onChange(Array.from(new Set([...selectedIds, id])));
      return;
    }
    onChange(selectedIds.filter((selectedId) => selectedId !== id));
  };

  if (loading) {
    return (
      <div
        style={{
          display: "grid",
          placeItems: "center",
          minHeight: 86,
          border: "1px solid var(--dy-border-subtle)",
          borderRadius: 12,
          background: "var(--dy-elevated)",
        }}
      >
        <Spin size="small" />
      </div>
    );
  }

  if (materials.length === 0) {
    return (
      <div
        style={{
          border: "1px solid var(--dy-border-subtle)",
          borderRadius: 12,
          background: "var(--dy-elevated)",
          padding: 12,
        }}
      >
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="当前内容还没有可用于发布的素材"
        />
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 8 }}>
      {materials.map((material) => {
        const disabled = material.status !== "ready";
        const checked = selectedIds.includes(material.id);
        return (
          <label
            key={material.id}
            style={{
              display: "grid",
              gridTemplateColumns: "24px minmax(0, 1fr) auto",
              gap: 10,
              alignItems: "center",
              padding: "10px 12px",
              border: checked
                ? "1px solid var(--dy-accent)"
                : "1px solid var(--dy-border-subtle)",
              borderRadius: 12,
              background: checked ? "rgba(214, 0, 26, 0.06)" : "var(--dy-elevated)",
              cursor: disabled ? "not-allowed" : "pointer",
              opacity: disabled ? 0.62 : 1,
            }}
          >
            <Checkbox
              checked={checked}
              disabled={disabled}
              onChange={(event) => toggle(material.id, event.target.checked)}
            />
            <div style={{ minWidth: 0 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <Typography.Text strong>#{material.id}</Typography.Text>
                <Tag style={{ marginInlineEnd: 0 }}>{material.kind}</Tag>
                <Tag
                  color={
                    material.status === "ready"
                      ? "green"
                      : material.status === "failed"
                        ? "red"
                        : "gold"
                  }
                  style={{ marginInlineEnd: 0 }}
                >
                  {material.status}
                </Tag>
                {material.provider && (
                  <span style={{ fontSize: 12, color: "var(--dy-muted)" }}>
                    {material.provider}
                  </span>
                )}
              </div>
              <div
                style={{
                  marginTop: 4,
                  color: "var(--dy-muted)",
                  fontSize: 12,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {material.file_url ?? material.error ?? "素材尚未生成可访问文件"}
              </div>
            </div>
            <span className="dy-tabular" style={{ fontSize: 12, color: "var(--dy-muted)" }}>
              {formatBytes(material.size_bytes)}
            </span>
          </label>
        );
      })}
    </div>
  );
}

function formatBytes(value: number | null): string {
  if (value == null) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function AcceptancePanel({
  acceptance,
  history,
  rejectDraft,
  onDraftChange,
  onApprove,
  onReject,
  onRejudge,
  onRerunStage,
  onRollback,
  actionLoading,
}: {
  acceptance: DeliverableAcceptance;
  history: Deliverable[];
  rejectDraft: RejectDraft;
  onDraftChange: (patch: Partial<RejectDraft>) => void;
  onApprove: () => void;
  onReject: () => void;
  onRejudge: () => void;
  onRerunStage: (stage: ContentStage) => void;
  onRollback: (deliverableId: number) => void;
  actionLoading: boolean;
}) {
  const statusMeta = ACCEPTANCE_TAG[acceptance.status];
  const stage = TYPE_TO_STAGE[acceptance.deliverable_type];
  const hasFailure = acceptance.acceptance_items.some((item) => item.status === "fail");

  return (
    <section
      style={{
        background: "var(--dy-surface)",
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 10,
        padding: 14,
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 10,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <FileDoneOutlined style={{ color: "var(--dy-accent)" }} />
            <Typography.Text strong>{acceptance.title}</Typography.Text>
            <Tag style={{ marginInlineEnd: 0 }}>
              {TYPE_LABEL[acceptance.deliverable_type]}
            </Tag>
            <Tag
              color={statusMeta.color}
              icon={statusMeta.icon}
              style={{ marginInlineEnd: 0 }}
            >
              {statusMeta.label}
            </Tag>
          </div>
          <div
            style={{
              display: "flex",
              gap: 10,
              flexWrap: "wrap",
              marginTop: 6,
              color: "var(--dy-muted)",
              fontSize: 12,
            }}
          >
            <span>{acceptance.agent_name}</span>
            <span className="dy-tabular">{acceptance.agent_code}</span>
            <span className="dy-tabular">v{acceptance.version}</span>
          </div>
        </div>
        <Button
          type="primary"
          icon={<CheckCircleOutlined />}
          disabled={acceptance.status === "approved"}
          loading={actionLoading}
          onClick={onApprove}
        >
          通过
        </Button>
      </header>

      <Typography.Paragraph style={{ marginBottom: 12, color: "var(--dy-muted)" }}>
        {acceptance.summary}
      </Typography.Paragraph>

      <div style={{ display: "grid", gap: 8, marginBottom: 12 }}>
        {acceptance.acceptance_items.map((item) => (
          <div
            key={item.label}
            style={{
              display: "grid",
              gridTemplateColumns: "92px minmax(0, 1fr)",
              gap: 10,
              alignItems: "start",
              padding: "8px 10px",
              border: "1px solid var(--dy-border-subtle)",
              borderRadius: 8,
              background: "var(--dy-elevated)",
            }}
          >
            <Tag
              color={
                item.status === "pass" ? "green" : item.status === "warn" ? "gold" : "red"
              }
              style={{ width: 72, textAlign: "center", marginInlineEnd: 0 }}
            >
              {item.status === "pass" ? "通过" : item.status === "warn" ? "注意" : "未通过"}
            </Tag>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 500, color: "var(--dy-text)", marginBottom: 2 }}>
                {item.label}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--dy-muted)", lineHeight: 1.55 }}>
                {item.note}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div
        style={{
          padding: 12,
          borderRadius: 8,
          border: hasFailure
            ? "1px solid rgba(240,86,107,0.36)"
            : "1px solid var(--dy-border-subtle)",
          background: "var(--dy-elevated)",
        }}
      >
        <Typography.Text strong>打回与重跑</Typography.Text>
        <Input.TextArea
          value={rejectDraft.reason}
          onChange={(event) => onDraftChange({ reason: event.target.value })}
          placeholder="写明打回原因，例如：第三条脚本开头太像硬广，需要重写钩子。"
          autoSize={{ minRows: 2, maxRows: 4 }}
          style={{ marginTop: 8, marginBottom: 10 }}
        />
        <Radio.Group
          value={rejectDraft.rerunScope}
          onChange={(event) => onDraftChange({ rerunScope: event.target.value })}
          optionType="button"
          buttonStyle="solid"
          options={(Object.keys(RERUN_SCOPE_LABEL) as RerunScope[]).map((value) => ({
            label: RERUN_SCOPE_LABEL[value],
            value,
          }))}
          style={{ marginBottom: 10 }}
        />
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 12,
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <Checkbox
            checked={rejectDraft.askBrainRejudge}
            onChange={(event) => onDraftChange({ askBrainRejudge: event.target.checked })}
          >
            让运营大脑重新判断
          </Checkbox>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Button
              icon={<RobotOutlined />}
              loading={actionLoading}
              onClick={onRejudge}
            >
              仅重判
            </Button>
            <Button
              danger
              icon={<CloseCircleOutlined />}
              loading={actionLoading}
              onClick={onReject}
            >
              打回
            </Button>
            {stage && (
              <Button
                icon={<ReloadOutlined />}
                loading={actionLoading}
                onClick={() => onRerunStage(stage)}
              >
                按阶段重跑
              </Button>
            )}
          </div>
        </div>
      </div>

      {(acceptance.brain_rejudge_summary || acceptance.brain_rejudge_basis.length > 0) && (
        <Alert
          style={{ marginTop: 12 }}
          type="info"
          showIcon
          icon={<RobotOutlined />}
          message={acceptance.brain_rejudge_summary ?? "运营大脑可重新判断重跑方案"}
          description={
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {acceptance.brain_rejudge_basis.map((basis) => (
                <li key={basis}>{basis}</li>
              ))}
            </ul>
          }
        />
      )}

      <Divider style={{ margin: "14px 0 10px" }} />
      <Collapse
        size="small"
        ghost
        items={[
          {
            key: "history",
            label: (
              <span>
                <HistoryOutlined /> 历史版本
              </span>
            ),
            children: (
              <VersionHistory
                acceptance={acceptance}
                history={history}
                onRollback={onRollback}
                actionLoading={actionLoading}
              />
            ),
          },
        ]}
      />
    </section>
  );
}

function VersionHistory({
  acceptance,
  history,
  onRollback,
  actionLoading,
}: {
  acceptance: DeliverableAcceptance;
  history: Deliverable[];
  onRollback: (deliverableId: number) => void;
  actionLoading: boolean;
}) {
  const syntheticHistory =
    history.length > 0
      ? history
      : acceptance.history_versions.map<Deliverable>((version) => ({
          id: (acceptance.deliverable_id ?? acceptance.id) * 10 + version.version,
          agent_code: acceptance.agent_code,
          type: acceptance.deliverable_type,
          version: version.version,
          status: version.status,
          payload: { note: version.note },
          created_at: version.created_at,
        }));

  if (syntheticHistory.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史版本" />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {syntheticHistory.map((d) => (
        <div
          key={d.id}
          style={{
            border: "1px solid var(--dy-border-subtle)",
            borderRadius: 8,
            padding: 10,
            background: d.status === "superseded" ? "transparent" : "var(--dy-elevated)",
            opacity: d.status === "superseded" ? 0.76 : 1,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 8,
            }}
          >
            <span className="dy-tabular" style={{ fontWeight: 600 }}>
              v{d.version}
            </span>
            <Tag
              color={
                STATUS_TAG[d.status].color === "silver"
                  ? undefined
                  : STATUS_TAG[d.status].color
              }
              style={{
                marginInlineEnd: 0,
                ...(STATUS_TAG[d.status].color === "silver" ? silverTagStyle : undefined),
              }}
            >
              {STATUS_TAG[d.status].label}
            </Tag>
            <span
              className="dy-tabular"
              style={{ fontSize: 11.5, color: "var(--dy-faint)", marginLeft: "auto" }}
            >
              {d.agent_code}
            </span>
            {d.status === "superseded" && (
              <Button
                size="small"
                type="text"
                icon={<RollbackOutlined />}
                loading={actionLoading}
                onClick={() => onRollback(d.id)}
              >
                回滚
              </Button>
            )}
          </div>
          {d.type === "video_asset" && <VideoPreview payload={d.payload} />}
          <pre
            style={{
              margin: 0,
              fontSize: 11.5,
              lineHeight: 1.5,
              color: "var(--dy-muted)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              maxHeight: 120,
              overflow: "auto",
            }}
          >
            {JSON.stringify(d.payload, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  );
}

function VideoPreview({ payload }: { payload: Record<string, unknown> }) {
  const status = typeof payload.gen_status === "string" ? payload.gen_status : undefined;
  const rawUrl = typeof payload.video_url === "string" ? payload.video_url : undefined;
  const url = rawUrl
    ? rawUrl.startsWith("http")
      ? rawUrl
      : `${API_BASE}${rawUrl}`
    : undefined;

  if (url) {
    return (
      <video
        src={url}
        controls
        style={{ width: "100%", borderRadius: 8, marginBottom: 10, background: "#000" }}
      />
    );
  }

  const label =
    status === "queued" || status === "ready"
      ? "出片中，完成后自动刷新"
      : status?.startsWith("error")
        ? `出片失败：${status.slice(7)}`
        : "等待出片";
  const isError = status?.startsWith("error");
  return (
    <div
      style={{
        marginBottom: 10,
        padding: "12px 14px",
        borderRadius: 8,
        background: "var(--dy-elevated)",
        border: "1px solid var(--dy-border-subtle)",
        fontSize: 12.5,
        color: isError ? "var(--dy-error)" : "var(--dy-muted)",
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      {!isError && <LoadingOutlined />}
      {label}
    </div>
  );
}
