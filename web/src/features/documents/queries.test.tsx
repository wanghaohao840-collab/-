import {
  focusManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
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
  useDocumentsQuery,
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

function UserDataHarness() {
  const documents = useDocumentsQuery();
  const imports = useImportsQuery();
  return (
    <>
      <output aria-label="文档所有者">
        {documents.data?.items[0]?.name ?? "loading"}
      </output>
      <output aria-label="导入所有者">
        {imports.data?.[0]?.tasks[0]?.original_name ?? "loading"}
      </output>
    </>
  );
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
    focusManager.setFocused(undefined);
    vi.restoreAllMocks();
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

  it("refetches once for a visibility/focus return sequence and invalidates completed documents", async () => {
    requestMock
      .mockResolvedValueOnce([batch("running", 20)])
      .mockResolvedValue([batch("succeeded", 100)]);
    const client = createClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");

    render(<ImportsHarness />, {
      wrapper: ({ children }) => <Wrapper client={client}>{children}</Wrapper>,
    });
    expect(await screen.findByText("running")).toBeVisible();
    let visibility: DocumentVisibilityState = "hidden";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(
      () => visibility,
    );
    act(() => window.dispatchEvent(new Event("visibilitychange")));
    visibility = "visible";
    act(() => window.dispatchEvent(new Event("visibilitychange")));
    expect(await screen.findByText("succeeded")).toBeVisible();
    await waitFor(() => expect(requestMock).toHaveBeenCalledTimes(2));

    act(() => window.dispatchEvent(new Event("focus")));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(requestMock).toHaveBeenCalledTimes(2);

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: DOCUMENTS_QUERY_KEY });
    });
  });

  it("removes user-scoped document and import data before a new user mounts", async () => {
    let user: "a" | "b" = "a";
    requestMock.mockImplementation((url: string) => {
      if (url === "/api/v1/documents") {
        return Promise.resolve({
          items: [
            {
              document_id: `document-${user}`,
              name: `user-${user}.md`,
              file_suffix: ".md",
              size_bytes: 12,
              loaded_at: null,
              status: "ready",
            },
          ],
        });
      }
      const currentBatch = batch("failed");
      currentBatch.batch_id = `batch-${user}`;
      currentBatch.tasks[0]!.original_name = `user-${user}-import.md`;
      return Promise.resolve([currentBatch]);
    });
    const client = createClient();
    const first = render(<UserDataHarness />, {
      wrapper: ({ children }) => <Wrapper client={client}>{children}</Wrapper>,
    });
    expect(await screen.findByText("user-a.md")).toBeVisible();
    expect(await screen.findByText("user-a-import.md")).toBeVisible();

    first.unmount();
    await waitFor(() => {
      expect(client.getQueryData(DOCUMENTS_QUERY_KEY)).toBeUndefined();
      expect(client.getQueryData(IMPORTS_QUERY_KEY)).toBeUndefined();
    });

    user = "b";
    render(<UserDataHarness />, {
      wrapper: ({ children }) => <Wrapper client={client}>{children}</Wrapper>,
    });
    expect(screen.queryByText("user-a.md")).not.toBeInTheDocument();
    expect(screen.queryByText("user-a-import.md")).not.toBeInTheDocument();
    expect(await screen.findByText("user-b.md")).toBeVisible();
    expect(await screen.findByText("user-b-import.md")).toBeVisible();
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
