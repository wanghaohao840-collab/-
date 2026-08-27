import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MessageList, QaComposer, QaOverlay, SourcePanel, SummaryStatus } from "./QaWorkspace";
import type { QaJob, QaMessage, QaSource } from "../../features/qa/types";

const source: QaSource = { citation_id: "citation-1", document_id: "doc-1", document_name: "研究.md", page_number: 3, section: "方法", excerpt: "证据片段", reference: "研究.md#方法", truncated: false, source_type: "document" };
const failed: QaMessage = { message_id: "message-1", conversation_id: "conversation-1", turn_id: "turn-1", role: "assistant", status: "failed", mode: "auto", content: "", source_state: "none", retry_of_message_id: null, safe_error_code: "QA_ENGINE_UNAVAILABLE", trace_id: null, created_at: "now", updated_at: "now", completed_at: null, sources: [] };

describe("QA workspace components", () => {
  it("requires two documents and submits only on a non-composing shortcut", async () => {
    const submit = vi.fn();
    render(<QaComposer busy={false} documentCount={1} onSubmit={submit} />);
    expect(screen.getByRole("option", { name: "对比（需至少两篇文档）" })).toBeDisabled();
    const input = screen.getByLabelText("向这些文档提问");
    await userEvent.type(input, "什么是结论{enter}");
    expect(submit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true, isComposing: true });
    expect(submit).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
    expect(submit).toHaveBeenCalledWith("什么是结论", "auto");
  });

  it("renders safe failure/retry and complete citation evidence", async () => {
    const retry = vi.fn();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<><MessageList messages={[failed]} onSources={vi.fn()} onRetry={retry} /><SourcePanel sources={[source]} /></>);
    expect(screen.getByRole("alert")).toHaveTextContent("QA_ENGINE_UNAVAILABLE");
    await userEvent.click(screen.getByRole("button", { name: "重试回答" }));
    expect(retry).toHaveBeenCalledWith(failed);
    expect(screen.getByText("研究.md#方法")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "复制引用 1" }));
    expect(writeText).toHaveBeenCalledWith("研究.md#方法");
    expect(screen.getByText("引用 1 已复制")).toBeVisible();
    writeText.mockRejectedValueOnce(new Error("private browser failure"));
    await userEvent.click(screen.getByRole("button", { name: "复制引用 1" }));
    expect(screen.getByText("复制失败，请手动选择引用文本")).toBeVisible();
    expect(document.body).not.toHaveTextContent("private browser failure");
  });

  it("renders a decorative cue only while summary work is active", () => {
    const job: QaJob = { job_id: "job-1", conversation_id: "conversation-1", input_message_id: "input-1", assistant_message_id: "assistant-1", status: "running", stage: "summarizing", progress: 40, cancel_requested_at: null, attempt_count: 1, max_attempts: 3, safe_error_code: null, trace_id: null, created_at: "now", started_at: "now", finished_at: null, updated_at: "now" };
    const { rerender } = render(<SummaryStatus job={job} onCancel={vi.fn()} />);
    expect(document.querySelector(".qa-summary-status__skeleton")).toBeInTheDocument();
    rerender(<SummaryStatus job={{ ...job, status: "completed", progress: 100 }} onCancel={vi.fn()} />);
    expect(document.querySelector(".qa-summary-status__skeleton")).not.toBeInTheDocument();
  });

  it("traps focus, closes on Escape and returns focus", async () => {
    const close = vi.fn();
    const trigger = document.createElement("button");
    document.body.append(trigger); trigger.focus();
    const { unmount } = render(<QaOverlay title="引用来源" returnFocusTo={trigger} onClose={close}><button>内部操作</button></QaOverlay>);
    expect(screen.getByRole("button", { name: "关闭引用来源" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(close).toHaveBeenCalled();
    unmount();
    expect(trigger).toHaveFocus();
    trigger.remove();
  });
});
