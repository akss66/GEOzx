/* eslint-disable react-refresh/only-export-components -- artifact copy helper is shared with business-facing views */
import { Button, Input, Popconfirm, Tag } from "antd";
import { useMemo, useRef, useState } from "react";

import type { Artifact, ArtifactSection, DeliverableAction } from "../../types";
import { presentDeliverable } from "./deliverablePresentation";

export type ArtifactAction =
  | { type: "view_full_report"; artifact: Artifact }
  | {
      type: "execute";
      artifact: Artifact;
      action: DeliverableAction;
      input: Record<string, unknown>;
      idempotencyKey: string;
    }
  | { type: "export"; artifact: Artifact };

const INTERNAL = /(?:acceptance|checklist|content[ _-]?hash|debug|evidence[ _-]?hash|kernel|policy|prompt|sha256|source[ _-]?id|trace|raw|tool[ _-]?log|credential|stack)/i;
const BANNED_BUSINESS_COPY = /脚本生成中|正式成果|采用成果|成果/g;

const BUSINESS_TITLES: Record<string, string> = {
  core_conclusion: "核心结论",
  conclusion: "核心结论",
  data_period: "数据周期",
  period: "数据周期",
  date_range: "数据周期",
  key_metrics: "关键数据",
  key_facts: "关键事实",
  interpretation: "数据解读",
  data_limits: "数据限制",
  next_action: "下一步",
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
  actionPending = false,
}: {
  artifact: Artifact;
  onAction: (action: ArtifactAction) => void;
  revisionPending?: boolean;
  actionPending?: boolean;
}) {
  const actionKeys = useRef(new Map<string, string>());
  const [fullReportOpen, setFullReportOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [technicalEvidenceOpen, setTechnicalEvidenceOpen] = useState(false);
  const [evidencePage, setEvidencePage] = useState(1);
  const [editingRevision, setEditingRevision] = useState(false);
  const [revisionNote, setRevisionNote] = useState("");
  const [revisionDrafts, setRevisionDrafts] = useState<Record<string, string>>({});
  const [editingSchedule, setEditingSchedule] = useState(false);
  const [scheduledAt, setScheduledAt] = useState("");
  const isAccountAnalysis = artifact.artifact_type === "account_analysis_answer";
  const sections = useMemo(() => artifact.sections.filter(isBusinessSection), [artifact.sections]);
  const accountAnalysisSections = sections.filter((section) => [
    "conclusion",
    "key_facts",
    "interpretation",
    "recommendations",
    "data_limits",
    "next_action",
  ].includes(section.key));
  const primarySections = isAccountAnalysis
    ? accountAnalysisSections
    : sections.filter((section) => PRIMARY_KEYS.includes(section.key));
  const remainingSections = isAccountAnalysis
    ? []
    : sections.filter((section) => !PRIMARY_KEYS.includes(section.key));
  const hasRemainingDetails = remainingSections.length > 0;
  const evidence = artifact.evidence_refs.filter((item) => isSafeText(item.kind) && isSafeText(item.label));
  const evidenceSummary = artifact.evidence_summary ?? fallbackEvidenceSummary(evidence);
  const evidencePageSize = 10;
  const evidencePages = Math.max(1, Math.ceil(evidence.length / evidencePageSize));
  const visibleEvidence = evidence.slice(
    (evidencePage - 1) * evidencePageSize,
    evidencePage * evidencePageSize,
  );
  const revisionInProgress = revisionPending || artifact.status === "revision_requested";
  const presentation = presentDeliverable(artifact);
  const summary = businessText(artifact.summary, "当前运营内容已完成安全核验。");
  const revisionAction = artifact.next_actions.find((action) => action.code === "request_revision");
  const scheduleAction = artifact.next_actions.find((action) => action.code === "add_to_schedule");
  const directActions = artifact.next_actions.filter((action) => ![
    "request_revision",
    "add_to_schedule",
  ].includes(action.code));
  const executeAction = (action: DeliverableAction, input: Record<string, unknown>) => {
    const fingerprint = `${artifact.id}:${artifact.version}:${action.code}:${JSON.stringify(input)}`;
    const idempotencyKey = actionKeys.current.get(fingerprint)
      ?? `artifact-${artifact.id}-${action.code}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`;
    actionKeys.current.set(fingerprint, idempotencyKey);
    onAction({ type: "execute", artifact, action, input, idempotencyKey });
  };

  return (
    <article className="tz-artifact-card" aria-label={`运营内容：${presentation.typeLabel} · V${artifact.version}`}>
      <header className="tz-artifact-card__header">
        <div>
          <span className="tz-artifact-card__eyebrow">版本 · <strong>V{artifact.version}</strong></span>
          <h3>{presentation.typeLabel}</h3>
        </div>
        <Tag color={statusColor(artifact.status)}>{statusCopy(artifact.status)}</Tag>
      </header>

      <p className="tz-artifact-card__completion">{presentation.completionLabel}</p>
      {!isAccountAnalysis ? <p className="tz-artifact-card__summary">{summary}</p> : null}

      <div className={`tz-artifact-card__sections${isAccountAnalysis ? " tz-artifact-card__sections--analysis" : ""}`}>
        {isAccountAnalysis
          ? <AccountAnalysisBody sections={primarySections} />
          : primarySections.map((section) => <BusinessSection key={section.key} section={section} />)}
      </div>

      {revisionInProgress ? (
        <div className="tz-artifact-card__revision-progress" role="status">正在准备 V{artifact.version + 1} 更新内容</div>
      ) : null}

      <p className="tz-artifact-card__evidence-summary">
        {isAccountAnalysis ? "分析依据：" : "调用专家 / 依据："}
        {isAccountAnalysis
          ? analysisEvidenceSummaryCopy(evidenceSummary)
          : evidenceSummary.total > 0 ? `已核验 ${evidenceSummary.total} 条依据` : "暂无额外可核查依据"}
      </p>

      {hasRemainingDetails ? (
        <div
          id={`artifact-details-${artifact.id}-${artifact.version}`}
          className="tz-artifact-card__sections tz-artifact-card__sections--remaining"
          hidden={!fullReportOpen}
        >
          {remainingSections.map((section) => <BusinessSection key={section.key} section={section} />)}
        </div>
      ) : null}

      <div className="tz-artifact-card__actions">
        {hasRemainingDetails && !fullReportOpen ? (
          <Button type="primary" aria-expanded="false" aria-controls={`artifact-details-${artifact.id}-${artifact.version}`} onClick={() => {
            setFullReportOpen(true);
            onAction({ type: "view_full_report", artifact });
          }}>
            {presentation.primaryAction.label}
          </Button>
        ) : null}
        {directActions.map((action) => action.code === "export" ? (
          <Button key={action.code} onClick={() => onAction({ type: "export", artifact })}>
            {action.label}
          </Button>
        ) : action.requires_confirmation ? (
          <Popconfirm
            key={action.code}
            title={`确认${action.label}？`}
            description="确认后系统会创建真实业务记录，并保留可追踪的执行结果。"
            okText="确认执行"
            cancelText="取消"
            onConfirm={() => executeAction(action, { confirmed: true })}
          >
            <Button loading={actionPending} disabled={actionPending}>{action.label}</Button>
          </Popconfirm>
        ) : (
          <Button key={action.code} loading={actionPending} disabled={actionPending} onClick={() => executeAction(action, {})}>
            {action.label}
          </Button>
        ))}
        {scheduleAction ? (
          <Button disabled={actionPending} onClick={() => setEditingSchedule((open) => !open)}>{scheduleAction.label}</Button>
        ) : null}
        {revisionAction ? (
          <Button disabled={actionPending} onClick={() => setEditingRevision((open) => {
            if (!open) setRevisionDrafts(createRevisionDrafts(artifact));
            return !open;
          })}>{revisionAction.label}</Button>
        ) : null}
      </div>

      {editingSchedule && scheduleAction ? (
        <section className="tz-artifact-card__revision" aria-label="设置内容排期">
          <Input
            type="datetime-local"
            aria-label="计划发布时间"
            value={scheduledAt}
            onChange={(event) => setScheduledAt(event.target.value)}
          />
          <div>
            <Button onClick={() => setEditingSchedule(false)}>取消</Button>
            <Popconfirm
              title="确认加入内容排期？"
              description="系统将按你选择的时间创建一条真实排期记录。"
              okText="确认排期"
              cancelText="再检查一下"
              disabled={!scheduledAt}
              onConfirm={() => executeAction(scheduleAction, {
                  confirmed: true,
                  scheduled_at: new Date(scheduledAt).toISOString(),
                  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
                })}
            >
              <Button type="primary" loading={actionPending} disabled={!scheduledAt || actionPending}>确认排期</Button>
            </Popconfirm>
          </div>
        </section>
      ) : null}

      {editingRevision ? (
        <section className="tz-artifact-card__revision" aria-label="修改运营内容">
          <p>直接修改下面的业务内容；保存后会生成一个新的可追踪版本。</p>
          {sections.filter(isEditableRevisionSection).map((section) => (
            <label key={section.key}>
              <span>{BUSINESS_TITLES[section.key] ?? businessText(section.title, "业务内容")}</span>
              <Input.TextArea
                aria-label={`修改${BUSINESS_TITLES[section.key] ?? businessText(section.title, "业务内容")}`}
                value={revisionDrafts[section.key] ?? ""}
                autoSize={{ minRows: 2, maxRows: 8 }}
                onChange={(event) => setRevisionDrafts((current) => ({
                  ...current,
                  [section.key]: event.target.value,
                }))}
              />
            </label>
          ))}
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
              loading={actionPending}
              disabled={!revisionNote.trim() || actionPending}
              onClick={() => revisionAction && executeAction(revisionAction, {
                  note: revisionNote.trim(),
                  payload: buildArtifactRevisionPayload(artifact, revisionDrafts),
                })}
            >
              提交修改
            </Button>
          </div>
        </section>
      ) : null}

      <section className="tz-artifact-card__evidence">
        <Button type="link" onClick={() => setEvidenceOpen((open) => !open)}>
          {isAccountAnalysis ? "查看分析依据" : "查看生成依据"}
        </Button>
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
                <summary>{isAccountAnalysis ? "技术详情" : "技术依据"}（{evidence.length} 条）</summary>
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

function buildArtifactRevisionPayload(
  artifact: Artifact,
  drafts: Record<string, string>,
): Record<string, unknown> {
  return {
    title: artifact.title,
    summary: artifact.summary,
    ...(artifact.presentation_format
      ? { presentation_format: artifact.presentation_format }
      : {}),
    ...Object.fromEntries(artifact.sections.filter(isBusinessSection).map((section) => [
      section.key,
      parseRevisionDraft(section.content, drafts[section.key]),
    ])),
  };
}

function createRevisionDrafts(artifact: Artifact) {
  return Object.fromEntries(artifact.sections.filter(isEditableRevisionSection).map((section) => [
    section.key,
    formatRevisionDraft(section.content),
  ]));
}

function isEditableRevisionSection(section: ArtifactSection) {
  return isBusinessSection(section) && !["participating_experts", "critic"].includes(section.key);
}

function formatRevisionDraft(value: ArtifactSection["content"]): string {
  if (Array.isArray(value)) return value.map((item) => renderBusinessValue(item)).join("\n");
  if (typeof value === "object" && value !== null) return JSON.stringify(value, null, 2);
  return String(value);
}

function parseRevisionDraft(original: ArtifactSection["content"], draft: string | undefined) {
  if (draft == null) return original;
  if (Array.isArray(original)) return draft.split("\n").map((item) => item.trim()).filter(Boolean);
  if (typeof original === "number") return Number(draft);
  if (typeof original === "boolean") return draft.trim().toLowerCase() === "true";
  if (typeof original === "object" && original !== null) {
    try {
      return JSON.parse(draft) as Record<string, unknown>;
    } catch {
      return original;
    }
  }
  return draft.trim();
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
  const period = group.period ? safeText(group.period) : "";
  const periodCopy = period ? `，${period}` : "";
  return `${safeText(group.label)}：${group.count} 条${metricCopy}${periodCopy}`;
}

function analysisEvidenceSummaryCopy(summary: NonNullable<Artifact["evidence_summary"]>) {
  if (summary.total <= 0) return "当前没有可核查的数据记录";
  const metricCount = summary.groups.reduce((total, group) => total + group.metric_count, 0);
  return metricCount > 0
    ? `已核验 ${metricCount} 类指标、${summary.total} 条数据记录`
    : `已核验 ${summary.total} 条数据记录`;
}

function AccountAnalysisBody({ sections }: { sections: ArtifactSection[] }) {
  return (
    <>
      {sections.map((section) => (
        <section
          key={section.key}
          className={`tz-artifact-card__section tz-artifact-card__section--analysis tz-artifact-card__section--${section.key}`}
        >
          <h4>{accountAnalysisTitle(section.key)}</h4>
          <div>{renderAccountAnalysisContent(section)}</div>
        </section>
      ))}
    </>
  );
}

function accountAnalysisTitle(key: string) {
  return key === "recommendations" ? "下一步建议" : BUSINESS_TITLES[key] ?? "分析信息";
}

function renderAccountAnalysisContent(section: ArtifactSection) {
  if (section.key === "key_facts" && Array.isArray(section.content)) {
    const facts = section.content.map(formatAnalysisFact).filter(Boolean);
    return facts.length > 0 ? <ul>{facts.map((fact) => <li key={fact}>{fact}</li>)}</ul> : "—";
  }
  if (section.key === "recommendations" && Array.isArray(section.content)) {
    const recommendations = section.content.map(formatRecommendation).filter(Boolean);
    return recommendations.length > 0
      ? <ol>{recommendations.map((recommendation) => <li key={recommendation}>{recommendation}</li>)}</ol>
      : "—";
  }
  return renderContent(section.content);
}

function formatAnalysisFact(value: unknown) {
  if (!isRecord(value)) return "";
  const label = typeof value.label === "string" ? safeText(value.label) : "";
  const current = formatMetricValue(value.current_value, value.unit);
  if (!label || !current) return "";
  const comparison = formatMetricComparison(value);
  return comparison ? `${label}：${current}，${comparison}` : `${label}：${current}`;
}

function formatMetricValue(value: unknown, unit: unknown) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  const formatted = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
  const safeUnit = typeof unit === "string" && isSafeText(unit) ? safeText(unit) : "";
  return `${formatted}${safeUnit ? ` ${safeUnit}` : ""}`;
}

function formatMetricComparison(value: Record<string, unknown>) {
  if (typeof value.previous_value !== "number" || !Number.isFinite(value.previous_value)) return "";
  const direction = value.direction === "up" ? "上升" : value.direction === "down" ? "下降" : "基本持平";
  if (typeof value.relative_change !== "number" || !Number.isFinite(value.relative_change)) {
    return `较上一周期${direction}`;
  }
  const percent = new Intl.NumberFormat("zh-CN", {
    style: "percent",
    maximumFractionDigits: 1,
  }).format(Math.abs(value.relative_change));
  return `较上一周期${direction} ${percent}`;
}

function formatRecommendation(value: unknown) {
  if (!isRecord(value)) return "";
  const action = typeof value.action === "string" ? safeText(value.action) : "";
  if (!action || !isSafeText(action)) return "";
  const rationale = typeof value.rationale === "string" && isSafeText(value.rationale)
    ? safeText(value.rationale)
    : "";
  const metric = typeof value.validation_metric === "string" && isSafeText(value.validation_metric)
    ? safeText(value.validation_metric)
    : "";
  const days = typeof value.observation_days === "number" && Number.isInteger(value.observation_days)
    ? value.observation_days
    : null;
  const rationaleCopy = rationale ? `原因：${rationale}` : "";
  const validationCopy = metric ? `用${metric}${days ? `观察 ${days} 天` : "验证"}` : "";
  return [action, rationaleCopy, validationCopy].filter(Boolean).join("；");
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
  if (typeof content === "number" || typeof content === "boolean") return String(content);
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
  return value.replace(/\s+/g, " ").trim().replace(BANNED_BUSINESS_COPY, "运营内容");
}

function businessText(value: string, fallback: string) {
  const clean = safeText(value);
  return clean && isSafeText(clean) && (hasChinese(clean) || !/[a-z]/i.test(clean)) ? clean : fallback;
}

export function businessArtifactTitle(artifact: Artifact) {
  return businessText(artifact.title, "运营报告");
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
    ready_for_review: "待你确认",
    accepted: "已完成",
    revision_requested: "需要修改",
    superseded: "已完成",
  }[status];
}

function statusColor(status: Artifact["status"]) {
  return status === "accepted" ? "success" : status === "revision_requested" ? "processing" : "gold";
}
