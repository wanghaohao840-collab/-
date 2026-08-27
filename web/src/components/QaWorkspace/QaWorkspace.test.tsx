import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MessageList, QaComposer, QaOverlay, SourcePanel } from "./QaWorkspace";
import type { QaMessage, QaSource } from "../../features/qa/types";

const source: QaSource = { citation_id: "citation-1", document_id: "doc-1", document_name: "研究.md", page_number: 3, section: "方法", excerpt: "证据片段", reference: "研究.md#方法", truncated: false, source_type: "document" };
const failed: QaMessage = { message_id: "message-1", conversation_id: "conversation-1", turn_id: "turn-1", role: "assistant", status: "failed", mode: "auto", content: "", source_state: "none", retry_of_message_id: null, safe_error_code: "QA_ENGINE_UNAVAILABLE", trace_id: null, created_at: "now", updated_at: "now", completed_at: null, sources: [] };

describe("QA workspace components", () => {
  it("requires two documents before compare and submits on Enter", async () => {
    const submit = vi.fn();
    render(<QaComposer busy={false} documentCount={1} onSubmit={submit} />);
    expect(screen.getByRole("option", { name: "对比（需至少两篇文档）" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("向这些文档提问"), "什么是结论{enter}");
    expect(submit).toHaveBeenCalledWith("什么是结论", "auto");
  });

  it("renders safe failure/retry and complete citation evidence", async () => {
    const retry = vi.fn();
    render(<><MessageList messages={[failed]} onSources={vi.fn()} onRetry={retry} /><SourcePanel sources={[source]} /></>);
    expect(screen.getByRole("alert")).toHaveTextContent("QA_ENGINE_UNAVAILABLE");
    await userEvent.click(screen.getByRole("button", { name: "重试回答" }));
    expect(retry).toHaveBeenCalledWith(failed);
    expect(screen.getByText("研究.md#方法")).toBeVisible();
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
