import { api } from "./client";
import type { MaterialAsset } from "../types";

export async function listMaterials(params?: {
  contentItemId?: number;
}): Promise<MaterialAsset[]> {
  const { data } = await api.get<MaterialAsset[]>("/materials", {
    params:
      params?.contentItemId != null
        ? { content_item_id: params.contentItemId }
        : undefined,
  });
  return data;
}
