import { create } from "zustand";

import type { Platform } from "../types";

const WORKSPACE_KEY = "tongzhouxing_current_workspace";

interface StoredWorkspace {
  platform: Platform;
  accountId: number | null;
}

interface CurrentWorkspaceState extends StoredWorkspace {
  setPlatform: (platform: Platform) => void;
  setAccountId: (accountId: number | null) => void;
  clear: () => void;
}

function readStoredWorkspace(): StoredWorkspace {
  try {
    const raw = localStorage.getItem(WORKSPACE_KEY);
    if (!raw) return { platform: "douyin", accountId: null };
    const parsed = JSON.parse(raw) as Partial<StoredWorkspace>;
    return {
      platform: parsed.platform === "douyin" ? "douyin" : "douyin",
      accountId: typeof parsed.accountId === "number" ? parsed.accountId : null,
    };
  } catch {
    return { platform: "douyin", accountId: null };
  }
}

function persistWorkspace(value: StoredWorkspace) {
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify(value));
}

export const useCurrentWorkspace = create<CurrentWorkspaceState>((set, get) => ({
  ...readStoredWorkspace(),
  setPlatform: (platform) => {
    const next = { platform, accountId: platform === get().platform ? get().accountId : null };
    persistWorkspace(next);
    set(next);
  },
  setAccountId: (accountId) => {
    const next = { platform: get().platform, accountId };
    persistWorkspace(next);
    set(next);
  },
  clear: () => {
    const next = { platform: "douyin" as Platform, accountId: null };
    persistWorkspace(next);
    set(next);
  },
}));
