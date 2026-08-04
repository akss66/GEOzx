import { Button } from "antd";
import { useEffect, useState } from "react";

import { getArtifact } from "../../api/brain";
import type { Artifact } from "../../types";
import { ArtifactCard, type ArtifactAction } from "./ArtifactCard";

export function TurnArtifact({
  artifactId,
  accountId,
  threadAccountId,
  threadId,
  sourceTurnId,
  refreshKey,
  onAction,
  revisingArtifactId,
  actionPendingArtifactId,
  revisionArtifacts = [],
  sourceArtifactOverride,
  ...shared
}: {
  artifactId: number;
  accountId: number;
  threadAccountId: number;
  threadId: number;
  sourceTurnId: number;
  refreshKey: number;
  onAction?: (action: ArtifactAction) => void;
  revisingArtifactId: number | null;
  actionPendingArtifactId?: number | null;
  revisionArtifacts?: Artifact[];
  sourceArtifactOverride?: Artifact;
  className: string;
  "data-testid": string;
  "data-projection-key": string;
}) {
  const [state, setState] = useState<ArtifactState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setState((previous) => previous.artifact
      ? { kind: "refreshing", artifact: previous.artifact }
      : { kind: "loading" });
    void getArtifact(artifactId)
      .then((artifact) => {
        if (!current) return;
        setState((previous) => matchesSource(artifact, {
          artifactId,
          accountId,
          threadAccountId,
          threadId,
          sourceTurnId,
        })
          ? { kind: "ready", artifact }
          : previous.artifact
            ? { kind: "refresh-invalid", artifact: previous.artifact }
            : { kind: "invalid" });
      })
      .catch(() => {
        if (!current) return;
        setState((previous) => previous.artifact
          ? { kind: "refresh-error", artifact: previous.artifact }
          : { kind: "error" });
      });
    return () => { current = false; };
  }, [accountId, artifactId, attempt, refreshKey, sourceTurnId, threadAccountId, threadId]);

  const source = state.artifact && sourceArtifactOverride
    && matchesSourceOverride(sourceArtifactOverride, state.artifact)
    ? sourceArtifactOverride
    : state.artifact;
  const revisionChain = source ? validatedRevisionChain(revisionArtifacts, source) : null;

  return (
    <section {...shared} aria-live="polite">
      {source ? (
        <>
          <ArtifactCard
            artifact={source}
            onAction={onAction ?? (() => {})}
            revisionPending={revisingArtifactId === source.id}
            actionPending={actionPendingArtifactId === source.id}
          />
          {revisionChain?.map((revision, index) => (
            <section
              key={revision.id}
              className="tz-artifact-card__revision-version"
              aria-label={`Revision V${revision.version}`}
            >
              <p>{index === revisionChain.length - 1
                ? `修订后的最新版本 V${revision.version}`
                : `已保留修订版本 V${revision.version}`}</p>
              <ArtifactCard
                artifact={revision}
                onAction={onAction ?? (() => {})}
                revisionPending={revisingArtifactId === revision.id}
                actionPending={actionPendingArtifactId === revision.id}
              />
            </section>
          ))}
          {revisionChain === null ? <span>修订版本校验失败，请重试。</span> : null}
        </>
      ) : null}
      {state.kind === "loading" ? <span>正在校验运营内容…</span> : null}
      {state.kind === "refreshing" ? <span>正在更新运营内容…</span> : null}
      {state.kind === "invalid" ? <span>运营内容校验失败，请重试。</span> : null}
      {state.kind === "error" ? <span>运营内容暂时无法加载，请重试。</span> : null}
      {state.kind === "refresh-invalid" ? <span>运营内容更新校验失败，已保留已验证版本。</span> : null}
      {state.kind === "refresh-error" ? <span>运营内容更新失败，已保留已验证版本。</span> : null}
      {(state.kind !== "ready" && state.kind !== "loading" && state.kind !== "refreshing") || revisionChain === null ? (
        <Button size="small" onClick={() => setAttempt((value) => value + 1)}>重试</Button>
      ) : null}
    </section>
  );
}

type ArtifactState =
  | { kind: "loading"; artifact?: undefined }
  | { kind: "refreshing"; artifact: Artifact }
  | { kind: "invalid"; artifact?: undefined }
  | { kind: "refresh-invalid"; artifact: Artifact }
  | { kind: "error"; artifact?: undefined }
  | { kind: "refresh-error"; artifact: Artifact }
  | { kind: "ready"; artifact: Artifact };

function validatedRevisionChain(revisions: Artifact[], source: Artifact): Artifact[] | null {
  const seenIds = new Set<number>([source.id]);
  let previousVersion = source.version;

  for (const revision of revisions) {
    if (seenIds.has(revision.id)
      || revision.version !== previousVersion + 1
      || revision.account_id !== source.account_id
      || revision.thread_id !== source.thread_id
      || revision.turn_id !== source.turn_id
      || revision.artifact_type !== source.artifact_type) {
      return null;
    }
    seenIds.add(revision.id);
    previousVersion = revision.version;
  }
  return revisions;
}

function matchesSourceOverride(override: Artifact, source: Artifact) {
  return override.id === source.id
    && override.version === source.version
    && override.account_id === source.account_id
    && override.thread_id === source.thread_id
    && override.turn_id === source.turn_id
    && override.artifact_type === source.artifact_type;
}

function matchesSource(
  artifact: Artifact,
  source: {
    artifactId: number;
    accountId: number;
    threadAccountId: number;
    threadId: number;
    sourceTurnId: number;
  },
) {
  return artifact.id === source.artifactId
    && artifact.account_id === source.accountId
    && source.accountId === source.threadAccountId
    && artifact.thread_id === source.threadId
    && artifact.turn_id === source.sourceTurnId;
}
