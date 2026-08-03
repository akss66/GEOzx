/* eslint-disable react-refresh/only-export-components -- artifact copy helper is shared with business-facing views */
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
  highlights: "关键亮点",
  critic: "质量审核",
};

const NESTED_BUSINESS_TITLES: Record<string, string> = {
  engagement_rate: "互动率",
  completion_rate: "完播率",
  conversion_rate: "转化率",
  views: "播放量",
  likes: "点赞量",
  comments: "评论量",
  shares: "分享量",
  followers: "粉丝数",
  new_followers: "新增粉丝",
  follower_growth: "粉丝增长",
  revenue: "营收",
  cost: "成本",
  roi: "投入产出比",
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
  "critic",
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
  const [technicalEvidenceOpen, setTechnicalEvidenceOpen] = useState(false);
  const [evidencePage, setEvidencePage] = useState(1);
  const [editingRevision, setEditingRevision] = useState(false);
  const [revisionNote, setRevisionNote] = useState("");
  const sections = useMemo(() => artifact.sections.filter(isBusinessSection), [artifact.sections]);
  const primarySections = sections.filter((section) => PRIMARY_KEYS.includes(section.key));
  const remainingSections = sections.filter((section) => !PRIMARY_KEYS.includes(section.key));
  const evidence = artifact.evidence_refs.filter((item) => isSafeText(item.kind) && isSafeText(item.label));
  const evidenceSummary = artifact.evidence_summary ?? fallbackEvidenceSummary(evidence);
  const evidencePageSize = 10;
  const evidencePages = Math.max(1, Math.ceil(evidence.length / evidencePageSize));
  const visibleEvidence = evidence.slice(
    (evidencePage - 1) * evidencePageSize,
    evidencePage * evidencePageSize,
  );
  const canAct = ["draft", "ready_for_review"].includes(artifact.status);
  const revisionInProgress = revisionPending || artifact.status === "revision_requested";
  const title = businessArtifactTitle(artifact);
  const summary = businessText(artifact.summary, "成果内容已完成安全核验。");

  return (
    <article className="tz-artifact-card" aria-label={`Artifact: ${title}`}>
      <header className="tz-artifact-card__header">
        <div>
          <span className="tz-artifact-card__eyebrow">正式成果 · <strong>V{artifact.version}</strong></span>
          <h3>{title}</h3>
        </div>
        <Tag color={statusColor(artifact.status)}>{statusCopy(artifact.status)}</Tag>
      </header>

      <p className="tz-artifact-card__summary">{summary}</p>

      <div className="tz-artifact-card__sections">
        {primarySections.map((section) => <BusinessSection key={section.key} section={section} />)}
      </div>

      {revisionInProgress ? (
        <div className="tz-artifact-card__revision-progress" role="status">正在生成 V{artifact.version + 1}</div>
      ) : null}

      <p className="tz-artifact-card__evidence-summary">
        调用专家 / 依据：{evidenceSummary.total > 0 ? `已核验 ${evidenceSummary.total} 条依据` : "暂无额外可核查依据"}
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
            {evidenceSummary.groups.length > 0 ? (
              <ul aria-label="业务依据摘要">
                {evidenceSummary.groups.map((group) => (
                  <li key={group.kind}>{evidenceGroupCopy(group)}</li>
                ))}
              </ul>
            ) : null}
            {evidence.length > 0 ? (
              <details
                open={technicalEvidenceOpen}
                onToggle={(event) => setTechnicalEvidenceOpen(event.currentTarget.open)}
              >
                <summary>技术依据（{evidence.length} 条）</summary>
                <ul>
                  {visibleEvidence.map((item) => (
                    <li key={`${item.kind}-${item.id}`}>{safeText(item.label)}</li>
                  ))}
                </ul>
                {evidencePages > 1 ? (
                  <div className="tz-artifact-card__evidence-pagination">
                    <Button
                      size="small"
                      disabled={evidencePage === 1}
                      onClick={() => setEvidencePage((page) => Math.max(1, page - 1))}
                    >
                      上一页
                    </Button>
                    <span>{evidencePage} / {evidencePages}</span>
                    <Button
                      size="small"
                      disabled={evidencePage === evidencePages}
                      onClick={() => setEvidencePage((page) => Math.min(evidencePages, page + 1))}
                    >
                      下一页
                    </Button>
                  </div>
                ) : null}
              </details>
            ) : null}
          </div>
        ) : null}
      </section>
    </article>
  );
}

function fallbackEvidenceSummary(evidence: Artifact["evidence_refs"]) {
  return {
    total: evidence.length,
    groups: evidence.length > 0 ? [{
      kind: "business_evidence",
      label: "业务数据依据",
      count: evidence.length,
      metric_count: 0,
      period: null,
    }] : [],
  };
}

function evidenceGroupCopy(group: NonNullable<Artifact["evidence_summary"]>["groups"][number]) {
  const metricCopy = group.metric_count > 0 ? `，覆盖 ${group.metric_count} 项指标` : "";
  const periodCopy = group.period ? `，${group.period}` : "";
  return `${safeText(group.label)}：${group.count} 条${metricCopy}${periodCopy}`;
}

function BusinessSection({ section }: { section: ArtifactSection }) {
  return (
    <section className="tz-artifact-card__section">
      <h4>{BUSINESS_TITLES[section.key] ?? businessText(section.title, "业务信息")}</h4>
      <div>{section.key === "critic" ? "质量审核已完成，详细依据请查看生成依据。" : renderContent(section.content)}</div>
    </section>
  );
}

function renderContent(content: ArtifactSection["content"]) {
  if (typeof content === "string") return isSafeText(content) ? safeText(content) : "—";
  if (Array.isArray(content)) {
    const items = content.map((item) => renderBusinessValue(item)).filter(Boolean);
    return items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : "—";
  }
  const entries = Object.entries(content)
    .map(([key, value]) => {
      const label = businessObjectLabel(key);
      const display = renderBusinessValue(value);
      return label && display ? `${label}：${display}` : "";
    })
    .filter(Boolean);
  return entries.length ? <ul>{entries.map((item) => <li key={item}>{item}</li>)}</ul> : "—";
}

function renderBusinessValue(value: unknown): string {
  if (typeof value === "string") return isSafeText(value) ? safeText(value) : "";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(renderBusinessValue).filter(Boolean).join("；");
  if (isRecord(value)) {
    return Object.entries(value)
      .map(([key, nested]) => {
        const label = businessObjectLabel(key);
        const display = renderBusinessValue(nested);
        return label && display ? `${label}：${display}` : "";
      })
      .filter(Boolean)
      .join("；");
  }
  return "";
}

function isBusinessSection(section: ArtifactSection) {
  return !INTERNAL.test(section.key) && !INTERNAL.test(section.title);
}

function isSafeText(value: string) {
  return !INTERNAL.test(value);
}

function safeText(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function businessText(value: string, fallback: string) {
  const clean = safeText(value);
  return clean && isSafeText(clean) && (hasChinese(clean) || !/[a-z]/i.test(clean)) ? clean : fallback;
}

export function businessArtifactTitle(artifact: Artifact) {
  return businessText(artifact.title, "正式成果");
}

function businessObjectLabel(key: string) {
  if (INTERNAL.test(key)) return null;
  if (NESTED_BUSINESS_TITLES[key]) return NESTED_BUSINESS_TITLES[key];
  return hasChinese(key) && isSafeText(key) ? safeText(key) : null;
}

function hasChinese(value: string) {
  return /[\u3400-\u9fff]/.test(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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
