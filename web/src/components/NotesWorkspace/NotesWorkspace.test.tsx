import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NotesWorkspace } from "./NotesWorkspace";
import type { Note } from "../../features/notes/types";

const note: Note = { id: "n1", body_markdown: "# 服务端内容", concept: "RAG", tags: ["学习"], sources: [], version: 1, projection_state: "ready", created_at: "2026-01-01", updated_at: "2026-01-01", deleted_at: null };

describe("NotesWorkspace", () => {
  it("keeps a draft local until the explicit save action", async () => {
    const user = userEvent.setup();
    const save = vi.fn();
    render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={save} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const body = screen.getByLabelText("笔记正文");
    await user.clear(body);
    await user.type(body, "新的学习笔记");
    expect(save).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "保存笔记" }));
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ body_markdown: "新的学习笔记" }));
  });

  it("exposes source-deleted tombstones without old snapshots", () => {
    const deleted: Note = { ...note, sources: [{ id: null, kind: "qa_citation", deleted: true, qa_thread_id: null, qa_message_id: null, citation_id: null, document_id: null, locator: null, title_snapshot: null, excerpt_snapshot: null, created_at: "2026-01-01", source_deleted_at: "now" }] };
    render(<NotesWorkspace items={[deleted]} selectedNote={deleted} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    expect(screen.getByText("来源已删除")).toBeVisible();
    expect(screen.queryByText("旧标题")).not.toBeInTheDocument();
  });
});
