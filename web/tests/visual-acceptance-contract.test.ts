import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const e2eRoot = resolve(import.meta.dirname, "../e2e");
const visualSpec = readFileSync(resolve(e2eRoot, "visual.spec.ts"), "utf8");

describe("visual acceptance contract", () => {
  it("does not inject network responses or browser history state", () => {
    expect(visualSpec).not.toMatch(/\bpage\.route\s*\(/);
    expect(visualSpec).not.toMatch(/\broute\.fulfill\s*\(/);
    expect(visualSpec).not.toMatch(/\bhistory\.(?:pushState|replaceState)\s*\(/);
  });

  it("keeps exactly the twenty-two reviewed baselines", () => {
    const snapshots = readdirSync(resolve(e2eRoot, "visual.spec.ts-snapshots"))
      .filter((name) => name.endsWith(".png"))
      .sort();

    expect(snapshots).toEqual([
      "documents-complete-desktop.png",
      "documents-complete-mobile.png",
      "documents-complete-tablet.png",
      "documents-empty-desktop.png",
      "documents-empty-mobile.png",
      "documents-empty-tablet.png",
      "login-desktop.png",
      "login-mobile.png",
      "login-tablet.png",
      "more-drawer-mobile.png",
      "server-error-desktop.png",
      "server-error-mobile.png",
      "server-error-tablet.png",
      "session-expired-desktop.png",
      "session-expired-mobile.png",
      "session-expired-tablet.png",
      "shell-desktop.png",
      "shell-mobile.png",
      "shell-tablet.png",
      "validation-error-desktop.png",
      "validation-error-mobile.png",
      "validation-error-tablet.png",
    ]);
  });
});
