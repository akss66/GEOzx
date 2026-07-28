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
  revisionPending,
  revisionArtifact,
  ...shared
}: {
  artifactId: number;
  accountId: number;
  threadAccountId: number;
  threadId: number;
  sourceTurnId: number;
  refreshKey: number;
  onAction?: (action: ArtifactAction) => void;
  revisionPending: boolean;
  revisionArtifact?: Artifact;
  className: string;
  "data-testid": string;
  "data-projection-key": string;
}) {
  const [state, setState] = useState<ArtifactState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setState({ kind: "loading" });
    void getArtifact(artifactId)
      .then((artifact) => {
        if (!current) return;
        setState(matchesSource(artifact, {
          artifactId,
          accountId,
          threadAccountId,
          threadId,
          sourceTurnId,
        }) ? { kind: "ready", artifact } : { kind: "invalid" });
      })
      .catch(() => {
        if (current) setState({ kind: "error" });
      });
    return () => { current = false; };
  }, [accountId, artifactId, attempt, refreshKey, sourceTurnId, threadAccountId, threadId]);

  const revision = state.kind === "ready" && revisionArtifact && matchesRevision(revisionArtifact, state.artifact)
    ? revisionArtifact
    : null;

  return (
    <section {...shared} aria-live="polite">
      {state.kind === "ready" ? (
        <>
          <ArtifactCard
            artifact={state.artifact}
            onAction={onAction ?? (() => {})}
            revisionPending={revisionPending && !revision}
          />
          {revision ? (
            <section className="tz-artifact-card__revision-version" aria-label={`Revision V${revision.version}`}>
              <p>修订后的最新版本 V{revision.version}</p>
              <ArtifactCard artifact={revision} onAction={onAction ?? (() => {})} />
            </section>
          ) : null}
        </>
      ) : null}
      {state.kind === "loading" ? <span>正在核验成果…</span> : null}
      {state.kind === "invalid" ? <span>成果校验失败，请重试。</span> : null}
      {state.kind === "error" ? <span>成果暂时无法加载，请重试。</span> : null}
      {state.kind !== "ready" && state.kind !== "loading" ? (
        <Button size="small" onClick={() => setAttempt((value) => value + 1)}>重试</Button>
      ) : null}
    </section>
  );
}

function matchesRevision(revision: Artifact, source: Artifact) {
  return revision.id !== source.id
    && revision.version > source.version
    && revision.account_id === source.account_id
    && revision.thread_id === source.thread_id
    && revision.turn_id === source.turn_id;
}

type ArtifactState =
  | { kind: "loading" }
  | { kind: "invalid" }
  | { kind: "error" }
  | { kind: "ready"; artifact: Artifact };

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
