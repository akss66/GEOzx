import { useQuery } from "@tanstack/react-query";
import { Button } from "antd";
import { useEffect, useMemo, useRef, useState } from "react";

import { listArtifacts } from "../../api/brain";
import type { Artifact, ArtifactStatus } from "../../types";

type Filters = {
  artifactType: string;
  status: ArtifactStatus | "";
  createdFrom: string;
  createdTo: string;
};

const INITIAL_FILTERS: Filters = {
  artifactType: "",
  status: "",
  createdFrom: "",
  createdTo: "",
};

const STATUS_OPTIONS: Array<{ value: ArtifactStatus; label: string }> = [
  { value: "draft", label: "草稿" },
  { value: "ready_for_review", label: "待采用" },
  { value: "accepted", label: "已采用" },
  { value: "revision_requested", label: "修改中" },
  { value: "superseded", label: "已更新" },
];

const ARTIFACT_TYPE_OPTIONS = [
  { value: "account_inspection_report", label: "账号体检报告" },
  { value: "positioning_strategy", label: "账号定位策略" },
  { value: "topic_plan", label: "选题规划" },
  { value: "publish_calendar", label: "发布日历" },
  { value: "video_script", label: "视频脚本" },
  { value: "art_prompt", label: "美术提示词" },
  { value: "video_asset", label: "视频素材" },
  { value: "edited_video", label: "剪辑成片" },
  { value: "review_report", label: "复盘报告" },
  { value: "ad_plan", label: "投放计划" },
  { value: "cs_record", label: "客服记录" },
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
      filters.artifactType,
      filters.status,
      filters.createdFrom,
      filters.createdTo,
      page,
    ],
    queryFn: () => listArtifacts({
      accountId: accountId!,
      artifactType: filters.artifactType || undefined,
      status: filters.status || undefined,
      page,
      pageSize: 20,
    }),
    enabled: accountId != null,
  });
  const accountArtifacts = useMemo(
    () => (query.data?.data ?? []).filter((artifact) => artifact.account_id === accountId),
    [accountId, query.data?.data],
  );
  const visibleArtifacts = useMemo(
    () => accountArtifacts.filter((artifact) => isInDateRange(artifact.created_at, filters)),
    [accountArtifacts, filters],
  );
  const updateFilter = <Key extends keyof Filters>(key: Key, value: Filters[Key]) => {
    setPage(1);
    setFilters((current) => ({ ...current, [key]: value }));
  };

  if (accountId == null) {
    return <section className="tz-artifact-center" aria-label="成果中心">请先选择账号，再查看该账号的正式成果。</section>;
  }

  return (
    <section className="tz-artifact-center" aria-label="成果中心">
      <header className="tz-artifact-center__header">
        <div>
          <span>当前账号</span>
          <h2>成果中心</h2>
        </div>
        <small>{filters.createdFrom || filters.createdTo ? "仅筛当前页" : `第 ${page} 页`}</small>
      </header>
      <div className="tz-artifact-center__filters" aria-label="成果筛选">
        <label>
          成果类型
          <select value={filters.artifactType} onChange={(event) => updateFilter("artifactType", event.target.value)}>
            <option value="">全部类型</option>
            {ARTIFACT_TYPE_OPTIONS.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
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

      {query.isPending ? <p role="status">正在加载成果…</p> : null}
      {query.isError ? (
        <div className="tz-artifact-center__error" role="alert">
          <p>成果暂时无法加载，请重试。</p>
          <Button onClick={() => void query.refetch()} aria-label="重试加载成果">重新加载</Button>
        </div>
      ) : null}
      {!query.isPending && !query.isError && visibleArtifacts.length === 0 ? (
        <p className="tz-artifact-center__empty">当前账号下没有符合筛选条件的成果。</p>
      ) : null}
      <div className="tz-artifact-center__list">
        {visibleArtifacts.map((artifact) => (
          <article key={artifact.id} className="tz-artifact-center__row">
            <div>
              <strong>{artifact.title}</strong>
              <span>{artifactTypeLabel(artifact.artifact_type)} · V{artifact.version} · {statusLabel(artifact.status)}</span>
            </div>
            <Button onClick={() => onSelect(artifact)} aria-label={`打开成果：${artifact.title}`}>查看</Button>
          </article>
        ))}
      </div>
      {query.data && query.data.pagination.pages > 1 ? (
        <footer className="tz-artifact-center__pagination">
          <Button disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>上一页</Button>
          <span>第 {page} / {query.data.pagination.pages} 页</span>
          <Button
            disabled={page >= query.data.pagination.pages}
            onClick={() => setPage((current) => current + 1)}
            aria-label="下一页"
          >
            下一页
          </Button>
        </footer>
      ) : null}
    </section>
  );
}

function artifactTypeLabel(value: string) {
  return ARTIFACT_TYPE_OPTIONS.find((option) => option.value === value)?.label ?? "其他成果";
}

function statusLabel(value: ArtifactStatus) {
  return STATUS_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

function isInDateRange(createdAt: string, filters: Filters) {
  const day = createdAt.slice(0, 10);
  return (!filters.createdFrom || day >= filters.createdFrom)
    && (!filters.createdTo || day <= filters.createdTo);
}
