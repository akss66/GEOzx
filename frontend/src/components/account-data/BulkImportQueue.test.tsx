// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createAccountDataImportJob,
  getAccountDataImportJob,
  listAccountDataImportJobs,
  retryAccountDataImportFile,
} from "../../api/accountData";
import { BulkImportQueue } from "./BulkImportQueue";

vi.mock("../../api/accountData", async () => {
  const actual = await vi.importActual("../../api/accountData");
  return {
    ...actual,
    createAccountDataImportJob: vi.fn(),
    getAccountDataImportJob: vi.fn(),
    listAccountDataImportJobs: vi.fn(),
    retryAccountDataImportFile: vi.fn(),
  };
});

const createJob = vi.mocked(createAccountDataImportJob);
const getJob = vi.mocked(getAccountDataImportJob);
const listJobs = vi.mocked(listAccountDataImportJobs);
const retryFile = vi.mocked(retryAccountDataImportFile);

function jobFixture() {
  return {
    id: 41,
    account_id: 7,
    client_request_id: "request-41",
    status: "completed_with_errors" as const,
    file_count: 2,
    completed_file_count: 1,
    failed_file_count: 1,
    started_at: "2026-07-31T10:00:00Z",
    completed_at: "2026-07-31T10:00:02Z",
    files: [
      {
        id: 101,
        retry_of_file_id: null,
        ordinal: 1,
        filename: "作品数据.xlsx",
        content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size: 10,
        sha256: "a".repeat(64),
        status: "completed" as const,
        error_payload: {},
        started_at: null,
        completed_at: null,
        datasets: [
          {
            id: 201,
            template_code: "douyin_work_list_v1",
            sheet_name: "作品列表",
            dataset_ordinal: 1,
            status: "committed" as const,
            row_count: 30,
          },
        ],
      },
      {
        id: 102,
        retry_of_file_id: null,
        ordinal: 2,
        filename: "损坏数据.xlsx",
        content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size: 10,
        sha256: "b".repeat(64),
        status: "failed" as const,
        error_payload: { message: "文件结构损坏" },
        started_at: null,
        completed_at: null,
        datasets: [],
      },
    ],
  };
}

describe("BulkImportQueue", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    listJobs.mockResolvedValue([]);
  });

  it("restores recent import jobs after the page is reopened", async () => {
    listJobs.mockResolvedValue([jobFixture()]);

    render(<BulkImportQueue accountId={7} onTerminal={vi.fn()} />);

    expect(await screen.findByText("损坏数据.xlsx")).toBeInTheDocument();
    expect(listJobs).toHaveBeenCalledWith(7);
    expect(screen.getByLabelText("重新上传 损坏数据.xlsx")).toBeInTheDocument();
  });

  it("submits every selected file in one request", async () => {
    createJob.mockResolvedValue(jobFixture());
    render(<BulkImportQueue accountId={7} onTerminal={vi.fn()} />);
    const input = screen.getByTestId("account-data-file-input");
    const files = [
      new File(["a"], "作品数据.xlsx"),
      new File(["b"], "粉丝数据.xlsx"),
    ];

    fireEvent.change(input, { target: { files } });

    await waitFor(() => {
      expect(createJob).toHaveBeenCalledWith(
        7,
        files,
        expect.any(String),
      );
    });
    expect(await screen.findByText("作品数据.xlsx")).toBeInTheDocument();
    expect(screen.getByText("损坏数据.xlsx")).toBeInTheDocument();
  });

  it("accepts dropped files and keeps successful files when one fails", async () => {
    createJob.mockResolvedValue(jobFixture());
    render(<BulkImportQueue accountId={7} onTerminal={vi.fn()} />);
    const dropzone = screen.getByRole("group", { name: "拖入账号数据文件" });
    expect(screen.getByRole("button", { name: "选择账号数据文件" })).toBeInTheDocument();
    const files = [
      new File(["a"], "作品数据.xlsx"),
      new File(["b"], "损坏数据.xlsx"),
    ];

    fireEvent.drop(dropzone, { dataTransfer: { files } });

    expect(await screen.findByText("已写入")).toBeInTheDocument();
    expect(screen.getByText("导入失败")).toBeInTheDocument();
    expect(screen.getByText("作品列表 · 30 行")).toBeInTheDocument();
    expect(screen.getByLabelText("重新上传 损坏数据.xlsx")).toBeInTheDocument();
  });

  it("reuploads only the selected failed file", async () => {
    createJob.mockResolvedValue(jobFixture());
    retryFile.mockResolvedValue({
      ...jobFixture(),
      status: "processing",
      files: [
        ...jobFixture().files,
        {
          ...jobFixture().files[1],
          id: 103,
          retry_of_file_id: 102,
          ordinal: 3,
          status: "queued",
        },
      ],
    });
    getJob.mockResolvedValue(jobFixture());
    render(<BulkImportQueue accountId={7} onTerminal={vi.fn()} />);
    fireEvent.change(screen.getByTestId("account-data-file-input"), {
      target: { files: [new File(["b"], "损坏数据.xlsx")] },
    });
    const replacement = new File(["fixed"], "修正数据.xlsx");
    fireEvent.change(
      await screen.findByLabelText("重新上传 损坏数据.xlsx"),
      { target: { files: [replacement] } },
    );

    await waitFor(() => {
      expect(retryFile).toHaveBeenCalledWith(7, 41, 102, replacement);
    });
  });

  it("keeps the existing queue when more files are added", async () => {
    createJob
      .mockResolvedValueOnce(jobFixture())
      .mockResolvedValueOnce({
        ...jobFixture(),
        id: 42,
        client_request_id: "request-42",
        files: [{
          ...jobFixture().files[0],
          id: 104,
          filename: "粉丝数据.xlsx",
          datasets: [],
        }],
      });
    render(<BulkImportQueue accountId={7} onTerminal={vi.fn()} />);
    const input = screen.getByTestId("account-data-file-input");

    fireEvent.change(input, {
      target: { files: [new File(["a"], "作品数据.xlsx")] },
    });
    expect(await screen.findByText("作品数据.xlsx")).toBeInTheDocument();
    fireEvent.change(input, {
      target: { files: [new File(["b"], "粉丝数据.xlsx")] },
    });

    expect(await screen.findByText("粉丝数据.xlsx")).toBeInTheDocument();
    expect(screen.getByText("作品数据.xlsx")).toBeInTheDocument();
  });
});
