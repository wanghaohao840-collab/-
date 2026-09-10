import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InsightsPage } from "./InsightsPage";

const mocks = { stats: vi.fn(), reports: vi.fn(), report: vi.fn(), create: vi.fn() };
vi.mock("../features/insights/queries", () => ({
  useStats: () => mocks.stats(), useReports: () => mocks.reports(),
  useReport: () => mocks.report(), useCreateReport: () => mocks.create(),
}));

describe("InsightsPage", () => {
  beforeEach(() => {
    mocks.stats.mockReturnValue({ isPending: false, error: null, data: { document_count: 1, completed_question_count: 2, note_count: 3, report_count: 0, active_days: 1, window_days: 30, activity: [{ date: "2026-09-03", documents: 1, questions: 2, notes: 3, total: 6 }] } });
    mocks.reports.mockReturnValue({ isPending: false, data: [] });
    mocks.report.mockReturnValue({ isPending: false, data: null, error: null });
    mocks.create.mockReturnValue({ isPending: false, error: null, mutateAsync: vi.fn() });
  });

  it("shows verified statistics and disclosure", () => {
    render(<MemoryRouter><InsightsPage /></MemoryRouter>);
    expect(screen.getByText("近 30 天活动")).toBeVisible();
    expect(screen.getByText(/不推断阅读时长或掌握度/)).toBeVisible();
  });

  it("stores the reports tab in the URL and shows its empty state", () => {
    render(<MemoryRouter><InsightsPage /></MemoryRouter>);
    fireEvent.click(screen.getByRole("tab", { name: "学习报告" }));
    expect(screen.getByText("尚未生成学习报告。")).toBeVisible();
    expect(screen.getByRole("button", { name: "生成报告" })).toBeEnabled();
  });

  it("offers a retry after statistics fail", () => {
    const refetch = vi.fn();
    mocks.stats.mockReturnValue({ isPending: false, error: new Error("offline"), refetch });
    render(<MemoryRouter><InsightsPage /></MemoryRouter>);
    expect(screen.getByRole("alert")).toHaveTextContent("统计加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("distinguishes report history errors from a genuinely empty library", () => {
    mocks.reports.mockReturnValue({ isPending: false, error: new Error("offline") });
    render(<MemoryRouter initialEntries={["/insights?tab=reports"]}><InsightsPage /></MemoryRouter>);
    expect(screen.getByRole("alert")).toHaveTextContent("报告历史加载失败");
    expect(screen.queryByText("尚未生成学习报告。")).not.toBeInTheDocument();
  });
});
