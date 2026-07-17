import {
  CloseOutlined,
  HistoryOutlined,
  RedoOutlined,
  RollbackOutlined,
} from "@ant-design/icons";
import { Button, Tag, Tooltip } from "antd";

import type { ContentStage, ContentWorkspace, Deliverable } from "../../types";
import { DeliverableEditor } from "./DeliverableEditor";
import { PublishPreparation } from "./PublishPreparation";
import type { ContentInspectorMode } from "./ContentCanvas";
import {
  CONTENT_STAGES,
  deliverableLabel,
  deliverableStatusLabel,
  stageLabel,
} from "./contentPresentation";

const MODE_LABELS: Record<ContentInspectorMode, string> = {
  materials: "素材",
  versions: "版本历史",
  stages: "生产阶段",
  agents: "Agent 协作",
  approvals: "审批与风险",
  publish: "发布准备",
};

export function ContentInspector({
  mode,
  workspace,
  canOperate,
  editingDeliverable,
  saving,
  actionLoading,
  onClose,
  onCancelEdit,
  onSaveRevision,
  onRerun,
  onRollback,
}: {
  mode: ContentInspectorMode | null;
  workspace: ContentWorkspace | null;
  canOperate: boolean;
  editingDeliverable: Deliverable | null;
  saving: boolean;
  actionLoading: boolean;
  onClose: () => void;
  onCancelEdit: () => void;
  onSaveRevision: (payload: Record<string, unknown>, note: string) => void;
  onRerun: (stage: ContentStage) => void;
  onRollback: (deliverableId: number) => void;
}) {
  if ((!mode && !editingDeliverable) || !workspace) return null;
  const title = editingDeliverable ? "修订成果" : MODE_LABELS[mode!];

  return (
    <aside className="content-inspector" aria-label={title}>
      <header className="content-inspector__header">
        <div><span>工作检查器</span><strong>{title}</strong></div>
        <Tooltip title="关闭">
          <Button
            type="text"
            icon={<CloseOutlined />}
            aria-label="关闭检查器"
            onClick={editingDeliverable ? onCancelEdit : onClose}
          />
        </Tooltip>
      </header>
      <div className="content-inspector__body">
        {editingDeliverable ? (
          <DeliverableEditor
            deliverable={editingDeliverable}
            saving={saving}
            onSave={onSaveRevision}
          />
        ) : mode === "materials" ? (
          <MaterialsPanel workspace={workspace} />
        ) : mode === "versions" ? (
          <VersionsPanel
            workspace={workspace}
            loading={actionLoading}
            canOperate={canOperate}
            onRollback={onRollback}
          />
        ) : mode === "stages" ? (
          <StagesPanel
            workspace={workspace}
            loading={actionLoading}
            canOperate={canOperate}
            onRerun={onRerun}
          />
        ) : mode === "agents" ? (
          <AgentsPanel workspace={workspace} />
        ) : mode === "approvals" ? (
          <ApprovalsPanel workspace={workspace} />
        ) : mode === "publish" ? (
          <PublishPreparation workspace={workspace} canOperate={canOperate} />
        ) : null}
      </div>
    </aside>
  );
}

function MaterialsPanel({ workspace }: { workspace: ContentWorkspace }) {
  if (workspace.materials.length === 0) {
    return <InspectorEmpty title="尚无素材" copy="素材生成或上传完成后，会在这里显示来源、状态和关联成果。" />;
  }
  return (
    <div className="content-object-list">
      {workspace.materials.map((material) => (
        <article key={material.id} className="content-object-row">
          <span className="content-object-row__index">{material.kind === "video" ? "VID" : "IMG"}</span>
          <div><strong>{material.kind === "video" ? "视频素材" : "图片素材"} #{material.id}</strong><small>{material.provider ?? "本地素材"} · {materialStatus(material.status)}</small></div>
          <Tag bordered={false}>{material.size_bytes ? formatBytes(material.size_bytes) : "待统计"}</Tag>
        </article>
      ))}
    </div>
  );
}

function VersionsPanel({ workspace, loading, canOperate, onRollback }: { workspace: ContentWorkspace; loading: boolean; canOperate: boolean; onRollback: (id: number) => void }) {
  if (workspace.deliverables.length === 0) {
    return <InspectorEmpty title="尚无版本" copy="每次专家重做或人工修订都会生成新版本，旧版本不会被覆盖。" />;
  }
  return (
    <div className="content-object-list">
      {[...workspace.deliverables].sort((a, b) => b.created_at.localeCompare(a.created_at)).map((deliverable) => (
        <article key={deliverable.id} className="content-version-row">
          <HistoryOutlined />
          <div><strong>{deliverableLabel(deliverable.type)} · v{deliverable.version}</strong><small>{deliverableStatusLabel(deliverable.status)} · {formatDate(deliverable.created_at)}</small></div>
          {deliverable.status === "superseded" ? (
            <Tooltip title="恢复为当前版本"><Button type="text" icon={<RollbackOutlined />} loading={loading} disabled={!canOperate} onClick={() => onRollback(deliverable.id)} /></Tooltip>
          ) : <Tag bordered={false}>当前</Tag>}
        </article>
      ))}
    </div>
  );
}

function StagesPanel({ workspace, loading, canOperate, onRerun }: { workspace: ContentWorkspace; loading: boolean; canOperate: boolean; onRerun: (stage: ContentStage) => void }) {
  const taskByStage = new Map(workspace.tasks.map((task) => [task.stage, task]));
  return (
    <div className="content-stage-list">
      {CONTENT_STAGES.map((stage, index) => {
        const task = taskByStage.get(stage.key);
        return (
          <article key={stage.key} data-status={task?.status ?? "pending"}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div><strong>{stage.label}</strong><small>{task ? taskStatus(task.status) : "尚未开始"}</small></div>
            {task ? <Button type="text" icon={<RedoOutlined />} loading={loading} disabled={!canOperate} onClick={() => onRerun(stage.key)} /> : null}
          </article>
        );
      })}
    </div>
  );
}

function AgentsPanel({ workspace }: { workspace: ContentWorkspace }) {
  if (workspace.tasks.length === 0) return <InspectorEmpty title="尚未调用专家" copy="启动生产后，参与这条内容的专家和当前状态会在这里出现。" />;
  return (
    <div className="content-object-list">
      {workspace.tasks.map((task) => (
        <article key={task.id} className="content-agent-row">
          <span>{agentInitial(task.agent_code)}</span>
          <div><strong>{agentName(task.agent_code)}</strong><small>{stageLabel(task.stage)} · {taskStatus(task.status)}</small></div>
          <Tag bordered={false}>{taskStatus(task.status)}</Tag>
        </article>
      ))}
    </div>
  );
}

function ApprovalsPanel({ workspace }: { workspace: ContentWorkspace }) {
  const pendingTools = workspace.publish_tool_calls.filter((call) => call.status === "waiting_approval");
  if (workspace.gates.length === 0 && workspace.compliance.length === 0 && pendingTools.length === 0) {
    return <InspectorEmpty title="当前没有待处理审批" copy="质量门、合规风险和发布包确认会集中显示在这里，并同步进入人工审批模块。" />;
  }
  return (
    <div className="content-approval-list">
      {workspace.gates.map((gate) => (
        <article key={`gate-${gate.id}`}><span data-tone={gate.status === "pending" ? "warning" : "success"} /><div><strong>{gateLabel(gate.gate)}</strong><p>{gate.status === "pending" ? "等待人工审核" : gate.status === "approved" ? "已通过" : "已处理"}</p></div></article>
      ))}
      {workspace.compliance.map((check) => (
        <article key={`check-${check.id}`}><span data-tone={check.risk === "block" ? "error" : check.risk === "warn" ? "warning" : "success"} /><div><strong>内容合规检查</strong><p>{check.summary}</p></div></article>
      ))}
      {pendingTools.map((call) => (
        <article key={`tool-${call.id}`}><span data-tone="warning" /><div><strong>发布包确认</strong><p>发布包已生成，等待人工确认后进入手动发布流程。</p></div></article>
      ))}
    </div>
  );
}

function InspectorEmpty({ title, copy }: { title: string; copy: string }) {
  return <div className="content-inspector-empty"><strong>{title}</strong><p>{copy}</p></div>;
}

function materialStatus(status: string) { return { queued: "等待生成", generating: "生成中", ready: "已就绪", failed: "生成失败" }[status] ?? status; }
function taskStatus(status: string) { return { pending: "等待中", running: "执行中", done: "已完成", failed: "失败", blocked: "等待处理" }[status] ?? status; }
function gateLabel(gate: string) { return { positioning_review: "定位审核", topic_review: "选题审核", script_compliance: "脚本合规", final_video_review: "成片审核", pre_publish_review: "发布前审核", large_ad_spend: "投放审批" }[gate] ?? gate; }
function agentName(code: string) { return { "01-positioning": "账号定位专家", "02-content": "编导文案专家", "03-art": "视觉指导专家", "04-video": "视频创作专家", "05-editing": "剪辑专家", "06-operation": "账号运营专家" }[code] ?? "专业 Agent"; }
function agentInitial(code: string) { return { "01-positioning": "PX", "02-content": "CD", "03-art": "AD", "04-video": "VD", "05-editing": "ED", "06-operation": "OP" }[code] ?? "AG"; }
function formatDate(value: string) { return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function formatBytes(value: number) { if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`; return `${(value / 1024 / 1024).toFixed(1)} MB`; }
