import { api } from "./client";
import type {
  Account,
  AccountAssignmentsInput,
  AccountGroup,
  AccountMatrix,
  Client,
  ClientStatus,
  CreateAccountInput,
  DistributionAction,
  DouyinAccountCapabilities,
  DouyinAuthorizeUrl,
  DouyinCapabilityKey,
  DouyinDataSyncResult,
  DouyinJsSignature,
  DouyinScanAddInput,
  DouyinTrialWhitelistUrl,
  IntegrationStatus,
  AuthStatus,
  DataSyncStatus,
  GroupDimension,
  Platform,
  PlatformIntegration,
  Project,
  ProjectStatus,
  UpdatePlatformIntegrationInput,
} from "../types";

// —— 项目 ——

export async function listProjects(): Promise<Project[]> {
  const { data } = await api.get<Project[]>("/projects");
  return data;
}

export async function createProject(input: {
  name: string;
  description?: string;
  client_id?: number | null;
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

// —— 客户 ——

export async function listClients(): Promise<Client[]> {
  const { data } = await api.get<Client[]>("/clients");
  return data;
}

export async function createClient(input: { name: string }): Promise<Client> {
  const { data } = await api.post<Client>("/clients", input);
  return data;
}

export async function updateClient(
  id: number,
  patch: { name?: string; status?: ClientStatus },
): Promise<Client> {
  const { data } = await api.patch<Client>(`/clients/${id}`, patch);
  return data;
}

export async function archiveClient(id: number): Promise<void> {
  await api.delete(`/clients/${id}`);
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

export async function listAccounts(filters?: { groupId?: number; projectId?: number }): Promise<Account[]> {
  const { data } = await api.get<Account[]>("/accounts", {
    params: {
      ...(filters?.groupId != null ? { group_id: filters.groupId } : {}),
      ...(filters?.projectId != null ? { project_id: filters.projectId } : {}),
    },
  });
  return data;
}

export async function getAccount(id: number): Promise<Account> {
  const { data } = await api.get<Account>(`/accounts/${id}`);
  return data;
}

export async function replaceAccountAssignments(
  id: number,
  input: AccountAssignmentsInput,
): Promise<Account> {
  const { data } = await api.put<Account>(`/accounts/${id}/assignments`, input);
  return data;
}

export async function getAccountMatrix(projectId?: number): Promise<AccountMatrix> {
  const { data } = await api.get<AccountMatrix>("/account-matrix", {
    params: projectId != null ? { project_id: projectId } : undefined,
  });
  return data;
}

export async function listPlatformIntegrations(): Promise<PlatformIntegration[]> {
  const { data } = await api.get<PlatformIntegration[]>("/platform-integrations");
  return data;
}

export async function updatePlatformIntegration(
  platform: Platform,
  patch: UpdatePlatformIntegrationInput,
): Promise<PlatformIntegration> {
  const { data } = await api.patch<PlatformIntegration>(
    `/platform-integrations/${platform}`,
    patch,
  );
  return data;
}

export async function createDouyinAuthorizeUrl(accountId: number): Promise<DouyinAuthorizeUrl> {
  const { data } = await api.post<DouyinAuthorizeUrl>(
    "/platform-integrations/douyin/oauth/authorize",
    { account_id: accountId },
  );
  return data;
}

export async function getDouyinAccountCapabilities(
  accountId: number,
): Promise<DouyinAccountCapabilities> {
  const { data } = await api.get<DouyinAccountCapabilities>(
    `/platform-integrations/douyin/accounts/${accountId}/capabilities`,
  );
  return data;
}

export async function createDouyinIncrementalAuthorizeUrl(
  accountId: number,
  capabilityKey: DouyinCapabilityKey,
): Promise<DouyinAuthorizeUrl> {
  const { data } = await api.post<DouyinAuthorizeUrl>(
    "/platform-integrations/douyin/oauth/incremental-authorize",
    { account_id: accountId, capability_key: capabilityKey },
  );
  return data;
}

export async function createDouyinScanAddUrl(input: DouyinScanAddInput): Promise<DouyinAuthorizeUrl> {
  const { data } = await api.post<DouyinAuthorizeUrl>(
    "/platform-integrations/douyin/oauth/scan-add",
    input,
  );
  return data;
}

export async function createDouyinTrialWhitelistUrl(): Promise<DouyinTrialWhitelistUrl> {
  const { data } = await api.post<DouyinTrialWhitelistUrl>(
    "/platform-integrations/douyin/oauth/trial-whitelist",
  );
  return data;
}

export async function createDouyinJsSignature(url: string): Promise<DouyinJsSignature> {
  const { data } = await api.post<DouyinJsSignature>(
    "/platform-integrations/douyin/js-signature",
    { url },
  );
  return data;
}

export async function syncDouyinAccountMetrics(accountId: number): Promise<DouyinDataSyncResult> {
  const { data } = await api.post<DouyinDataSyncResult>(
    `/platform-integrations/douyin/accounts/${accountId}/sync-metrics`,
  );
  return data;
}

export async function createAccount(input: CreateAccountInput): Promise<Account> {
  const { data } = await api.post<Account>("/accounts", input);
  return data;
}

export async function updateAccount(
  id: number,
  patch: Partial<
    Pick<Account, "nickname" | "group_id" | "project_id" | "status" | "external_account_id">
  >,
): Promise<Account> {
  const { data } = await api.patch<Account>(`/accounts/${id}`, patch);
  return data;
}

export async function batchUpdateAccounts(input: {
  account_ids: number[];
  group_id?: number | null;
  project_id?: number | null;
  status?: Account["status"];
}): Promise<Account[]> {
  const { data } = await api.patch<Account[]>("/accounts/batch", input);
  return data;
}

export async function updateAccountIntegration(
  id: number,
  patch: {
    integration_status?: IntegrationStatus;
    auth_status?: AuthStatus;
    data_sync_status?: DataSyncStatus;
    note?: string;
  },
): Promise<Account> {
  const { data } = await api.patch<Account>(`/accounts/${id}/integration`, patch);
  return data;
}

export async function createDistributionAction(input: {
  platform: Platform;
  account_ids: number[];
  action_type?: string;
  content_item_id?: number | null;
  project_id?: number | null;
  note?: string | null;
}): Promise<DistributionAction> {
  const { data } = await api.post<DistributionAction>("/distribution/actions", input);
  return data;
}

export async function deleteAccount(id: number): Promise<void> {
  await api.delete(`/accounts/${id}`);
}
