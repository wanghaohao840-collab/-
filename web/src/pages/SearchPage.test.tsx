import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { SearchPage } from "./SearchPage";
import { highlightedExcerpt } from "../components/ResearchWorkspace/presentation";

const mock = vi.hoisted(() => ({ request: vi.fn(), identity: "alice" }));
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ status: "authenticated", username: mock.identity, csrfToken: mock.identity, request: mock.request }) }));
const id = "00000000-0000-0000-0000-000000000001";
const item = { document_id: id, name: "学习资料.md", loaded_at: "today", status: "ready" };
const resultItem = { document_id: id, document_name: item.name, excerpt: "<script>原始摘录</script> 检索证据", rank: 1, score: 0.5, page_number: null, section: null, locator: { document_id: id, chunk_id: "c0", chunk_index: 0, content_sha256: "a".repeat(64) } };
const response = { request_id: "r", document_ids: [id], result_count: 1, results: [resultItem] };
function Path() { const location = useLocation(); return <output data-testid="path">{location.pathname}{location.search}</output>; }
function setup(path = "/search") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = <QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><SearchPage /><Path /></MemoryRouter></QueryClientProvider>;
  return { client, ui, ...render(ui) };
}
async function submit() {
  fireEvent.click(await screen.findByRole("checkbox", { name: item.name }));
  fireEvent.change(screen.getByLabelText("你想从这些资料中找到什么？"), { target: { value: "检索" } });
  fireEvent.click(screen.getByRole("button", { name: "检索证据" }));
}
async function detail() { await submit(); fireEvent.click(await screen.findByRole("button", { name: `查看来源 1：${item.name}` })); }

describe("SearchPage", () => {
  beforeEach(() => {
    mock.identity = "alice";
    mock.request.mockReset().mockImplementation(async (url: string) => {
      if (url === "/api/v1/documents") return { items: [item] };
      if (url === "/api/v1/search") return response;
      if (url === "/api/v1/notes") return { id: "saved-note" };
      throw new Error(`Unexpected test request ${url}`);
    });
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute("open", ""); this.querySelector<HTMLButtonElement>("button")?.focus(); } });
    Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function (this: HTMLDialogElement) { this.removeAttribute("open"); } });
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
  it("selects a linked source document only after confirming it is in this account's library", async () => {
    setup(`/search?documents=${id}`);
    expect(await screen.findByRole("checkbox", { name: item.name })).toBeChecked();
    expect(mock.request.mock.calls.filter(([url]) => url === "/api/v1/search")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("你想从这些资料中找到什么？"), { target: { value: "学习闭环" } });
    fireEvent.click(screen.getByRole("button", { name: "检索证据" }));
    expect(await screen.findByText("返回 1 条相关片段")).toBeVisible();
    const request = mock.request.mock.calls.find(([url]) => url === "/api/v1/search");
    expect(JSON.parse(request![1].body)).toMatchObject({ document_ids: [id] });
  });
  it("does not preselect a linked document that is absent from this account's library", async () => {
    setup("/search?documents=missing-document");
    expect(await screen.findByRole("checkbox", { name: item.name })).not.toBeChecked();
    expect(screen.getByText("已选择 0 份资料")).toBeVisible();
    expect(mock.request.mock.calls.filter(([url]) => url === "/api/v1/search")).toHaveLength(0);
  });
  it("requires explicit scope and nonblank query, without auto-submission or persistence", async () => {
    setup();
    expect(screen.getByRole("button", { name: "检索证据" })).toBeDisabled();
    fireEvent.click(screen.getByText("更多操作"));
    expect(screen.getByRole("link", { name: "前往旧版" })).toHaveAttribute("href", "/legacy/");
    await submit();
    expect(await screen.findByText("返回 1 条相关片段")).toBeVisible();
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/search$/);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(document.querySelector("script")).toBeNull();
    fireEvent.change(screen.getByLabelText("你想从这些资料中找到什么？"), { target: { value: "changed" } });
    expect(screen.queryByRole("button", { name: /查看来源 1/ })).not.toBeInTheDocument();
    expect(screen.getByText("检索条件已变化，请重新检索。")).toBeVisible();
  });
  it("highlights metacharacters literally and keeps markup as text", () => {
    render(<p>{highlightedExcerpt("a.* <img> a.*", "a.*")}</p>);
    expect(document.querySelectorAll("mark")).toHaveLength(2);
    expect(document.querySelector("img")).toBeNull();
  });
  it("fills suggestions without searching and filters the library without losing selections", async () => {
    setup();
    fireEvent.click(await screen.findByRole("checkbox", { name: item.name }));
    fireEvent.click(screen.getByRole("button", { name: /哪些内容提到了 GraphRAG/ }));
    expect(screen.getByLabelText("你想从这些资料中找到什么？")).toHaveValue("哪些内容提到了 GraphRAG？");
    expect(mock.request.mock.calls.filter(([url]) => url === "/api/v1/search")).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("筛选文档"), { target: { value: "不存在的资料" } });
    expect(screen.getByText("没有匹配的资料，试试其他名称。")).toBeVisible();
    expect(screen.getByText("已选择 1 份资料")).toBeVisible();
    fireEvent.change(screen.getByLabelText("筛选文档"), { target: { value: "" } });
    expect(screen.getByRole("checkbox", { name: item.name })).toBeChecked();
  });
  it("opens a source-backed draft directly from the evidence card", async () => {
    setup(); await submit();
    fireEvent.click(await screen.findByRole("button", { name: `加入笔记 1：${item.name}` }));
    expect(screen.getByLabelText("笔记正文（保存前可编辑）")).toHaveValue(resultItem.excerpt);
    expect(mock.request.mock.calls.filter(([url]) => url === "/api/v1/notes")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "保存并打开笔记" })).toBeEnabled();
  });
  it("supports a recoverable denied clipboard and native dialog focus return", async () => {
    setup();
    await submit();
    const trigger = await screen.findByRole("button", { name: /查看来源 1/ });
    trigger.focus(); fireEvent.click(trigger);
    expect(screen.getByRole("button", { name: "关闭来源详情" })).toHaveFocus();
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "复制引用" }));
    expect(await screen.findByText(/无法访问剪贴板/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭来源详情" }));
    expect(trigger).toHaveFocus();
  });
  it("passes only the selected document ID to QA confirmation", async () => {
    setup(); await detail();
    fireEvent.click(screen.getByRole("button", { name: "基于此文档问答" }));
    expect(screen.getByTestId("path")).toHaveTextContent(`/qa?documents=${id}`);
    expect(mock.request.mock.calls.filter(([url]) => url.includes("/qa"))).toHaveLength(0);
  });
  it("saves edited text with locator-only authority and opens the saved note", async () => {
    setup(); await detail();
    fireEvent.click(screen.getByRole("button", { name: "保存为笔记" }));
    fireEvent.change(screen.getByLabelText("笔记正文（保存前可编辑）"), { target: { value: "我的理解" } });
    expect(mock.request.mock.calls.filter(([url]) => url === "/api/v1/notes")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "保存并打开笔记" }));
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/notes?note=saved-note"));
    const body = JSON.parse(mock.request.mock.calls.find(([url]) => url === "/api/v1/notes")![1].body);
    expect(body.body_markdown).toBe("我的理解");
    expect(body.source).toEqual({ kind: "document_chunk", locator: resultItem.locator });
  });
  it("reuses idempotency key on retry, changes it for an edited payload", async () => {
    const base = mock.request.getMockImplementation()!;
    mock.request.mockImplementation((url, options) => url === "/api/v1/notes" ? Promise.reject(new ApiError(503, "NOTE_SOURCE_UNAVAILABLE", "请稍后重试")) : base(url, options));
    setup(); await detail(); fireEvent.click(screen.getByRole("button", { name: "保存为笔记" }));
    for (let count = 1; count <= 3; count++) {
      if (count === 3) fireEvent.change(screen.getByLabelText("笔记正文（保存前可编辑）"), { target: { value: "edited" } });
      fireEvent.click(screen.getByRole("button", { name: "保存并打开笔记" }));
      await waitFor(() => expect(mock.request.mock.calls.filter(([url]) => url === "/api/v1/notes")).toHaveLength(count));
      await screen.findByRole("alert");
      await waitFor(() => expect(screen.getByRole("button", { name: "保存并打开笔记" })).toBeEnabled());
    }
    const bodies = mock.request.mock.calls.filter(([url]) => url === "/api/v1/notes").map(([, options]) => JSON.parse(options.body));
    expect(bodies[0].client_request_id).toBe(bodies[1].client_request_id);
    expect(bodies[2].client_request_id).not.toBe(bodies[0].client_request_id);
  });
  it("invalidates results and detail after the selected document disappears", async () => {
    const { client } = setup(); await detail();
    act(() => client.setQueriesData({ queryKey: ["documents"] }, { items: [] }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /查看来源 1/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "检索证据" })).toBeDisabled();
  });
  it("distinguishes an empty successful search from idle", async () => {
    const base = mock.request.getMockImplementation()!;
    mock.request.mockImplementation((url, options) => url === "/api/v1/search" ? Promise.resolve({ ...response, result_count: 0, results: [] }) : base(url, options));
    setup(); await submit();
    expect(await screen.findByRole("heading", { name: "未找到相关片段" })).toBeVisible();
  });
  it("limits scope to ten documents and offers only 5, 10 and 20 results", async () => {
    const base = mock.request.getMockImplementation()!;
    mock.request.mockImplementation((url, options) => url === "/api/v1/documents" ? Promise.resolve({ items: Array.from({ length: 11 }, (_, index) => ({ ...item, document_id: String(index), name: `Doc ${index}` })) }) : base(url, options));
    setup();
    const choices = await screen.findAllByRole("checkbox");
    choices.slice(0, 10).forEach((checkbox) => fireEvent.click(checkbox));
    expect(choices[10]).toBeDisabled();
    expect(screen.getAllByRole("option").map((option) => (option as HTMLOptionElement).value)).toEqual(["5", "10", "20"]);
    expect(screen.getByLabelText("你想从这些资料中找到什么？")).toHaveAttribute("maxlength", "1000");
    fireEvent.change(screen.getByLabelText("你想从这些资料中找到什么？"), { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "检索证据" })).toBeDisabled();
  });
  it("shows loading, then a retryable error without claiming there are zero results", async () => {
    let reject!: (error: unknown) => void;
    const base = mock.request.getMockImplementation()!;
    mock.request.mockImplementation((url, options) => url === "/api/v1/search" ? new Promise((_, fail) => { reject = fail; }) : base(url, options));
    setup(); await submit();
    expect(await screen.findByText("正在检索所选文档…")).toBeVisible();
    await act(async () => reject(new ApiError(429, "SEARCH_BUSY", "检索繁忙", {}, null, true)));
    expect(await screen.findByRole("alert")).toHaveTextContent("检索繁忙");
    expect(screen.queryByText("未找到相关片段")).not.toBeInTheDocument();
    expect(screen.getByLabelText("你想从这些资料中找到什么？")).toHaveValue("检索");
  });
  it("retains edited text when the source is stale and never navigates", async () => {
    const base = mock.request.getMockImplementation()!;
    mock.request.mockImplementation((url, options) => url === "/api/v1/notes" ? Promise.reject(new ApiError(404, "NOTE_SOURCE_NOT_FOUND", "not found")) : base(url, options));
    setup(); await detail(); fireEvent.click(screen.getByRole("button", { name: "保存为笔记" }));
    fireEvent.change(screen.getByLabelText("笔记正文（保存前可编辑）"), { target: { value: "保留理解" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并打开笔记" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("来源已变化");
    expect(screen.getByLabelText("笔记正文（保存前可编辑）")).toHaveValue("保留理解");
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/search$/);
  });
  it("does not navigate or show private scope when a save finishes after a user change", async () => {
    let resolve!: (value: unknown) => void;
    const base = mock.request.getMockImplementation()!;
    mock.request.mockImplementation((url, options) => {
      if (url === "/api/v1/notes") return new Promise((done) => { resolve = done; });
      if (url === "/api/v1/documents" && mock.identity === "bob") return Promise.resolve({ items: [] });
      return base(url, options);
    });
    const { client, rerender } = setup(); await detail();
    fireEvent.click(screen.getByRole("button", { name: "保存为笔记" }));
    fireEvent.click(screen.getByRole("button", { name: "保存并打开笔记" }));
    await waitFor(() => expect(resolve).toBeTypeOf("function"));
    mock.identity = "bob";
    rerender(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/search"]}><SearchPage /><Path /></MemoryRouter></QueryClientProvider>);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: item.name })).not.toBeInTheDocument();
    await act(async () => resolve({ id: "alice-note" }));
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/search$/);
    expect(client.getQueryCache().findAll({ queryKey: ["notes"] })).toHaveLength(0);
  });
});
