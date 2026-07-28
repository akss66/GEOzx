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
  { value: "draft", label: "Draft" },
  { value: "ready_for_review", label: "Ready for review" },
  { value: "accepted", label: "Accepted" },
  { value: "revision_requested", label: "Revision requested" },
  { value: "superseded", label: "Superseded" },
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
  const typeOptions = useMemo(
    () => Array.from(new Set(accountArtifacts.map((artifact) => artifact.artifact_type))).sort(),
    [accountArtifacts],
  );

  const updateFilter = <Key extends keyof Filters>(key: Key, value: Filters[Key]) => {
    setPage(1);
    setFilters((current) => ({ ...current, [key]: value }));
  };

  if (accountId == null) {
    return <section className="tz-artifact-center" aria-label="Artifact center">Select an account to view its results.</section>;
  }

  return (
    <section className="tz-artifact-center" aria-label="Artifact center">
      <header className="tz-artifact-center__header">
        <div>
          <span>Account results</span>
          <h2>Artifacts</h2>
        </div>
        <small>{filters.createdFrom || filters.createdTo ? "Current page only" : `Page ${page}`}</small>
      </header>
      <div className="tz-artifact-center__filters" aria-label="Artifact filters">
        <label>
          Artifact type
          <select value={filters.artifactType} onChange={(event) => updateFilter("artifactType", event.target.value)}>
            <option value="">All types</option>
            {typeOptions.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
        </label>
        <label>
          Artifact status
          <select value={filters.status} onChange={(event) => updateFilter("status", event.target.value as Filters["status"])}>
            <option value="">All statuses</option>
            {STATUS_OPTIONS.map((status) => <option key={status.value} value={status.value}>{status.label}</option>)}
          </select>
        </label>
        <label>
          Created from
          <input type="date" value={filters.createdFrom} onChange={(event) => updateFilter("createdFrom", event.target.value)} />
        </label>
        <label>
          Created to
          <input type="date" value={filters.createdTo} onChange={(event) => updateFilter("createdTo", event.target.value)} />
        </label>
      </div>

      {query.isPending ? <p role="status">Loading artifacts…</p> : null}
      {query.isError ? (
        <div className="tz-artifact-center__error" role="alert">
          <p>Artifacts are temporarily unavailable.</p>
          <Button onClick={() => void query.refetch()} aria-label="Retry artifacts">Retry</Button>
        </div>
      ) : null}
      {!query.isPending && !query.isError && visibleArtifacts.length === 0 ? (
        <p className="tz-artifact-center__empty">No results match the current account and filters.</p>
      ) : null}
      <div className="tz-artifact-center__list">
        {visibleArtifacts.map((artifact) => (
          <article key={artifact.id} className="tz-artifact-center__row">
            <div>
              <strong>{artifact.title}</strong>
              <span>{artifact.artifact_type} · V{artifact.version} · {artifact.status}</span>
            </div>
            <Button onClick={() => onSelect(artifact)} aria-label={`Open ${artifact.title}`}>Open</Button>
          </article>
        ))}
      </div>
      {query.data && query.data.pagination.pages > 1 ? (
        <footer className="tz-artifact-center__pagination">
          <Button disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>Previous page</Button>
          <span>Page {page} of {query.data.pagination.pages}</span>
          <Button
            disabled={page >= query.data.pagination.pages}
            onClick={() => setPage((current) => current + 1)}
            aria-label="Next page"
          >
            Next page
          </Button>
        </footer>
      ) : null}
    </section>
  );
}

function isInDateRange(createdAt: string, filters: Filters) {
  const day = createdAt.slice(0, 10);
  return (!filters.createdFrom || day >= filters.createdFrom)
    && (!filters.createdTo || day <= filters.createdTo);
}
