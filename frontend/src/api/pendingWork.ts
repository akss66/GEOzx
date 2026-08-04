import { api } from "./client";

export type PendingWorkKind =
  | "clarification"
  | "approval"
  | "shoot_task"
  | "manual_publish"
  | "account_data";

export type PendingWorkTarget =
  | { type: "conversation_turn"; thread_id: number; turn_id: number }
  | { type: "account_data" }
  | { type: "task_workspace" };

export interface PendingWorkItem {
  id: string;
  kind: PendingWorkKind;
  action_label: string;
  account_id: number;
  thread_id: number | null;
  turn_id: number | null;
  due_at: string | null;
  reason: string;
  next_step_after_completion: string;
  target: PendingWorkTarget;
}

export interface PendingWorkGroup {
  kind: PendingWorkKind;
  label: string;
  count: number;
  items: PendingWorkItem[];
}

export interface PendingWorkResponse {
  account_id: number;
  groups: PendingWorkGroup[];
}

export interface PendingWorkCompletion {
  id: string;
  kind: "shoot_task" | "manual_publish";
  account_id: number;
  completed: true;
  event_id: number;
  next_step_after_completion: string;
}

export const pendingWorkQueryKey = (accountId: number) => (
  ["account-pending-work", accountId] as const
);

export async function getAccountPendingWork(
  accountId: number,
): Promise<PendingWorkResponse> {
  const { data } = await api.get<PendingWorkResponse>(
    `/accounts/${accountId}/pending-work`,
  );
  return data;
}

export async function completePendingShootTask(
  accountId: number,
  shootTaskId: number,
): Promise<PendingWorkCompletion> {
  const { data } = await api.post<PendingWorkCompletion>(
    `/accounts/${accountId}/pending-work/shoot-tasks/${shootTaskId}/complete`,
  );
  return data;
}

export async function publishPendingScheduleEntry(
  accountId: number,
  scheduleEntryId: number,
): Promise<PendingWorkCompletion> {
  const { data } = await api.post<PendingWorkCompletion>(
    `/accounts/${accountId}/pending-work/schedule-entries/${scheduleEntryId}/publish`,
  );
  return data;
}
