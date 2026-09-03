import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/client";
import "../../styles/documents.css";
import { ImportDialog } from "./ImportDialog";

describe("ImportDialog", () => {
  afterEach(() => {
    document.body.style.overflow = "";
  });

  it("traps focus, closes on Escape, restores focus and body overflow", async () => {
    const trigger = document.createElement("button");
    trigger.textContent = "导入文档";
    document.body.append(trigger);
    trigger.focus();
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <ImportDialog
        open
        returnFocusTo={trigger}
        onClose={onClose}
        onImport={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "导入文档" })).toBeVisible();
    const close = screen.getByRole("button", { name: "关闭导入文档" });
    expect(close).toHaveFocus();
    expect(document.body).toHaveStyle({ overflow: "hidden" });
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();

    rerender(
      <ImportDialog
        open={false}
        returnFocusTo={trigger}
        onClose={onClose}
        onImport={vi.fn()}
      />,
    );
    expect(document.body.style.overflow).toBe("");
    expect(trigger).toHaveFocus();
    trigger.remove();
  });

  it("validates files, exposes invalid/drag states, and removes by local key", async () => {
    const user = userEvent.setup();
    render(
      <ImportDialog open returnFocusTo={null} onClose={vi.fn()} onImport={vi.fn()} />,
    );
    const picker = screen.getByTestId("file-picker");

    fireEvent.dragOver(picker, { dataTransfer: { files: [] } });
    expect(picker).toHaveAttribute("data-state", "drag-active");
    fireEvent.drop(picker, {
      dataTransfer: { files: [new File(["bad"], "unsafe.exe")] },
    });
    expect(picker).toHaveAttribute("data-state", "invalid");
    expect(screen.getByRole("alert")).toHaveTextContent("不支持");

    const input = screen.getByLabelText("选择文档");
    const first = new File(["first"], "duplicate.md", { type: "text/markdown" });
    const second = new File(["second"], "duplicate.md", { type: "text/markdown" });
    await user.upload(input, [first, second]);
    expect(screen.getAllByText("duplicate.md")).toHaveLength(2);
    await user.click(screen.getAllByRole("button", { name: "移除 duplicate.md" })[0]!);
    expect(screen.getAllByText("duplicate.md")).toHaveLength(1);
  });

  it("submits real files and displays only a safe ApiError message", async () => {
    const onImport = vi
      .fn<(files: File[]) => Promise<void>>()
      .mockRejectedValue(new ApiError(413, "import_file_too_large", "单个文件不能超过 100 MiB"));
    const user = userEvent.setup();
    render(
      <ImportDialog open returnFocusTo={null} onClose={vi.fn()} onImport={onImport} />,
    );
    const file = new File(["notes"], "notes.md", { type: "text/markdown" });
    await user.upload(screen.getByLabelText("选择文档"), file);
    await user.click(screen.getByRole("button", { name: "开始导入" }));

    expect(onImport).toHaveBeenCalledWith([file]);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "单个文件不能超过 100 MiB",
    );
    expect(screen.queryByText("import_file_too_large")).not.toBeInTheDocument();
  });

  it("enforces the 20-file, 100 MiB and 500 MiB client limits", async () => {
    const user = userEvent.setup();
    render(
      <ImportDialog open returnFocusTo={null} onClose={vi.fn()} onImport={vi.fn()} />,
    );
    const input = screen.getByLabelText("选择文档");
    const tooMany = Array.from(
      { length: 21 },
      (_, index) => new File([String(index)], `file-${index}.md`),
    );
    await user.upload(input, tooMany);
    expect(screen.getByRole("alert")).toHaveTextContent("最多选择 20 个文件");

    const oversized = new File(["large"], "large.pdf");
    Object.defineProperty(oversized, "size", { value: 100 * 1024 * 1024 + 1 });
    fireEvent.change(input, { target: { files: [oversized] } });
    expect(screen.getByRole("alert")).toHaveTextContent("超过单文件 100 MiB");

    const batch = Array.from({ length: 6 }, (_, index) => {
      const file = new File([String(index)], `batch-${index}.pdf`);
      Object.defineProperty(file, "size", { value: 90 * 1024 * 1024 });
      return file;
    });
    fireEvent.change(input, { target: { files: batch } });
    expect(screen.getByRole("alert")).toHaveTextContent("每批 500 MiB");
  });

  it("publishes the approved mobile-sheet and 44px control CSS contracts", () => {
    render(
      <ImportDialog open returnFocusTo={null} onClose={vi.fn()} onImport={vi.fn()} />,
    );
    expect(screen.getByRole("dialog", { name: "导入文档" })).toHaveClass(
      "import-dialog",
    );
    expect(screen.getByRole("button", { name: "关闭导入文档" })).toHaveClass(
      "document-action-target",
    );
    expect(screen.getByRole("button", { name: "开始导入" })).toHaveClass(
      "import-dialog__primary",
    );
    expect(document.documentElement).toHaveStyle({
      "--document-control-target": "44px",
    });
  });

  it("restores a pre-existing body overflow value when unmounted", async () => {
    document.body.style.overflow = "clip";
    const { unmount } = render(
      <ImportDialog open returnFocusTo={null} onClose={vi.fn()} onImport={vi.fn()} />,
    );
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    await waitFor(() => expect(document.body.style.overflow).toBe("clip"));
  });
});
