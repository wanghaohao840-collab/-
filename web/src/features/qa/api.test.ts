import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest } from "../../api/client";
import type { AuthContextValue } from "../../auth/AuthProvider";
import { askQaQuestion, createQaConversation, startQaSummary } from "./api";

describe("QA API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends exact fixed-scope and idempotent request bodies", async () => {
    const request = vi.fn().mockResolvedValue({}) as unknown as AuthContextValue["request"];
    await createQaConversation(request, ["doc-a", "doc-b"]);
    await askQaQuestion(request, "conversation-1", "比较结论", "compare", "request-stable");
    await startQaSummary(request, "conversation-1", "聚焦方法", "summary-stable");
    expect(request).toHaveBeenNthCalledWith(1, "/api/v1/qa/conversations", expect.objectContaining({ method: "POST", body: JSON.stringify({ document_ids: ["doc-a", "doc-b"] }) }));
    expect(request).toHaveBeenNthCalledWith(2, "/api/v1/qa/conversations/conversation-1/messages", expect.objectContaining({ body: JSON.stringify({ question: "比较结论", mode: "compare", client_request_id: "request-stable" }) }));
    expect(request).toHaveBeenNthCalledWith(3, "/api/v1/qa/conversations/conversation-1/summary-jobs", expect.objectContaining({ body: JSON.stringify({ instruction: "聚焦方法", client_request_id: "summary-stable" }) }));
  });

  it("maps safe trace and retryability while tolerating missing trace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "QA_ENGINE_UNAVAILABLE", message: "暂时不可用", retryable: true, field_errors: {}, trace_id: "trace-safe" } }), { status: 503 })));
    const error = await apiRequest("/api/v1/qa/capabilities").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ traceId: "trace-safe", retryable: true, code: "QA_ENGINE_UNAVAILABLE" });
  });
});
