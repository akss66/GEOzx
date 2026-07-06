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

// —— 运营大脑 / 专家团 ——

export type AgentCode =
  | "00-decision"
  | "01-positioning"
  | "02-content-director"
  | "03-art-director"
  | "04-video-creator"
  | "05-editor"
  | "06-operator"
  | "07-advertiser"
  | "08-customer-service";

export type AgentGroup =
  | "control"
  | "strategy"
  | "creative"
  | "operation"
  | "growth"
  | "feedback";

export type BrainTaskStatus =
  | "draft"
  | "pending_confirmation"
  | "running"
  | "pending_acceptance"
  | "completed"
  | "failed";

export type BrainTaskGroupBy = "status" | "project" | "account_group" | "task_type";

export type RerunScope = "current_agent" | "upstream" | "downstream" | "full_chain";

export type AutomationLevel = "manual" | "confirm" | "auto";

export interface TaskBrief {
  goal: string;
  project_id: number | null;
  project_name: string | null;
  account_group_id: number | null;
  account_group_name: string | null;
  platforms: Platform[];
  account_ids: number[];
  cycle: string;
  budget: number | null;
  content_goal: string;
  risk_constraints: string[];
  expected_outputs: string[];
  confirmation_actions: string[];
}

export interface DraftBrainTaskInput {
  goal: string;
  project_id?: number | null;
  account_group_id?: number | null;
  platforms?: Platform[];
  account_ids?: number[];
}

export interface OrchestrationPlanStep {
  id: string;
  agent_code: AgentCode;
  agent_name: string;
  phase: string;
  intent: string;
  status: "planned" | "running" | "done" | "blocked" | "skipped" | "failed";
  depends_on: string[];
  expected_output: string;
  risk_level: "low" | "medium" | "high";
  execution_kind?: string;
  human_gate?: boolean;
  tool_codes?: string[];
}

export interface OrchestrationPlan {
  id: number;
  summary: string;
  steps: OrchestrationPlanStep[];
  quality_gates: string[];
  estimated_cost: number;
  requires_human_confirmation: boolean;
}

export interface BrainTask {
  id: number;
  content_item_id: number | null;
  title: string;
  type: "content_creation" | "account_diagnosis" | "review_optimization" | "matrix_distribution";
  status: BrainTaskStatus;
  brief: TaskBrief;
  plan: OrchestrationPlan;
  progress: number;
  current_focus: string;
  risk_count: number;
  context_closed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentProfile {
  code: AgentCode;
  name: string;
  group: AgentGroup;
  one_liner: string;
  model: string;
  fallback_model: string | null;
  automation_level: AutomationLevel;
  tools: string[];
  typical_tasks: string[];
  standard_outputs: DeliverableType[];
  current_task: {
    task_id: number;
    title: string;
    project_name: string;
    account_group_name: string;
    platforms: Platform[];
    progress: number;
    risk_level: "low" | "medium" | "high";
    blockers: string[];
    next_action: string;
    output_summary: string;
  } | null;
  tool_summary: AgentToolCallSummary;
}

export interface AgentInvocation {
  id: number;
  task_id: number;
  agent_code: AgentCode;
  agent_name: string;
  status: "queued" | "running" | "done" | "failed" | "blocked";
  input_summary: string;
  output_summary: string;
  model: string;
  token_count: number;
  cost: number;
  failure_reason: string | null;
  upstream: number[];
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentToolCallSummaryItem {
  id: number;
  task_id: number;
  tool_code: string;
  tool_name: string;
  status: string;
  permission_mode: string;
  requires_human_confirmation: boolean;
  input_summary: string;
  output_summary: string;
  error: string | null;
  created_at: string;
}

export interface AgentToolCallSummary {
  total_calls: number;
  pending_approvals: number;
  failed_calls: number;
  recent_calls: AgentToolCallSummaryItem[];
}

export interface AgentToolCall {
  id: number;
  org_id: number;
  task_id: number;
  invocation_id: number | null;
  module: string;
  agent_code: string | null;
  tool_code: string;
  tool_name: string;
  status:
    | "planned"
    | "running"
    | "success"
    | "failed"
    | "blocked"
    | "waiting_approval"
    | "skipped";
  permission_mode: "auto" | "confirm" | "manual" | string;
  requires_human_confirmation: boolean;
  input_summary: string;
  output_summary: string;
  error: string | null;
  latency_ms: number | null;
  cost: number;
  meta: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeliverableAcceptance {
  id: number;
  task_id: number;
  deliverable_id: number | null;
  agent_code: AgentCode;
  agent_name: string;
  deliverable_type: DeliverableType;
  title: string;
  version: number;
  summary: string;
  acceptance_items: {
    label: string;
    status: "pass" | "warn" | "fail";
    note: string;
  }[];
  history_versions: {
    version: number;
    status: DeliverableStatus;
    note: string;
    created_at: string;
  }[];
  status: "pending" | "approved" | "rejected" | "rerun_requested";
  reviewer_note: string | null;
  rerun_scope: RerunScope | null;
  brain_rejudge_summary: string | null;
  brain_rejudge_basis: string[];
}

export interface AutomationPolicy {
  id: number;
  project_id: number | null;
  account_group_id: number | null;
  platform: Platform | null;
  action_type: string;
  level: AutomationLevel;
}

export interface CostModelRow {
  model: string;
  calls: number;
  tokens: number;
  cost: number;
}

export interface CostAgentRow {
  agent_code: string;
  agent_name: string;
  calls: number;
  tokens: number;
  cost: number;
}

export interface CostTaskRow {
  task_id: number;
  title: string;
  type: BrainTask["type"];
  calls: number;
  tokens: number;
  cost: number;
}

export interface CostBrainRow {
  type: BrainTask["type"];
  tasks: number;
  calls: number;
  tokens: number;
  cost: number;
}

export interface CostOverview {
  total_cost: number;
  total_calls: number;
  total_tokens: number;
  by_brain: CostBrainRow[];
  by_model: CostModelRow[];
  by_agent: CostAgentRow[];
  by_task: CostTaskRow[];
}

export type RiskCategory = "quality_gate" | "account_auth" | "model_failure" | "data_sync";
export type RiskSeverity = "low" | "medium" | "high";

export interface RiskQueueItem {
  id: string;
  category: RiskCategory;
  severity: RiskSeverity;
  title: string;
  description: string;
  source: string;
  status: string;
  created_at: string;
}

// —— 工作区域：项目 / 账号矩阵 ——

export type Platform = "douyin" | "xiaohongshu" | "shipinhao";
export type ProjectStatus = "active" | "paused" | "archived";
export type AccountStatus = "active" | "inactive" | "banned";
export type GroupDimension = "track" | "persona" | "platform";
export type IntegrationStatus = "oauth_ready" | "connected" | "manual" | "disabled";
export type AuthStatus = "unauthorized" | "authorized" | "expired" | "manual";
export type DataSyncStatus =
  | "not_configured"
  | "pending"
  | "syncing"
  | "healthy"
  | "failed"
  | "manual";
export type PlatformIntegrationStatus =
  | "not_configured"
  | "configured"
  | "pending_review"
  | "connected"
  | "disabled";

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
  project_id: number | null;
  status: AccountStatus;
  external_account_id: string | null;
  integration_status: IntegrationStatus;
  auth_status: AuthStatus;
  data_sync_status: DataSyncStatus;
  created_at: string;
}

export interface CreateAccountInput {
  nickname: string;
  platform: Platform;
  group_id?: number | null;
  project_id?: number | null;
  external_account_id?: string | null;
}

export interface DouyinScanAddInput {
  nickname?: string | null;
  group_id?: number | null;
  project_id?: number | null;
}

export interface AccountMatrixGroup {
  id: number;
  name: string;
  dimension: GroupDimension;
  accounts: Account[];
}

export interface PlatformMatrixSummary {
  platform: Platform;
  total: number;
  active: number;
  integration_status: IntegrationStatus;
  auth_status: AuthStatus;
  data_sync_status: DataSyncStatus;
}

export interface AccountMatrix {
  groups: AccountMatrixGroup[];
  ungrouped_accounts: Account[];
  platforms: PlatformMatrixSummary[];
}

export interface PlatformIntegration {
  id: number | null;
  platform: Platform;
  status: PlatformIntegrationStatus;
  client_key: string | null;
  client_secret_configured: boolean;
  redirect_uri: string | null;
  js_sdk_domain: string | null;
  auth_status: AuthStatus | "not_configured";
  data_sync_status: DataSyncStatus;
  scopes: string[];
  capabilities: Record<string, string>;
  official_docs: string[];
  note: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface UpdatePlatformIntegrationInput {
  status?: PlatformIntegrationStatus;
  client_key?: string | null;
  client_secret_ref?: string | null;
  redirect_uri?: string | null;
  js_sdk_domain?: string | null;
  auth_status?: PlatformIntegration["auth_status"];
  data_sync_status?: DataSyncStatus;
  scopes?: string[];
  capabilities?: Record<string, string>;
  note?: string | null;
}

export interface DouyinAuthorizeUrl {
  platform: "douyin";
  client_key: string;
  redirect_uri: string;
  scopes: string[];
  state: string;
  authorization_url: string;
}

export interface DouyinTrialWhitelistUrl {
  platform: "douyin";
  client_key: string;
  redirect_uri: string;
  scopes: string[];
  authorization_url: string;
}

export interface DouyinJsSignature {
  platform: "douyin";
  client_key: string;
  nonce_str: string;
  timestamp: number;
  url: string;
  signature: string;
}

export interface DouyinDataSyncResult {
  account_id: number;
  platform: "douyin";
  data_sync_status: DataSyncStatus;
  profile_synced: boolean;
  video_count: number;
  snapshot_count: number;
  last_sync_at: string;
}

export interface DistributionAction {
  id: number;
  platform: Platform;
  account_ids: number[];
  action_type: string;
  status: "recorded";
  content_item_id: number | null;
  project_id: number | null;
  note: string | null;
  created_at: string;
}

// —— 编排：流程执行 / 质量门 ——

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

export interface MaterialAsset {
  id: number;
  content_item_id: number | null;
  deliverable_id: number | null;
  kind: string;
  provider: string | null;
  status: "queued" | "generating" | "ready" | "failed";
  size_bytes: number | null;
  file_url: string | null;
  error: string | null;
  created_at: string;
}

export interface PendingGate {
  id: number;
  gate: GateType;
  status: GateStatus;
  content_item_id: number;
  content_title: string;
  created_at: string;
  compliance: ComplianceCheck | null;
}

export type ComplianceRisk = "pass" | "warn" | "block";

export interface PublishReadinessInput {
  platform?: "douyin" | "xiaohongshu" | "shipinhao";
  title: string;
  body?: string;
  topics?: string[];
  scheduled_at?: string | null;
  material_ids?: number[];
  cover_material_id?: number | null;
  visibility?: "public" | "friends" | "private";
  allow_comment?: boolean;
}

export interface PublishCapability {
  platform: "douyin" | "xiaohongshu" | "shipinhao";
  content_types: ("video" | "image_text")[];
  supported_fields: string[];
  execution_mode: "official_api" | "manual_checklist" | "browser_runner_disabled";
  permission_status: "oauth_authorized" | "pending_review" | "prepare_only";
  browser_runner_enabled: boolean;
}

export interface PublishReadinessFinding {
  level: ComplianceRisk;
  code: string;
  message: string;
}

export interface PublishPackage {
  platform: "douyin" | "xiaohongshu" | "shipinhao";
  account_id: number | null;
  content_type: "video" | "image_text";
  title: string;
  body: string;
  topics: string[];
  scheduled_at: string | null;
  material_ids: number[];
  cover_material_id: number | null;
  visibility: "public" | "friends" | "private";
  allow_comment: boolean;
  execution_mode: "official_api" | "manual_checklist" | "browser_runner_disabled";
  manual_steps: string[];
}

export interface PublishReadiness {
  content_item_id: number;
  platform: "douyin" | "xiaohongshu" | "shipinhao";
  ready: boolean;
  risk: ComplianceRisk;
  package: PublishPackage;
  findings: PublishReadinessFinding[];
  tool_call: AgentToolCall;
}

export type MatrixPlanStatus =
  | "draft"
  | "pending_approval"
  | "queued"
  | "running"
  | "waiting_manual"
  | "completed"
  | "failed"
  | "cancelled";

export interface MatrixDistributionItem {
  id: number;
  org_id: number;
  plan_id: number;
  account_id: number;
  material_id: number;
  platform: Platform;
  status: MatrixPlanStatus;
  tool_call_id: number | null;
  publish_package: PublishPackage;
  retry_count: number;
  next_retry_at: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface MatrixDistributionPlan {
  id: number;
  org_id: number;
  content_item_id: number | null;
  created_by_id: number | null;
  title: string;
  body: string;
  platforms: Platform[];
  account_ids: number[];
  material_ids: number[];
  topics: string[];
  cover_material_id: number | null;
  scheduled_at: string | null;
  status: MatrixPlanStatus;
  items: MatrixDistributionItem[];
  created_at: string;
  updated_at: string;
}

export interface CreateMatrixDistributionPlanInput {
  platforms?: Platform[];
  account_ids: number[];
  material_ids: number[];
  content_item_id?: number | null;
  title: string;
  body?: string;
  topics?: string[];
  cover_material_id?: number | null;
  scheduled_at?: string | null;
}

export interface ComplianceFinding {
  word: string;
  category: string;
  level: "warn" | "block";
}

export interface ComplianceCheck {
  id: number;
  risk: ComplianceRisk;
  summary: string;
  findings: ComplianceFinding[] | null;
  created_at: string;
}

// —— 闭环反馈：优化建议追踪 ——

export type OptimizationSuggestionStatus = "suggested" | "accepted" | "verified";

export interface OptimizationSuggestion {
  id: number;
  content_item_id: number;
  content_title: string;
  source_deliverable_id: number | null;
  target_stage: ContentStage | null;
  suggestion: string;
  status: OptimizationSuggestionStatus;
  note: string | null;
  accepted_at: string | null;
  verified_at: string | null;
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
