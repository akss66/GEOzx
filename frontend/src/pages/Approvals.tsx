import {
  CheckOutlined,
  ClockCircleOutlined,
  CloseOutlined,
  FileDoneOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App as AntApp, Button, Input, Segmented, Skeleton, Tag } from "antd";
import { useEffect, useMemo, useState } from "react";

import { getApprovalWorkspace } from "../api/approvals";
import {
  approveDeliverableAcceptance,
  approveToolCall,
  rejectDeliverableAcceptance,
} from "../api/brain";
import { approveGate } from "../api/orchestrator";
import {
  APPROVAL_KIND_LABEL,
  APPROVAL_RISK_LABEL,
  approvalFindingCopy,
  filterApprovalItems,
  readApprovalAcceptance,
  readApprovalDeliverable,
  readApprovalPublishPackage,
  relativeApprovalTime,
  type ApprovalFilter,
} from "../components/approvals/approvalPresentation";
import { PublishExecutionQueue } from "../components/approvals/PublishExecutionQueue";
import {
  deliverableLabel,
  deliverableSections,
  displayContentTitle,
} from "../components/content/contentPresentation";
import { PageHeader } from "../components/ui";
import { useEventStream } from "../hooks/useEventStream";
import { useCurrentWorkspace } from "../stores/currentWorkspace";
import type {
  ApprovalQueueItem,
  Deliverable,
  PublishPackage,
  PublishReadinessFinding,
} from "../types";
import { businessToolName } from "../components/presentation/toolNames";
import "../styles/approval-workbench.css";

type DecisionInput = {
  item: ApprovalQueueItem;
  approved: boolean;
  note: string;
};

const FILTER_OPTIONS: { label: string; value: ApprovalFilter }[] = [
  { label: "全部", value: "all" },
  { label: "高风险", value: "high_risk" },
  { label: "内容", value: "content" },
  { label: "外部动作", value: "external" },
];

export default function Approvals() {
  const { message } = AntApp.useApp();
  const qc = useQueryClient();
  const { clientId, projectId, accountId } = useCurrentWorkspace();
  const [filter, setFilter] = useState<ApprovalFilter>("all");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState(false);

  const query = useQuery({
    queryKey: ["approval-workspace", clientId, projectId, accountId],
    queryFn: () => getApprovalWorkspace({
      client_id: clientId,
      project_id: projectId,
      account_id: accountId,
    }),
  });

  useEventStream(() => {
    qc.invalidateQueries({ queryKey: ["approval-workspace"] });
  });

  const items = query.data?.items ?? [];
  const visibleItems = useMemo(
    () => filterApprovalItems(items, filter),
    [filter, items],
  );

  useEffect(() => {
    if (visibleItems.some((item) => item.key === selectedKey)) return;
    setSelectedKey(visibleItems[0]?.key ?? null);
  }, [selectedKey, visibleItems]);

  const selected = visibleItems.find((item) => item.key === selectedKey) ?? null;

  useEffect(() => {
    setNote("");
    setNoteError(false);
  }, [selected?.key]);

  const decisionMutation = useMutation({
    mutationFn: async ({ item, approved, note: decisionNote }: DecisionInput) => {
      if (item.kind === "gate") {
        return approveGate(item.source_id, approved, decisionNote || undefined);
      }
      if (item.kind === "tool_call") {
        return approveToolCall({
          toolCallId: item.source_id,
          approved,
          comment: decisionNote || undefined,
        });
      }
      const acceptance = readApprovalAcceptance(item);
      if (!acceptance) throw new Error("成果验收数据不完整");
      if (approved) return approveDeliverableAcceptance(acceptance, decisionNote || undefined);
      return rejectDeliverableAcceptance({
        acceptance,
        reason: decisionNote,
        rerun_scope: "current_agent",
        ask_brain_rejudge: true,
      });
    },
    onSuccess: (_result, variables) => {
      const currentIndex = visibleItems.findIndex((item) => item.key === variables.item.key);
      const next = visibleItems[currentIndex + 1] ?? visibleItems[currentIndex - 1] ?? null;
      setSelectedKey(next?.key ?? null);
      setNote("");
      setNoteError(false);
      qc.invalidateQueries({ queryKey: ["approval-workspace"] });
      qc.invalidateQueries({ queryKey: ["content-items"] });
      qc.invalidateQueries({ queryKey: ["brain-tasks"] });
      qc.invalidateQueries({ queryKey: ["publish-jobs"] });
      message.success(variables.approved ? "审批已通过，已进入下一项" : "修改意见已提交");
    },
    onError: (error) => message.error(errorMessage(error)),
  });

  const decide = (approved: boolean) => {
    if (!selected || !selected.can_decide) return;
    const cleanNote = note.trim();
    if (!approved && !cleanNote) {
      setNoteError(true);
      return;
    }
    decisionMutation.mutate({ item: selected, approved, note: cleanNote });
  };

  return (
    <div className="approval-page">
      <PageHeader
        title="人工审批"
        subtitle="在外部动作发生前确认风险，在正式成果进入下游前给出明确判断"
        extra={
          <div className="approval-header-status">
            <span><i />真实队列</span>
            <strong>{query.data?.counts.total ?? 0}</strong>
          </div>
        }
      />

      <section className="approval-workbench">
        <ApprovalQueue
          loading={query.isLoading}
          error={query.isError}
          items={visibleItems}
          total={query.data?.counts.total ?? 0}
          selectedKey={selectedKey}
          filter={filter}
          onFilter={setFilter}
          onSelect={setSelectedKey}
          onRetry={() => void query.refetch()}
        />
        <ApprovalPreview item={selected} loading={query.isLoading} />
        <ApprovalDecision
          item={selected}
          note={note}
          noteError={noteError}
          loading={decisionMutation.isPending}
          onNoteChange={(value) => {
            setNote(value);
            if (value.trim()) setNoteError(false);
          }}
          onApprove={() => decide(true)}
          onReject={() => decide(false)}
        />
      </section>
      <PublishExecutionQueue accountId={accountId} />
    </div>
  );
}

function ApprovalQueue({
  loading,
  error,
  items,
  total,
  selectedKey,
  filter,
  onFilter,
  onSelect,
  onRetry,
}: {
  loading: boolean;
  error: boolean;
  items: ApprovalQueueItem[];
  total: number;
  selectedKey: string | null;
  filter: ApprovalFilter;
  onFilter: (value: ApprovalFilter) => void;
  onSelect: (key: string) => void;
  onRetry: () => void;
}) {
  return (
    <aside className="approval-queue" aria-label="待审批队列">
      <header className="approval-pane-header">
        <div><span>待审批队列</span><strong>{total}</strong></div>
        <small>按进入时间排序</small>
      </header>
      <Segmented
        block
        size="small"
        value={filter}
        options={FILTER_OPTIONS}
        onChange={(value) => onFilter(value as ApprovalFilter)}
      />
      <div className="approval-queue-list">
        {loading ? (
          Array.from({ length: 5 }).map((_, index) => <Skeleton.Button key={index} active block />)
        ) : error ? (
          <div className="approval-queue-state">
            <strong>审批队列加载失败</strong>
            <Button type="link" icon={<ReloadOutlined />} onClick={onRetry}>重新加载</Button>
          </div>
        ) : items.length === 0 ? (
          <div className="approval-queue-state">
            <CheckOutlined />
            <strong>当前筛选已处理完</strong>
            <span>新审批进入后会自动出现在这里。</span>
          </div>
        ) : items.map((item) => (
          <button
            type="button"
            key={item.key}
            className={`approval-queue-item${item.key === selectedKey ? " is-selected" : ""}`}
            onClick={() => onSelect(item.key)}
          >
            <span className="approval-queue-item__rail" data-risk={item.risk_level} />
            <span className="approval-queue-item__meta">
              <span>{APPROVAL_KIND_LABEL[item.kind]}</span>
              <i data-risk={item.risk_level}>{APPROVAL_RISK_LABEL[item.risk_level]}</i>
            </span>
            <strong>{approvalItemTitle(item)}</strong>
            <small>{item.project_name}{item.account_name ? ` · ${item.account_name}` : ""}</small>
            <time><ClockCircleOutlined /> {relativeApprovalTime(item.created_at)}</time>
          </button>
        ))}
      </div>
    </aside>
  );
}

function ApprovalPreview({ item, loading }: { item: ApprovalQueueItem | null; loading: boolean }) {
  if (loading) {
    return <main className="approval-preview"><Skeleton active paragraph={{ rows: 12 }} /></main>;
  }
  if (!item) {
    return (
      <main className="approval-preview approval-preview--empty">
        <span>审</span>
        <strong>选择一项查看完整内容</strong>
        <p>正式成果、发布包和工具影响会在这里完整展开。</p>
      </main>
    );
  }
  const deliverable = readApprovalDeliverable(item);
  const publishPackage = readApprovalPublishPackage(item);
  return (
    <main className="approval-preview">
      <header className="approval-preview-header">
        <div className="approval-preview-context">
          <span>{item.project_name}</span><i />
          <span>{item.account_name ?? "未绑定账号"}</span><i />
          <span>{item.category}</span>
        </div>
        <h1>{approvalItemTitle(item)}</h1>
        <p>{item.summary}</p>
      </header>
      {publishPackage ? (
        <PublishPackagePreview item={item} publishPackage={publishPackage} />
      ) : deliverable ? (
        <DeliverablePreview deliverable={deliverable} />
      ) : item.kind === "deliverable" ? (
        <AcceptancePreview item={item} />
      ) : (
        <ToolPreview item={item} />
      )}
    </main>
  );
}

function DeliverablePreview({ deliverable }: { deliverable: Deliverable }) {
  return (
    <article className="approval-document">
      <header><FileDoneOutlined /><div><span>正式成果</span><strong>{deliverableLabel(deliverable.type)} · v{deliverable.version}</strong></div></header>
      {deliverableSections(deliverable).map((section) => (
        <section key={section.label}>
          <h2>{section.label}</h2>
          {section.value ? <p>{section.value}</p> : null}
          {section.items ? <ol>{section.items.map((value, index) => <li key={`${index}-${value}`}>{value}</li>)}</ol> : null}
          {section.metrics ? <dl>{section.metrics.map((value) => <div key={value.label}><dt>{value.label}</dt><dd>{value.value}</dd></div>)}</dl> : null}
        </section>
      ))}
    </article>
  );
}

function AcceptancePreview({ item }: { item: ApprovalQueueItem }) {
  const acceptance = readApprovalAcceptance(item);
  if (!acceptance) return <ToolPreview item={item} />;
  return (
    <article className="approval-document">
      <header><FileDoneOutlined /><div><span>{acceptance.agent_name}</span><strong>{acceptance.title} · v{acceptance.version}</strong></div></header>
      <section><h2>成果摘要</h2><p>{acceptance.summary}</p></section>
      <section>
        <h2>验收标准</h2>
        <ol>{acceptance.acceptance_items.map((row, index) => <li key={`${index}-${row.label}`}><strong>{row.label}</strong><span>{row.note}</span></li>)}</ol>
      </section>
    </article>
  );
}

function PublishPackagePreview({
  item,
  publishPackage,
}: {
  item: ApprovalQueueItem;
  publishPackage: PublishPackage;
}) {
  const findings = Array.isArray(item.preview.findings)
    ? item.preview.findings.filter(isPublishFinding)
    : [];
  return (
    <article className="approval-publish-preview">
      <div className="approval-publish-facts">
        <Fact label="发布账号" value={item.account_name ?? `账号 #${publishPackage.account_id ?? "-"}`} />
        <Fact label="内容形式" value={publishPackage.content_type === "video" ? "视频" : "图文"} />
        <Fact label="发布时间" value={publishPackage.scheduled_at ? formatDate(publishPackage.scheduled_at) : "人工选择发布时间"} />
        <Fact label="可见范围" value={visibilityLabel(publishPackage.visibility)} />
      </div>
      <section><span>发布标题</span><h2>{publishPackage.title}</h2></section>
      <section><span>正文</span><p>{publishPackage.body || "未填写正文"}</p></section>
      <section><span>话题</span><div className="approval-topic-list">{publishPackage.topics.length ? publishPackage.topics.map((topic) => <Tag key={topic}>#{topic}</Tag>) : <small>未设置话题</small>}</div></section>
      <section className="approval-material-line"><span>素材与封面</span><p>素材 {publishPackage.material_ids.map((id) => `#${id}`).join(" / ") || "未选择"} · 封面 {publishPackage.cover_material_id ? `#${publishPackage.cover_material_id}` : "未指定"} · {publishPackage.allow_comment ? "允许评论" : "关闭评论"}</p></section>
      <section>
        <span>人工发布步骤</span>
        <ol className="approval-manual-steps">{publishPackage.manual_steps.map((step, index) => <li key={`${index}-${step}`}><b>{String(index + 1).padStart(2, "0")}</b><p>{step}</p></li>)}</ol>
      </section>
      {findings.length ? <section><span>发布前检查</span><div className="approval-finding-list">{findings.map((finding) => <p key={`${finding.code}-${finding.message}`} data-level={finding.level}>{finding.level === "pass" ? "通过" : finding.level === "warn" ? "注意" : "阻断"}<span>{approvalFindingCopy(finding.code, finding.message)}</span></p>)}</div></section> : null}
    </article>
  );
}

function ToolPreview({ item }: { item: ApprovalQueueItem }) {
  const toolName = readString(item.preview.tool_name);
  return (
    <article className="approval-tool-preview">
      <RobotOutlined />
      <span>Agent 请求执行</span>
      <h2>{toolName ? businessToolName("", toolName) : item.title}</h2>
      <dl>
        <div><dt>输入</dt><dd>{readString(item.preview.input_summary) || "未提供输入摘要"}</dd></div>
        <div><dt>预期结果</dt><dd>{readString(item.preview.output_summary) || item.summary}</dd></div>
      </dl>
    </article>
  );
}

function approvalItemTitle(item: ApprovalQueueItem) {
  const title = displayContentTitle(item.title);
  if (item.kind !== "tool_call" || /[\u3400-\u9fff]/u.test(title)) return title;
  return businessToolName("", readString(item.preview.tool_name) || title);
}

function ApprovalDecision({
  item,
  note,
  noteError,
  loading,
  onNoteChange,
  onApprove,
  onReject,
}: {
  item: ApprovalQueueItem | null;
  note: string;
  noteError: boolean;
  loading: boolean;
  onNoteChange: (value: string) => void;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <aside className="approval-decision" aria-label="风险与审批操作">
      <header className="approval-pane-header"><div><span>判断与影响</span></div><small>人工决策</small></header>
      {!item ? (
        <div className="approval-decision-empty"><SafetyCertificateOutlined /><strong>暂无待判断对象</strong></div>
      ) : (
        <>
          <div className="approval-risk-heading" data-risk={item.risk_level}>
            <SafetyCertificateOutlined />
            <div><span>风险等级</span><strong>{APPROVAL_RISK_LABEL[item.risk_level]}</strong></div>
          </div>
          <DecisionSection title="为什么需要你确认" items={item.risk_reasons} />
          <DecisionSection title="决策影响" items={item.impact} />
          <section className="approval-agent-explanation">
            <span><RobotOutlined /> Agent 解释</span><p>{item.agent_explanation}</p>
          </section>
          <div className={`approval-note${noteError ? " has-error" : ""}`}>
            <label htmlFor="approval-note">修改意见</label>
            <Input.TextArea
              id="approval-note"
              value={note}
              maxLength={1000}
              autoSize={{ minRows: 4, maxRows: 8 }}
              placeholder="通过时可补充备注；驳回时必须写明修改要求。"
              onChange={(event) => onNoteChange(event.target.value)}
            />
            {noteError ? <span>请先写明修改意见，再驳回并重跑。</span> : null}
          </div>
          {!item.can_decide ? <p className="approval-readonly">你可以查看此审批，但当前项目角色没有决策权限。</p> : null}
          <div className="approval-decision-actions">
            <Button danger icon={<CloseOutlined />} disabled={!item.can_decide} loading={loading} onClick={onReject}>
              {item.kind === "deliverable" ? "驳回并重跑" : "驳回"}
            </Button>
            <Button type="primary" icon={item.kind === "tool_call" ? <SendOutlined /> : <CheckOutlined />} disabled={!item.can_decide} loading={loading} onClick={onApprove}>
              {item.kind === "tool_call" ? "允许执行" : "通过"}
            </Button>
          </div>
        </>
      )}
    </aside>
  );
}

function DecisionSection({ title, items }: { title: string; items: string[] }) {
  return <section className="approval-decision-section"><h2>{title}</h2><ul>{items.map((item) => <li key={item}>{item}</li>)}</ul></section>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function visibilityLabel(value: PublishPackage["visibility"]) {
  return { public: "公开", friends: "朋友可见", private: "私密" }[value];
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function isPublishFinding(value: unknown): value is PublishReadinessFinding {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Partial<PublishReadinessFinding>;
  return typeof row.code === "string" && typeof row.message === "string" && ["pass", "warn", "block"].includes(String(row.level));
}

function readString(value: unknown) {
  return typeof value === "string" ? value : "";
}

function errorMessage(error: unknown) {
  if (error instanceof Error && error.message) return error.message;
  return "审批处理失败，请重试";
}
