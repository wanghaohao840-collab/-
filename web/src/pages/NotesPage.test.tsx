import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { render, screen } from "@testing-library/react";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
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
});
