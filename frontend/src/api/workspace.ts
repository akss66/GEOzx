import { api } from "./client";
import type {
  Account,
  AccountGroup,
  CreateAccountInput,
  GroupDimension,
  Project,
  ProjectStatus,
} from "../types";

// —— 项目 ——

export async function listProjects(): Promise<Project[]> {
  const { data } = await api.get<Project[]>("/projects");
  return data;
}

export async function createProject(input: {
  name: string;
  description?: string;
}): Promise<Project> {
  const { data } = await api.post<Project>("/projects", input);
  return data;
}

export async function updateProject(
  id: number,
  patch: { name?: string; description?: string; status?: ProjectStatus },
): Promise<Project> {
  const { data } = await api.patch<Project>(`/projects/${id}`, patch);
  return data;
}

export async function deleteProject(id: number): Promise<void> {
  await api.delete(`/projects/${id}`);
}

// —— 账号分组 ——

export async function listAccountGroups(): Promise<AccountGroup[]> {
  const { data } = await api.get<AccountGroup[]>("/account-groups");
  return data;
}

export async function createAccountGroup(input: {
  name: string;
  dimension: GroupDimension;
}): Promise<AccountGroup> {
  const { data } = await api.post<AccountGroup>("/account-groups", input);
  return data;
}

// —— 账号 ——

export async function listAccounts(groupId?: number): Promise<Account[]> {
  const { data } = await api.get<Account[]>("/accounts", {
    params: groupId != null ? { group_id: groupId } : undefined,
  });
  return data;
}

export async function createAccount(input: CreateAccountInput): Promise<Account> {
  const { data } = await api.post<Account>("/accounts", input);
  return data;
}

export async function updateAccount(
  id: number,
  patch: Partial<Pick<Account, "nickname" | "group_id" | "status" | "external_account_id">>,
): Promise<Account> {
  const { data } = await api.patch<Account>(`/accounts/${id}`, patch);
  return data;
}

export async function deleteAccount(id: number): Promise<void> {
  await api.delete(`/accounts/${id}`);
}
