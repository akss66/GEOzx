import { api } from "./client";
import type { ApprovalWorkspace } from "../types";

export interface ApprovalWorkspaceContext {
  client_id?: number | null;
  project_id?: number | null;
  account_id?: number | null;
}

export async function getApprovalWorkspace(
  context: ApprovalWorkspaceContext,
): Promise<ApprovalWorkspace> {
  const params = Object.fromEntries(
    Object.entries(context).filter(([, value]) => value != null),
  );
  const { data } = await api.get<ApprovalWorkspace>("/approvals/workspace", { params });
  return data;
}
