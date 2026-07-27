import { api } from "./client";
import type {
  AgentInvocation,
  AgentToolCall,
  BrainRuntime,
  BrainTask,
  DeliverableAcceptance,
  DraftBrainTaskInput,
  ExperienceMemorySummary,
  ReflectionRecordSummary,
  RerunScope,
} from "../types";

export async function listBrainTasks(): Promise<BrainTask[]> {
  const { data } = await api.get<BrainTask[]>("/brain/tasks");
  return data;
}

export async function draftBrainTask(input: string | DraftBrainTaskInput): Promise<BrainTask> {
  const payload = typeof input === "string" ? { goal: input } : input;
  const { data } = await api.post<BrainTask>("/brain/tasks/draft", payload);
  return data;
}

export async function confirmBrainTask(task: BrainTask): Promise<BrainTask> {
  const { data } = await api.post<BrainTask>(`/brain/tasks/${task.id}/confirm`);
  return data;
}

export async function getBrainTaskRuntime(taskId: number): Promise<BrainRuntime> {
  const { data } = await api.get<BrainRuntime>(`/brain/tasks/${taskId}/runtime`);
  return data;
}

export async function refreshBrainObservation(
  taskId: number,
): Promise<ReflectionRecordSummary> {
  const { data } = await api.post<ReflectionRecordSummary>(
    `/brain/tasks/${taskId}/observation/refresh`,
  );
  return data;
}

export async function verifyBrainExperienceCandidate(input: {
  taskId: number;
  candidateKey: string;
  verificationNote: string;
}): Promise<ExperienceMemorySummary> {
  const { data } = await api.post<ExperienceMemorySummary>(
    `/brain/tasks/${input.taskId}/experience-candidates/${encodeURIComponent(input.candidateKey)}/verify`,
    {
      candidate_key: input.candidateKey,
      verification_note: input.verificationNote,
    },
  );
  return data;
}

export async function sendBrainMessage(input: {
  message: string;
  client_message_id?: string;
  task_id?: number;
  project_id?: number | null;
  account_id?: number | null;
  platform?: "douyin";
}): Promise<BrainRuntime> {
  const { data } = await api.post<BrainRuntime>("/brain/messages", input);
  return data;
}

export async function stopBrainGeneration(input: {
  clientMessageId: string;
  taskId?: number | null;
}): Promise<{ client_message_id: string; stop_requested: boolean }> {
  const { data } = await api.post<{
    client_message_id: string;
    stop_requested: boolean;
  }>(`/brain/generations/${encodeURIComponent(input.clientMessageId)}/stop`, {
    task_id: input.taskId ?? null,
  });
  return data;
}

export async function regenerateBrainMessage(input: {
  taskId: number;
  clientMessageId: string;
}): Promise<BrainRuntime> {
  const { data } = await api.post<BrainRuntime>(
    `/brain/tasks/${input.taskId}/regenerate`,
    { client_message_id: input.clientMessageId },
  );
  return data;
}

export async function selectBrainDecision(input: {
  taskId: number;
  decisionId: string;
  choiceId: string;
}): Promise<BrainRuntime> {
  const { data } = await api.post<BrainRuntime>(
    `/brain/tasks/${input.taskId}/decisions/${input.decisionId}/select`,
    { choice_id: input.choiceId },
  );
  return data;
}

export async function reviseBrainDecision(input: {
  taskId: number;
  decisionId: string;
  comment: string;
  requestNewOptions?: boolean;
}): Promise<BrainRuntime> {
  const { data } = await api.post<BrainRuntime>(
    `/brain/tasks/${input.taskId}/decisions/${input.decisionId}/revise`,
    {
      comment: input.comment,
      request_new_options: input.requestNewOptions ?? false,
    },
  );
  return data;
}

export async function listTaskInvocations(taskId: number): Promise<AgentInvocation[]> {
  const { data } = await api.get<AgentInvocation[]>(`/brain/tasks/${taskId}/invocations`);
  return data;
}

export async function listTaskToolCalls(taskId: number): Promise<AgentToolCall[]> {
  const { data } = await api.get<AgentToolCall[]>(`/brain/tasks/${taskId}/tool-calls`);
  return data;
}

export async function listPendingToolCallApprovals(): Promise<AgentToolCall[]> {
  const { data } = await api.get<AgentToolCall[]>("/brain/tool-calls/pending-approvals");
  return data;
}

export async function approveToolCall(input: {
  toolCallId: number;
  approved: boolean;
  comment?: string;
}): Promise<AgentToolCall> {
  const { data } = await api.post<AgentToolCall>(
    `/brain/tool-calls/${input.toolCallId}/approve`,
    {
      approved: input.approved,
      comment: input.comment,
    },
  );
  return data;
}

export async function listDeliverableAcceptances(
  taskId: number,
): Promise<DeliverableAcceptance[]> {
  const { data } = await api.get<DeliverableAcceptance[]>(
    `/brain/tasks/${taskId}/acceptances`,
  );
  return data;
}

export async function approveDeliverableAcceptance(
  acceptance: DeliverableAcceptance,
  reviewerNote?: string,
): Promise<DeliverableAcceptance> {
  const { data } = await api.post<DeliverableAcceptance>(
    `/brain/tasks/${acceptance.task_id}/accept`,
    {
      acceptance_id: acceptance.id,
      reviewer_note: reviewerNote,
    },
  );
  return data;
}

export async function rejectDeliverableAcceptance(input: {
  acceptance: DeliverableAcceptance;
  reason: string;
  rerun_scope: RerunScope;
  ask_brain_rejudge: boolean;
}): Promise<DeliverableAcceptance> {
  const { data } = await api.post<DeliverableAcceptance>(
    `/brain/tasks/${input.acceptance.task_id}/rerun`,
    {
      acceptance_id: input.acceptance.id,
      reason: input.reason,
      rerun_scope: input.rerun_scope,
      ask_brain_rejudge: input.ask_brain_rejudge,
    },
  );
  return data;
}

export async function rejudgeDeliverableAcceptance(
  acceptance: DeliverableAcceptance,
): Promise<DeliverableAcceptance> {
  const { data } = await api.post<DeliverableAcceptance>(
    `/brain/tasks/${acceptance.task_id}/rejudge`,
    { acceptance_id: acceptance.id },
  );
  return data;
}

export async function closeTaskMemory(
  taskId: number,
): Promise<{ task_id: number; closed: boolean; context_closed_at: string }> {
  const { data } = await api.post<{
    task_id: number;
    closed: boolean;
    context_closed_at: string;
  }>(`/brain/tasks/${taskId}/close-memory`);
  return data;
}
