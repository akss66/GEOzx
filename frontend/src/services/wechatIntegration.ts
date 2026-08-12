import { api } from "../api/client";
import type {
  WechatAuthorizationSession,
  WechatCapabilitySnapshot,
  WechatCapabilityState,
  WechatKnowledgeBase,
  WechatKnowledgeBinding,
} from "../types/wechatArticle";

type JsonRecord = Record<string, unknown>;
const MAX_KNOWLEDGE_BASE_PAGES = 100;

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

function capability(value: unknown): WechatCapabilityState {
  const source = record(value);
  return {
    canUse: source.can_use === true,
    reason: nullableString(source.reason),
    permissionIds: Array.isArray(source.permission_ids)
      ? source.permission_ids.filter((item): item is number => typeof item === "number")
      : [],
  };
}

export function isOfficialWechatAuthorizationUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && url.hostname === "mp.weixin.qq.com"
      && url.pathname === "/cgi-bin/componentloginpage";
  } catch {
    return false;
  }
}

export async function createWechatAuthorizationSession(input: {
  clientId?: number;
  projectId?: number;
  knowledgeBaseId?: number;
} = {}): Promise<WechatAuthorizationSession> {
  const body: JsonRecord = {};
  if (input.clientId !== undefined) body.client_id = input.clientId;
  if (input.projectId !== undefined) body.project_id = input.projectId;
  if (input.knowledgeBaseId !== undefined) body.knowledge_base_id = input.knowledgeBaseId;
  const { data } = await api.post("/platform-integrations/wechat/authorization-sessions", body);
  const source = record(data);
  return {
    authorizationUrl: string(source.authorization_url),
    expiresAt: string(source.expires_at),
    stateId: string(source.state_id),
  };
}

export async function getWechatAccountCapabilities(
  accountId: number,
): Promise<WechatCapabilitySnapshot> {
  const { data } = await api.get(`/accounts/${accountId}/platform-capabilities`);
  const source = record(data);
  return {
    accountId: number(source.account_id),
    uploadArticleImage: capability(source.upload_article_image),
    addPermanentMaterial: capability(source.add_permanent_material),
    draftAdd: capability(source.draft_add),
    draftGet: capability(source.draft_get),
    draftUpdate: capability(source.draft_update),
    analytics: capability(source.analytics),
    freepublish: capability(source.freepublish),
    checkedAt: string(source.checked_at),
  };
}

function parseKnowledgeBase(value: unknown): WechatKnowledgeBase {
  const source = record(value);
  return {
    id: number(source.id),
    clientId: nullableNumber(source.client_id),
    kind: source.kind === "organization_shared" ? "organization_shared" : "brand",
    name: string(source.name),
    description: nullableString(source.description),
    status: string(source.status),
    version: number(source.version),
  };
}

export async function listWechatKnowledgeBases(input: { limit?: number; offset?: number } = {}) {
  const limit = Math.min(Math.max(Math.trunc(input.limit ?? 100), 1), 100);
  const initialOffset = Math.max(Math.trunc(input.offset ?? 0), 0);
  const bases: WechatKnowledgeBase[] = [];
  let offset = initialOffset;
  let total = 0;

  for (let page = 0; page < MAX_KNOWLEDGE_BASE_PAGES; page += 1) {
    const { data } = await api.get("/knowledge-bases", { params: { limit, offset } });
    const source = record(data);
    const pagination = record(source.pagination);
    const pageData = Array.isArray(source.data) ? source.data.map(parseKnowledgeBase) : [];
    total = number(pagination.total);
    bases.push(...pageData);

    if (offset + pageData.length >= total) break;
    const nextOffset = number(pagination.offset) + pageData.length;
    if (pageData.length === 0 || nextOffset <= offset) {
      throw new Error("Knowledge-base pagination made no progress");
    }
    if (page === MAX_KNOWLEDGE_BASE_PAGES - 1) {
      throw new Error("Knowledge-base pagination exceeded the safe page limit");
    }
    offset = nextOffset;
  }

  return {
    data: bases,
    pagination: {
      limit,
      offset: initialOffset,
      total,
    },
  };
}

function parseBinding(value: unknown): WechatKnowledgeBinding {
  const source = record(value);
  return {
    id: number(source.id),
    accountId: number(source.account_id),
    knowledgeBaseId: number(source.knowledge_base_id),
    knowledgeBaseKind: string(source.knowledge_base_kind),
    clientId: nullableNumber(source.client_id),
    bindingType: string(source.binding_type),
    status: string(source.status),
    boundAt: string(source.bound_at),
  };
}

export async function getWechatKnowledgeBinding(accountId: number): Promise<WechatKnowledgeBinding | null> {
  try {
    const { data } = await api.get(`/accounts/${accountId}/knowledge-binding`);
    return parseBinding(data);
  } catch (error) {
    if ((error as { response?: { status?: number } })?.response?.status === 404) return null;
    throw error;
  }
}

export async function bindWechatKnowledgeBase(accountId: number, knowledgeBaseId: number) {
  const { data } = await api.put(`/accounts/${accountId}/knowledge-binding`, {
    knowledge_base_id: knowledgeBaseId,
  });
  return parseBinding(data);
}

export async function unbindWechatKnowledgeBase(accountId: number): Promise<void> {
  await api.delete(`/accounts/${accountId}/knowledge-binding`);
}
