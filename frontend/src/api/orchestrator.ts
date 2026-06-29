import { api } from "./client";
import type { ContentItem, ContentStage, Deliverable, PendingGate } from "../types";

export async function listContentItems(projectId?: number): Promise<ContentItem[]> {
  const { data } = await api.get<ContentItem[]>("/content-items", {
    params: projectId != null ? { project_id: projectId } : undefined,
  });
  return data;
}

export async function createContentItem(input: {
  project_id: number;
  title: string;
  account_id?: number | null;
}): Promise<ContentItem> {
  const { data } = await api.post<ContentItem>("/content-items", input);
  return data;
}

export async function startPipeline(contentItemId: number): Promise<void> {
  await api.post(`/content-items/${contentItemId}/start`);
}

export async function listPendingGates(): Promise<PendingGate[]> {
  const { data } = await api.get<PendingGate[]>("/gates");
  return data;
}

export async function approveGate(
  approvalId: number,
  approved: boolean,
  comment?: string,
): Promise<void> {
  await api.post(`/gates/${approvalId}/approve`, { approved, comment });
}

export async function listDeliverableHistory(contentItemId: number): Promise<Deliverable[]> {
  const { data } = await api.get<Deliverable[]>(`/content-items/${contentItemId}/deliverables`);
  return data;
}

export async function rerunStage(contentItemId: number, stage: ContentStage): Promise<void> {
  await api.post(`/content-items/${contentItemId}/rerun`, { stage });
}

export async function rollbackDeliverable(deliverableId: number): Promise<void> {
  await api.post(`/deliverables/${deliverableId}/rollback`);
}
