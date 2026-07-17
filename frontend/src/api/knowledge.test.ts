import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  approveKnowledgeSuggestion,
  createKnowledge,
  listKnowledge,
  listKnowledgeCitations,
  listKnowledgeSuggestions,
} from "./knowledge";
import { api } from "./client";

vi.mock("./client", () => ({ api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }));

const get = api.get as unknown as Mock;
const post = api.post as unknown as Mock;

describe("knowledge api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("always lists knowledge inside an explicit client and project scope", async () => {
    get.mockResolvedValueOnce({ data: [] });
    get.mockResolvedValueOnce({ data: [] });
    get.mockResolvedValueOnce({ data: [] });

    await listKnowledge(3, 7, "user_persona");
    await listKnowledgeSuggestions(3, 7);
    await listKnowledgeCitations(11, 3, 7);

    expect(get).toHaveBeenNthCalledWith(1, "/knowledge", {
      params: { client_id: 3, project_id: 7, category: "user_persona" },
    });
    expect(get).toHaveBeenNthCalledWith(2, "/knowledge-suggestions", {
      params: { client_id: 3, project_id: 7, status: "pending" },
    });
    expect(get).toHaveBeenNthCalledWith(3, "/knowledge/11/citations", {
      params: { client_id: 3, project_id: 7 },
    });
  });

  it("creates reviewed manual knowledge and approves agent suggestions separately", async () => {
    post.mockResolvedValueOnce({ data: { id: 8 } });
    post.mockResolvedValueOnce({ data: { suggestion: { id: 9 }, entry: { id: 10 } } });

    await createKnowledge({
      client_id: 3,
      project_id: 7,
      category: "hot_content",
      title: "真实实测结构",
      content: "先冲突，再证据。",
      source_label: "运营复盘",
    });
    await approveKnowledgeSuggestion(9, "确认可复用");

    expect(post).toHaveBeenNthCalledWith(1, "/knowledge", expect.objectContaining({
      client_id: 3,
      project_id: 7,
      source_label: "运营复盘",
    }));
    expect(post).toHaveBeenNthCalledWith(2, "/knowledge-suggestions/9/approve", {
      review_note: "确认可复用",
    });
  });
});
