import { api } from "./client";

export type AccountDataSourceKind =
  | "official_api"
  | "platform_export"
  | "screenshot_verified"
  | "manual_entry";

export type AccountDataCoverage = "available" | "partial" | "missing";
export type AccountDataImportBatchStatus =
  | "preview_ready"
  | "committed"
  | "revoked";
export type AccountDataImportRowStatus =
  | "ready"
  | "invalid"
  | "needs_resolution"
  | "committed"
  | "revoked";

export interface AccountDataImportArtifact {
  id: number;
  filename: string;
  content_type: string;
  byte_size: number;
  sha256: string;
  download_url: string;
}

export interface AccountDataImportConflict {
  id: number;
  row_number: number;
  status: string;
  field_name: string;
  conflict_code: string;
  message: string;
  candidate_content_ids: number[];
  resolved_by_id: number | null;
  resolved_at: string | null;
}

export interface AccountDataImportRow {
  id: number;
  row_number: number;
  status: AccountDataImportRowStatus;
  raw_values: Record<string, unknown>;
  normalized_values: Record<string, unknown>;
  field_errors: Array<Record<string, unknown>>;
  warnings: Array<Record<string, unknown>>;
  candidate_content_ids: number[];
  projected_target_ids: Array<Record<string, unknown>>;
  platform_content_record_id: number | null;
  resolution_outcome: string | null;
  resolved_by_id: number | null;
  resolved_at: string | null;
}

export interface AccountDataImportBatchSummary {
  id: number;
  status: AccountDataImportBatchStatus;
  source_kind: AccountDataSourceKind;
  template_code: string;
  row_count: number;
  period_start: string | null;
  period_end: string | null;
  committed_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface AccountDataImportBatch extends AccountDataImportBatchSummary {
  artifacts: AccountDataImportArtifact[];
  rows: AccountDataImportRow[];
  conflicts: AccountDataImportConflict[];
}

export interface AccountDataImportBatchList {
  items: AccountDataImportBatchSummary[];
}

export interface AccountDataStatusSource {
  batch_id: number;
  source_kind: AccountDataSourceKind;
  template_code: string;
  data_domain: string;
  committed_at: string;
  period_start: string | null;
  period_end: string | null;
}

export interface AccountDataStatus {
  account_id: number;
  latest_confirmed_at: string | null;
  coverage: Record<string, AccountDataCoverage>;
  sources: AccountDataStatusSource[];
}

export async function getAccountDataStatus(accountId: number): Promise<AccountDataStatus> {
  const { data } = await api.get<AccountDataStatus>(`/account-data/${accountId}/status`);
  return data;
}

export async function listAccountDataImports(
  accountId: number,
): Promise<AccountDataImportBatchList> {
  const { data } = await api.get<AccountDataImportBatchList>(
    `/account-data/${accountId}/imports`,
  );
  return data;
}

export async function getAccountDataImportBatch(
  accountId: number,
  batchId: number,
): Promise<AccountDataImportBatch> {
  const { data } = await api.get<AccountDataImportBatch>(
    `/account-data/${accountId}/imports/${batchId}`,
  );
  return data;
}

export async function uploadAccountDataImport(
  accountId: number,
  file: File,
): Promise<AccountDataImportBatch> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await api.post<AccountDataImportBatch>(
    `/account-data/${accountId}/imports`,
    formData,
    {
      headers: { "Content-Type": "multipart/form-data" },
    },
  );
  return data;
}

export async function resolveAccountDataImportRow(
  accountId: number,
  batchId: number,
  rowNumber: number,
  selectedContentId: number | null,
): Promise<AccountDataImportRow> {
  const { data } = await api.patch<AccountDataImportRow>(
    `/account-data/${accountId}/imports/${batchId}/rows/${rowNumber}`,
    { selected_content_id: selectedContentId },
  );
  return data;
}

export async function commitAccountDataImportBatch(
  accountId: number,
  batchId: number,
): Promise<AccountDataImportBatch> {
  const { data } = await api.post<AccountDataImportBatch>(
    `/account-data/${accountId}/imports/${batchId}/commit`,
  );
  return data;
}

export async function revokeAccountDataImportBatch(
  accountId: number,
  batchId: number,
): Promise<AccountDataImportBatch> {
  const { data } = await api.post<AccountDataImportBatch>(
    `/account-data/${accountId}/imports/${batchId}/revoke`,
  );
  return data;
}
