import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NotesWorkspace } from "./NotesWorkspace";
import { noteTitle } from "./NoteList";
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

  it("consumes the saved Note so a successful save clears dirty state", async () => {
    const user = userEvent.setup();
    const saved = { ...note, body_markdown: "# 已保存内容", version: 2 };
    const save = vi.fn().mockResolvedValue(saved);
    const dirtyStates: boolean[] = [];
    render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={save} onDirtyChange={(dirty) => dirtyStates.push(dirty)} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const body = screen.getByLabelText("笔记正文");
    await user.clear(body);
    await user.type(body, "# 已保存内容");
    await user.click(screen.getByRole("button", { name: "保存笔记" }));
    expect(await screen.findByText("已保存")).toBeVisible();
    expect(screen.getByRole("button", { name: "保存笔记" })).toBeDisabled();
    expect(dirtyStates.at(-1)).toBe(false);
  });

  it("uses a non-empty concept before the first non-empty Markdown line", () => {
    expect(noteTitle("\n# Markdown 标题", "概念优先")).toBe("概念优先");
    expect(noteTitle("\n## **Markdown** 标题", "")).toBe("Markdown 标题");
  });

  it("exposes source-deleted tombstones without old snapshots", () => {
    const deleted: Note = { ...note, sources: [{ id: null, kind: "qa_citation", deleted: true, qa_thread_id: null, qa_message_id: null, citation_id: null, document_id: null, locator: null, title_snapshot: null, excerpt_snapshot: null, created_at: "2026-01-01", source_deleted_at: "now" }] };
    render(<NotesWorkspace items={[deleted]} selectedNote={deleted} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    expect(screen.getByText("来源已删除")).toBeVisible();
    expect(screen.queryByText("旧标题")).not.toBeInTheDocument();
  });

  it("derives the first Markdown heading and exposes pending/failed projection states", async () => {
    const retry = vi.fn();
    const pending = { ...note, concept: null, body_markdown: "# Derived title\ncontent", projection_state: "pending" as const };
    const { rerender } = render(<NotesWorkspace items={[pending]} selectedNote={pending} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={retry} />);
    expect(screen.getByRole("button", { name: /Derived title/ })).toBeVisible();
    expect(screen.getByRole("heading", { level: 2, name: "已加载 1 条笔记" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("正在同步");
    const failed = { ...pending, projection_state: "failed" as const };
    rerender(<NotesWorkspace items={[failed]} selectedNote={failed} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={retry} />);
    await userEvent.click(screen.getByRole("button", { name: "重试投影" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("shows both empty-state actions without auto-creating a note", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    const onOpenQa = vi.fn();
    render(<NotesWorkspace items={[]} onSelect={vi.fn()} onSave={vi.fn()} onCreate={onCreate} onOpenQa={onOpenQa} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    expect(screen.getByText("没有符合条件的笔记")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "从 QA 记录" }));
    expect(onOpenQa).toHaveBeenCalledOnce();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("traps and restores focus for the source overlay", async () => {
    const user = userEvent.setup();
    const trigger = document.createElement("button"); document.body.append(trigger); trigger.focus();
    render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const sourceButton = screen.getAllByRole("button", { hidden: true }).find((button) => button.textContent === "来源");
    expect(sourceButton).toBeDefined();
    await user.click(sourceButton!);
    expect(screen.getByRole("dialog", { name: "笔记来源" })).toBeVisible();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "笔记来源" })).not.toBeInTheDocument();
  });

  it("keeps source drawer focus stable across parent rerenders and reports locator copy", async () => {
    const user = userEvent.setup();
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } });
    const sourceNote = { ...note, sources: [{ id: "s1", kind: "qa_citation" as const, deleted: false, qa_thread_id: "t1", qa_message_id: "m1", citation_id: "c1", document_id: null, locator: { citation_id: "c1" }, title_snapshot: "来源", excerpt_snapshot: "摘录", created_at: "2026-01-01", source_deleted_at: null }] };
    const { rerender } = render(<NotesWorkspace items={[sourceNote]} selectedNote={sourceNote} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const sourceButton = screen.getAllByRole("button", { hidden: true }).find((button) => button.textContent === "来源");
    await user.click(sourceButton!);
    const copyButton = within(screen.getByRole("dialog", { name: "笔记来源" })).getByRole("button", { name: "复制来源定位" });
    copyButton.focus();
    rerender(<NotesWorkspace items={[{ ...sourceNote, projection_state: "failed" }]} selectedNote={{ ...sourceNote, projection_state: "failed" }} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    expect(document.activeElement).toBe(copyButton);
    await user.click(copyButton);
    expect(await screen.findByText("来源定位已复制")).toBeVisible();
    Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: originalClipboard });
  });

  it("reports locator copy failure without blocking the source drawer", async () => {
    const user = userEvent.setup();
    const originalClipboard = navigator.clipboard;
    Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    const sourceNote = { ...note, sources: [{ id: "s1", kind: "qa_citation" as const, deleted: false, qa_thread_id: "t1", qa_message_id: "m1", citation_id: "c1", document_id: null, locator: { citation_id: "c1" }, title_snapshot: "来源", excerpt_snapshot: "摘录", created_at: "2026-01-01", source_deleted_at: null }] };
    render(<NotesWorkspace items={[sourceNote]} selectedNote={sourceNote} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const sourceButton = screen.getAllByRole("button", { hidden: true }).find((button) => button.textContent === "来源");
    await user.click(sourceButton!);
    await user.click(within(screen.getByRole("dialog", { name: "笔记来源" })).getByRole("button", { name: "复制来源定位" }));
    expect(await screen.findByText("复制失败，请重试")).toBeVisible();
    Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: originalClipboard });
  });

  it("reports a projection retry failure without an unhandled rejection", async () => {
    const user = userEvent.setup();
    const retry = vi.fn().mockRejectedValue(new Error("offline"));
    const failed = { ...note, projection_state: "failed" as const };
    render(<NotesWorkspace items={[failed]} selectedNote={failed} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={retry} />);
    await user.click(screen.getByRole("button", { name: "重试投影" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("投影重试失败，请重试");
  });

  it("provides complete filter and clear confirmation surfaces", async () => {
    const user = userEvent.setup();
    render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn().mockResolvedValue(undefined)} onRetryProjection={vi.fn()} queryValue="memory" tagsValue="rag" sourceValue="" onFilterChange={vi.fn()} />);
    const filterButton = screen.getAllByRole("button", { hidden: true }).find((button) => button.textContent === "筛选");
    expect(filterButton).toBeDefined();
    await user.click(filterButton!);
    const filterDialog = screen.getByRole("dialog", { name: "筛选笔记" });
    expect(filterDialog).toContainElement(screen.getByLabelText("筛选中的标签"));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "清空笔记" }));
    expect(screen.getByRole("dialog", { name: "清空全部笔记" })).toBeVisible();
  });
});
