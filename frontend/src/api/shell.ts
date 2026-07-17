import { api } from "./client";
import type { Account, Client, Project } from "../types";

export interface WorkspaceContext {
  clients: Client[];
  selected_client: Client | null;
  projects: Project[];
  selected_project: Project | null;
  accounts: Account[];
}

export interface WorkspaceSearchResult {
  kind: "client" | "project" | "account";
  id: number;
  title: string;
  subtitle: string | null;
  path: string;
  client_id: number | null;
  project_id: number | null;
  account_id: number | null;
}

export interface ShellNotification {
  id: number;
  type: string;
  title: string;
  body: string | null;
  path: string | null;
  read_at: string | null;
  created_at: string;
}

export async function getWorkspaceContext(
  clientId?: number | null,
  projectId?: number | null,
): Promise<WorkspaceContext> {
  const { data } = await api.get<WorkspaceContext>("/workspace-context", {
    params: {
      ...(clientId != null ? { client_id: clientId } : {}),
      ...(projectId != null ? { project_id: projectId } : {}),
    },
  });
  return data;
}

export async function searchWorkspace(query: string): Promise<WorkspaceSearchResult[]> {
  const { data } = await api.get<WorkspaceSearchResult[]>("/search", {
    params: { q: query.trim() },
  });
  return data;
}

export async function listNotifications(): Promise<ShellNotification[]> {
  const { data } = await api.get<ShellNotification[]>("/notifications");
  return data;
}

export async function getUnreadNotificationCount(): Promise<number> {
  const { data } = await api.get<{ count: number }>("/notifications/unread-count");
  return data.count;
}

export async function markNotificationRead(id: number): Promise<ShellNotification> {
  const { data } = await api.patch<ShellNotification>(`/notifications/${id}/read`);
  return data;
}
