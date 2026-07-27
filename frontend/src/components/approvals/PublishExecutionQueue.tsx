import { useQuery, useQueryClient } from "@tanstack/react-query";

import { listPublishJobs } from "../../services/publishing";
import type { PublishJob } from "../../types/publishing";
import { PublishJobPanel } from "../content/PublishJobPanel";

const VISIBLE_STATUSES = new Set<PublishJob["status"]>([
  "task_created",
  "handoff_ready",
  "user_publishing",
  "waiting_bind",
  "bound",
  "observing",
  "failed",
  "expired",
]);

export function PublishExecutionQueue({ accountId }: { accountId: number | null }) {
  const queryClient = useQueryClient();
  const queryKey = ["publish-jobs", accountId] as const;
  const query = useQuery({
    queryKey,
    queryFn: () => listPublishJobs({ accountId, limit: 50 }),
    enabled: accountId != null,
    refetchInterval: (state) =>
      shouldPoll(state.state.data as PublishJob[] | undefined) ? 5000 : false,
  });
  const visibleJobs = (query.data ?? [])
    .filter((job) => VISIBLE_STATUSES.has(job.status))
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at));

  if (accountId == null || (!query.isLoading && !query.isError && visibleJobs.length === 0)) {
    return null;
  }

  const updateJob = (job: PublishJob) => {
    queryClient.setQueryData<PublishJob[]>(queryKey, (current = []) =>
      [job, ...current.filter((item) => item.id !== job.id)],
    );
  };

  return (
    <section className="approval-publish-queue" aria-label="待发布任务">
      <header>
        <div>
          <span>审批后执行</span>
          <h2>待发布任务</h2>
        </div>
        <p>审批通过后，在这里扫码或直接拉起抖音完成官方投稿。</p>
      </header>
      {query.isLoading ? (
        <div className="approval-publish-queue__state">正在读取发布任务...</div>
      ) : query.isError ? (
        <button type="button" className="approval-publish-queue__state" onClick={() => void query.refetch()}>
          发布任务加载失败，点击重试
        </button>
      ) : (
        <div className="approval-publish-queue__list">
          {visibleJobs.map((job) => (
            <PublishJobPanel key={job.id} job={job} onJobChange={updateJob} />
          ))}
        </div>
      )}
    </section>
  );
}

function shouldPoll(jobs: PublishJob[] | undefined) {
  return Boolean(jobs?.some((job) =>
    ["task_created", "handoff_ready", "user_publishing", "waiting_bind", "bound", "observing"]
      .includes(job.status),
  ));
}
