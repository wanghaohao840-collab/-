import { describe, expect, it } from "vitest";
import { headingTitle, withTitle } from "./notePresentation";

describe("Markdown note titles", () => {
  it("reads without changing old content and inserts a title only on edit", () => {
    const original = "Existing understanding\n\n## Details\nEvidence";
    expect(headingTitle(original)).toBe("");
    expect(withTitle(original, "New title")).toBe(`# New title\n\n${original}`);
  });
  it("updates only the first level-one heading and keeps replacement characters literal", () => {
    expect(withTitle("# Old\r\n\r\nEvidence\n# Another", "$& understanding"))
      .toBe("# $& understanding\r\n\r\nEvidence\n# Another");
  });
});
