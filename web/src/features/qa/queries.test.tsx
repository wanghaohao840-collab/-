import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import * as qaApi from "./api";
import {
  qaActiveSummaryKey,
  qaConversationKey,
  qaJobKey,
  qaMessagesKey,
  useQaActiveSummary,
  useQaConversations,
  useQaJob,
  useQaMessages,
  useQaMutations,
} from "./queries";
import type { QaJob, QaMessage } from "./types";

const mocks = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../auth/AuthProvider", () => ({ useAuth: () => ({ request: mocks.request }) }));

const completedJob: QaJob = { job_id: "job-1", conversation_id: "conversation-1", input_message_id: "input-1", assistant_message_id: "assistant-1", status: "completed", stage: "completed", progress: 100, cancel_requested_at: null, attempt_count: 1, max_attempts: 3, safe_error_code: null, trace_id: null, created_at: "now", started_at: "now", finished_at: "now", updated_at: "done" };
const pendingMessage: QaMessage = { message_id: "assistant-1", conversation_id: "conversation-1", turn_id: "turn-1", role: "assistant", status: "pending", mode: "auto", content: "", source_state: "none", retry_of_message_id: null, safe_error_code: null, trace_id: null, created_at: "now", updated_at: "now", completed_at: null, sources: [] };

const message = (id: string): QaMessage => ({
  ...pendingMessage,
  message_id: id,
  status: "completed",
  content: id,
  completed_at: "now",
});

const conversation = (id: string) => ({
  conversation_id: id,
  title: id,
  origin: "product" as const,
  rolling_summary: "",
  summary_version: 0,
  created_at: "now",
  updated_at: "now",
  last_message_at: "now",
  documents: [],
});

function harness() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false, retryDelay: 0 } } });
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, wrapper };
}

describe("QA query lifecycle", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("reconciles conversation and message caches when durable polling completes", async () => {
    vi.spyOn(qaApi, "getQaJob").mockResolvedValue(completedJob);
    const { client, wrapper } = harness();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useQaJob("job-1"), { wrapper });
    await waitFor(() => expect(result.current.data?.status).toBe("completed"));
    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: qaConversationKey("conversation-1") });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: qaMessagesKey("conversation-1") });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: qaActiveSummaryKey("conversation-1") });
    });
  });

  it("loads recent messages first and prepends older pages without duplicates", async () => {
    const staleDuplicate = { ...message("m-3"), content: "stale-copy" };
    vi.spyOn(qaApi, "listQaMessages")
      .mockResolvedValueOnce({ items: [message("m-3"), message("m-4")], next_cursor: "older-2" })
      .mockResolvedValueOnce({ items: [message("m-1"), message("m-2"), staleDuplicate], next_cursor: null });
    const { wrapper } = harness();
    const { result, rerender } = renderHook(() => useQaMessages("conversation-1"), { wrapper });

    await waitFor(() => expect(result.current.items.map((item) => item.message_id)).toEqual(["m-3", "m-4"]));
    await act(() => result.current.fetchNextPage());

    await waitFor(() => expect(result.current.items.map((item) => item.message_id)).toEqual(["m-1", "m-2", "m-3", "m-4"]));
    expect(result.current.items.find((item) => item.message_id === "m-3")?.content).toBe("m-3");
    const items = result.current.items;
    const data = result.current.data;
    expect(data?.pages).toHaveLength(2);
    expect(data?.pageParams).toEqual([null, "older-2"]);
    expect(data?.items).toBe(items);
    rerender();
    expect(result.current.items).toBe(items);
    expect(result.current.data).toBe(data);
    expect(result.current.data?.items).toBe(items);
    expect(qaApi.listQaMessages).toHaveBeenNthCalledWith(2, expect.anything(), "conversation-1", "older-2", expect.any(AbortSignal));
    expect(result.current.hasNextPage).toBe(false);
  });

  it("keeps polling when any loaded message page contains pending work", async () => {
    vi.useFakeTimers();
    const list = vi.spyOn(qaApi, "listQaMessages")
      .mockResolvedValueOnce({ items: [message("completed-latest")], next_cursor: "older" })
      .mockResolvedValueOnce({ items: [{ ...pendingMessage, message_id: "pending-older" }], next_cursor: null })
      .mockResolvedValue({ items: [message("completed-latest")], next_cursor: "older" });
    const { wrapper } = harness();
    const { result } = renderHook(() => useQaMessages("conversation-1"), { wrapper });
    await vi.advanceTimersByTimeAsync(0);
    await act(async () => { await result.current.fetchNextPage(); });
    expect(list).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1500);
    expect(list.mock.calls.length).toBeGreaterThanOrEqual(3);
    vi.useRealTimers();
  });

  it("discovers the active summary by selected conversation", async () => {
    vi.spyOn(qaApi, "getQaActiveSummary").mockResolvedValue({ job: completedJob });
    const { wrapper } = harness();
    const { result } = renderHook(() => useQaActiveSummary("conversation-1"), { wrapper });
    await waitFor(() => expect(result.current.data?.job?.job_id).toBe("job-1"));
  });

  it("keeps conversations recent-first while loading older cursor pages", async () => {
    vi.spyOn(qaApi, "listQaConversations")
      .mockResolvedValueOnce({
        items: [conversation("c-4"), conversation("c-3")],
        next_cursor: "older-conversations",
      })
      .mockResolvedValueOnce({
        items: [conversation("c-3"), conversation("c-2")],
        next_cursor: null,
      });
    const { wrapper } = harness();
    const { result } = renderHook(() => useQaConversations(), { wrapper });

    await waitFor(() => expect(result.current.items.map((item) => item.conversation_id)).toEqual(["c-4", "c-3"]));
    await act(() => result.current.fetchNextPage());

    await waitFor(() => expect(result.current.items.map((item) => item.conversation_id)).toEqual(["c-4", "c-3", "c-2"]));
    expect(qaApi.listQaConversations).toHaveBeenNthCalledWith(
      2,
      expect.anything(),
      "older-conversations",
      expect.any(AbortSignal),
    );
  });

  it("cancels a durable job and stores the authoritative server response", async () => {
    const cancelled = { ...completedJob, status: "cancelled" as const, stage: "cancelled" };
    vi.spyOn(qaApi, "cancelQaJob").mockResolvedValue(cancelled);
    const { client, wrapper } = harness();
    const { result } = renderHook(useQaMutations, { wrapper });
    await act(() => result.current.cancel.mutateAsync("job-1"));
    expect(client.getQueryData(qaJobKey("job-1"))).toEqual(cancelled);
  });

  it("seeds active-summary discovery when a summary job starts", async () => {
    vi.spyOn(qaApi, "startQaSummary").mockResolvedValue(completedJob);
    const { client, wrapper } = harness();
    const { result } = renderHook(useQaMutations, { wrapper });
    await act(() => result.current.summarize.mutateAsync({ conversationId: "conversation-1", instruction: "聚焦方法", clientRequestId: "summary-stable" }));
    expect(client.getQueryData(qaJobKey("job-1"))).toEqual(completedJob);
    expect(client.getQueryData(qaActiveSummaryKey("conversation-1"))).toEqual({ job: completedJob });
  });

  it("reuses one client request ID for the single safe network retry", async () => {
    const ask = vi.spyOn(qaApi, "askQaQuestion")
      .mockRejectedValueOnce(new ApiError(503, "QA_ENGINE_UNAVAILABLE", "暂时不可用", {}, "trace-1", true))
      .mockResolvedValueOnce(pendingMessage);
    const { wrapper } = harness();
    const { result } = renderHook(useQaMutations, { wrapper });
    await act(() => result.current.ask.mutateAsync({ conversationId: "conversation-1", question: "结论？", mode: "auto", clientRequestId: "stable-request" }));
    expect(ask).toHaveBeenCalledTimes(2);
    expect(ask.mock.calls.map((call) => call[4])).toEqual(["stable-request", "stable-request"]);
  });
});
