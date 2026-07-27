import type { Platform, PublishPackage } from "../types";

export type PublishJobStatus =
  | "draft"
  | "pending_approval"
  | "task_created"
  | "handoff_ready"
  | "user_publishing"
  | "waiting_bind"
  | "bound"
  | "observing"
  | "completed"
  | "failed"
  | "expired"
  | "cancelled";

export interface PublishJob {
  id: number;
  org_id: number;
  account_id: number;
  active_client_id: number | null;
  active_project_id: number | null;
  created_by_id: number | null;
  brain_task_id: number | null;
  tool_call_id: number | null;
  platform_content_record_id: number | null;
  platform: Platform;
  status: PublishJobStatus;
  idempotency_key: string;
  publish_package: PublishPackage;
  capabilities_snapshot: Record<string, unknown>;
  approval_snapshot: Record<string, unknown>;
  share_id: string | null;
  posting_task_id: string | null;
  external_video_id: string | null;
  external_item_id: string | null;
  expires_at: string | null;
  handoff_started_at: string | null;
  bound_at: string | null;
  retry_count: number;
  next_retry_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  last_platform_log_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreatePublishJobInput {
  account_id: number;
  active_client_id: number | null;
  active_project_id: number | null;
  tool_call_id: number;
  idempotency_key: string;
  publish_package: PublishPackage;
}

export interface PublishHandoff {
  job: PublishJob;
  schema_url: string;
  expires_at: string;
}
