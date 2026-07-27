import { api } from "../api/client";
import type {
  CreatePublishJobInput,
  PublishHandoff,
  PublishJob,
} from "../types/publishing";

export async function createPublishJob(
  input: CreatePublishJobInput,
): Promise<PublishJob> {
  const { data } = await api.post<PublishJob>("/publishing/jobs", input);
  return data;
}

export async function listPublishJobs(input?: {
  accountId?: number | null;
  limit?: number;
}): Promise<PublishJob[]> {
  const params: Record<string, number> = {};
  if (input?.accountId != null) params.account_id = input.accountId;
  if (input?.limit != null) params.limit = input.limit;
  const { data } = await api.get<PublishJob[]>("/publishing/jobs", {
    params: Object.keys(params).length ? params : undefined,
  });
  return data;
}

export async function preparePublishHandoff(
  jobId: number,
): Promise<PublishHandoff> {
  const { data } = await api.post<PublishHandoff>(
    `/publishing/jobs/${jobId}/handoff`,
  );
  return data;
}

export async function markPublishJobLaunched(
  jobId: number,
): Promise<PublishJob> {
  const { data } = await api.post<PublishJob>(
    `/publishing/jobs/${jobId}/launched`,
  );
  return data;
}

export async function retryPublishJob(jobId: number): Promise<PublishJob> {
  const { data } = await api.post<PublishJob>(
    `/publishing/jobs/${jobId}/retry`,
  );
  return data;
}

export async function cancelPublishJob(jobId: number): Promise<PublishJob> {
  const { data } = await api.post<PublishJob>(
    `/publishing/jobs/${jobId}/cancel`,
  );
  return data;
}
