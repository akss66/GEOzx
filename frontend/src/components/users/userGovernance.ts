import { presentApiError } from "../../api/errors";
import type {
  AccountAccessCatalogItem,
  AccountScopeMode,
  UpdateUserAccessInput,
  UserAccessCatalog,
  UserDeletionBlocker,
  UserDetail,
  UserGovernanceErrorCode,
  WorkspaceRole,
} from "../../types";

export const WORKSPACE_ROLE_LABEL: Record<WorkspaceRole, string> = {
  lead: "负责人",
  operator: "运营",
  editor: "内容",
  reviewer: "审核",
};

export const WORKSPACE_ROLE_OPTIONS = [
  { value: "lead", label: "负责人" },
  { value: "operator", label: "运营" },
  { value: "editor", label: "内容" },
  { value: "reviewer", label: "审核" },
] satisfies Array<{ value: WorkspaceRole; label: string }>;

const GOVERNANCE_ERROR_COPY: Record<UserGovernanceErrorCode, string> = {
  LAST_ACTIVE_ADMIN: "至少保留一位启用中的管理员。",
  SECONDARY_PASSWORD_COOLDOWN: "二级密码刚完成设置或重置，冷却结束后才能执行危险操作。",
  SECONDARY_PASSWORD_INVALID: "二级密码不正确，请重新输入。",
  SECONDARY_PASSWORD_LOCKED: "二级密码已被暂时锁定，请稍后再试。",
  SECONDARY_PASSWORD_NOT_CONFIGURED: "请先为当前登录管理员设置二级密码。",
  USER_DELETION_EMAIL_MISMATCH: "确认邮箱必须与目标成员邮箱完全一致。",
  USER_DELETION_PREVIEW_EXPIRED: "删除预览已过期，请重新获取最新影响预览。",
  USER_DELETION_PREVIEW_INVALID: "当前删除预览不可用，请重新获取影响预览。",
  USER_DELETION_PREVIEW_STALE: "成员数据已变化，请重新获取最新影响预览。",
  USER_DELETION_PREVIEW_USED: "这份删除预览已经使用过，请重新获取新的影响预览。",
  USER_DELETION_TRANSACTION_FAILED: "永久删除没有完成，系统已自动回滚，请稍后重试。",
  USER_LAST_ACTIVE_ADMIN_REQUIRED: "至少保留一位启用中的管理员。",
  USER_SELF_ADMIN_CHANGE_FORBIDDEN: "不能移除当前登录管理员自己的管理员身份。",
  USER_SELF_DELETION_FORBIDDEN: "不能永久删除当前登录管理员本人。",
  USER_SELF_PASSWORD_RESET_FORBIDDEN: "不能在这里重置当前登录账号自己的登录密码。",
};

const DELETION_BLOCKER_COPY: Record<UserDeletionBlocker, string> = {
  LAST_ACTIVE_ADMIN: "这是最后一位启用中的管理员，当前不能永久删除。",
  USER_SELF_DELETION_FORBIDDEN: "当前登录管理员不能永久删除自己。",
};

export type AccessDraft = Required<UpdateUserAccessInput>;

export function detailToAccessDraft(detail: UserDetail): AccessDraft {
  return {
    clients: detail.client_memberships.map((item) => ({
      client_id: item.client_id,
      role: item.role,
    })),
    projects: detail.project_memberships.map((item) => ({
      project_id: item.project_id,
      role: item.role,
    })),
    account_scope_mode: detail.account_scope_mode,
    account_ids: [...detail.account_ids],
  };
}

export function normalizeAccessDraft(draft: AccessDraft): AccessDraft {
  return {
    clients: [...draft.clients].sort((left, right) => left.client_id - right.client_id),
    projects: [...draft.projects].sort((left, right) => left.project_id - right.project_id),
    account_scope_mode: draft.account_scope_mode,
    account_ids: [...new Set(draft.account_ids)].sort((left, right) => left - right),
  };
}

export function areAccessDraftsEqual(left: AccessDraft, right: AccessDraft) {
  return JSON.stringify(normalizeAccessDraft(left)) === JSON.stringify(normalizeAccessDraft(right));
}

export function hasAccessAnomaly(detail: UserDetail) {
  if (detail.has_global_access) return false;
  return detail.client_memberships.length === 0 && detail.project_memberships.length === 0;
}

export function hasAvailableAccountCatalog(
  catalog: UserAccessCatalog | null,
): catalog is UserAccessCatalog & {
  account_catalog_status: "available";
  accounts: AccountAccessCatalogItem[];
} {
  return catalog?.account_catalog_status === "available" && Array.isArray(catalog.accounts);
}

export function getAccessibleAccounts(detail: Pick<UserDetail, "has_global_access" | "client_memberships" | "project_memberships">, catalog: UserAccessCatalog | null) {
  if (!hasAvailableAccountCatalog(catalog)) return [] as AccountAccessCatalogItem[];
  const accounts = catalog.accounts;
  if (detail.has_global_access) return [...accounts];

  const clientIds = new Set(detail.client_memberships.map((item) => item.client_id));
  const projectIds = new Set(detail.project_memberships.map((item) => item.project_id));

  return accounts.filter((account) => {
    if (account.client_id != null && clientIds.has(account.client_id)) return true;
    return (account.project_ids ?? []).some((projectId) => projectIds.has(projectId));
  });
}

export function getEffectiveAccounts(
  draft: Pick<AccessDraft, "account_ids" | "account_scope_mode" | "clients" | "projects">,
  catalog: UserAccessCatalog | null,
) {
  const accessible = getAccessibleAccounts({
    has_global_access: false,
    client_memberships: draft.clients.map((item) => ({
      client_id: item.client_id,
      client_name: "",
      role: item.role,
    })),
    project_memberships: draft.projects.map((item) => ({
      project_id: item.project_id,
      project_name: "",
      client_id: null,
      client_name: null,
      role: item.role,
    })),
  }, catalog);

  if (draft.account_scope_mode === "all_accessible") return accessible;

  const selectedIds = new Set(draft.account_ids);
  return accessible.filter((account) => selectedIds.has(account.id));
}

export function clampSelectedAccounts(accountIds: number[], accessibleAccounts: AccountAccessCatalogItem[]) {
  const visibleIds = new Set(accessibleAccounts.map((account) => account.id));
  return accountIds.filter((accountId) => visibleIds.has(accountId));
}

export function summarizeScopeMode(mode: AccountScopeMode) {
  return mode === "all_accessible" ? "全部可见账号" : "仅指定账号";
}

export function formatGovernanceError(error: unknown, fallback: string) {
  const code = extractGovernanceErrorCode(error);
  if (code && GOVERNANCE_ERROR_COPY[code]) return GOVERNANCE_ERROR_COPY[code];
  return presentApiError(error, fallback).message;
}

export function extractGovernanceErrorCode(error: unknown): UserGovernanceErrorCode | null {
  if (!error || typeof error !== "object") return null;
  const response = (error as { response?: { data?: { detail?: { code?: unknown } } } }).response;
  const code = response?.data?.detail?.code;
  return typeof code === "string" ? code as UserGovernanceErrorCode : null;
}

export function formatDeletionBlocker(blocker: UserDeletionBlocker) {
  return DELETION_BLOCKER_COPY[blocker] ?? blocker;
}

export function formatDateTime(value: string | null) {
  if (!value) return "未配置";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
