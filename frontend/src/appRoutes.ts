export type AppPage =
  | "brain"
  | "agents"
  | "tasks"
  | "approvals"
  | "review"
  | "cost"
  | "risks"
  | "accounts"
  | "account-data"
  | "knowledge"
  | "config"
  | "models"
  | "users";

export interface AppRouteItem {
  page: AppPage;
  path?: string;
  index?: boolean;
  adminOnly?: boolean;
}

export const APP_ROUTES: readonly AppRouteItem[] = [
  { index: true, page: "brain" },
  { path: "brain", page: "brain" },
  { path: "agents", page: "agents" },
  { path: "tasks", page: "tasks" },
  { path: "approvals", page: "approvals" },
  { path: "review", page: "review" },
  { path: "cost", page: "cost" },
  { path: "risks", page: "risks" },
  { path: "accounts", page: "accounts" },
  { path: "accounts/:accountId/data", page: "account-data" },
  { path: "knowledge", page: "knowledge" },
  { path: "config", page: "config", adminOnly: true },
  { path: "models", page: "models", adminOnly: true },
  { path: "users", page: "users", adminOnly: true },
] as const;

export const PUBLIC_ROUTES = [{ path: "/login", page: "login" }] as const;
