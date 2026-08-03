import { useQuery } from "@tanstack/react-query";
import { Button } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { listArtifacts } from "../../api/brain";
import type { Artifact, ArtifactStatus } from "../../types";
import { presentDeliverable } from "./deliverablePresentation";

type BusinessGroup = "diagnosis" | "benchmark" | "topics" | "shooting" | "publishing";

type Filters = {
  businessGroup: BusinessGroup | "";
  status: ArtifactStatus | "";
  createdFrom: string;
  createdTo: string;
};

const INITIAL_FILTERS: Filters = {
  businessGroup: "",
  status: "",
  createdFrom: "",
  createdTo: "",
};

const STATUS_OPTIONS: Array<{ value: ArtifactStatus; label: string }> = [
  { value: "draft", label: "草稿" },
  { value: "ready_for_review", label: "待你确认" },
  { value: "accepted", label: "已完成" },
  { value: "revision_requested", label: "需要修改" },
  { value: "superseded", label: "已完成" },
];

const BUSINESS_GROUPS: Array<{ key: BusinessGroup; label: string; artifactTypes: string[] }> = [
  {
    key: "diagnosis",
    label: "诊断与复盘",
    artifactTypes: ["account_inspection_report", "review_report", "engagement_review", "cs_record"],
  },
  {
    key: "benchmark",
    label: "对标分析",
    artifactTypes: ["positioning_strategy", "account_positioning"],
  },
  { key: "topics", label: "选题", artifactTypes: ["topic_plan"] },
  {
    key: "shooting",
    label: "拍摄稿",
    artifactTypes: ["video_script", "visual_brief", "art_prompt", "video_asset", "edited_video"],
  },
  {
    key: "publishing",
    label: "发布安排",
    artifactTypes: [
      "publish_calendar",
      "content_calendar",
      "platform_publish_receipt",
      "operation_execution_plan",
      "ad_plan",
    ],
  },
];

export function ArtifactCenter({
  accountId,
  onSelect,
}: {
  accountId: number | null;
  onSelect: (artifact: Artifact | null) => void;
}) {
  const [filters, setFilters] = useState<Filters>(INITIAL_FILTERS);
  const [page, setPage] = useState(1);
  const onSelectRef = useRef(onSelect);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    setFilters(INITIAL_FILTERS);
    setPage(1);
    onSelectRef.current(null);
  }, [accountId]);

  const query = useQuery({
    queryKey: [
      "account-artifacts",
      accountId,
      groupTypes(filters.businessGroup),
      filters.status,
      filters.createdFrom,
      filters.createdTo,
      page,
    ],
    queryFn: () => listArtifacts({
      accountId: accountId!,
      ...(groupTypes(filters.businessGroup) ? { artifactTypes: groupTypes(filters.businessGroup) } : {}),
      status: filters.status || undefined,
      ...(filters.createdFrom ? { createdFrom: filters.createdFrom } : {}),
      ...(filters.createdTo ? { createdTo: filters.createdTo } : {}),
      page,
      pageSize: 20,
    }),
    enabled: accountId != null,
  });
  const accountArtifacts = useMemo(
    () => (query.data?.data ?? []).filter((artifact) => artifact.account_id === accountId),
    [accountId, query.data?.data],
  );
  const groupedArtifacts = useMemo(() => BUSINESS_GROUPS.map((group) => ({
    ...group,
    artifacts: accountArtifacts.filter((artifact) => groupFor(artifact) === group.key),
  })), [accountArtifacts]);
  const unclassifiedArtifacts = useMemo(
    () => accountArtifacts.filter((artifact) => groupFor(artifact) == null),
    [accountArtifacts],
  );
  const dateRangeError = isReversedDateRangeError(query.error);
  const updateFilter = <Key extends keyof Filters>(key: Key, value: Filters[Key]) => {
    setPage(1);
    setFilters((current) => ({ ...current, [key]: value }));
  };

  if (accountId == null) {
    return <section className="tz-artifact-center" aria-label="方案与内容">请先选择账号，再查看该账号的方案与内容。</section>;
  }

  return (
    <section className="tz-artifact-center" aria-label="方案与内容">
      <header className="tz-artifact-center__header">
        <div>
          <span>当前账号</span>
          <h2>方案与内容</h2>
        </div>
        <small>第 {page} 页</small>
      </header>
      <div className="tz-artifact-center__filters" aria-label="方案与内容筛选">
        <label>
          业务类型
          <select
            value={filters.businessGroup}
            onChange={(event) => updateFilter("businessGroup", event.target.value as Filters["businessGroup"])}
          >
            <option value="">全部业务</option>
            {BUSINESS_GROUPS.map((group) => (
              <option key={group.key} value={group.key}>{group.label}</option>
            ))}
          </select>
        </label>
        <label>
          状态
          <select value={filters.status} onChange={(event) => updateFilter("status", event.target.value as Filters["status"])}>
            <option value="">全部状态</option>
            {STATUS_OPTIONS.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
          </select>
        </label>
        <label>
          创建时间（起）
          <input type="date" value={filters.createdFrom} onChange={(event) => updateFilter("createdFrom", event.target.value)} />
        </label>
        <label>
          创建时间（止）
          <input type="date" value={filters.createdTo} onChange={(event) => updateFilter("createdTo", event.target.value)} />
        </label>
      </div>

      {query.isPending ? <p role="status">正在加载方案与内容…</p> : null}
      {query.isError ? (
        <div className="tz-artifact-center__error" role="alert">
          <p>{dateRangeError ? "创建时间的开始日期不能晚于结束日期。" : "方案与内容暂时无法加载，请重试。"}</p>
          {!dateRangeError ? (
            <Button onClick={() => void query.refetch()} aria-label="重新加载方案与内容">重新加载</Button>
          ) : null}
        </div>
      ) : null}
      {!query.isPending && !query.isError && accountArtifacts.length === 0 ? (
        <p className="tz-artifact-center__empty">当前账号下没有符合筛选条件的方案与内容。</p>
      ) : null}
      <div className="tz-artifact-center__list">
        {groupedArtifacts.map((group) => (
          <section key={group.key} className="tz-artifact-center__group" aria-label={group.label}>
            <h3>{group.label}</h3>
            {group.artifacts.length === 0 ? <p>暂无内容</p> : group.artifacts.map((artifact) => (
              <article key={artifact.id} className="tz-artifact-center__row">
                <div>
                  <strong>{presentDeliverable(artifact).typeLabel}</strong>
                  <span>V{artifact.version} · {statusLabel(artifact.status)} · 创建于 {formatCreatedAt(artifact.created_at)}</span>
                  {dataPeriod(artifact) ? <span>数据周期：{dataPeriod(artifact)}</span> : null}
                  <span>下一步：{nextStep(artifact)}</span>
                </div>
                <Button onClick={() => onSelect(artifact)} aria-label={`查看方案与内容：${presentDeliverable(artifact).typeLabel}`}>查看</Button>
              </article>
            ))}
          </section>
        ))}
      </div>
      {unclassifiedArtifacts.length > 0 ? (
        <p className="tz-artifact-center__unclassified" role="status">
          有 {unclassifiedArtifacts.length} 项新类型内容待归类
        </p>
      ) : null}
      {query.data && query.data.pagination.pages > 1 ? (
        <footer className="tz-artifact-center__pagination">
          <Button disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>上一页</Button>
          <span>第 {page} / {query.data.pagination.pages} 页</span>
          <Button disabled={page >= query.data.pagination.pages} onClick={() => setPage((current) => current + 1)} aria-label="下一页">下一页</Button>
        </footer>
      ) : null}
    </section>
  );
}

function groupFor(artifact: Artifact): BusinessGroup | null {
  return BUSINESS_GROUPS.find((group) => group.artifactTypes.includes(artifact.artifact_type))?.key ?? null;
}

function groupTypes(group: BusinessGroup | "") {
  return group ? BUSINESS_GROUPS.find((item) => item.key === group)?.artifactTypes : undefined;
}

function statusLabel(value: ArtifactStatus) {
  return STATUS_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

function isReversedDateRangeError(error: unknown) {
  const response = (error as {
    response?: { status?: unknown; data?: { detail?: unknown } };
  } | null)?.response;
  return response?.status === 422
    && response.data?.detail === "created_from must be on or before created_to";
}

function dataPeriod(artifact: Artifact) {
  return textSection(artifact, ["data_period", "period", "date_range"]);
}

function nextStep(artifact: Artifact) {
  return textSection(artifact, ["next_step", "next_action", "next_actions", "action_items"])
    ?? "未提供";
}

function textSection(artifact: Artifact, keys: string[]) {
  const content = artifact.sections.find((section) => keys.includes(section.key))?.content;
  if (typeof content === "string" && content.trim()) return content.trim();
  if (Array.isArray(content)) {
    const first = content.find((item): item is string => typeof item === "string" && item.trim().length > 0);
    return first?.trim();
  }
  return null;
}

function formatCreatedAt(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString("zh-CN");
}
