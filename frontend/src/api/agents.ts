import { api } from "./client";
import type { AgentCode, AgentProfile } from "../types";

export async function listAgents(): Promise<AgentProfile[]> {
  const { data } = await api.get<AgentProfile[]>("/agents");
  return data;
}

export async function getAgent(code: AgentCode): Promise<AgentProfile> {
  const { data } = await api.get<AgentProfile>(`/agents/${code}`);
  return data;
}

export async function invokeAgent(code: AgentCode, goal: string): Promise<AgentProfile> {
  await api.post(`/agents/${code}/invoke`, { prompt: goal });
  return getAgent(code);
}
