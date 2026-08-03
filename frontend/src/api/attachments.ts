import { api } from "./client";
import type { ConversationAttachment } from "../types";

export async function uploadConversationAttachments(
  threadId: number,
  files: File[],
): Promise<ConversationAttachment[]> {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  const { data } = await api.post<ConversationAttachment[]>(
    `/brain/conversations/${threadId}/attachments`,
    body,
  );
  return data;
}

export async function deleteConversationAttachment(
  threadId: number,
  attachmentId: number,
): Promise<void> {
  await api.delete(
    `/brain/conversations/${threadId}/attachments/${attachmentId}`,
  );
}
