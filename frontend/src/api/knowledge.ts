import { api } from "./client";
import type { CreateKnowledgeInput, KnowledgeCategory, KnowledgeEntry } from "../types";

export async function listKnowledge(category?: KnowledgeCategory): Promise<KnowledgeEntry[]> {
  const { data } = await api.get<KnowledgeEntry[]>("/knowledge", {
    params: category ? { category } : undefined,
  });
  return data;
}

export async function createKnowledge(input: CreateKnowledgeInput): Promise<KnowledgeEntry> {
  const { data } = await api.post<KnowledgeEntry>("/knowledge", input);
  return data;
}

export async function updateKnowledge(
  id: number,
  patch: { title?: string; payload?: Record<string, unknown>; tags?: string[] | null },
): Promise<KnowledgeEntry> {
  const { data } = await api.patch<KnowledgeEntry>(`/knowledge/${id}`, patch);
  return data;
}

export async function deleteKnowledge(id: number): Promise<void> {
  await api.delete(`/knowledge/${id}`);
}
