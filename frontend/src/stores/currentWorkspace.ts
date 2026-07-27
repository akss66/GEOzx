import { create } from "zustand";

import type { Account, Platform } from "../types";

const WORKSPACE_KEY = "tongzhouxing_current_workspace";

export interface WorkspaceSelection {
  clientId: number | null;
  projectId: number | null;
  platform: Platform;
  accountId: number | null;
}

interface StoredWorkspaceV2 extends WorkspaceSelection {
  version: 2;
}

interface CurrentWorkspaceState extends WorkspaceSelection {
  setClientId: (clientId: number | null) => void;
  setProjectId: (projectId: number | null) => void;
  setPlatform: (platform: Platform) => void;
  setAccountId: (accountId: number | null) => void;
  hydrate: (selection: WorkspaceSelection) => void;
  clear: () => void;
}

const EMPTY_WORKSPACE: WorkspaceSelection = {
  clientId: null,
  projectId: null,
  platform: "douyin",
  accountId: null,
};

export function listSelectableWorkspaceAccounts(accounts: Account[], platform: Platform) {
  return accounts.filter(
    (account) => account.platform === platform && account.status === "active",
  );
}

export function resolveWorkspaceAccount(
  accounts: Account[],
  platform: Platform,
  accountId: number | null,
) {
  if (accountId == null) return null;
  return listSelectableWorkspaceAccounts(accounts, platform).find(
    (account) => account.id === accountId,
  ) ?? null;
}

export function resolveAccountWorkspaceSelection(
  account: Account,
  current: WorkspaceSelection,
): WorkspaceSelection {
  return {
    clientId: account.client_id ?? account.client_ids?.[0] ?? current.clientId,
    projectId: account.project_id ?? account.project_ids?.[0] ?? current.projectId,
    platform: account.platform,
    accountId: account.id,
  };
}

function readStoredWorkspace(): WorkspaceSelection {
  try {
    const raw = localStorage.getItem(WORKSPACE_KEY);
    if (!raw) return EMPTY_WORKSPACE;
    const parsed = JSON.parse(raw) as Partial<StoredWorkspaceV2>;
    return {
      clientId: typeof parsed.clientId === "number" ? parsed.clientId : null,
      projectId: typeof parsed.projectId === "number" ? parsed.projectId : null,
      platform: parsed.platform === "douyin" ? "douyin" : "douyin",
      accountId: typeof parsed.accountId === "number" ? parsed.accountId : null,
    };
  } catch {
    return EMPTY_WORKSPACE;
  }
}

function persistWorkspace(value: WorkspaceSelection) {
  const stored: StoredWorkspaceV2 = { version: 2, ...value };
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify(stored));
}

function updateSelection(
  set: (selection: Partial<CurrentWorkspaceState>) => void,
  value: WorkspaceSelection,
) {
  persistWorkspace(value);
  set(value);
}

export const useCurrentWorkspace = create<CurrentWorkspaceState>((set, get) => ({
  ...readStoredWorkspace(),
  setClientId: (clientId) => {
    const next = { ...get(), clientId, projectId: null };
    updateSelection(set, next);
  },
  setProjectId: (projectId) => {
    const next = { ...get(), projectId };
    updateSelection(set, next);
  },
  setPlatform: (platform) => {
    const next = {
      ...get(),
      platform,
      accountId: platform === get().platform ? get().accountId : null,
    };
    updateSelection(set, next);
  },
  setAccountId: (accountId) => {
    const next = { ...get(), accountId };
    updateSelection(set, next);
  },
  hydrate: (selection) => updateSelection(set, selection),
  clear: () => updateSelection(set, EMPTY_WORKSPACE),
}));
