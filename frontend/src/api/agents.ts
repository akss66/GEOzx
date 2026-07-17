import { api } from "./client";
import type {
  AgentCode,
  AgentDirectRun,
  AgentHandoff,
  AgentManagement,
  AgentProfile,
  KnowledgeSuggestion,
  UpdateAgentManagementInput,
} from "../types";

export async function listAgents(): Promise<AgentProfile[]> {
  const { data } = await api.get<AgentProfile[]>("/agents");
  return data;
}

export async function getAgent(code: AgentCode): Promise<AgentProfile> {
  const { data } = await api.get<AgentProfile>(`/agents/${code}`);
  return data;
}

export async function listAgentManagement(): Promise<AgentManagement[]> {
  const { data } = await api.get<AgentManagement[]>("/agents/management");
  return data;
}

export async function updateAgentManagement(
  code: AgentCode,
  input: UpdateAgentManagementInput,
): Promise<AgentManagement> {
  const { data } = await api.put<AgentManagement>(`/agents/${code}/management`, input);
  return data;
}

export async function invokeAgent(
  code: AgentCode,
  input: {
    prompt: string;
    projectId: number;
    accountId: number;
    sourceTaskId?: number;
  },
): Promise<AgentDirectRun> {
  const { data } = await api.post<AgentDirectRun>(`/agents/${code}/invoke`, {
    prompt: input.prompt,
    project_id: input.projectId,
    account_id: input.accountId,
    ...(input.sourceTaskId != null ? { source_task_id: input.sourceTaskId } : {}),
  });
  return data;
}

export async function listAgentRuns(
  code: AgentCode,
  projectId: number,
  accountId: number,
): Promise<AgentDirectRun[]> {
  const { data } = await api.get<AgentDirectRun[]>(`/agents/${code}/runs`, {
    params: { project_id: projectId, account_id: accountId },
  });
  return data;
}

export async function handoffAgentRun(taskId: number): Promise<AgentHandoff> {
  const { data } = await api.post<AgentHandoff>(`/agents/runs/${taskId}/handoff`);
  return data;
}

export async function suggestAgentRunKnowledge(taskId: number): Promise<KnowledgeSuggestion> {
  const { data } = await api.post<KnowledgeSuggestion>(
    `/agents/runs/${taskId}/knowledge-suggestion`,
  );
  return data;
}
