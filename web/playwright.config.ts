import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: {
    timeout: 5_000,
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      scale: "css",
    },
  },
  reporter: [["line"]],
  outputDir: "test-results",
  snapshotPathTemplate:
    "{testDir}/{testFilePath}-snapshots/{arg}-{projectName}{ext}",
  use: {
    ...devices["Desktop Chrome"],
    browserName: "chromium",
    colorScheme: "light",
    contextOptions: { reducedMotion: "reduce" },
    locale: "zh-CN",
    screenshot: "only-on-failure",
    trace: "off",
    video: "off",
  },
  projects: [
    {
      name: "desktop",
      use: { viewport: { width: 1440, height: 1024 } },
    },
    {
      name: "tablet",
      use: { viewport: { width: 1024, height: 768 } },
    },
    {
      name: "mobile",
      use: { viewport: { width: 390, height: 844 } },
    },
  ],
});
