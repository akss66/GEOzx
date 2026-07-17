import { api } from "./client";
import type {
  CreateKnowledgeInput,
  KnowledgeCategory,
  KnowledgeCitation,
  KnowledgeEntry,
  KnowledgeSuggestion,
} from "../types";

export async function listKnowledge(
  clientId: number,
  projectId?: number | null,
  category?: KnowledgeCategory,
): Promise<KnowledgeEntry[]> {
  const { data } = await api.get<KnowledgeEntry[]>("/knowledge", {
    params: {
      client_id: clientId,
      project_id: projectId ?? undefined,
      category,
    },
  });
  return data;
}

export async function createKnowledge(input: CreateKnowledgeInput): Promise<KnowledgeEntry> {
  const { data } = await api.post<KnowledgeEntry>("/knowledge", input);
  return data;
}

export async function updateKnowledge(
  id: number,
  patch: Partial<Pick<KnowledgeEntry,
    "title" | "content" | "payload" | "tags" | "source_type" | "source_label" | "source_url" | "status"
  >>,
): Promise<KnowledgeEntry> {
  const { data } = await api.patch<KnowledgeEntry>(`/knowledge/${id}`, patch);
  return data;
}

export async function archiveKnowledge(id: number): Promise<void> {
  await api.delete(`/knowledge/${id}`);
}

export async function listKnowledgeCitations(
  id: number,
  clientId: number,
  projectId?: number | null,
): Promise<KnowledgeCitation[]> {
  const { data } = await api.get<KnowledgeCitation[]>(`/knowledge/${id}/citations`, {
    params: { client_id: clientId, project_id: projectId ?? undefined },
  });
  return data;
}

export async function listKnowledgeSuggestions(
  clientId: number,
  projectId?: number | null,
): Promise<KnowledgeSuggestion[]> {
  const { data } = await api.get<KnowledgeSuggestion[]>("/knowledge-suggestions", {
    params: { client_id: clientId, project_id: projectId ?? undefined, status: "pending" },
  });
  return data;
}

export async function approveKnowledgeSuggestion(
  id: number,
  reviewNote?: string,
): Promise<{ suggestion: KnowledgeSuggestion; entry: KnowledgeEntry }> {
  const { data } = await api.post(`/knowledge-suggestions/${id}/approve`, {
    review_note: reviewNote || undefined,
  });
  return data;
}

export async function rejectKnowledgeSuggestion(
  id: number,
  reviewNote?: string,
): Promise<KnowledgeSuggestion> {
  const { data } = await api.post(`/knowledge-suggestions/${id}/reject`, {
    review_note: reviewNote || undefined,
  });
  return data;
}
