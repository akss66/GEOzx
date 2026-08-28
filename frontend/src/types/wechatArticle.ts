export const WECHAT_CAPABILITY_KEYS = [
  "uploadArticleImage",
  "addPermanentMaterial",
  "draftAdd",
  "draftGet",
  "draftUpdate",
  "analytics",
  "freepublish",
] as const;

export type WechatCapabilityKey = (typeof WECHAT_CAPABILITY_KEYS)[number];

export interface WechatCapabilityState {
  canUse: boolean;
  reason: string | null;
  permissionIds: number[];
}

export type WechatCapabilitySnapshot = {
  accountId: number;
  checkedAt: string;
} & Record<WechatCapabilityKey, WechatCapabilityState>;

export interface WechatAuthorizationSession {
  authorizationUrl: string;
  expiresAt: string;
  stateId: string;
}

export interface WechatKnowledgeBase {
  id: number;
  clientId: number | null;
  kind: "brand" | "organization_shared";
  name: string;
  description: string | null;
  status: string;
  version: number;
}

export interface WechatKnowledgeBinding {
  id: number;
  accountId: number;
  knowledgeBaseId: number;
  knowledgeBaseKind: string;
  clientId: number | null;
  bindingType: string;
  status: string;
  boundAt: string;
}
