import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OverviewPage } from "./OverviewPage";

const useOverview = vi.fn();
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ status: "authenticated", username: "测试读者" }) }));
vi.mock("../features/insights/queries", () => ({ useOverview: () => useOverview() }));

describe("OverviewPage", () => {
  beforeEach(() => useOverview.mockReset());
  it("renders truthful empty actions without sample data", () => {
    useOverview.mockReturnValue({ isPending: false, error: null, data: { stats: { document_count: 0, completed_question_count: 0, note_count: 0, active_days: 0, report_count: 0, window_days: 30, activity: [] }, recent_documents: [], recent_questions: [] } });
    render(<MemoryRouter><OverviewPage /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "你好，测试读者" })).toBeVisible();
    expect(screen.getByText("还没有可学习的文档。")).toBeVisible();
    expect(screen.getByRole("link", { name: "开始问答" })).toHaveAttribute("href", "/qa");
  });

  it("renders returned document and question records", () => {
    useOverview.mockReturnValue({ isPending: false, error: null, data: { stats: { document_count: 1, completed_question_count: 1, note_count: 0, active_days: 1, report_count: 0, window_days: 30, activity: [] }, recent_documents: [{ document_id: "doc", name: "真实文档.md", loaded_at: "2026-09-03T00:00:00Z" }], recent_questions: [{ question: "真实问题", asked_at: "2026-09-03T01:00:00Z", document_names: ["真实文档.md"] }] } });
    render(<MemoryRouter><OverviewPage /></MemoryRouter>);
    expect(screen.getAllByText("真实文档.md")).toHaveLength(2);
    expect(screen.getByText("真实问题")).toBeVisible();
  });

  it("announces loading without showing invented metrics", () => {
    useOverview.mockReturnValue({ isPending: true });
    render(<MemoryRouter><OverviewPage /></MemoryRouter>);
    expect(screen.getByRole("status")).toHaveTextContent("正在加载学习概览");
    expect(screen.queryByLabelText("学习指标")).not.toBeInTheDocument();
  });

  it("offers a retry after a failed overview request", () => {
    const refetch = vi.fn();
    useOverview.mockReturnValue({ isPending: false, error: new Error("offline"), refetch });
    render(<MemoryRouter><OverviewPage /></MemoryRouter>);
    expect(screen.getByRole("alert")).toHaveTextContent("概览加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(refetch).toHaveBeenCalledOnce();
  });
});
