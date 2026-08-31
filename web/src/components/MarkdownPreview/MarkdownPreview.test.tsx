import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownPreview } from "./MarkdownPreview";

describe("MarkdownPreview", () => {
  it("renders GFM while dropping hostile HTML, protocols and event attributes", () => {
    render(<MarkdownPreview markdown={'# Safe\n\n- [x] done\n\n<script>alert(1)</script><iframe src="javascript:alert(1)" onload="alert(2)"></iframe>\n\n[bad](javascript:alert(1))'} />);
    expect(screen.getByRole("heading", { name: "Safe" })).toBeVisible();
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.queryByText("alert(1)")).not.toBeInTheDocument();
    expect(document.querySelector("script, iframe, [onload]")).toBeNull();
    expect(document.querySelector("a[href^='javascript']")).toBeNull();
  });

  it("marks external links noopener while retaining safe relative links", () => {
    render(<MarkdownPreview markdown={'[external](https://example.com) [relative](/notes/1)'} />);
    expect(screen.getByRole("link", { name: "external" })).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: "relative" })).not.toHaveAttribute("rel");
  });
});
