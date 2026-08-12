import { api } from "../api/client";

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function string(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function number(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function nullableNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export interface WechatArticleClaim {
  claimId: string;
  blockId: string;
  kind: string;
  text: string;
  citationIds: number[];
}

export interface WechatArticleBlock {
  type: string;
  blockId: string;
  [key: string]: unknown;
}

export interface WechatArticleDocument {
  title: string;
  digest: string;
  author: string | null;
  blocks: WechatArticleBlock[];
  claims: WechatArticleClaim[];
}

export interface WechatArticleImageSlot {
  id: number;
  stableKey: string;
  purpose: string;
  aspectRatio: string;
  visualBrief: string;
  status: string;
  selectedMaterialId: number | null;
  lockVersion: number;
  hasPrompt: boolean;
}

export interface WechatArticleWorkingCopy {
  articleId: number;
  accountId: number;
  accountName: string;
  document: WechatArticleDocument;
  lockVersion: number;
  basedOnDeliverableId: number | null;
  imageSlots: WechatArticleImageSlot[];
}

export interface SaveWechatArticleWorkingCopyInput {
  expectedLockVersion: number;
  document: WechatArticleDocument;
}

export interface WechatArticlePreview {
  articleId: number;
  document: WechatArticleDocument;
  renderedHtml: string | null;
}

export interface WechatArticleVersion {
  id: number;
  articleId: number;
  version: number;
  trigger: string;
  document: WechatArticleDocument;
}

export interface WechatArticleVersionDiff {
  baseVersion: number;
  targetVersion: number;
  added: string[];
  removed: string[];
  moved: string[];
  changed: string[];
  textSemanticChangeRatio: number;
}

export interface WechatArticlePrompt {
  prompt: string;
}

export interface WechatArticleImageGenerationResult {
  requestedSlotIds: number[];
  materialIds: number[];
  failedSlotIds: number[];
}

export interface SelectWechatArticleImageMaterialInput {
  materialId: number;
  expectedLockVersion: number;
}

export interface UploadWechatArticleImageResult {
  materialId: number;
  status: string;
}

export interface WechatArticleReadinessIssue {
  code: string;
  message: string;
  claimId: string | null;
}

export interface WechatArticleDraftSyncContext {
  targetAccount: { id: number; name: string };
  articleTitle: string;
  articleVersionId: number;
  imageCount: number;
  readiness: {
    canSync: boolean;
    blockers: WechatArticleReadinessIssue[];
    warnings: WechatArticleReadinessIssue[];
    unresolvedClaimCount: number;
  };
  remote: {
    status: string;
    remoteHash: string | null;
    updatedAt: string;
    errorCode: string | null;
    operationType: string;
  } | null;
}

const REMOTE_DRAFT_SYNC_MANUAL_REVIEW_STATUSES = new Set([
  "wechat_conflict",
  "wechat_reconciliation_required",
]);

export function requiresWechatDraftSyncManualReview(
  context: Pick<WechatArticleDraftSyncContext, "remote"> | null | undefined,
): boolean {
  return context?.remote ? REMOTE_DRAFT_SYNC_MANUAL_REVIEW_STATUSES.has(context.remote.status) : false;
}

export function canConfirmWechatDraftSync(context: WechatArticleDraftSyncContext | null): boolean {
  return Boolean(context && context.readiness.canSync && !requiresWechatDraftSyncManualReview(context));
}

export interface CreateWechatDraftSyncInput {
  articleVersionId: number;
  idempotencyKey: string;
  expectedRemoteHash: string | null;
  conflictStrategy: "fail" | "create_new" | "overwrite_confirmed";
}

export interface WechatDraftSyncJob {
  id: number;
  accountId: number;
  articleId: number;
  articleVersionId: number;
  status: string;
  conflictStrategy: string;
  externalMediaId: string | null;
  expectedRemoteHash: string | null;
  observedRemoteHash: string | null;
  retryable: boolean;
  errorCode: string | null;
  createdAt: string;
  updatedAt: string;
}

export class WechatArticleVersionConflictError extends Error {
  currentLockVersion: number;

  constructor(currentLockVersion: number) {
    super("ARTICLE_VERSION_CONFLICT");
    this.name = "WechatArticleVersionConflictError";
    this.currentLockVersion = currentLockVersion;
  }
}

function parseBlock(value: unknown): WechatArticleBlock {
  const source = record(value);
  const block: Record<string, unknown> = {};
  for (const [key, entry] of Object.entries(source)) {
    if (key === "block_id") block.blockId = string(entry);
    else if (key === "slot_key") block.slotKey = string(entry);
    else block[key] = entry;
  }
  return {
    type: string(source.type),
    blockId: string(source.block_id),
    ...block,
  };
}

function parseClaim(value: unknown): WechatArticleClaim {
  const source = record(value);
  return {
    claimId: string(source.claim_id),
    blockId: string(source.block_id),
    kind: string(source.kind),
    text: string(source.text),
    citationIds: Array.isArray(source.citation_ids)
      ? source.citation_ids.filter((item): item is number => typeof item === "number")
      : [],
  };
}

function parseDocument(value: unknown): WechatArticleDocument {
  const source = record(value);
  return {
    title: string(source.title),
    digest: string(source.digest),
    author: nullableString(source.author),
    blocks: Array.isArray(source.blocks) ? source.blocks.map(parseBlock) : [],
    claims: Array.isArray(source.claims) ? source.claims.map(parseClaim) : [],
  };
}

function serializeBlock(block: WechatArticleBlock): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(block)) {
    if (key === "blockId") result.block_id = value;
    else if (key === "slotKey") result.slot_key = value;
    else result[key] = value;
  }
  return result;
}

function serializeClaim(claim: WechatArticleClaim): Record<string, unknown> {
  return {
    claim_id: claim.claimId,
    block_id: claim.blockId,
    kind: claim.kind,
    text: claim.text,
    citation_ids: claim.citationIds,
  };
}

function serializeDocument(document: WechatArticleDocument): Record<string, unknown> {
  return {
    title: document.title,
    digest: document.digest,
    author: document.author,
    blocks: document.blocks.map(serializeBlock),
    claims: document.claims.map(serializeClaim),
  };
}

function parseImageSlot(value: unknown): WechatArticleImageSlot {
  const source = record(value);
  return {
    id: number(source.id),
    stableKey: string(source.stableKey),
    purpose: string(source.purpose),
    aspectRatio: string(source.aspectRatio),
    visualBrief: string(source.visualBrief),
    status: string(source.status),
    selectedMaterialId: nullableNumber(source.selectedMaterialId),
    lockVersion: number(source.lockVersion),
    hasPrompt: source.hasPrompt === true,
  };
}

function parseWorkingCopy(value: unknown): WechatArticleWorkingCopy {
  const source = record(value);
  return {
    articleId: number(source.articleId),
    accountId: number(source.accountId),
    accountName: string(source.accountName),
    document: parseDocument(source.document),
    lockVersion: number(source.lockVersion),
    basedOnDeliverableId: nullableNumber(source.basedOnDeliverableId),
    imageSlots: Array.isArray(source.imageSlots) ? source.imageSlots.map(parseImageSlot) : [],
  };
}

function parseVersion(value: unknown): WechatArticleVersion {
  const source = record(value);
  return {
    id: number(source.id),
    articleId: number(source.articleId),
    version: number(source.version),
    trigger: string(source.trigger),
    document: parseDocument(source.document),
  };
}

function parseDraftSyncJob(value: unknown): WechatDraftSyncJob {
  const source = record(value);
  return {
    id: number(source.id),
    accountId: number(source.account_id),
    articleId: number(source.article_id),
    articleVersionId: number(source.article_version_id),
    status: string(source.status),
    conflictStrategy: string(source.conflict_strategy),
    externalMediaId: nullableString(source.external_media_id),
    expectedRemoteHash: nullableString(source.expected_remote_hash),
    observedRemoteHash: nullableString(source.observed_remote_hash),
    retryable: source.retryable === true,
    errorCode: nullableString(source.error_code),
    createdAt: string(source.created_at),
    updatedAt: string(source.updated_at),
  };
}

function parseReadinessIssue(value: unknown): WechatArticleReadinessIssue {
  const source = record(value);
  return {
    code: string(source.code),
    message: string(source.message),
    claimId: nullableString(source.claimId),
  };
}

function parseDraftSyncContext(value: unknown): WechatArticleDraftSyncContext {
  const source = record(value);
  const readiness = record(source.readiness);
  const remoteSource = source.remote == null ? null : record(source.remote);
  return {
    targetAccount: {
      id: number(record(source.targetAccount).id),
      name: string(record(source.targetAccount).name),
    },
    articleTitle: string(source.articleTitle),
    articleVersionId: number(source.articleVersionId),
    imageCount: number(source.imageCount),
    readiness: {
      canSync: readiness.canSync === true,
      blockers: Array.isArray(readiness.blockers) ? readiness.blockers.map(parseReadinessIssue) : [],
      warnings: Array.isArray(readiness.warnings) ? readiness.warnings.map(parseReadinessIssue) : [],
      unresolvedClaimCount: number(readiness.unresolvedClaimCount),
    },
    remote: remoteSource == null ? null : {
      status: string(remoteSource.status),
      remoteHash: nullableString(remoteSource.remoteHash),
      updatedAt: string(remoteSource.updatedAt),
      errorCode: nullableString(remoteSource.errorCode),
      operationType: string(remoteSource.operationType),
    },
  };
}

function toVersionConflict(error: unknown): WechatArticleVersionConflictError | null {
  const response = record(record(error).response);
  if (number(response.status) !== 409) return null;
  const body = record(response.data);
  const payload = record(body.error);
  if (string(payload.code) !== "ARTICLE_VERSION_CONFLICT") return null;
  const details = record(payload.details);
  return new WechatArticleVersionConflictError(number(details.currentLockVersion));
}

export async function getWechatArticleWorkingCopy(articleId: number): Promise<WechatArticleWorkingCopy> {
  const { data } = await api.get(`/wechat-articles/${articleId}/working-copy`);
  return parseWorkingCopy(data);
}

export async function saveWechatArticleWorkingCopy(
  articleId: number,
  input: SaveWechatArticleWorkingCopyInput,
): Promise<WechatArticleWorkingCopy> {
  try {
    const { data } = await api.patch(`/wechat-articles/${articleId}/working-copy`, {
      expected_lock_version: input.expectedLockVersion,
      document: serializeDocument(input.document),
    });
    return parseWorkingCopy(data);
  } catch (error) {
    const conflict = toVersionConflict(error);
    if (conflict) throw conflict;
    throw error;
  }
}

export async function getWechatArticlePreview(articleId: number): Promise<WechatArticlePreview> {
  const { data } = await api.get(`/wechat-articles/${articleId}/preview`);
  const source = record(data);
  return {
    articleId: number(source.articleId),
    document: parseDocument(source.document),
    renderedHtml: nullableString(source.renderedHtml),
  };
}

export async function listWechatArticleVersions(articleId: number): Promise<WechatArticleVersion[]> {
  const { data } = await api.get(`/wechat-articles/${articleId}/versions`);
  return Array.isArray(data) ? data.map(parseVersion) : [];
}

export async function getWechatArticleVersionDiff(
  articleId: number,
  targetVersion: number,
  baseVersion: number,
): Promise<WechatArticleVersionDiff> {
  const { data } = await api.get(
    `/wechat-articles/${articleId}/versions/${targetVersion}/diff`,
    { params: { base_version: baseVersion } },
  );
  const source = record(data);
  return {
    baseVersion: number(source.baseVersion),
    targetVersion: number(source.targetVersion),
    added: Array.isArray(source.added) ? source.added.map(string) : [],
    removed: Array.isArray(source.removed) ? source.removed.map(string) : [],
    moved: Array.isArray(source.moved) ? source.moved.map(string) : [],
    changed: Array.isArray(source.changed) ? source.changed.map(string) : [],
    textSemanticChangeRatio: typeof source.textSemanticChangeRatio === "number"
      ? source.textSemanticChangeRatio
      : 0,
  };
}

export async function getWechatArticleImagePrompt(
  articleId: number,
  slotId: number,
): Promise<WechatArticlePrompt> {
  const { data } = await api.get(`/wechat-articles/${articleId}/image-slots/${slotId}/prompt`);
  return { prompt: string(record(data).prompt) };
}

export async function generateAllWechatArticleImages(
  articleId: number,
  idempotencyKey: string,
  referenceMaterialIds: number[] = [],
): Promise<WechatArticleImageGenerationResult> {
  const { data } = await api.post(`/wechat-articles/${articleId}/image-generations`, {
    idempotency_key: idempotencyKey,
    reference_material_ids: referenceMaterialIds,
  });
  const source = record(data);
  return {
    requestedSlotIds: Array.isArray(source.requestedSlotIds) ? source.requestedSlotIds.map(number) : [],
    materialIds: Array.isArray(source.materialIds) ? source.materialIds.map(number) : [],
    failedSlotIds: Array.isArray(source.failedSlotIds) ? source.failedSlotIds.map(number) : [],
  };
}

export async function generateWechatArticleImage(
  articleId: number,
  slotId: number,
  idempotencyKey: string,
  referenceMaterialIds: number[] = [],
): Promise<WechatArticleImageGenerationResult> {
  const { data } = await api.post(`/wechat-articles/${articleId}/image-slots/${slotId}/generations`, {
    idempotency_key: idempotencyKey,
    reference_material_ids: referenceMaterialIds,
  });
  const source = record(data);
  return {
    requestedSlotIds: Array.isArray(source.requestedSlotIds) ? source.requestedSlotIds.map(number) : [],
    materialIds: Array.isArray(source.materialIds) ? source.materialIds.map(number) : [],
    failedSlotIds: Array.isArray(source.failedSlotIds) ? source.failedSlotIds.map(number) : [],
  };
}

export async function uploadWechatArticleImage(
  articleId: number,
  slotId: number,
  file: File,
): Promise<UploadWechatArticleImageResult> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await api.post(`/wechat-articles/${articleId}/image-slots/${slotId}/uploads`, formData);
  const source = record(data);
  return {
    materialId: number(source.materialId),
    status: string(source.status),
  };
}

export async function selectWechatArticleImageMaterial(
  articleId: number,
  slotId: number,
  input: SelectWechatArticleImageMaterialInput,
): Promise<WechatArticleImageSlot> {
  const { data } = await api.put(`/wechat-articles/${articleId}/image-slots/${slotId}/selection`, {
    material_id: input.materialId,
    expected_lock_version: input.expectedLockVersion,
  });
  return parseImageSlot(data);
}

export async function createWechatArticleVersion(articleId: number): Promise<WechatArticleVersion> {
  const { data } = await api.post(`/wechat-articles/${articleId}/versions`);
  return parseVersion(data);
}

export async function getWechatArticleDraftSyncContext(
  articleId: number,
  articleVersionId: number,
): Promise<WechatArticleDraftSyncContext> {
  const { data } = await api.get(`/wechat-articles/${articleId}/draft-sync-context`, {
    params: { article_version_id: articleVersionId },
  });
  return parseDraftSyncContext(data);
}

export async function createWechatDraftSync(
  articleId: number,
  input: CreateWechatDraftSyncInput,
): Promise<WechatDraftSyncJob> {
  const { data } = await api.post(`/wechat-articles/${articleId}/draft-syncs`, {
    article_version_id: input.articleVersionId,
    idempotency_key: input.idempotencyKey,
    expected_remote_hash: input.expectedRemoteHash,
    conflict_strategy: input.conflictStrategy,
  });
  return parseDraftSyncJob(data);
}

export async function getWechatDraftSync(syncId: number): Promise<WechatDraftSyncJob> {
  const { data } = await api.get(`/wechat-draft-syncs/${syncId}`);
  return parseDraftSyncJob(data);
}
