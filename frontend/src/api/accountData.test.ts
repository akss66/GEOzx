// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import {
  commitAccountDataImportBatch,
  downloadAccountDataArtifact,
  getAccountDataStatus,
  listAccountDataImports,
  resolveAccountDataImportRow,
  uploadAccountDataImport,
  revokeAccountDataImportBatch,
} from "./accountData";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;
const apiPatch = api.patch as unknown as Mock;

describe("account data api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal(
      "URL",
      Object.assign(URL, {
        createObjectURL: vi.fn(() => "blob:account-data"),
        revokeObjectURL: vi.fn(),
      }),
    );
  });

  it("loads source coverage for one explicit account", async () => {
    apiGet.mockResolvedValueOnce({
      data: {
        account_id: 9,
        latest_confirmed_at: "2026-07-22T08:10:00Z",
        coverage: {
          account_metrics: "available",
          content_metrics: "partial",
          benchmarks: "missing",
        },
        sources: [],
      },
    });

    await getAccountDataStatus(9);

    expect(apiGet).toHaveBeenCalledWith("/account-data/9/status");
  });

  it("uploads one import file through multipart form data", async () => {
    apiPost.mockResolvedValueOnce({ data: { id: 12, status: "preview_ready" } });
    const file = new File(["play"], "works.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });

    await uploadAccountDataImport(9, file);

    expect(apiPost).toHaveBeenCalledWith(
      "/account-data/9/imports",
      expect.any(FormData),
      expect.objectContaining({
        headers: { "Content-Type": "multipart/form-data" },
      }),
    );
  });

  it("lists import history within the selected account scope", async () => {
    apiGet.mockResolvedValueOnce({ data: { items: [{ id: 15, status: "committed" }] } });

    await listAccountDataImports(9);

    expect(apiGet).toHaveBeenCalledWith("/account-data/9/imports");
  });

  it("resolves one ambiguous row against an existing content record", async () => {
    apiPatch.mockResolvedValueOnce({ data: { row_number: 2, status: "ready" } });

    await resolveAccountDataImportRow(9, 12, 2, 91);

    expect(apiPatch).toHaveBeenCalledWith("/account-data/9/imports/12/rows/2", {
      selected_content_id: 91,
    });
  });

  it("commits and revokes the same scoped batch through dedicated endpoints", async () => {
    apiPost.mockResolvedValue({ data: { id: 12, status: "committed" } });

    await commitAccountDataImportBatch(9, 12);
    await revokeAccountDataImportBatch(9, 12);

    expect(apiPost).toHaveBeenNthCalledWith(1, "/account-data/9/imports/12/commit");
    expect(apiPost).toHaveBeenNthCalledWith(2, "/account-data/9/imports/12/revoke");
  });

  it("downloads one import artifact through the authenticated api blob path", async () => {
    apiGet.mockResolvedValueOnce({ data: new Blob(["file"], { type: "text/csv" }) });
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);

    await downloadAccountDataArtifact({
      id: 501,
      filename: "works.csv",
      content_type: "text/csv",
      byte_size: 2048,
      sha256: "a".repeat(64),
      download_url: "/account-data/9/imports/12/artifacts/501",
    });

    expect(apiGet).toHaveBeenCalledWith("/account-data/9/imports/12/artifacts/501", {
      responseType: "blob",
    });
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:account-data");
  });
});
