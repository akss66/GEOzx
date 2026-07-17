import type { Account } from "../../types";

export type AccountActionMode =
  | "official_authorize"
  | "sync_metrics"
  | "coming_soon";

export function getAccountActionMode(account: Account): AccountActionMode {
  if (account.platform !== "douyin") return "coming_soon";
  return account.auth_status === "authorized"
    ? "sync_metrics"
    : "official_authorize";
}
