import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  ImportBatch,
  ImportCounts,
  ImportStage,
  ImportTask,
} from "../../features/documents/types";
import { ImportBatchPanel } from "./ImportBatchPanel";

function importTask(overrides: Partial<ImportTask> = {}): ImportTask {
  return {
    task_id: "task-1",
    document_id: "document-1",
    original_name: "notes.md",
    file_suffix: ".md",
    size_bytes: 12,
    status: "running",
    stage: "parsing",
    progress: 42,
    error_code: null,
    error_summary: null,
    cancel_requested_at: null,
    created_at: "2026-08-22T10:00:00+00:00",
    started_at: "2026-08-22T10:00:01+00:00",
    finished_at: null,
    updated_at: "2026-08-22T10:00:02+00:00",
    ...overrides,
  };
}

function counts(tasks: ImportTask[]): ImportCounts {
  return tasks.reduce<ImportCounts>(
    (current, task) => ({
      ...current,
      [task.status]: current[task.status] + 1,
    }),
    {
      total: tasks.length,
      queued: 0,
      running: 0,
      retry_wait: 0,
      succeeded: 0,
      failed: 0,
      cancelled: 0,
    },
  );
}

function importBatch(tasks: ImportTask[]): ImportBatch {
  return {
    batch_id: "batch-1",
    created_at: "2026-08-22T10:00:00+00:00",
    updated_at: "2026-08-22T10:00:02+00:00",
    counts: counts(tasks),
    tasks,
  };
}

function renderPanel(tasks: ImportTask[]) {
  return render(
    <ImportBatchPanel
      batches={[importBatch(tasks)]}
      onCancel={vi.fn()}
      onRetry={vi.fn()}
      onRetryFailed={vi.fn()}
    />,
  );
}

describe("ImportBatchPanel", () => {
  it.each([
    ["parsing", "正在解析文档"],
    ["chunking", "正在切分内容"],
    ["embedding", "正在生成索引"],
    ["persisting", "正在保存索引"],
    ["committing", "正在提交文档"],
  ] as const)("maps the %s server stage", (stage, label) => {
    renderPanel([importTask({ stage: stage as ImportStage })]);
    expect(screen.getByText(`${label} · 42%`)).toBeVisible();
  });

  it("keeps an active mixed batch active while exposing its failed task", () => {
    renderPanel([
      importTask({ task_id: "running", original_name: "running.md" }),
      importTask({
        task_id: "failed",
        original_name: "failed.md",
        status: "failed",
        stage: "failed",
        progress: 100,
        error_code: "import_stage_failed",
        error_summary: "索引失败，请重试",
      }),
    ]);

    expect(
      screen.getByRole("heading", { name: "正在导入，1 个文件失败" }),
    ).toBeVisible();
    expect(screen.queryByText(/导入完成/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试 failed.md" })).toBeVisible();
  });

  it("shows a pending cancellation and prevents a duplicate cancel request", () => {
    renderPanel([
      importTask({
        cancel_requested_at: "2026-08-22T10:00:03+00:00",
        stage: "embedding",
      }),
    ]);

    expect(screen.getAllByText("正在取消")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "取消 notes.md" })).not.toBeInTheDocument();
  });

  it("renders terminal results as a non-interactive status block", () => {
    renderPanel([
      importTask({ status: "succeeded", stage: "succeeded", progress: 100 }),
      importTask({
        task_id: "cancelled",
        original_name: "cancelled.md",
        status: "cancelled",
        stage: "cancelled",
      }),
    ]);

    const terminal = screen.getByRole("region", { name: "最近导入结果" });
    expect(terminal).toHaveTextContent("完成 1 · 取消 1");
    expect(terminal.querySelector("summary")).not.toBeInTheDocument();
  });
});
