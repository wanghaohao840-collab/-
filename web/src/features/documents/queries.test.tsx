import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/client";
import {
  cancelImportTask,
  deleteDocument,
  listDocuments,
  listImports,
  retryFailedImports,
  retryImportTask,
  submitImports,
} from "./api";
import {
  DOCUMENTS_QUERY_KEY,
  hasActiveImports,
  IMPORTS_QUERY_KEY,
  useDocumentMutations,
  useImportsQuery,
} from "./queries";
import type { ImportBatch, ImportStatus } from "./types";

const requestMock = vi.hoisted(() => vi.fn());

vi.mock("../../auth/AuthProvider", () => ({
  useAuth: () => ({ request: requestMock }),
}));

function batch(status: ImportStatus, progress = 0): ImportBatch {
  return {
    batch_id: "batch-1",
    created_at: "2026-08-22T10:00:00+00:00",
    updated_at: "2026-08-22T10:00:00+00:00",
    counts: {
      total: 1,
      queued: status === "queued" ? 1 : 0,
      running: status === "running" ? 1 : 0,
      retry_wait: status === "retry_wait" ? 1 : 0,
      succeeded: status === "succeeded" ? 1 : 0,
      failed: status === "failed" ? 1 : 0,
      cancelled: status === "cancelled" ? 1 : 0,
    },
    tasks: [
      {
        task_id: "task-1",
        document_id: "document-1",
        original_name: "notes.md",
        file_suffix: ".md",
        size_bytes: 12,
        status,
        stage:
          status === "running"
            ? "embedding"
            : status === "retry_wait"
              ? "queued"
              : status,
        progress,
        error_code: status === "failed" ? "import_stage_failed" : null,
        error_summary: status === "failed" ? "索引失败，请重试" : null,
        cancel_requested_at: null,
        created_at: "2026-08-22T10:00:00+00:00",
        started_at: null,
        finished_at: null,
        updated_at: "2026-08-22T10:00:00+00:00",
      },
    ],
  };
}

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children, client }: PropsWithChildren<{ client: QueryClient }>) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function ImportsHarness() {
  const query = useImportsQuery();
  return <output>{query.data?.[0]?.tasks[0]?.status ?? "loading"}</output>;
}

function MutationHarness() {
  const mutations = useDocumentMutations();
  return (
    <>
      <button
        type="button"
        onClick={() => mutations.retryTask.mutate({ batchId: "batch-1", taskId: "task-1" })}
      >
        重试
      </button>
      <button
        type="button"
        onClick={() => mutations.removeDocument.mutate({ documentId: "document-1" })}
      >
        删除
      </button>
    </>
  );
}

describe("document query contracts", () => {
  afterEach(() => {
    vi.useRealTimers();
    requestMock.mockReset();
  });

  it("fixes exact query keys and active-only status semantics", () => {
    expect(DOCUMENTS_QUERY_KEY).toEqual(["documents"]);
    expect(IMPORTS_QUERY_KEY).toEqual(["imports", { limit: 20 }]);
    expect(hasActiveImports([batch("queued")])).toBe(true);
    expect(hasActiveImports([batch("running")])).toBe(true);
    expect(hasActiveImports([batch("retry_wait")])).toBe(true);
    expect(hasActiveImports([batch("succeeded")])).toBe(false);
    expect(hasActiveImports([batch("failed")])).toBe(false);
    expect(hasActiveImports([batch("cancelled")])).toBe(false);
  });

  it("uses the exact authenticated API routes and leaves multipart headers unset", async () => {
    requestMock.mockResolvedValue(undefined);
    const file = new File(["hello"], "notes.md", { type: "text/markdown" });

    await listDocuments(requestMock);
    await listImports(requestMock);
    await deleteDocument(requestMock, "doc/with space");
    await submitImports(requestMock, [file]);
    await retryImportTask(requestMock, "batch/1", "task/1");
    await retryFailedImports(requestMock, "batch/1");
    await cancelImportTask(requestMock, "batch/1", "task/1");

    expect(requestMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/documents",
      "/api/v1/imports?limit=20",
      "/api/v1/documents/doc%2Fwith%20space",
      "/api/v1/imports",
      "/api/v1/imports/batch%2F1/tasks/task%2F1/retry",
      "/api/v1/imports/batch%2F1/retry-failed",
      "/api/v1/imports/batch%2F1/tasks/task%2F1/cancel",
    ]);
    const uploadOptions = requestMock.mock.calls[3]?.[1];
    expect(uploadOptions?.body).toBeInstanceOf(FormData);
    expect(new Headers(uploadOptions?.headers).has("Content-Type")).toBe(false);
    expect((uploadOptions?.body as FormData).getAll("files")).toEqual([file]);
  });

  it("polls every two seconds only while visible data has an active task", async () => {
    vi.useFakeTimers();
    requestMock
      .mockResolvedValueOnce([batch("running", 10)])
      .mockResolvedValueOnce([batch("succeeded", 100)]);
    const client = createClient();

    render(<ImportsHarness />, {
      wrapper: ({ children }) => <Wrapper client={client}>{children}</Wrapper>,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("running")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2001);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("succeeded")).toBeVisible();
    expect(requestMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(requestMock).toHaveBeenCalledTimes(2);
  });

  it("refetches on focus and invalidates documents after active-to-succeeded", async () => {
    requestMock
      .mockResolvedValueOnce([batch("running", 20)])
      .mockResolvedValueOnce([batch("succeeded", 100)]);
    const client = createClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    render(<ImportsHarness />, {
      wrapper: ({ children }) => <Wrapper client={client}>{children}</Wrapper>,
    });
    expect(await screen.findByText("running")).toBeVisible();
    act(() => window.dispatchEvent(new Event("focus")));
    expect(await screen.findByText("succeeded")).toBeVisible();
    expect(requestMock).toHaveBeenCalledTimes(2);

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: DOCUMENTS_QUERY_KEY });
    });
  });

  it("writes a mutation's server batch into cache before invalidating imports", async () => {
    const authoritative = batch("queued");
    requestMock.mockResolvedValue(authoritative);
    const client = createClient();
    client.setQueryData(IMPORTS_QUERY_KEY, [batch("failed")]);
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();

    render(<MutationHarness />, {
      wrapper: ({ children }) => <Wrapper client={client}>{children}</Wrapper>,
    });
    screen.getByRole("button", { name: "重试" }).click();

    await waitFor(() => {
      expect(client.getQueryData<ImportBatch[]>(IMPORTS_QUERY_KEY)?.[0]).toEqual(
        authoritative,
      );
    });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: IMPORTS_QUERY_KEY });
  });

  it.each([
    ["success", undefined],
    ["failure", new ApiError(500, "document_delete_failed", "文档删除失败，请重试")],
  ])("refetches authoritative document and import data after delete %s", async (_label, error) => {
    if (error) requestMock.mockRejectedValue(error);
    else requestMock.mockResolvedValue(undefined);
    const client = createClient();
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();

    render(<MutationHarness />, {
      wrapper: ({ children }) => <Wrapper client={client}>{children}</Wrapper>,
    });
    screen.getByRole("button", { name: "删除" }).click();

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: DOCUMENTS_QUERY_KEY });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: IMPORTS_QUERY_KEY });
    });
  });
});
