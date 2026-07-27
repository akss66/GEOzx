import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import {
  getSecondaryPasswordStatus,
  getUserAccessCatalog,
  getUserDetail,
  permanentlyDeleteUser,
  previewUserDeletion,
  resetUserPassword,
  setSecondaryPassword,
  updateUser,
  updateUserAccess,
} from "./auth";
import type {
  PermanentDeleteUserInput,
  PermanentDeleteUserResponse,
  SecondaryPasswordStatus,
  SetSecondaryPasswordInput,
  UserAccessCatalog,
  UserDeletionPreview,
  UserDetail,
} from "../types";

vi.mock("./client", () => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), put: vi.fn(), post: vi.fn() },
}));

const apiDelete = api.delete as unknown as Mock;
const apiGet = api.get as unknown as Mock;
const apiPatch = api.patch as unknown as Mock;
const apiPost = api.post as unknown as Mock;
const apiPut = api.put as unknown as Mock;

describe("user management api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("loads user detail and the access catalog", async () => {
    apiGet.mockResolvedValue({ data: {} });

    await getUserDetail(7);
    await getUserAccessCatalog();

    expect(apiGet).toHaveBeenNthCalledWith(1, "/users/7");
    expect(apiGet).toHaveBeenNthCalledWith(2, "/users/access-catalog");
  });

  it("preserves an unavailable account catalog when legacy responses omit accounts", async () => {
    apiGet.mockResolvedValue({
      data: {
        clients: [{ id: 3, name: "Legacy client", status: "active" }],
        projects: [{ id: 8, client_id: 3, name: "Legacy project", status: "active" }],
      },
    });

    await expect(getUserAccessCatalog()).resolves.toEqual({
      clients: [{ id: 3, name: "Legacy client", status: "active" }],
      projects: [{ id: 8, client_id: 3, name: "Legacy project", status: "active" }],
      account_catalog_status: "unavailable",
    });
  });

  it("preserves present legacy access IDs and marks incomplete member detail unavailable", async () => {
    apiGet.mockResolvedValue({
      data: {
        id: 7,
        email: "legacy@test.com",
        display_name: "Legacy member",
        role: "user",
        is_active: true,
        has_global_access: false,
        account_scope_mode: "selected",
        client_ids: [3],
        project_ids: [8],
      },
    });

    await expect(getUserDetail(7)).resolves.toEqual({
      id: 7,
      email: "legacy@test.com",
      display_name: "Legacy member",
      role: "user",
      is_active: true,
      has_global_access: false,
      account_scope_mode: "selected",
      access_detail_status: "unavailable",
      client_ids: [3],
      project_ids: [8],
      account_ids: [],
      client_memberships: [],
      project_memberships: [],
    });
  });

  it("updates user identity and replaces workspace access", async () => {
    apiPatch.mockResolvedValue({ data: {} });
    apiPut.mockResolvedValue({ data: {} });
    const access = {
      clients: [{ client_id: 3, role: "operator" as const }],
      projects: [{ project_id: 8, role: "reviewer" as const }],
    };

    await updateUser(7, { display_name: "运营同事", is_active: false });
    await updateUserAccess(7, access);

    expect(apiPatch).toHaveBeenCalledWith("/users/7", {
      display_name: "运营同事",
      is_active: false,
    });
    expect(apiPut).toHaveBeenCalledWith("/users/7/access", access);
  });

  it("uses the backend account-scope fields in detail, catalog, and access input", async () => {
    const detail: UserDetail = {
      id: 7,
      email: "member@test.com",
      display_name: "Member",
      role: "user",
      is_active: true,
      has_global_access: false,
      account_scope_mode: "selected",
      access_detail_status: "available",
      client_ids: [],
      project_ids: [],
      account_ids: [31],
      client_memberships: [],
      project_memberships: [],
    };
    const catalog: UserAccessCatalog = {
      account_catalog_status: "available",
      clients: [],
      projects: [],
      accounts: [
        {
          id: 31,
          client_id: 3,
          client_ids: [3],
          project_ids: [8],
          nickname: "Main account",
          platform: "douyin",
          status: "active",
        },
      ],
    };
    const access = {
      clients: [{ client_id: 3, role: "operator" as const }],
      projects: [{ project_id: 8, role: "reviewer" as const }],
      account_scope_mode: "selected" as const,
      account_ids: [31],
    };
    apiGet.mockResolvedValueOnce({ data: detail }).mockResolvedValueOnce({ data: catalog });
    apiPut.mockResolvedValue({ data: detail });

    await expect(getUserDetail(7)).resolves.toEqual(detail);
    await expect(getUserAccessCatalog()).resolves.toEqual(catalog);
    await expect(updateUserAccess(7, access)).resolves.toEqual(detail);

    expect(apiPut).toHaveBeenCalledWith("/users/7/access", access);
  });

  it("sets a secondary password with credentials only in the body", async () => {
    const input: SetSecondaryPasswordInput = {
      current_password: "current-password-123",
      secondary_password: "secondary-password-123",
    };
    const status: SecondaryPasswordStatus = {
      configured: true,
      deletion_available: false,
      delete_available_at: "2026-07-20T02:10:00Z",
      locked_until: null,
    };
    apiPut.mockResolvedValue({ data: status });

    await expect(setSecondaryPassword(input)).resolves.toEqual(status);

    expect(apiPut).toHaveBeenCalledWith("/users/me/secondary-password", input);
  });

  it("reads secondary-password status without credentials or request parameters", async () => {
    const status: SecondaryPasswordStatus = {
      configured: true,
      deletion_available: true,
      delete_available_at: "2026-07-20T02:10:00Z",
      locked_until: null,
    };
    apiGet.mockResolvedValue({ data: status });

    await expect(getSecondaryPasswordStatus()).resolves.toEqual(status);

    expect(apiGet).toHaveBeenCalledWith("/users/me/secondary-password/status");
  });

  it("resets a member password using a request body and returns no content", async () => {
    apiPost.mockResolvedValue({ data: undefined });

    await expect(resetUserPassword(7, { new_password: "replacement-password-123" })).resolves.toBeUndefined();

    expect(apiPost).toHaveBeenCalledWith("/users/7/reset-password", {
      new_password: "replacement-password-123",
    });
  });

  it("returns the deletion preview from the body-less preview endpoint", async () => {
    const preview: UserDeletionPreview = {
      target_user_id: 7,
      target_email: "member@test.com",
      counts: { tasks: 2, events: 4 },
      preview_token: "preview-token-with-sufficient-length",
      expires_at: "2026-07-20T02:05:00Z",
      allowed: true,
      blockers: [],
    };
    apiPost.mockResolvedValue({ data: preview });

    await expect(previewUserDeletion(7)).resolves.toEqual(preview);

    expect(apiPost).toHaveBeenCalledWith("/users/7/deletion-preview");
  });

  it("never places destructive credentials in a query string", async () => {
    const input: PermanentDeleteUserInput = {
      preview_token: "preview-token-with-sufficient-length",
      secondary_password: "secondary-password-123",
    };
    const response: PermanentDeleteUserResponse = {
      operation_id: "delete-operation-id",
      deleted_at: "2026-07-20T02:01:00Z",
      counts: { users: 1, tasks: 2 },
    };
    apiDelete.mockResolvedValue({ data: response });

    await expect(permanentlyDeleteUser(7, input)).resolves.toEqual(response);

    expect(apiDelete).toHaveBeenCalledWith("/users/7/permanent", { data: input });
  });
});
