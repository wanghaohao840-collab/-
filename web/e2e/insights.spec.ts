import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, registerUser, test, uniqueUsername } from "./fixtures";

async function checkAccessibility(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const violations = result.violations.filter((item) => item.impact === "serious" || item.impact === "critical");
  expect(violations, JSON.stringify(violations, null, 2)).toEqual([]);
}

test("overview and insights have a real report lifecycle in every viewport", async ({ appUrl, page }, testInfo) => {
  test.slow();
  await registerUser(page, appUrl, uniqueUsername(`insights_${testInfo.project.name}`));
  await expect(page.getByText("还没有可学习的文档。")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("overview.png"), fullPage: true });
  await checkAccessibility(page);

  await page.goto(`${appUrl}/insights`);
  await expect(page.getByRole("heading", { name: "学习洞察", level: 1 })).toBeVisible();
  await expect(page.getByText("近 30 天活动")).toBeVisible();
  await checkAccessibility(page);
  await page.getByRole("tab", { name: "学习报告" }).click();
  await expect(page).toHaveURL(`${appUrl}/insights?tab=reports`);
  await expect(page.getByText("尚未生成学习报告。")).toBeVisible();
  await page.getByRole("button", { name: "生成报告" }).click();
  await expect(page.getByRole("heading", { name: "知研学习报告" })).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Markdown", exact: true }).click();
  expect((await downloadPromise).suggestedFilename()).toMatch(/^zhiyan-learning-report-.*\.md$/);
  const wordPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "Word", exact: true }).click();
  expect((await wordPromise).suggestedFilename()).toMatch(/^zhiyan-learning-report-.*\.docx$/);
  await page.screenshot({ path: testInfo.outputPath("insights-report.png"), fullPage: true });
  await checkAccessibility(page);
  const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(sizes.scroll).toBeLessThanOrEqual(sizes.client + 1);
});
