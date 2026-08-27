import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import * as qaApi from "./api";
import { qaConversationKey, qaJobKey, qaMessagesKey, useQaJob, useQaMutations } from "./queries";
import type { QaJob, QaMessage } from "./types";

const mocks = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../auth/AuthProvider", () => ({ useAuth: () => ({ request: mocks.request }) }));

const completedJob: QaJob = { job_id: "job-1", conversation_id: "conversation-1", input_message_id: "input-1", assistant_message_id: "assistant-1", status: "completed", stage: "completed", progress: 100, cancel_requested_at: null, attempt_count: 1, max_attempts: 3, safe_error_code: null, trace_id: null, created_at: "now", started_at: "now", finished_at: "now", updated_at: "done" };
const pendingMessage: QaMessage = { message_id: "assistant-1", conversation_id: "conversation-1", turn_id: "turn-1", role: "assistant", status: "pending", mode: "auto", content: "", source_state: "none", retry_of_message_id: null, safe_error_code: null, trace_id: null, created_at: "now", updated_at: "now", completed_at: null, sources: [] };

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
    });
  });

  it("cancels a durable job and stores the authoritative server response", async () => {
    const cancelled = { ...completedJob, status: "cancelled" as const, stage: "cancelled" };
    vi.spyOn(qaApi, "cancelQaJob").mockResolvedValue(cancelled);
    const { client, wrapper } = harness();
    const { result } = renderHook(useQaMutations, { wrapper });
    await act(() => result.current.cancel.mutateAsync("job-1"));
    expect(client.getQueryData(qaJobKey("job-1"))).toEqual(cancelled);
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
