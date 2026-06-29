export type Role = "admin" | "user";

export interface User {
  id: number;
  email: string;
  display_name: string;
  role: Role;
  is_active: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface CreateUserInput {
  email: string;
  password: string;
  display_name: string;
  role: Role;
}

// —— 工作区域：项目 / 账号矩阵 ——

export type Platform = "douyin" | "xiaohongshu" | "shipinhao";
export type ProjectStatus = "active" | "paused" | "archived";
export type AccountStatus = "active" | "inactive" | "banned";
export type GroupDimension = "track" | "persona" | "platform";

export interface Project {
  id: number;
  name: string;
  description: string | null;
  status: ProjectStatus;
  created_at: string;
}

export interface AccountGroup {
  id: number;
  name: string;
  dimension: GroupDimension;
  created_at: string;
}

export interface Account {
  id: number;
  nickname: string;
  platform: Platform;
  group_id: number | null;
  status: AccountStatus;
  external_account_id: string | null;
  created_at: string;
}

export interface CreateAccountInput {
  nickname: string;
  platform: Platform;
  group_id?: number | null;
  external_account_id?: string | null;
}

// —— 编排：内容流水线 / 质量门 ——

export type ContentStage =
  | "positioning"
  | "content_direction"
  | "art_direction"
  | "video_creation"
  | "editing"
  | "operation"
  | "advertising"
  | "customer_service";

export type ContentStatus =
  | "draft"
  | "in_progress"
  | "blocked"
  | "published"
  | "archived";

export type AgentTaskStatus = "pending" | "running" | "done" | "failed" | "blocked";

export type GateType =
  | "positioning_review"
  | "topic_review"
  | "script_compliance"
  | "final_video_review"
  | "pre_publish_review"
  | "large_ad_spend";

export type GateStatus = "pending" | "approved" | "rejected" | "auto_passed";

export interface ContentItem {
  id: number;
  project_id: number;
  account_id: number | null;
  title: string;
  current_stage: ContentStage;
  status: ContentStatus;
  created_at: string;
}

export type DeliverableType =
  | "positioning_strategy"
  | "topic_plan"
  | "publish_calendar"
  | "video_script"
  | "art_prompt"
  | "video_asset"
  | "edited_video"
  | "review_report"
  | "ad_plan"
  | "cs_record";

export type DeliverableStatus =
  | "draft"
  | "pending_review"
  | "approved"
  | "rejected"
  | "superseded";

export interface Deliverable {
  id: number;
  agent_code: string;
  type: DeliverableType;
  version: number;
  status: DeliverableStatus;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface PendingGate {
  id: number;
  gate: GateType;
  status: GateStatus;
  content_item_id: number;
  content_title: string;
  created_at: string;
}

// —— 模型配置：per-Agent 首选/兜底 ——

export interface ModelConfig {
  id: number;
  agent_code: string;
  primary_model: string;
  fallback_model: string | null;
}

// —— 共享知识库 ——

export type KnowledgeCategory =
  | "hot_content"
  | "user_persona"
  | "prompt_library"
  | "script_library";

export interface KnowledgeEntry {
  id: number;
  category: KnowledgeCategory;
  title: string;
  payload: Record<string, unknown>;
  tags: string[] | null;
  created_at: string;
}

export interface CreateKnowledgeInput {
  category: KnowledgeCategory;
  title: string;
  payload?: Record<string, unknown>;
  tags?: string[] | null;
}
