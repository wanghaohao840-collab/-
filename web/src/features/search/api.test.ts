import { describe, expect, it, vi } from "vitest";
import { searchDocuments } from "./api";

describe("search API", () => {
  it("posts trimmed text and explicit scope without putting content in the URL", async () => {
    const request = vi.fn().mockResolvedValue({ results: [] });
    const signal = new AbortController().signal;
    await searchDocuments(request, { query: "  私密查询  ", document_ids: ["doc"], limit: 5 }, signal);
    expect(request).toHaveBeenCalledWith("/api/v1/search", {
      method: "POST", signal, cache: "no-store", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: "私密查询", document_ids: ["doc"], limit: 5 }),
    });
  });
});
