import type { GroupDimension, Platform } from "../types";

export type AccountMatrixView = "table" | "matrix" | "cards" | "projects";

export interface AccountMatrixPreferences {
  view: AccountMatrixView;
  projectId: number | null;
  dimension: GroupDimension | "all";
  platform: Platform | "all";
  groupId: number | null;
}

const STORAGE_KEY = "tongzhouxing_account_matrix_preferences";
const DEFAULTS: AccountMatrixPreferences = {
  view: "table",
  projectId: null,
  dimension: "all",
  platform: "all",
  groupId: null,
};

const VIEWS = new Set<AccountMatrixView>(["table", "matrix", "cards", "projects"]);
const DIMENSIONS = new Set<AccountMatrixPreferences["dimension"]>([
  "all",
  "track",
  "persona",
  "platform",
]);
const PLATFORMS = new Set<AccountMatrixPreferences["platform"]>([
  "all",
  "douyin",
  "xiaohongshu",
  "shipinhao",
]);

function positiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

export function loadAccountMatrixPreferences(): AccountMatrixPreferences {
  if (typeof globalThis.localStorage === "undefined") return { ...DEFAULTS };
  try {
    const parsed = JSON.parse(globalThis.localStorage.getItem(STORAGE_KEY) ?? "null") as
      | Record<string, unknown>
      | null;
    if (!parsed || parsed.version !== 1) return { ...DEFAULTS };
    return {
      view: VIEWS.has(parsed.view as AccountMatrixView)
        ? (parsed.view as AccountMatrixView)
        : DEFAULTS.view,
      projectId: positiveInteger(parsed.projectId),
      dimension: DIMENSIONS.has(parsed.dimension as AccountMatrixPreferences["dimension"])
        ? (parsed.dimension as AccountMatrixPreferences["dimension"])
        : DEFAULTS.dimension,
      platform: PLATFORMS.has(parsed.platform as AccountMatrixPreferences["platform"])
        ? (parsed.platform as AccountMatrixPreferences["platform"])
        : DEFAULTS.platform,
      groupId: positiveInteger(parsed.groupId),
    };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveAccountMatrixPreferences(preferences: AccountMatrixPreferences): void {
  if (typeof globalThis.localStorage === "undefined") return;
  globalThis.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ version: 1, ...preferences }),
  );
}
