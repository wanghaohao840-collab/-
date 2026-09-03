import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { render, screen } from "@testing-library/react";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as noteQueries from "../features/notes/queries";
import { NotesPage, historyRestoreDelta, notesListFilters, validatedPrefillSource } from "./NotesPage";

vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ request: vi.fn().mockResolvedValue({ enabled: true, items: [], next_cursor: null }) }) }));

describe("NotesPage", () => {
  it("separates QA prefill identifiers from list filters", () => {
    expect(notesListFilters(new URLSearchParams("source_kind=qa_answer&qa_message_id=m1&citation_id=c1")).source_kind).toBe("");
    expect(notesListFilters(new URLSearchParams("source_kind=qa_citation")).source_kind).toBe("qa_citation");
    expect(validatedPrefillSource(new URLSearchParams("source_kind=qa_citation&qa_message_id=m1"))).toBeNull();
    expect(validatedPrefillSource(new URLSearchParams("source_kind=qa_citation&qa_message_id=m1&citation_id=c1"))).toEqual({ kind: "qa_citation", qa_message_id: "m1", citation_id: "c1" });
    expect(validatedPrefillSource(new URLSearchParams("source_kind=qa_answer&citation_id=c1"))).toBeNull();
    expect(historyRestoreDelta(4, 3)).toBe(1);
    expect(historyRestoreDelta(4, 5)).toBe(-1);
    expect(historyRestoreDelta(undefined, 5)).toBe(0);
  });

  it("renders the notes workspace route with URL filters", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/notes?query=memory&tags=rag,study"]}><NotesPage /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByRole("heading", { level: 1, name: "学习笔记" })).toBeVisible();
    expect(screen.getByLabelText("搜索笔记")).toHaveValue("memory");
  });

  it("restores a canceled history traversal and allows a confirmed traversal", async () => {
    const user = userEvent.setup();
    const originalConfirm = window.confirm;
    const originalUrl = window.location.href;
    window.history.replaceState({ idx: 10 }, "", "/notes?note=new");
    const go = vi.spyOn(window.history, "go").mockImplementation(() => undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><BrowserRouter><NotesPage /></BrowserRouter></QueryClientProvider>);
    await user.type(await screen.findByLabelText("笔记正文"), "draft");
    expect(await screen.findByText("有未保存更改")).toBeVisible();
    window.history.pushState({ idx: 11 }, "", "/qa");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(go).toHaveBeenCalledWith(-1);
    window.history.pushState({ idx: 10 }, "", "/notes?note=new");
    window.dispatchEvent(new PopStateEvent("popstate"));
    confirm.mockReturnValue(true);
    window.history.pushState({ idx: 12 }, "", "/documents");
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(go).toHaveBeenCalledTimes(1);
    go.mockRestore();
    confirm.mockRestore();
    window.confirm = originalConfirm;
    window.history.replaceState({}, "", originalUrl);
  });

  it("guards dirty fresh drafts across filters and the QA empty-state CTA", async () => {
    const user = userEvent.setup();
    const originalConfirm = window.confirm;
    const originalUrl = window.location.href;
    window.history.replaceState({ idx: 20 }, "", "/notes?note=new");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><BrowserRouter><NotesPage /></BrowserRouter></QueryClientProvider>);
    const body = await screen.findByLabelText("笔记正文");
    await user.type(body, "draft");
    const search = screen.getByLabelText("搜索笔记");
    await user.type(search, "memory");
    await user.keyboard("{Enter}");
    expect(window.location.search).toContain("note=new");
    expect(body).toHaveValue("draft");
    confirm.mockReturnValue(true);
    await user.keyboard("{Enter}");
    expect(window.location.pathname).toBe("/notes");
    expect(window.location.search).not.toContain("note=");

    const newButton = screen.getAllByRole("button", { name: "新建笔记" })[0];
    await user.click(newButton);
    const newBody = await screen.findByLabelText("笔记正文");
    await user.type(newBody, "draft again");
    const qaCta = screen.getByRole("button", { name: "从 QA 记录" });
    confirm.mockReturnValue(false);
    await user.click(qaCta);
    expect(window.location.pathname).toBe("/notes");
    expect(window.location.search).toContain("note=new");
    expect(newBody).toHaveValue("draft again");
    confirm.mockReturnValue(true);
    await user.click(qaCta);
    expect(window.location.pathname).toBe("/qa");
    expect(window.location.search).not.toContain("note=");
    confirm.mockRestore();
    window.confirm = originalConfirm;
    window.history.replaceState({}, "", originalUrl);
  });

  it("consumes a successful fresh create before committing URL selection", async () => {
    const user = userEvent.setup();
    const originalUrl = window.location.href;
    window.history.replaceState({ idx: 30 }, "", "/notes?note=new");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const created = { id: "created-1", body_markdown: "draft", concept: null, tags: [], sources: [], version: 1, projection_state: "ready" as const, created_at: "2026-01-01", updated_at: "2026-01-01", deleted_at: null };
    const create = vi.fn().mockResolvedValue(created);
    const mutationStub = { create: { isPending: false, error: null, mutateAsync: create }, update: { isPending: false, error: null, mutateAsync: vi.fn() }, remove: { isPending: false, error: null, mutateAsync: vi.fn() }, clear: { isPending: false, error: null, mutateAsync: vi.fn() }, retryProjection: { isPending: false, error: null, mutateAsync: vi.fn() } };
    const mutations = vi.spyOn(noteQueries, "useNoteMutations").mockReturnValue(mutationStub as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><BrowserRouter><NotesPage /></BrowserRouter></QueryClientProvider>);
    const body = await screen.findByLabelText("笔记正文");
    await user.type(body, "draft");
    await user.click(screen.getByRole("button", { name: "保存笔记" }));
    expect(create).toHaveBeenCalledOnce();
    expect(confirm).not.toHaveBeenCalled();
    expect(await screen.findByText("已保存")).toBeVisible();
    expect(screen.getByRole("button", { name: "保存笔记" })).toBeDisabled();
    expect(window.location.pathname).toBe("/notes");
    expect(window.location.search).toContain("note=created-1");
    await user.click(screen.getByRole("button", { name: "保存笔记" }));
    expect(create).toHaveBeenCalledOnce();
    mutations.mockRestore();
    confirm.mockRestore();
    window.history.replaceState({}, "", originalUrl);
  });

  it("treats a valid QA citation prefill as an editable fresh draft", async () => {
    const user = userEvent.setup();
    const originalUrl = window.location.href;
    window.history.replaceState({}, "", "/notes?source_kind=qa_citation&qa_message_id=message-1&citation_id=citation-1");
    const created = { id: "created-source-1", body_markdown: "draft", concept: null, tags: [], sources: [], version: 1, projection_state: "ready" as const, created_at: "2026-01-01", updated_at: "2026-01-01", deleted_at: null };
    const create = vi.fn().mockResolvedValue(created);
    const mutationStub = { create: { isPending: false, error: null, mutateAsync: create }, update: { isPending: false, error: null, mutateAsync: vi.fn() }, remove: { isPending: false, error: null, mutateAsync: vi.fn() }, clear: { isPending: false, error: null, mutateAsync: vi.fn() }, retryProjection: { isPending: false, error: null, mutateAsync: vi.fn() } };
    const mutations = vi.spyOn(noteQueries, "useNoteMutations").mockReturnValue(mutationStub as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><BrowserRouter><NotesPage /></BrowserRouter></QueryClientProvider>);
    await user.type(await screen.findByLabelText("笔记正文"), "draft");
    await user.click(screen.getByRole("button", { name: "保存笔记" }));
    expect(create).toHaveBeenCalledWith(expect.objectContaining({
      source: { kind: "qa_citation", qa_message_id: "message-1", citation_id: "citation-1" },
    }));
    mutations.mockRestore();
    window.history.replaceState({}, "", originalUrl);
  });
});
