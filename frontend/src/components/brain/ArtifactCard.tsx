import { Button, Input, Tag } from "antd";
import { useMemo, useState } from "react";

import type { Artifact, ArtifactSection } from "../../types";

export type ArtifactAction =
  | { type: "view_full_report"; artifact: Artifact }
  | { type: "accept"; artifact: Artifact }
  | { type: "accept_and_continue"; artifact: Artifact }
  | { type: "request_revision"; artifact: Artifact; note: string };

const INTERNAL = /(?:acceptance|checklist|debug|kernel|policy|prompt|trace|raw|tool[ _-]?log|credential|stack)/i;

const BUSINESS_TITLES: Record<string, string> = {
  core_conclusion: "核心结论",
  conclusion: "核心结论",
  data_period: "数据周期",
  period: "数据周期",
  date_range: "数据周期",
  key_metrics: "关键数据",
  issues: "主要问题",
  optimization_suggestions: "优化建议",
  recommendations: "优化建议",
  participating_experts: "调用专家",
};

const PRIMARY_KEYS = [
  "core_conclusion",
  "conclusion",
  "data_period",
  "period",
  "date_range",
  "key_metrics",
  "issues",
  "optimization_suggestions",
  "recommendations",
  "participating_experts",
];

export function ArtifactCard({
  artifact,
  onAction,
  revisionPending = false,
}: {
  artifact: Artifact;
  onAction: (action: ArtifactAction) => void;
  revisionPending?: boolean;
}) {
  const [fullReportOpen, setFullReportOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [editingRevision, setEditingRevision] = useState(false);
  const [revisionNote, setRevisionNote] = useState("");
  const sections = useMemo(() => artifact.sections.filter(isBusinessSection), [artifact.sections]);
  const primarySections = sections.filter((section) => PRIMARY_KEYS.includes(section.key));
  const remainingSections = sections.filter((section) => !PRIMARY_KEYS.includes(section.key));
  const evidence = artifact.evidence_refs.filter((item) => isSafeText(item.kind) && isSafeText(item.label));
  const canAct = ["draft", "ready_for_review"].includes(artifact.status);
  const revisionInProgress = revisionPending || artifact.status === "revision_requested";

  return (
    <article className="tz-artifact-card" aria-label={`Artifact: ${artifact.title}`}>
      <header className="tz-artifact-card__header">
        <div>
          <span className="tz-artifact-card__eyebrow">正式成果 · <strong>V{artifact.version}</strong></span>
          <h3>{artifact.title}</h3>
        </div>
        <Tag color={statusColor(artifact.status)}>{statusCopy(artifact.status)}</Tag>
      </header>

      <p className="tz-artifact-card__summary">{safeText(artifact.summary)}</p>

      <div className="tz-artifact-card__sections">
        {primarySections.map((section) => <BusinessSection key={section.key} section={section} />)}
      </div>

      {revisionInProgress ? (
        <div className="tz-artifact-card__revision-progress" role="status">正在生成 V{artifact.version + 1}</div>
      ) : null}

      <p className="tz-artifact-card__evidence-summary">
        调用专家 / 依据：{evidence.length > 0 ? `已引用 ${evidence.length} 项可核查依据` : "暂无额外可核查依据"}
      </p>

      {fullReportOpen && remainingSections.length > 0 ? (
        <div className="tz-artifact-card__sections tz-artifact-card__sections--remaining">
          {remainingSections.map((section) => <BusinessSection key={section.key} section={section} />)}
        </div>
      ) : null}

      <div className="tz-artifact-card__actions">
        <Button onClick={() => {
          setFullReportOpen((open) => !open);
          onAction({ type: "view_full_report", artifact });
        }}>
          查看完整报告
        </Button>
        {canAct ? <>
          <Button onClick={() => onAction({ type: "accept", artifact })}>仅采用报告</Button>
          <Button type="primary" onClick={() => onAction({ type: "accept_and_continue", artifact })}>
            采用并创建下一步
          </Button>
          <Button onClick={() => setEditingRevision((open) => !open)}>提出修改</Button>
        </> : null}
      </div>

      {editingRevision ? (
        <section className="tz-artifact-card__revision" aria-label="修改成果">
          <Input.TextArea
            aria-label="修改说明"
            value={revisionNote}
            rows={3}
            maxLength={1000}
            placeholder="请写明需要调整的具体内容"
            onChange={(event) => setRevisionNote(event.target.value)}
          />
          <div>
            <Button onClick={() => setEditingRevision(false)}>取消</Button>
            <Button
              type="primary"
              disabled={!revisionNote.trim()}
              onClick={() => onAction({
                type: "request_revision",
                artifact,
                note: revisionNote.trim(),
              })}
            >
              提交修改
            </Button>
          </div>
        </section>
      ) : null}

      <section className="tz-artifact-card__evidence">
        <Button type="link" onClick={() => setEvidenceOpen((open) => !open)}>查看生成依据</Button>
        {evidenceOpen ? (
          <div className="tz-artifact-card__evidence-detail">
            <p>{artifact.quality ? `质量${artifact.quality.passed ? "通过" : "待复核"}（${Math.round(artifact.quality.score)} 分）` : "质量结果待补充"}</p>
            {evidence.length > 0 ? <ul>{evidence.map((item) => <li key={`${item.kind}-${item.id}`}>{safeText(item.label)}</li>)}</ul> : null}
          </div>
        ) : null}
      </section>
    </article>
  );
}

function BusinessSection({ section }: { section: ArtifactSection }) {
  return (
    <section className="tz-artifact-card__section">
      <h4>{BUSINESS_TITLES[section.key] ?? safeTitle(section.title)}</h4>
      <div>{renderContent(section.content)}</div>
    </section>
  );
}

function renderContent(content: ArtifactSection["content"]) {
  if (typeof content === "string") return safeText(content);
  if (Array.isArray(content)) {
    const items = content.map((item) => renderUnknown(item)).filter(Boolean);
    return items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : "—";
  }
  const entries = Object.entries(content)
    .filter(([key, value]) => !INTERNAL.test(key) && isSafeText(String(value)))
    .map(([key, value]) => `${safeTitle(key)}：${renderUnknown(value)}`)
    .filter(Boolean);
  return entries.length ? <ul>{entries.map((item) => <li key={item}>{item}</li>)}</ul> : "—";
}

function renderUnknown(value: unknown): string {
  if (typeof value === "string") return isSafeText(value) ? safeText(value) : "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function isBusinessSection(section: ArtifactSection) {
  return !INTERNAL.test(section.key) && !INTERNAL.test(section.title) && isSafeText(String(section.content));
}

function isSafeText(value: string) {
  return !INTERNAL.test(value);
}

function safeText(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function safeTitle(value: string) {
  return safeText(value).replaceAll("_", " ");
}

function statusCopy(status: Artifact["status"]) {
  return {
    draft: "草稿",
    ready_for_review: "待采用",
    accepted: "已采用",
    revision_requested: "已提出修改",
    superseded: "已更新",
  }[status];
}

function statusColor(status: Artifact["status"]) {
  return status === "accepted" ? "success" : status === "revision_requested" ? "processing" : "gold";
}
