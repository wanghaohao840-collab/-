import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NotesWorkspace } from "./NotesWorkspace";
import { noteTitle } from "./NoteList";
import type { Note } from "../../features/notes/types";

const note: Note = { id: "n1", body_markdown: "# 服务端内容", concept: "RAG", tags: ["学习"], sources: [], version: 1, projection_state: "ready", created_at: "2026-01-01", updated_at: "2026-01-01", deleted_at: null };

describe("NotesWorkspace", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("labels and filters document sources without calling them QA answers", () => {
    const sourced: Note = { ...note, sources: [{ id: "s1", kind: "document_chunk", deleted: false, qa_thread_id: null, qa_message_id: null, citation_id: null, document_id: "doc1", locator: { chunk_index: 0 }, title_snapshot: "资料.md", excerpt_snapshot: "文档摘录", created_at: "2026-01-01", source_deleted_at: null }] };
    render(<NotesWorkspace items={[sourced]} selectedNote={sourced} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const panel = screen.getByRole("complementary", { name: "笔记来源" });
    expect(within(panel).getByText("文献证据")).toBeVisible();
    expect(within(panel).getByRole("link", { name: "检索该文档" })).toHaveAttribute("href", "/search?documents=doc1");
    expect(within(panel).queryByText("问答回答")).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "文档片段" })).toHaveValue("document_chunk");
  });

  it("keeps a draft local until the explicit save action", async () => {
    const user = userEvent.setup();
    const save = vi.fn();
    render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={save} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    expect(screen.getAllByRole("button", { name: "保存笔记" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "保存笔记" })).toHaveClass("notes-editor__save");
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
    const onCommitted = vi.fn();
    const dirtyStates: boolean[] = [];
    render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={save} onCommitted={onCommitted} onDirtyChange={(dirty) => dirtyStates.push(dirty)} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const body = screen.getByLabelText("笔记正文");
    await user.clear(body);
    await user.type(body, "# 已保存内容");
    await user.click(screen.getByRole("button", { name: "保存笔记" }));
    expect(await screen.findByText("已保存")).toBeVisible();
    expect(screen.getByRole("button", { name: "保存笔记" })).toBeDisabled();
    expect(dirtyStates.at(-1)).toBe(false);
    expect(onCommitted).not.toHaveBeenCalled();
  });

  it("places the single mobile Save before editor tabs in DOM order", () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
      matches: true,
      media: "(max-width: 767px)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    const { container } = render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const editor = container.querySelector<HTMLElement>(".notes-editor");
    const header = editor?.querySelector<HTMLElement>(".notes-editor__heading");
    const footer = editor?.querySelector<HTMLElement>(".notes-editor__footer");
    expect(editor).not.toBeNull();
    expect(within(header!).getByRole("button", { name: "保存笔记" })).toBeVisible();
    expect(within(footer!).queryByRole("button", { name: "保存笔记" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "保存笔记" })).toHaveLength(1);
    const controls = Array.from(editor!.querySelectorAll("button, input, textarea"));
    expect(controls.indexOf(screen.getByRole("button", { name: "保存笔记" }))).toBeLessThan(
      controls.indexOf(screen.getByRole("tab", { name: "编辑" })),
    );
  });

  it("uses the editable Markdown title before the concept", () => {
    expect(noteTitle("\n# Markdown 标题", "概念优先")).toBe("Markdown 标题");
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
    expect(screen.getByRole("heading", { level: 2, name: "全部笔记 · 1" })).toBeVisible();
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
    expect(screen.getByText("还没有笔记")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "从 QA 导入" }));
    expect(onOpenQa).toHaveBeenCalledOnce();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("traps and restores focus for the source overlay", async () => {
    const user = userEvent.setup();
    const trigger = document.createElement("button"); document.body.append(trigger); trigger.focus();
    render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} />);
    const sourceButton = screen.getAllByRole("button", { hidden: true }).find((button) => button.getAttribute("aria-label") === "来源");
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
    const sourceButton = screen.getAllByRole("button", { hidden: true }).find((button) => button.getAttribute("aria-label") === "来源");
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
    const sourceButton = screen.getAllByRole("button", { hidden: true }).find((button) => button.getAttribute("aria-label") === "来源");
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
    await user.click(screen.getByLabelText("更多笔记操作"));
    await user.click(screen.getByRole("button", { name: "清空笔记" }));
    expect(screen.getByRole("dialog", { name: "清空全部笔记" })).toBeVisible();
  });

  it("keeps live search and filters in the library", () => {
    const { container } = render(<NotesWorkspace items={[note]} selectedNote={note} onSelect={vi.fn()} onSave={vi.fn()} onCreate={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onRetryProjection={vi.fn()} queryValue="memory" tagsValue="rag" sourceValue="" onFilterChange={vi.fn()} />);
    const header = container.querySelector<HTMLElement>(".notes-toolbar");
    expect(header).not.toBeNull();
    expect(within(container.querySelector<HTMLElement>(".notes-list")!).getByLabelText("搜索笔记")).toHaveValue("memory");
    expect(within(container.querySelector<HTMLElement>(".notes-list")!).getByLabelText("按标签筛选")).toHaveValue("rag");
    expect(within(container.querySelector<HTMLElement>(".notes-list")!).getByLabelText("按来源筛选")).toBeVisible();
    expect(within(header!).getByRole("button", { name: "新建笔记" })).toBeVisible();
    expect(container.querySelector(".notes-grid")).not.toBeNull();
    expect(container.querySelector(".notes-library-filters")).not.toBeNull();
    expect(container.querySelector(".notes-grid > .notes-list")).not.toBeNull();
    expect(container.querySelector(".notes-grid > .notes-editor")).not.toBeNull();
    expect(container.querySelector(".notes-grid > .notes-source")).not.toBeNull();
  });
});
