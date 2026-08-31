import { describe, expect, it, vi } from "vitest";
import * as api from "./api";

describe("notes api", () => {
  it("serializes filters and opaque cursor without dropping AND tags", async () => {
    const request = vi.fn().mockResolvedValue({ items: [], next_cursor: null });
    await api.listNotes(request, { cursor: "opaque+/=", limit: 7, query: "  memory  ", tags: ["rag", "学习"], source_kind: "qa_citation" });
    const url = request.mock.calls[0][0] as string;
    expect(url).toBe("/api/v1/notes?cursor=opaque%2B%2F%3D&limit=7&query=memory&tags=rag%2C%E5%AD%A6%E4%B9%A0&source_kind=qa_citation&sort=updated_desc");
  });

  it("sends only server-accepted create fields and expected version", async () => {
    const request = vi.fn().mockResolvedValue({});
    await api.createNote(request, { body_markdown: "# note", concept: "RAG", tags: ["study"], client_request_id: "client-1", source: { kind: "qa_citation", qa_message_id: "message-1", citation_id: "citation-1" } });
    await api.updateNote(request, "note/1", { body_markdown: "updated", concept: null, tags: [], expected_version: 3 });
    expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({ body_markdown: "# note", concept: "RAG", tags: ["study"], client_request_id: "client-1", source: { kind: "qa_citation", qa_message_id: "message-1", citation_id: "citation-1" } });
    expect(request.mock.calls[1][0]).toBe("/api/v1/notes/note%2F1");
    expect(JSON.parse(request.mock.calls[1][1].body).expected_version).toBe(3);
  });

  it("uses the exact destructive endpoints", async () => {
    const request = vi.fn().mockResolvedValue({});
    await api.deleteNote(request, "n1", { expected_version: 2 });
    await api.clearNotes(request);
    await api.retryNoteProjections(request);
    expect(request.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ["/api/v1/notes/n1", "DELETE"], ["/api/v1/notes/clear", "POST"], ["/api/v1/notes/projections/retry", "POST"],
    ]);
    expect(JSON.parse(request.mock.calls[1][1].body)).toEqual({ confirmation: "清空全部笔记" });
  });
});
