import { api } from "./client";
import type {
  CreateUserInput,
  LoginResponse,
  PermanentDeleteUserInput,
  PermanentDeleteUserResponse,
  ResetUserPasswordInput,
  SecondaryPasswordStatus,
  SetSecondaryPasswordInput,
  UpdateUserAccessInput,
  UpdateUserInput,
  User,
  UserRosterItem,
  UserAccessCatalog,
  UserAccessCatalogResponse,
  UserDeletionPreview,
  UserDetail,
  UserDetailResponse,
} from "../types";

export async function login(email: string, password: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/login", { email, password });
  return data;
}

export async function getMe(): Promise<User> {
  const { data } = await api.get<User>("/auth/me");
  return data;
}

export async function listUsers(): Promise<UserRosterItem[]> {
  const { data } = await api.get<UserRosterItem[]>("/users");
  return data;
}

export async function createUser(input: CreateUserInput): Promise<User> {
  const { data } = await api.post<User>("/users", input);
  return data;
}

export async function getUserDetail(userId: number): Promise<UserDetail> {
  const { data } = await api.get<UserDetailResponse>(`/users/${userId}`);
  return normalizeUserDetail(data);
}

function normalizeUserDetail(data: UserDetailResponse): UserDetail {
  const clientMemberships = Array.isArray(data?.client_memberships)
    ? data.client_memberships
    : [];
  const projectMemberships = Array.isArray(data?.project_memberships)
    ? data.project_memberships
    : [];
  const accountIds = Array.isArray(data?.account_ids) ? data.account_ids : [];
  const hasKnownScopeMode = data?.account_scope_mode === "all_accessible"
    || data?.account_scope_mode === "selected";
  const accountScopeMode = data?.account_scope_mode === "selected"
    ? "selected"
    : "all_accessible";
  const accessDetailAvailable = hasKnownScopeMode
    && Array.isArray(data?.account_ids)
    && Array.isArray(data?.client_memberships)
    && Array.isArray(data?.project_memberships);

  return {
    ...data,
    has_global_access: typeof data?.has_global_access === "boolean"
      ? data.has_global_access
      : data?.role === "admin",
    account_scope_mode: accountScopeMode,
    access_detail_status: accessDetailAvailable ? "available" : "unavailable",
    client_ids: Array.isArray(data?.client_ids)
      ? [...data.client_ids]
      : clientMemberships.map((item) => item.client_id),
    project_ids: Array.isArray(data?.project_ids)
      ? [...data.project_ids]
      : projectMemberships.map((item) => item.project_id),
    account_ids: [...accountIds],
    client_memberships: clientMemberships,
    project_memberships: projectMemberships,
  };
}

export async function getUserAccessCatalog(): Promise<UserAccessCatalog> {
  const { data } = await api.get<UserAccessCatalogResponse>("/users/access-catalog");
  const baseCatalog = {
    clients: Array.isArray(data?.clients) ? data.clients : [],
    projects: Array.isArray(data?.projects) ? data.projects : [],
  };
  if (!Array.isArray(data?.accounts)) {
    return { ...baseCatalog, account_catalog_status: "unavailable" };
  }
  return {
    ...baseCatalog,
    account_catalog_status: "available",
    accounts: data.accounts.map((account) => ({
      ...account,
      client_ids: Array.isArray(account.client_ids)
        ? account.client_ids
        : account.client_id == null
          ? []
          : [account.client_id],
      project_ids: Array.isArray(account.project_ids) ? account.project_ids : [],
    })),
  };
}

export async function updateUser(userId: number, input: UpdateUserInput): Promise<User> {
  const { data } = await api.patch<User>(`/users/${userId}`, input);
  return data;
}

export async function updateUserAccess(
  userId: number,
  input: UpdateUserAccessInput,
): Promise<UserDetail> {
  const { data } = await api.put<UserDetailResponse>(`/users/${userId}/access`, input);
  return normalizeUserDetail(data);
}

export async function setSecondaryPassword(
  input: SetSecondaryPasswordInput,
): Promise<SecondaryPasswordStatus> {
  const { data } = await api.put<SecondaryPasswordStatus>("/users/me/secondary-password", input);
  return data;
}

export async function getSecondaryPasswordStatus(): Promise<SecondaryPasswordStatus> {
  const { data } = await api.get<SecondaryPasswordStatus>("/users/me/secondary-password/status");
  return data;
}

export async function resetUserPassword(
  userId: number,
  input: ResetUserPasswordInput,
): Promise<void> {
  await api.post<void>(`/users/${userId}/reset-password`, input);
}

export async function previewUserDeletion(userId: number): Promise<UserDeletionPreview> {
  const { data } = await api.post<UserDeletionPreview>(`/users/${userId}/deletion-preview`);
  return data;
}

export async function permanentlyDeleteUser(
  userId: number,
  input: PermanentDeleteUserInput,
): Promise<PermanentDeleteUserResponse> {
  const { data } = await api.delete<PermanentDeleteUserResponse>(`/users/${userId}/permanent`, {
    data: input,
  });
  return data;
}
