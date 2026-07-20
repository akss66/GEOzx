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

export type WorkspaceRole = "lead" | "operator" | "editor" | "reviewer";

export interface ClientMembership {
  client_id: number;
  client_name: string;
  role: WorkspaceRole;
}

export interface ProjectMembership {
  project_id: number;
  project_name: string;
  client_id: number | null;
  client_name: string | null;
  role: WorkspaceRole;
}

export type AccountScopeMode = "all_accessible" | "selected";
export type UserAccessDetailStatus = "available" | "unavailable";

export interface UserDetail extends User {
  has_global_access: boolean;
  account_scope_mode: AccountScopeMode;
  access_detail_status: UserAccessDetailStatus;
  client_ids: number[];
  project_ids: number[];
  account_ids: number[];
  client_memberships: ClientMembership[];
  project_memberships: ProjectMembership[];
}

export interface UserDetailResponse extends User {
  has_global_access?: boolean | null;
  account_scope_mode?: AccountScopeMode | null;
  client_ids?: number[] | null;
  project_ids?: number[] | null;
  account_ids?: number[] | null;
  client_memberships?: ClientMembership[] | null;
  project_memberships?: ProjectMembership[] | null;
}

export interface AccountAccessCatalogItem {
  id: number;
  client_id: number | null;
  project_ids: number[];
  nickname: string;
  platform: Platform;
  status: AccountStatus;
}

interface UserAccessCatalogBase {
  clients: Array<{ id: number; name: string; status: "active" | "archived" }>;
  projects: Array<{
    id: number;
    client_id: number | null;
    name: string;
    status: "active" | "paused" | "archived";
  }>;
}

export type UserAccessCatalog = UserAccessCatalogBase & (
  | { account_catalog_status: "available"; accounts: AccountAccessCatalogItem[] }
  | { account_catalog_status: "unavailable"; accounts?: never }
);

export interface UserAccessCatalogResponse {
  clients?: UserAccessCatalogBase["clients"] | null;
  projects?: UserAccessCatalogBase["projects"] | null;
  accounts?: Array<Omit<AccountAccessCatalogItem, "project_ids"> & {
    project_ids?: number[] | null;
  }> | null;
}

export interface UpdateUserInput {
  email?: string;
  display_name?: string;
  role?: Role;
  is_active?: boolean;
}

export interface UpdateUserAccessInput {
  clients: Array<{ client_id: number; role: WorkspaceRole }>;
  projects: Array<{ project_id: number; role: WorkspaceRole }>;
  account_scope_mode?: AccountScopeMode;
  account_ids?: number[];
}

export interface SetSecondaryPasswordInput {
  current_password: string;
  secondary_password: string;
}

export interface SecondaryPasswordStatus {
  configured: boolean;
  deletion_available: boolean;
  delete_available_at: string | null;
  locked_until: string | null;
}

export interface ResetUserPasswordInput {
  new_password: string;
}

export type UserDeletionBlocker =
  | "LAST_ACTIVE_ADMIN"
  | "USER_SELF_DELETION_FORBIDDEN";

export interface UserDeletionPreview {
  target_user_id: number;
  target_email: string;
  counts: Record<string, number>;
  preview_token: string;
  expires_at: string;
  allowed: boolean;
  blockers: UserDeletionBlocker[];
}

export interface PermanentDeleteUserInput {
  preview_token: string;
  target_email: string;
  secondary_password: string;
}

export interface PermanentDeleteUserResponse {
  operation_id: string;
  deleted_at: string;
  counts: Record<string, number>;
}

export type UserGovernanceErrorCode =
  | "CURRENT_PASSWORD_INVALID"
  | "LAST_ACTIVE_ADMIN"
  | "SECONDARY_PASSWORD_COOLDOWN"
  | "SECONDARY_PASSWORD_INVALID"
  | "SECONDARY_PASSWORD_LOCKED"
  | "SECONDARY_PASSWORD_NOT_CONFIGURED"
  | "USER_DELETION_EMAIL_MISMATCH"
  | "USER_DELETION_PREVIEW_EXPIRED"
  | "USER_DELETION_PREVIEW_INVALID"
  | "USER_DELETION_PREVIEW_STALE"
  | "USER_DELETION_PREVIEW_USED"
  | "USER_DELETION_TRANSACTION_FAILED"
  | "USER_LAST_ACTIVE_ADMIN_REQUIRED"
  | "USER_SELF_ADMIN_CHANGE_FORBIDDEN"
  | "USER_SELF_DELETION_FORBIDDEN"
  | "USER_SELF_PASSWORD_RESET_FORBIDDEN";

export interface UserGovernanceErrorDetail {
  code: UserGovernanceErrorCode;
  message: string;
}

export interface UserGovernanceErrorResponse {
  detail: UserGovernanceErrorDetail;
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
  tool_permissions?: Record<string, ToolPermissionMode>;
  quality_gates?: string[];
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
  runtime_mode: "legacy" | "langgraph" | string;
  thread_id: string | null;
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

export type ToolPermissionMode = "auto" | "confirm" | "manual" | "disabled";

export interface AgentManagementTool {
  code: string;
  name: string;
  description: string;
}

export interface AgentManagementGate {
  code: string;
  name: string;
  description: string;
  forced: boolean;
}

export interface AgentManagement {
  code: AgentCode;
  name: string;
  group: AgentGroup;
  enabled: boolean;
  responsibility: string;
  system_prompt: string;
  automation_level: AutomationLevel;
  tool_permissions: Record<string, ToolPermissionMode>;
  quality_gates: string[];
  available_tools: AgentManagementTool[];
  available_quality_gates: AgentManagementGate[];
  typical_tasks: string[];
  standard_outputs: DeliverableType[];
  updated_at: string | null;
}

export interface UpdateAgentManagementInput {
  enabled: boolean;
  responsibility: string;
  system_prompt: string;
  tool_permissions: Record<string, ToolPermissionMode>;
  quality_gates: string[];
}

export interface AgentDirectRun {
  task: BrainTask;
  invocation: AgentInvocation;
  deliverable: Deliverable;
  acceptance: DeliverableAcceptance;
  knowledge_sources: Array<{
    id: number;
    category: KnowledgeCategory;
    title: string;
    source_label: string;
    version: number;
  }>;
  message: string;
}

export interface AgentHandoff {
  task_id: number;
  project_id: number;
  account_id: number;
  prompt: string;
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

export interface RuntimeEvent {
  id: number;
  type: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export type BrainIntentKind =
  | "conversation"
  | "clarification"
  | "analysis"
  | "workflow"
  | "action";

export interface BrainIntentDecision {
  intent: BrainIntentKind;
  confidence: number;
  reason: string;
  missing_field: string | null;
  clarifying_question: string | null;
  suggested_expert_codes: AgentCode[];
  requires_account_context: boolean;
}

export interface BrainDecisionChoice {
  id: string;
  title: string;
  description: string;
  benefit: string;
  tradeoff: string;
  recommended: boolean;
}

export interface BrainDecisionRequest {
  id: string;
  title: string;
  summary: string;
  choices: BrainDecisionChoice[];
  allow_custom_input: boolean;
  status: "pending" | "selected" | "revised";
}

export interface BrainRuntime {
  task: BrainTask;
  thread_id: string | null;
  status: "legacy" | "running" | "waiting_permission" | "completed" | "failed" | string;
  timeline: RuntimeEvent[];
  invocations: AgentInvocation[];
  tool_calls: AgentToolCall[];
  acceptances: DeliverableAcceptance[];
  pending_permissions: AgentToolCall[];
  intent?: BrainIntentDecision | null;
  pending_decisions?: BrainDecisionRequest[];
  next_actions: string[];
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
    status: "pass" | "warn" | "fail" | "pending";
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

export type BudgetStatus = "no_budget" | "healthy" | "warning" | "exceeded";

export interface CostOverview {
  scope: {
    client_id: number;
    client_name: string;
    project_id: number | null;
    project_name: string | null;
    period_days: number;
    period_start: string;
    period_end: string;
  };
  summary: {
    actual_cost: number;
    budget: number | null;
    budget_usage: number | null;
    remaining_budget: number | null;
    task_count: number;
    agent_calls: number;
    tool_calls: number;
    failed_operations: number;
    budget_status: BudgetStatus;
  };
  by_project: Array<{
    project_id: number;
    project_name: string;
    budget: number | null;
    actual_cost: number;
    budget_usage: number | null;
    budget_status: BudgetStatus;
    task_count: number;
  }>;
  by_agent: Array<{
    agent_code: string;
    agent_name: string;
    calls: number;
    cost: number;
    failed_calls: number;
  }>;
  by_task: Array<{
    task_id: number;
    title: string;
    type: BrainTask["type"];
    status: string;
    agent_calls: number;
    tool_calls: number;
    cost: number;
  }>;
  by_tool: Array<{
    tool_code: string;
    tool_name: string;
    calls: number;
    cost: number;
    failed_calls: number;
  }>;
  daily: Array<{ date: string; cost: number }>;
}

export interface TechnicalCostOverview {
  period_days: number;
  period_start: string;
  period_end: string;
  summary: {
    total_cost: number;
    total_calls: number;
    total_tokens: number;
    failed_calls: number;
    fallback_attempts: number;
    average_latency_ms: number;
  };
  by_provider: Array<{
    provider: string;
    calls: number;
    tokens: number;
    cost: number;
    failed_calls: number;
    average_latency_ms: number;
  }>;
  by_model: Array<{
    provider: string;
    model: string;
    calls: number;
    tokens: number;
    cost: number;
    failed_calls: number;
    average_latency_ms: number;
  }>;
  by_agent: Array<{
    agent_code: string;
    calls: number;
    tokens: number;
    cost: number;
    failed_calls: number;
  }>;
  daily: Array<{ date: string; calls: number; failed_calls: number; cost: number }>;
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

export type ClientStatus = "active" | "archived";

export interface Client {
  id: number;
  name: string;
  status: ClientStatus;
  created_at: string;
}

export interface Project {
  id: number;
  client_id?: number | null;
  name: string;
  description: string | null;
  monthly_cost_budget_usd?: number | null;
  status: ProjectStatus;
  created_at: string;
}

export interface AccountGroup {
  id: number;
  name: string;
  dimension: GroupDimension;
  created_at: string;
}

export interface AccountCurrentTask {
  id: number;
  title: string;
  status: string;
  progress: number;
  current_focus: string;
}

export interface Account {
  id: number;
  client_id?: number | null;
  nickname: string;
  platform: Platform;
  group_id: number | null;
  project_id: number | null;
  project_ids?: number[];
  status: AccountStatus;
  external_account_id: string | null;
  integration_status: IntegrationStatus;
  auth_status: AuthStatus;
  data_sync_status: DataSyncStatus;
  avatar_url?: string | null;
  positioning_summary?: string | null;
  current_task?: AccountCurrentTask | null;
  risk_count?: number;
  last_sync_at?: string | null;
  publish_capability?: "prepare_only" | "manual_only" | "unavailable";
  created_at: string;
}

export interface CreateAccountInput {
  nickname: string;
  platform: Platform;
  client_id?: number | null;
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

export interface ContentAgentTask {
  id: number;
  agent_code: string;
  stage: ContentStage;
  status: AgentTaskStatus;
  output_deliverable_id: number | null;
}

export interface ContentGate {
  id: number;
  gate: GateType;
  status: GateStatus;
  decided_by: number | null;
  comment: string | null;
  created_at: string;
  decided_at: string | null;
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

export interface ContentWorkspace {
  content_item: ContentItem;
  project_name: string;
  account: {
    id: number;
    nickname: string;
    platform: Platform;
    auth_status: string;
  } | null;
  tasks: ContentAgentTask[];
  deliverables: Deliverable[];
  gates: ContentGate[];
  compliance: ComplianceCheck[];
  materials: MaterialAsset[];
  publish_tool_calls: AgentToolCall[];
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

export type ApprovalKind = "gate" | "tool_call" | "deliverable";
export type ApprovalRiskLevel = "low" | "medium" | "high" | "critical";

export interface ApprovalQueueItem {
  key: string;
  kind: ApprovalKind;
  source_id: number;
  project_id: number;
  project_name: string;
  account_id: number | null;
  account_name: string | null;
  content_item_id: number | null;
  content_title: string | null;
  task_id: number | null;
  category: string;
  title: string;
  summary: string;
  risk_level: ApprovalRiskLevel;
  risk_reasons: string[];
  impact: string[];
  agent_explanation: string;
  preview: Record<string, unknown>;
  can_decide: boolean;
  created_at: string;
}

export interface ApprovalWorkspace {
  items: ApprovalQueueItem[];
  counts: {
    total: number;
    critical: number;
    high: number;
    medium: number;
  };
  can_decide: boolean;
  generated_at: string;
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
  primary_provider_id: number | null;
  fallback_provider_id: number | null;
  primary_model: string;
  fallback_model: string | null;
}

export type ModelProviderCode = string;
export type ModelCallStatus = "ok" | "error";

export interface ModelProvider {
  code: ModelProviderCode;
  name: string;
  kind: "direct" | "router";
  enabled: boolean;
  credential_ref: string | null;
  credential_configured: boolean | null;
  runtime_ready: boolean;
  endpoint: string | null;
  supported_models: string[];
  note: string;
  updated_at: string | null;
}

export interface UpdateModelProviderInput {
  enabled: boolean;
  credential_ref: string | null;
}

export interface ModelRouteTarget {
  primary_provider_id: number;
  primary_model: string;
  fallback_provider_id: number | null;
  fallback_model: string | null;
}

export interface ModelRoute {
  id: number | null;
  agent_code: string;
  agent_name: string;
  primary_provider_id: number | null;
  fallback_provider_id: number | null;
  primary_model: string;
  fallback_model: string | null;
  temperature: number;
  max_tokens: number;
  timeout_seconds: number;
  updated_at: string | null;
}

interface ModelRoutePolicyInput {
  temperature: number;
  max_tokens: number;
  timeout_seconds: number;
}

export interface UpdateModelRouteInput
  extends ModelRouteTarget, ModelRoutePolicyInput {}

export interface ModelInfrastructureSummary {
  providers_total: number;
  providers_ready: number;
  routes_total: number;
  routes_with_fallback: number;
  calls_24h: number;
  failures_24h: number;
}

export interface ModelInfrastructureOverview {
  summary: ModelInfrastructureSummary;
  providers: ModelProvider[];
  routes: ModelRoute[];
}

export interface ModelCall {
  id: number;
  agent_code: string | null;
  agent_name: string;
  provider: string;
  model: string;
  total_tokens: number;
  cost_usd: number;
  latency_ms: number;
  status: ModelCallStatus;
  error_summary: string | null;
  created_at: string;
}

export interface ModelCallPage {
  total: number;
  items: ModelCall[];
}

export interface ModelProviderTemplate {
  code: string;
  display_name: string;
  base_url: string;
  protocol: string;
  models: string[];
}

export interface ModelProviderRouteRef {
  agent_code: string;
  agent_name: string;
}

export interface ModelProviderDetail {
  id: number;
  code: string;
  display_name: string;
  provider_type: string;
  template_code: string | null;
  protocol: string;
  base_url: string | null;
  enabled: boolean;
  sort_order: number;
  credential_source: string;
  key_configured: boolean;
  key_last_four: string | null;
  key_fingerprint: string | null;
  verification_status: string;
  verified_at: string | null;
  verification_error_code: string | null;
  models: string[] | null;
  models_updated_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  referenced_agents: ModelProviderRouteRef[];
}

interface CreatePresetModelProviderInput {
  template_code: string;
  provider_type?: never;
  code?: string | null;
  display_name?: string | null;
  base_url?: string | null;
  enabled?: boolean;
}

interface CreateCustomModelProviderInput {
  template_code?: null;
  provider_type: "custom_openai";
  code?: string | null;
  display_name: string;
  base_url: string;
  enabled?: boolean;
}

export type CreateModelProviderInput =
  | CreatePresetModelProviderInput
  | CreateCustomModelProviderInput;

type AtLeastOne<T> = {
  [K in keyof T]-?: Required<Pick<T, K>> & Partial<Omit<T, K>>;
}[keyof T];

export type PatchModelProviderInput = AtLeastOne<{
  display_name: string;
  base_url: string;
  enabled: boolean;
}>;

export interface ModelProviderVerifyResult {
  provider_id: number;
  verification_status: string;
  verification_error_code: string | null;
  verified_at: string | null;
  latency_ms: number;
}

export interface ModelProviderDiscoveryResult {
  provider_id: number;
  models: string[];
  models_updated_at: string | null;
  error_code: string | null;
}

export interface ModelProviderDeleteConflict {
  affected_agents: ModelProviderRouteRef[];
}

// —— 共享知识库 ——

export type KnowledgeCategory =
  | "hot_content"
  | "user_persona"
  | "prompt_library"
  | "script_library";

export interface KnowledgeEntry {
  id: number;
  client_id: number;
  project_id: number | null;
  category: KnowledgeCategory;
  title: string;
  content: string;
  payload: Record<string, unknown>;
  tags: string[] | null;
  source_type: "manual" | "agent" | "deliverable" | "external";
  source_label: string;
  source_url: string | null;
  version: number;
  status: "active" | "archived";
  created_by_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface CreateKnowledgeInput {
  client_id: number;
  project_id?: number | null;
  category: KnowledgeCategory;
  title: string;
  content: string;
  payload?: Record<string, unknown>;
  tags?: string[] | null;
  source_type?: "manual" | "deliverable" | "external";
  source_label: string;
  source_url?: string | null;
}

export interface KnowledgeSuggestion {
  id: number;
  client_id: number;
  project_id: number | null;
  category: KnowledgeCategory;
  title: string;
  content: string;
  payload: Record<string, unknown>;
  tags: string[] | null;
  source_agent_code: string;
  source_label: string;
  source_task_id: number | null;
  source_deliverable_id: number | null;
  status: "pending" | "approved" | "rejected";
  reviewed_by_id: number | null;
  reviewed_at: string | null;
  review_note: string | null;
  accepted_entry_id: number | null;
  created_at: string;
}

export interface KnowledgeCitation {
  id: number;
  entry_id: number;
  project_id: number | null;
  task_id: number | null;
  invocation_id: number | null;
  agent_code: string;
  context: string;
  created_at: string;
}
